"""A song build in its own process, so the web app can stop it.

A thread cannot be stopped from outside, and the alignment can run for minutes without returning to
Python code that could check a flag. A process can be killed, with ffmpeg and RsBuild under it.
"""

import os
import traceback

from .. import pipeline
from ..pipeline import PipelineError


def build(opts: pipeline.BuildOptions, conn) -> None:
    """Runs build_song and sends its events through conn: step, log, then done or error."""
    if hasattr(os, "setsid"):
        os.setsid()  # its own process group, so stopping it also stops ffmpeg and dotnet
    send = conn.send
    try:
        r = pipeline.build_song(opts, on_step=lambda n, t: send({"type": "step", "n": n, "of": pipeline.STEPS, "title": t}),
                                on_log=lambda line: send({"type": "log", "line": line}))
        send({"type": "done", "result": {
            "song": r.out_dir.name, "psarc": str(r.psarc), "deployed": str(r.deployed) if r.deployed else None,
            "meta": {k: v for k, v in r.meta.items() if k != "cover"}, "tracks": r.tracks, "tone": r.tone,
            "gaps": r.alignment.get("gaps", []), "suspicious_bars": len(r.alignment.get("suspicious_bars", [])),
            "warnings": r.warnings}})
    except PipelineError as e:
        send({"type": "error", "message": str(e), "detail": e.detail})
    except Exception as e:  # noqa: BLE001 — every failure must reach the page
        send({"type": "error", "message": f"Unexpected error: {e!r}", "detail": traceback.format_exc()})
    finally:
        conn.close()
