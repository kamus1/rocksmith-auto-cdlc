"""Conjunto de validación nuevo: la validación anterior ya se usó para medir, así que pasa a desarrollo.

Elige 12 canciones nuevas (3 por grupo estabilidad × compás) excluyendo las del banco y sus artistas,
con el mismo criterio que select_v1.py. Reescribe build/bench/benchmark.json (guarda el reparto anterior
en "set_v1") y deja la lista para extract.fsx en build/bench/extract_new.txt.
Uso: .venv/Scripts/python bench/select_new_val.py [--seed 11] [--prev desarrollo|<etiqueta>]
--prev: a qué conjunto pasa la validación anterior (por defecto, a desarrollo; con otra etiqueta, se aparta
y el desarrollo no cambia). El reparto anterior queda en "set_vN".
"""
import argparse, collections, json, random, re
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
B = ROOT / "build" / "bench"
p = argparse.ArgumentParser()
p.add_argument("--seed", type=int, default=11)
p.add_argument("--prev", default="desarrollo", help="conjunto al que pasa la validación anterior")
a = p.parse_args()

bench = json.loads((B / "benchmark.json").read_text(encoding="utf-8"))
round_ = 1 + max((int(k[5:]) for s in bench for k in s if k.startswith("set_v")), default=0)
used_paths = {s["path"] for s in bench}
used_artists = {s["artist"].lower() for s in bench}

songs = [json.loads(l) for l in open(B / "survey_all.jsonl", encoding="utf-8")]
tones = json.load(open(ROOT / "build" / "tones" / "index.json", encoding="utf-8"))
norm = lambda s: re.sub(r"[^a-z0-9]", "", s.lower())
tone_by_song = collections.defaultdict(set)
for t in tones:
    tone_by_song[norm(t["Song"])].add(t["Descriptors"])

rows = []
for s in songs:
    if s["path"] in used_paths or s["artist"].lower() in used_artists:
        continue
    b = np.array(s["beats"]); m = np.array(s["measures"])
    if len(b) < 50:
        continue
    ib = np.diff(b); med = np.median(ib)
    loc = np.array([np.median(ib[max(0, i - 8):i + 8]) for i in range(len(ib))])
    drift = float(np.std(loc / med))
    downs = np.where(m >= 0)[0]; per = np.diff(downs)
    mode = np.bincount(per).argmax() if len(per) else 4
    meter = int(np.sum(per != mode)) if len(per) else 0
    desc = " ".join(tone_by_song.get(norm(f"{s['artist']} - {s['title']}"), []))
    tone = ("acústica" if "Acoustic" in desc else "distorsión" if re.search("Distortion|High Gain|Fuzz", desc)
            else "limpio/overdrive" if desc else "?")
    rows.append(dict(artist=s["artist"], title=s["title"], path=s["path"], bpm=round(60000 / med),
                     drift=round(drift, 4), meter=meter, length=round(s["length"]), tone=tone))

stab = lambda r: "metrónomo" if r["drift"] < 0.01 else "variable"
met = lambda r: "irregular" if r["meter"] >= 4 else "regular"
random.seed(a.seed); random.shuffle(rows)
groups = collections.defaultdict(list)
for r in rows:
    groups[(stab(r), met(r))].append(r)
chosen, artists = [], set()
for key in sorted(groups):
    picked, seen = [], set()
    for r in sorted(groups[key], key=lambda r: (r["tone"] == "?",)):
        if r["artist"] in artists or r["path"] in {c["path"] for c in picked}:
            continue
        sig = (r["tone"], "lento" if r["bpm"] < 100 else "medio" if r["bpm"] < 150 else "rápido")
        if sig in seen:
            continue
        picked.append(r); seen.add(sig); artists.add(r["artist"])
        if len(picked) == 3:
            break
    for r in picked:
        r["set"] = "validación"
    chosen += picked

for r in chosen:
    print(f"{r['artist'][:22]:22s} | {r['title'][:30]:30s} | {r['bpm']:3d} bpm | {stab(r):9s} {r['drift']*100:4.1f}% | "
          f"{met(r):9s} ({r['meter']:3d}) | {r['tone']}")
for s in bench:
    s[f"set_v{round_}"] = s["set"]
    if s["set"] == "validación":
        s["set"] = a.prev
slug = lambda t: re.sub(r"[^a-z0-9]+", "-", t.lower()).strip("-")
(B / "extract_new.txt").write_text(
    "\n".join(f"{r['path']}|{B / 'songs' / slug(r['artist'] + ' ' + r['title'])}" for r in chosen), encoding="utf-8")
(B / "benchmark.json").write_text(json.dumps(bench + chosen, indent=1, ensure_ascii=False), encoding="utf-8")
