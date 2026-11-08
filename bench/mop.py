"""Aligns Master of Puppets (tab version 7) and compares it with Nacholede's hand-synced CDLC.

Needs, outside git: build/ref/*_lead.xml and *_rhythm.xml (reference charts) and build/ref7/
(the tab's timeline.json and song.wav, the reference CDLC's audio). See docs/ALIGNMENT.md.

Usage: .venv/Scripts/python bench/mop.py [--params '{"anchor_weight": 0.3}'] [--compare build/ref7/bt_h.json]
"""

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import eval_notes as ev  # noqa: E402
from cdlc import align  # noqa: E402


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--params", default="{}", help="align_hybrid parameters as JSON")
    p.add_argument("--compare", nargs="*", default=[], help="other bar_times.json files to compare")
    p.add_argument("--timeline", default="timeline.json", help="the tab's timeline, in build/ref7/")
    a = p.parse_args()
    d = ROOT / "build" / "ref7"
    out = d / "bt_mop.json"
    t0 = time.time()
    align.align_hybrid(d / a.timeline, d / "song.wav", out, **json.loads(a.params))
    print(f"alignment: {time.time() - t0:.0f} s")
    ref_dir = ROOT / "build" / "ref"
    ref = ev.ref_notes(sorted(ref_dir.glob("*_lead.xml")) + sorted(ref_dir.glob("*_rhythm.xml")))
    for f in [out] + [Path(c) for c in a.compare]:
        ev.summary(ref, ev.our_notes(d / a.timeline, f, tracks=set(range(16))), f.name)


if __name__ == "__main__":
    main()
