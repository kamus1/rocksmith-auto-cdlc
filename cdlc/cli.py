"""CDLC generator CLI: audio + Guitar Pro tab → Rocksmith 2014 .psarc.

Usage:
  python -m cdlc build --audio song.mp3 --tab song.gp
                       [--lead N] [--rhythm M | --no-rhythm] [--artist A --title T --album A --year Y]
                       [--tone category|file] [--cover img] [--name folder] [--no-deploy] [--no-lookup]
  python -m cdlc tones
  python -m cdlc inspect --tab song.gp
  python -m cdlc web [--port 8000] [--no-browser]

The logic lives in pipeline.py; this only parses arguments and prints progress.
"""

import argparse
import sys
from pathlib import Path

from . import pipeline
from .pipeline import TONE_CATEGORIES, PipelineError


def cmd_build(a: argparse.Namespace) -> None:
    opts = pipeline.BuildOptions(
        audio=Path(a.audio), tab=Path(a.tab), lead=a.lead, rhythm=a.rhythm, no_rhythm=a.no_rhythm,
        artist=a.artist, title=a.title, album=a.album, year=a.year, cover=a.cover, tone=a.tone,
        name=a.name, variant=a.variant, pad=a.pad, deploy=not a.no_deploy, lookup=not a.no_lookup)
    pipeline.build_song(opts, on_step=lambda n, title: print(f"[{n}/{pipeline.STEPS}] {title}", flush=True),
                        on_log=lambda line: print(f"      {line}", flush=True))


def cmd_tones(a: argparse.Namespace) -> None:
    print(pipeline.build_tones(Path(a.dlc) if a.dlc else None))


def cmd_inspect(a: argparse.Namespace) -> None:
    print(pipeline.rsbuild("inspect", str(Path(a.tab).resolve())))


def cmd_check(a: argparse.Namespace) -> None:
    missing = 0
    for key, value, fix, required in pipeline.environment_checks():
        if value:
            print(f"  OK       {key:8s} {value}")
        else:
            missing += required
            print(f"  {'MISSING' if required else 'optional':8s} {key:8s} {fix}")
    print("\nReady." if not missing else f"\n{missing} required item(s) missing.")
    if missing:
        raise SystemExit(1)


def cmd_web(a: argparse.Namespace) -> None:
    import threading
    import webbrowser

    import uvicorn

    url = f"http://127.0.0.1:{a.port}"
    print(f"Local web app at {url} (Ctrl+C to quit)")
    if not a.no_browser:
        threading.Timer(1.5, webbrowser.open, [url]).start()
    # Localhost only: it uses this PC's tools and dlc/ folder, and must not be exposed to the network
    uvicorn.run("cdlc.web.app:app", host="127.0.0.1", port=a.port, log_level="warning")


def main(argv: list[str] | None = None) -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    p = argparse.ArgumentParser(prog="cdlc", description="Audio + Guitar Pro tab → Rocksmith 2014 CDLC")
    sub = p.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("build", help="generate the .psarc and copy it to dlc/")
    b.add_argument("--audio", required=True)
    b.add_argument("--tab", required=True)
    b.add_argument("--lead", type=int, help="tab track for the Lead arrangement (default: automatic)")
    b.add_argument("--rhythm", type=int, help="track for the Rhythm arrangement (default: automatic)")
    b.add_argument("--no-rhythm", action="store_true", help="do not generate a Rhythm arrangement")
    b.add_argument("--artist")
    b.add_argument("--title")
    b.add_argument("--album")
    b.add_argument("--year", type=int)
    b.add_argument("--cover", help="cover art (png/jpg)")
    b.add_argument("--tone", help=f"tone file or category ({', '.join(TONE_CATEGORIES)}); "
                                  "default: same song or same artist from the library")
    b.add_argument("--name", help="folder name in build/")
    b.add_argument("--variant", help="variant: goes in the title and coexists with the normal version (e.g. \"Lead Guitar 1\")")
    b.add_argument("--pad", type=float, default=4.0, help="seconds of silence at the start (count-in)")
    b.add_argument("--no-deploy", action="store_true", help="do not copy to the game's dlc folder")
    b.add_argument("--no-lookup", action="store_true", help="do not look up album, year and cover on iTunes")
    b.set_defaults(func=cmd_build)

    t = sub.add_parser("tones", help="extract the tone library from the installed CDLC")
    t.add_argument("--dlc", default=None, help="dlc/ folder (default: the one in config.json or Steam's)")
    t.set_defaults(func=cmd_tones)

    i = sub.add_parser("inspect", help="show the tab's tracks, tunings and bars")
    i.add_argument("--tab", required=True)
    i.set_defaults(func=cmd_inspect)

    c = sub.add_parser("check", help="check that everything the build needs is installed")
    c.set_defaults(func=cmd_check)

    w = sub.add_parser("web",help="open the local web UI (http://127.0.0.1:8000)")
    w.add_argument("--port", type=int, default=8000)
    w.add_argument("--no-browser", action="store_true", help="do not open the browser")
    w.set_defaults(func=cmd_web)

    a = p.parse_args(argv)
    try:
        a.func(a)
    except PipelineError as e:
        if e.detail:
            sys.stderr.write(e.detail.rstrip() + "\n")
        raise SystemExit(f"Error: {e}")


if __name__ == "__main__":
    main()
