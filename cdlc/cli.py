"""CLI del generador de CDLC: audio + tab de Guitar Pro → .psarc de Rocksmith 2014.

Uso:
  python -m cdlc build --audio cancion.mp3 --tab cancion.gp --lead 0 [--rhythm 1]
                       [--artist A --title T --album A --year Y] [--name carpeta] [--no-deploy]
  python -m cdlc inspect --tab cancion.gp
"""

import argparse
import glob
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from . import align, audio

ROOT = Path(__file__).resolve().parent.parent
RSBUILD = ROOT / "src" / "RsBuild" / "bin" / "Release" / "net10.0" / "RsBuild.dll"
DOTNET = shutil.which("dotnet") or r"C:\Program Files\dotnet\dotnet.exe"
DLC_DIR = Path(r"C:\Program Files (x86)\Steam\steamapps\common\Rocksmith2014\dlc")
WINGET_LINKS = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Links"
SUPPORTED_WWISE = re.compile(r"20(19|21|22|23)")


def find_wwise() -> str | None:
    """Carpeta de una instalación de Wwise compatible (2019–2023)."""
    env = os.environ.get("WWISEROOT")
    if env and SUPPORTED_WWISE.search(env) and Path(env).is_dir():
        return env
    pattern = "WwiseConsole.exe"
    roots = [r"C:\Audiokinetic", r"C:\Program Files (x86)\Audiokinetic", r"C:\Program Files\Audiokinetic"]
    for root in roots:
        for d in sorted(glob.glob(os.path.join(root, "*")), reverse=True):
            if SUPPORTED_WWISE.search(os.path.basename(d)) and \
                    Path(d, "Authoring", "x64", "Release", "bin", pattern).exists():
                return d
    return None


def rsbuild(*args: str, env: dict | None = None) -> str:
    if not RSBUILD.exists():
        subprocess.run([DOTNET, "build", str(ROOT / "src" / "RsBuild"), "-c", "Release", "-v", "q"], check=True)
    p = subprocess.run([DOTNET, str(RSBUILD), *args], capture_output=True, text=True, encoding="utf-8",
                       errors="replace", env=env)
    if p.returncode != 0:
        sys.stderr.write(p.stdout + p.stderr)
        raise SystemExit(f"RsBuild {args[0]} falló (código {p.returncode})")
    return p.stdout


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "cancion"


def cmd_build(a: argparse.Namespace) -> None:
    if WINGET_LINKS.is_dir():
        os.environ["PATH"] = os.environ["PATH"] + os.pathsep + str(WINGET_LINKS)
    wwise = find_wwise()
    if wwise is None:
        raise SystemExit("No se encontró Wwise 2019–2023. Instálalo desde el Wwise Launcher (paquete Authoring).")

    audio_in = Path(a.audio).resolve()
    tab = Path(a.tab).resolve()
    out = ROOT / "build" / (a.name or slug(Path(a.tab).stem))
    out.mkdir(parents=True, exist_ok=True)

    print(f"[1/6] Audio → WAV (+{a.pad:g} s de silencio)")
    wav = audio.to_wav(audio_in, out / "song.wav", pad_seconds=a.pad)

    print("[2/6] Detección de beats (beat_this)")
    beats = audio.detect_beats(wav, out / "beats.json")
    print(f"      {len(beats['beats_ms'])} beats en {beats['device']}")

    print("[3/6] Línea de tiempo de la tab")
    print("      " + rsbuild("timeline", str(tab), str(out / "timeline.json")).strip())

    print("[4/6] Alineación tab ↔ audio")
    r = align.align_beats(out / "timeline.json", wav, out / "beats.json", out / "bar_times.json")
    bpm_note = f"compás 1 en {r['bar_times_ms'][0] / 1000:.2f} s, beats irregulares: {r['irregular_beat_intervals']}"
    print(f"      {bpm_note}")
    err = align.onset_error(out / "timeline.json", out / "bar_times.json", wav, a.lead)
    print(f"      error mediano de ataques (pista {a.lead}): {err['median_ms']} ms, "
          f"{err['within_50ms'] * 100:.0f}% a menos de 50 ms")

    print("[5/6] XML, audio Wwise y empaquetado")
    args = ["build", "--tab", str(tab), "--lead", str(a.lead), "--bar-times", str(out / "bar_times.json"),
            "--wav", str(wav), "--out-dir", str(out / "pkg")]
    if a.rhythm is not None:
        args += ["--rhythm", str(a.rhythm)]
    for key in ("artist", "title", "album", "year", "cover"):
        value = getattr(a, key)
        if value:
            args += [f"--{key}", str(value)]
    env = dict(os.environ, WWISEROOT=wwise)
    log = rsbuild(*args, env=env)
    print("      " + log.strip().replace("\n", "\n      "))
    psarc = Path(re.search(r"PSARC:\s+(.+)", log).group(1).strip())

    report = out / "pkg" / "report.md"
    with report.open("a", encoding="utf-8") as f:
        f.write(f"\n## Alineación\n- {bpm_note}\n- Error mediano de ataques: {err['median_ms']} ms "
                f"(p90 {err['p90_ms']} ms, {err['within_50ms'] * 100:.0f}% < 50 ms)\n")
        if r["suspicious_bars"]:
            f.write(f"- Compases con tempo raro: {r['suspicious_bars'][:40]}\n")

    if a.no_deploy:
        print(f"[6/6] Sin copiar a dlc/: {psarc}")
    else:
        shutil.copy2(psarc, DLC_DIR / psarc.name)
        print(f"[6/6] Copiado a {DLC_DIR / psarc.name}")
        print("      Vuelve a la pantalla de título del juego para que lo cargue.")


def cmd_inspect(a: argparse.Namespace) -> None:
    print(rsbuild("inspect", str(Path(a.tab).resolve())))


def main(argv: list[str] | None = None) -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    p = argparse.ArgumentParser(prog="cdlc", description="Audio + tab Guitar Pro → CDLC de Rocksmith 2014")
    sub = p.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("build", help="genera el .psarc y lo copia a dlc/")
    b.add_argument("--audio", required=True)
    b.add_argument("--tab", required=True)
    b.add_argument("--lead", type=int, required=True, help="pista de la tab para el arrangement Lead")
    b.add_argument("--rhythm", type=int, help="pista para un arrangement Rhythm (opcional)")
    b.add_argument("--artist")
    b.add_argument("--title")
    b.add_argument("--album")
    b.add_argument("--year", type=int)
    b.add_argument("--cover", help="portada (png/jpg)")
    b.add_argument("--name", help="nombre de la carpeta en build/")
    b.add_argument("--pad", type=float, default=4.0, help="segundos de silencio al inicio (count-in)")
    b.add_argument("--no-deploy", action="store_true", help="no copiar a la carpeta dlc del juego")
    b.set_defaults(func=cmd_build)

    i = sub.add_parser("inspect", help="muestra pistas, afinaciones y compases de la tab")
    i.add_argument("--tab", required=True)
    i.set_defaults(func=cmd_inspect)

    a = p.parse_args(argv)
    a.func(a)


if __name__ == "__main__":
    main()
