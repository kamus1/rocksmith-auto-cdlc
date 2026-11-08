"""Alignment benchmark against real, hand-synced CDLCs.

For each song in build/bench/benchmark.json (prepared with select_v1.py or select_new_val.py + extract.fsx):
  1. Converts the .wem audio to WAV.
  2. Builds a fixed-tempo "tab" with the same bar structure and the same notes
     (Lead, Rhythm, Bass), discarding the real timings.
  3. Aligns it with cdlc.align and compares each bar start with the real chart's.

Usage: .venv/Scripts/python bench/run.py [--set dev|validation|all] [--workers 4] [--tag name] [--drop 2]
"""

import argparse
import json
import os
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from cdlc import align, audio  # noqa: E402

TOOLS = ROOT / "external" / "Rocksmith2014.NET" / "src" / "Rocksmith2014.Audio" / "Tools"
GUITAR = [40, 45, 50, 55, 59, 64]
BASS = [28, 33, 38, 43, 47, 52]


def wem_to_wav(d: Path) -> Path:
    wav = d / "song.wav"
    if wav.exists():
        return wav
    ogg = d / "song.ogg"
    subprocess.run([str(TOOLS / "win" / "ww2ogg.exe"), str((d / "song.wem").resolve()), "-o", str(ogg.resolve()),
                    "--pcb", "packed_codebooks_aoTuV_603.bin"], cwd=TOOLS, check=True, capture_output=True)
    subprocess.run([str(TOOLS / "win" / "revorb.exe"), str(ogg.resolve())], cwd=TOOLS, check=True, capture_output=True)
    return audio.to_wav(ogg, wav, pad_seconds=0)


def max_level(root):
    return max(root.find("levels"), key=lambda l: len(l.find("notes")) + len(l.find("chords")))


def full_events(root) -> list:
    """Notes of the full chart, [(element with string/fret/sustain/bendValues, time in ms)], as in a
    Guitar Pro tab:
    - each phrase at the level of its maxDifficulty (in official charts each phrase has its own; a single
      level leaves whole phrases reduced or empty, e.g. the Audioslave intro);
    - repeated chords without <chordNote> (chordId only) expanded from their chordTemplate: otherwise
      30 to 90 % of the chords in many songs were lost."""
    levels = {int(l.get("difficulty")): l for l in root.find("levels")}
    tmpl = list(root.find("chordTemplates"))
    phrases = list(root.find("phrases"))
    its = list(root.find("phraseIterations"))
    if not its:
        chosen = [(max_level(root), -1e18, 1e18)]
    else:
        bounds = [float(p.get("time")) for p in its] + [1e18]
        bounds[0] = -1e18
        chosen = []
        for i, pi in enumerate(its):
            md = int(phrases[int(pi.get("phraseId"))].get("maxDifficulty"))
            lv = levels.get(md) or levels[max(k for k in levels if k <= md) if any(k <= md for k in levels) else max(levels)]
            chosen.append((lv, bounds[i], bounds[i + 1]))
    events = []
    for lv, a, b in chosen:
        for n in lv.find("notes"):
            t = float(n.get("time"))
            if a <= t < b:
                events.append((n, t * 1000))
        for c in lv.find("chords"):
            t = float(c.get("time"))
            if not a <= t < b:
                continue
            cns = c.findall("chordNote")
            if not cns:
                tp = tmpl[int(c.get("chordId"))]
                cns = [ET.Element("chordNote", {"string": str(i), "fret": tp.get(f"fret{i}"), "sustain": "0"})
                       for i in range(6) if int(tp.get(f"fret{i}", -1)) >= 0]
            events += [(cn, t * 1000) for cn in cns]
    return events


def build_timeline(d: Path, drop: int = 0, tag: str = "") -> tuple[Path, np.ndarray]:
    """Fixed-tempo tab from the reference XMLs. Returns (timeline.json, real time of each bar).

    drop > 0 removes that many bars from the tab around 60 % of the song (the audio keeps them): simulates a
    tab with one repeat fewer than the recording."""
    xmls = sorted(d.glob("*.xml"))
    lead = next((x for x in xmls if x.stem.endswith("_lead")), None) or next(x for x in xmls if "_rhythm" in x.stem or "_combo" in x.stem)
    r = ET.parse(lead).getroot()
    beats = np.array([float(e.get("time")) * 1000 for e in r.find("ebeats")])
    down = np.array([e.get("measure") is not None and int(e.get("measure")) >= 0 for e in r.find("ebeats")])
    q = float(np.median(np.diff(beats)))
    idx_down = np.where(down)[0]
    truth = np.append(beats[idx_down], beats[-1] + q)
    # Some charts mark irregular bars in eighth notes (7/8 as 7 beats of half a quarter note).
    # A tab would write them at their real value: each interval of ~½ quarter (or less) counts as such.
    # Long intervals are left alone (they may be ritardandos or fermatas, which the tab doesn't write).
    units = np.minimum(1.0, 2.0 ** np.round(np.log2(np.diff(beats) / q)))
    pos = np.concatenate([[0.0], np.cumsum(units)]) * q          # nominal position of each beat
    bar_starts = np.append(pos[idx_down], pos[-1] + q)
    bar_quarters = np.diff(bar_starts) / q

    def nominal(t_ms: float) -> float:
        return float(np.interp(t_ms, beats, pos))

    notes, held = [], []
    for track, x in enumerate(xmls):
        if "_lead" not in x.stem and "_rhythm" not in x.stem and "_bass" not in x.stem and "_combo" not in x.stem:
            continue
        rx = ET.parse(x).getroot()
        tun = rx.find("tuning")
        offs = [int(tun.get(f"string{i}", 0)) for i in range(6)]
        base = BASS if "_bass" in x.stem else GUITAR
        events = full_events(rx)
        for n, t in events:
            if t < beats[0] or t > beats[-1]:
                continue
            s, f = int(n.get("string")), int(n.get("fret"))
            sus = float(n.get("sustain", 0) or 0) * 1000
            midi = base[s] + offs[s] + f
            # Constant-pitch pieces: cuts at each bend point (step in whole tones) and at each bar start,
            # as Guitar Pro would write it with ties. The first is the attack; the rest are "held".
            t0, t1 = nominal(t), nominal(t + sus)
            bv = n.find("bendValues")
            bend = sorted((float(v.get("time")) * 1000, float(v.get("step", 0) or 0)) for v in bv) if bv is not None else []
            cuts = sorted({t0, t1} | {nominal(bt) for bt, _st in bend if t < bt < t + sus}
                          | {float(x) for x in bar_starts if t0 < x < t1})
            pitch_at = lambda x: midi + round(2 * next((st_ for bt, st_ in reversed(bend) if nominal(bt) <= x + 0.5), 0.0))
            pieces = [(cuts[i], cuts[i + 1] - cuts[i], pitch_at(cuts[i])) for i in range(len(cuts) - 1)] or [(t0, 0.0, midi)]
            notes.append([round(t0, 1), round(pieces[0][1], 1), pieces[0][2], track])
            held += [[round(a_, 1), round(du, 1), m] for a_, du, m in pieces[1:] if du > 0]
    if drop:
        j = int(len(bar_quarters) * 0.6)
        a, b = bar_starts[j], bar_starts[j + drop]
        notes = [[t - (b - a) if t >= b else t, du, m, tr] for t, du, m, tr in notes if not a <= t < b]
        held = [[t - (b - a) if t >= b else t, du, m] for t, du, m in held if not a <= t < b]
        bar_starts = np.concatenate([bar_starts[:j], bar_starts[j + drop:] - (b - a)])
        bar_quarters = np.concatenate([bar_quarters[:j], bar_quarters[j + drop:]])
        truth = np.concatenate([truth[:j], truth[j + drop:]])
    tl = {"bar_starts_ms": bar_starts.tolist(), "bar_quarters": bar_quarters.tolist(), "notes": notes, "held": held}
    path = d / (f"timeline{tag}_drop{drop}.json" if drop else f"timeline{tag}.json")
    path.write_text(json.dumps(tl), encoding="utf-8")
    return path, truth


def run_song(song: dict, kwargs: dict, drop: int = 0) -> dict:
    d = Path(song["dir"])
    t0 = time.time()
    try:
        wav = wem_to_wav(d)
        # Per-process file: two concurrent benchmark runs must not overwrite each other's tab
        tl, truth = build_timeline(d, drop, f"_{os.getpid()}")
        res = align.align_hybrid(tl, wav, d / f"bt_bench_{os.getpid()}.json", **kwargs)
        (d / f"bt_bench_{os.getpid()}.json").unlink()
        T = np.array(res["bar_times_ms"])
        n = min(len(T), len(truth))
        # Only bars with notes: empty ones (count-in, intros without guitar) have nothing to align
        tlj = json.loads(tl.read_text())
        tl.unlink()
        st = np.array(tlj["bar_starts_ms"])
        has = np.zeros(n, dtype=bool)
        for note in tlj["notes"]:
            k = int(np.searchsorted(st, note[0], side="right") - 1)
            if 0 <= k < n:
                has[k] = True
        err = (T[:n] - truth[:n])[has]
        a = np.abs(err)
        n = int(has.sum())
        beat = float(np.median(np.diff(truth)) / max(1.0, np.median(tlj["bar_quarters"])))
        return dict(song=f"{song['artist']} - {song['title']}", set=song["set"], group=song.get("group", ""),
                    bars=int(n), median=float(np.median(a)), p90=float(np.percentile(a, 90)),
                    over50=float(np.mean(a > 50)), over_half_beat=float(np.mean(a > beat / 2)),
                    bias=float(np.median(err)), gaps=res.get("gaps", []), seconds=round(time.time() - t0),
                    refine_gain=res.get("refine_gain"), refine_chroma_change=res.get("refine_chroma_change"))
    except Exception as e:  # noqa: BLE001 — one broken song must not stop the benchmark
        return dict(song=f"{song['artist']} - {song['title']}", set=song["set"], error=repr(e)[:200])


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--set", default="dev", help="dev, validation, all, or another label from benchmark.json")
    p.add_argument("--workers", type=int, default=4)
    p.add_argument("--tag", default="")
    p.add_argument("--drop", type=int, default=0, help="drop N bars from the tab around 60 %% (different structure)")
    p.add_argument("--params", default="{}", help='align_hybrid parameters as JSON, e.g. {"onset_lag_ms": 20}')
    a = p.parse_args()
    songs = json.loads((ROOT / "build" / "bench" / "benchmark.json").read_text(encoding="utf-8"))
    slug = lambda t: __import__("re").sub(r"[^a-z0-9]+", "-", t.lower()).strip("-")
    for s in songs:
        s.setdefault("dir", str(ROOT / "build" / "bench" / "songs" / slug(s["artist"] + " " + s["title"])))
        s["group"] = ("metronome" if s["drift"] < 0.01 else "variable") + "/" + ("irregular" if s["meter"] >= 4 else "regular")
    if a.set != "all":
        songs = [s for s in songs if s["set"] == a.set]
    kwargs = json.loads(a.params)
    with ProcessPoolExecutor(a.workers) as ex:
        results = list(ex.map(run_song, songs, [kwargs] * len(songs), [a.drop] * len(songs)))
    ok = [r for r in results if "error" not in r]
    for r in sorted(ok, key=lambda r: -r["median"]):
        print(f"{r['song'][:44]:44s} {r['group']:20s} median {r['median']:6.0f} ms | p90 {r['p90']:6.0f} | "
              f">50ms {r['over50']*100:3.0f}% | >½beat {r['over_half_beat']*100:3.0f}% | bias {r['bias']:+5.0f} | gaps {r['gaps']} | {r['seconds']}s")
    for r in results:
        if "error" in r:
            print("ERROR", r["song"], r["error"])
    if ok:
        med = np.median([r["median"] for r in ok]); hb = np.mean([r["over_half_beat"] for r in ok])
        good = sum(r["over_half_beat"] < 0.05 for r in ok)
        print(f"\nSUMMARY {a.set} {a.tag} {kwargs} drop={a.drop}: median of medians {med:.0f} ms | bars off by more than ½ beat {hb*100:.1f}% "
              f"| well-aligned songs (<5% bad bars) {good}/{len(ok)} | with gaps {sum(bool(r['gaps']) for r in ok)}")
        out = ROOT / "build" / "bench" / f"results_{a.set}_{a.tag or int(time.time())}.json"
        out.write_text(json.dumps(results, indent=1, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
