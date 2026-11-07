"""Compara dos ejecuciones del banco por canción: .venv/Scripts/python bench/compare.py <tag_a> <tag_b> [--set desarrollo]"""
import argparse, json
from pathlib import Path

B = Path(__file__).resolve().parent.parent / "build" / "bench"
p = argparse.ArgumentParser(); p.add_argument("a"); p.add_argument("b"); p.add_argument("--set", default="desarrollo")
a = p.parse_args()
load = lambda t: {r["song"]: r for r in json.loads((B / f"results_{a.set}_{t}.json").read_text(encoding="utf-8"))}
A, Bt = load(a.a), load(a.b)
for s in sorted(A, key=lambda s: -A[s].get("over_half_beat", 1)):
    ra, rb = A[s], Bt.get(s, {})
    ha, hb = ra.get("over_half_beat"), rb.get("over_half_beat")
    fmt = lambda h: "  err" if h is None else f"{h * 100:4.1f}%"
    mark = "" if ha is None or hb is None or abs(ha - hb) < 0.005 else ("  mejor" if hb < ha else "  PEOR")
    print(f"{s[:44]:44s} >½beat {fmt(ha)} -> {fmt(hb)}   mediana {ra.get('median', 0):4.0f} -> {rb.get('median', 0):4.0f} ms{mark}")
