"""Diagnoses one benchmark song: per-bar error (in beats) of the coarse stage and of the fine pass.

Usage: .venv/Scripts/python bench/diag.py <folder in build/bench/songs> [--drop N] [--params JSON] [--all]
Without --all it only prints the stretches with some bar off by more than ½ beat.
"""
import argparse, json, os, sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from cdlc import align  # noqa: E402
from bench.run import build_timeline, wem_to_wav  # noqa: E402

p = argparse.ArgumentParser()
p.add_argument("song")
p.add_argument("--drop", type=int, default=0)
p.add_argument("--params", default="{}")
p.add_argument("--all", action="store_true")
p.add_argument("--oracle", action="store_true", help="coarse stage = ground truth (does the fine pass keep it?)")
a = p.parse_args()
d = ROOT / "build" / "bench" / "songs" / a.song
wav = wem_to_wav(d)
tl_path, truth = build_timeline(d, a.drop, f"_diag{os.getpid()}")
if a.oracle:
    align.tempo_dp = lambda *_a, **_k: (truth.copy(), [])
res = align.align_hybrid(tl_path, wav, d / f"bt_diag{os.getpid()}.json", **json.loads(a.params))
tl_text = tl_path.read_text(); tl_path.unlink(); (d / f"bt_diag{os.getpid()}.json").unlink()
tl = json.loads(tl_text)
st = np.array(tl["bar_starts_ms"]); q = np.array(tl["bar_quarters"])
T = np.array(res["bar_times_ms"]); C = np.array(res["coarse_bar_times_ms"])
n = min(len(T), len(truth)) - 1
notes = np.zeros(n, dtype=int)
for t_ms, _d, midi, _tr in tl["notes"]:
    k = int(np.searchsorted(st, t_ms, side="right") - 1)
    if 0 <= k < n:
        notes[k] += 1
beat = np.diff(truth)[:n] / q[:n]
ec, ef = (C[:n] - truth[:n]) / beat, (T[:n] - truth[:n]) / beat
bad = (np.abs(ef) > 0.5) & (notes > 0)
print(f"bars with notes off by more than ½ beat: {bad.sum()}/{(notes > 0).sum()}  gaps {res['gaps']}  fine cost with/without gaps {res['fine_cost_with_gaps']} / {res['fine_cost_without_gaps']}  intermediate stage gain {res.get('refine_gain')} chroma change {res.get('refine_chroma_change')}")
show = np.ones(n, bool) if a.all else np.convolve(bad, np.ones(7), "same") > 0
for k in range(n):
    if show[k]:
        print(f"b{k + 1:4d} {truth[k] / 1000:7.2f}s notes {notes[k]:3d}  beat {beat[k]:4.0f} ms  coarse {ec[k]:+5.2f}  fine {ef[k]:+5.2f}"
              + ("  <--" if bad[k] else ""))
