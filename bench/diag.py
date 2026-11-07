"""Diagnóstico de una canción del banco: error por compás (en beats) de la etapa gruesa y del paso fino.

Uso: .venv/Scripts/python bench/diag.py <carpeta en build/bench/songs> [--quitar N] [--params JSON] [--todo]
Sin --todo solo imprime los tramos con algún compás a más de ½ beat.
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
p.add_argument("--quitar", type=int, default=0)
p.add_argument("--params", default="{}")
p.add_argument("--todo", action="store_true")
p.add_argument("--oracle", action="store_true", help="etapa gruesa = verdad (¿el paso fino la mantiene?)")
a = p.parse_args()
d = ROOT / "build" / "bench" / "songs" / a.song
wav = wem_to_wav(d)
tl_path, truth = build_timeline(d, a.quitar, f"_diag{os.getpid()}")
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
print(f"compases con notas a más de ½ beat: {bad.sum()}/{(notes > 0).sum()}  huecos {res['gaps']}  coste fino con/sin huecos {res['fine_cost_with_gaps']} / {res['fine_cost_without_gaps']}")
show = np.ones(n, bool) if a.todo else np.convolve(bad, np.ones(7), "same") > 0
for k in range(n):
    if show[k]:
        print(f"c{k + 1:4d} {truth[k] / 1000:7.2f}s notas {notes[k]:3d}  beat {beat[k]:4.0f} ms  grueso {ec[k]:+5.2f}  fino {ef[k]:+5.2f}"
              + ("  <--" if bad[k] else ""))
