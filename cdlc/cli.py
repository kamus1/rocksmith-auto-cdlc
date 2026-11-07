"""CLI del generador de CDLC: audio + tab de Guitar Pro → .psarc de Rocksmith 2014.

Uso:
  python -m cdlc build --audio cancion.mp3 --tab cancion.gp
                       [--lead N] [--rhythm M | --no-rhythm] [--artist A --title T --album A --year Y]
                       [--tone categoría|archivo] [--cover img] [--name carpeta] [--no-deploy] [--no-lookup]
  python -m cdlc tones
  python -m cdlc inspect --tab cancion.gp

La lógica está en pipeline.py; aquí solo se leen los argumentos y se imprime el progreso.
"""

import argparse
import sys
from pathlib import Path

from . import pipeline
from .pipeline import DLC_DIR, TONE_CATEGORIES, PipelineError


def cmd_build(a: argparse.Namespace) -> None:
    opts = pipeline.BuildOptions(
        audio=Path(a.audio), tab=Path(a.tab), lead=a.lead, rhythm=a.rhythm, no_rhythm=a.no_rhythm,
        artist=a.artist, title=a.title, album=a.album, year=a.year, cover=a.cover, tone=a.tone,
        name=a.name, pad=a.pad, deploy=not a.no_deploy, lookup=not a.no_lookup)
    pipeline.build_song(opts, on_step=lambda n, title: print(f"[{n}/{pipeline.STEPS}] {title}", flush=True),
                        on_log=lambda line: print(f"      {line}", flush=True))


def cmd_tones(a: argparse.Namespace) -> None:
    print(pipeline.build_tones(Path(a.dlc)))


def cmd_inspect(a: argparse.Namespace) -> None:
    print(pipeline.rsbuild("inspect", str(Path(a.tab).resolve())))


def main(argv: list[str] | None = None) -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    p = argparse.ArgumentParser(prog="cdlc", description="Audio + tab Guitar Pro → CDLC de Rocksmith 2014")
    sub = p.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("build", help="genera el .psarc y lo copia a dlc/")
    b.add_argument("--audio", required=True)
    b.add_argument("--tab", required=True)
    b.add_argument("--lead", type=int, help="pista de la tab para el arrangement Lead (por defecto: automática)")
    b.add_argument("--rhythm", type=int, help="pista para el arrangement Rhythm (por defecto: automática)")
    b.add_argument("--no-rhythm", action="store_true", help="no generar arrangement Rhythm")
    b.add_argument("--artist")
    b.add_argument("--title")
    b.add_argument("--album")
    b.add_argument("--year", type=int)
    b.add_argument("--cover", help="portada (png/jpg)")
    b.add_argument("--tone", help=f"archivo de tono o categoría ({', '.join(TONE_CATEGORIES)}); "
                                  "por defecto: misma canción o mismo artista de la biblioteca")
    b.add_argument("--name", help="nombre de la carpeta en build/")
    b.add_argument("--pad", type=float, default=4.0, help="segundos de silencio al inicio (count-in)")
    b.add_argument("--no-deploy", action="store_true", help="no copiar a la carpeta dlc del juego")
    b.add_argument("--no-lookup", action="store_true", help="no buscar álbum, año y portada en iTunes")
    b.set_defaults(func=cmd_build)

    t = sub.add_parser("tones", help="extrae la biblioteca de tonos de los CDLC instalados")
    t.add_argument("--dlc", default=str(DLC_DIR))
    t.set_defaults(func=cmd_tones)

    i = sub.add_parser("inspect", help="muestra pistas, afinaciones y compases de la tab")
    i.add_argument("--tab", required=True)
    i.set_defaults(func=cmd_inspect)

    a = p.parse_args(argv)
    try:
        a.func(a)
    except PipelineError as e:
        if e.detail:
            sys.stderr.write(e.detail.rstrip() + "\n")
        raise SystemExit(f"Error: {e}")


if __name__ == "__main__":
    main()
