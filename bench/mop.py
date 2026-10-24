"""Alinea Master of Puppets (tab ver 7) y lo compara con el CDLC de Nacholede, sincronizado a mano.

Necesita, fuera de git: build/ref/*_lead.xml y *_rhythm.xml (charts de referencia) y build/ref7/
(timeline.json de la tab y song.wav, el audio del CDLC de referencia). Ver docs/ALINEACION.md.

Uso: .venv/Scripts/python bench/mop.py [--params '{"anchor_weight": 0.3}'] [--compare build/ref7/bt_h.json]
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
    p.add_argument("--params", default="{}", help="parámetros de align_hybrid en JSON")
    p.add_argument("--compare", nargs="*", default=[], help="otros bar_times.json para comparar")
    a = p.parse_args()
    d = ROOT / "build" / "ref7"
    out = d / "bt_mop.json"
    t0 = time.time()
    align.align_hybrid(d / "timeline.json", d / "song.wav", out, **json.loads(a.params))
    print(f"alineación: {time.time() - t0:.0f} s")
    ref_dir = ROOT / "build" / "ref"
    ref = ev.ref_notes(sorted(ref_dir.glob("*_lead.xml")) + sorted(ref_dir.glob("*_rhythm.xml")))
    for f in [out] + [Path(c) for c in a.compare]:
        ev.summary(ref, ev.our_notes(d / "timeline.json", f, tracks=set(range(16))), f.name)


if __name__ == "__main__":
    main()
