"""Elige el banco de pruebas: 24 canciones variadas (12 desarrollo + 12 validación, equilibradas)."""
import json, numpy as np, collections, re, random

songs = [json.loads(l) for l in open("build/bench/survey_all.jsonl", encoding="utf-8")]
tones = json.load(open("build/tones/index.json", encoding="utf-8"))
norm = lambda s: re.sub(r"[^a-z0-9]", "", s.lower())
tone_by_song = collections.defaultdict(set)
for t in tones:
    tone_by_song[norm(t["Song"])].add(t["Descriptors"])

rows = []
for s in songs:
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
print(len(rows), "canciones;", collections.Counter((stab(r), met(r)) for r in rows))

# 6 canciones por grupo (estabilidad × compás), variando velocidad y tono; una por artista.
random.seed(7); random.shuffle(rows)
groups = collections.defaultdict(list)
for r in rows:
    groups[(stab(r), met(r))].append(r)
chosen, artists = [], set()
for key in sorted(groups):
    picked, seen = [], set()
    for r in sorted(groups[key], key=lambda r: (r["tone"] == "?",)):
        if r["artist"] in artists:
            continue
        sig = (r["tone"], "lento" if r["bpm"] < 100 else "medio" if r["bpm"] < 150 else "rápido")
        if sig in seen and len(seen) < 6:
            continue
        picked.append(r); seen.add(sig); artists.add(r["artist"])
        if len(picked) == 6:
            break
    for i, r in enumerate(picked):
        r["set"] = "desarrollo" if i % 2 == 0 else "validación"
    chosen += picked

for r in sorted(chosen, key=lambda r: (r["set"], stab(r), met(r))):
    print(f"{r['set'][:3]} | {r['artist'][:22]:22s} | {r['title'][:30]:30s} | {r['bpm']:3d} bpm | "
          f"{stab(r):9s} {r['drift']*100:4.1f}% | {met(r):9s} ({r['meter']:3d}) | {r['tone']}")
print(collections.Counter((r["set"], stab(r), met(r)) for r in chosen))
json.dump(chosen, open("build/bench/benchmark.json", "w", encoding="utf-8"), indent=1, ensure_ascii=False)
