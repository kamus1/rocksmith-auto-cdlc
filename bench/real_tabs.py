"""Real-tab benchmark: Guitar Pro tabs found on the web against the official, hand-synced charts.

In run.py the "tab" is built from the reference chart itself, so it has exactly the notes and the structure
of the recording. Here the tab is a real one: another transcription, maybe another structure, its own
written tempos, drums. The audio and the reference are the benchmark song's (build/bench/songs/<song>,
prepared for run.py). The tabs live outside git, in samples/bench/<source>/ (e.g. gprotab, songsterr), and
each one is matched to its song by the song title in its file name.

  inspect: what each tab is (tracks, bars, written length against the chart, key against the chart)
  run:     aligns each tab (as `cdlc build` does) and compares its notes with the reference chart's
  report:  evaluates the saved alignments of a run again (no aligning)

The comparison (window_errors) is per 10 s window: the shift that makes the most notes of equal pitch
coincide (±40 ms). It tolerates passages that the tab transcribes differently. Windows with less than
30 % of matches are unreliable: the tab lacks that part, writes it differently, or is more than 3 s away.
Each run keeps its files in build/bench/real/<tag>/.

Usage: .venv/bin/python bench/real_tabs.py inspect|run|report [--source songsterr] [--only davidian]
                                                               [--workers 3] [--tag name] [--params JSON]
                                                               [--transpose N]
"""

import argparse
import json
import re
import sys
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "bench"))
from cdlc import align, pipeline  # noqa: E402
import run  # noqa: E402

TABS = ROOT / "samples" / "bench"
SONGS = ROOT / "build" / "bench" / "songs"
OUT = ROOT / "build" / "bench" / "real"
PARTS = ("_lead", "_rhythm", "_bass", "_combo")


def squash(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", text.lower())


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def find_tabs(source: str | None, only: str | None, tag: str) -> list[dict]:
    """Tabs in samples/bench/<source>/, each with the benchmark song whose title is in its file name.
    Each tab's files of this run (timeline.json, bar_times.json) go in build/bench/real/<tag>/<source>/<tab>/."""
    songs = json.loads((ROOT / "build" / "bench" / "benchmark.json").read_text(encoding="utf-8"))
    out = []
    for f in sorted(TABS.glob("*/*.gp*")):
        if source and f.parent.name != source or only and squash(only) not in squash(f.stem):
            continue
        hits = [s for s in songs if squash(s["title"]) in squash(f.stem)]
        if not hits:
            print(f"no benchmark song for {f.name}: skipped")
            continue
        s = max(hits, key=lambda s: len(squash(s["title"])))
        out.append(dict(tab=str(f), source=f.parent.name, name=f.name, song=f"{s['artist']} - {s['title']}",
                        song_dir=str(SONGS / slug(s["artist"] + " " + s["title"])),
                        out=str(OUT / tag / f.parent.name / slug(f.stem))))
    return out


def ref_notes(d: Path) -> np.ndarray:
    """(ms, MIDI pitch) of every note of the reference chart (Lead, Rhythm, Bass), full charts as in run.py."""
    out = []
    for x in sorted(d.glob("*.xml")):
        if not any(p in x.stem for p in PARTS):
            continue
        rx = ET.parse(x).getroot()
        tun = rx.find("tuning")
        offs = [int(tun.get(f"string{i}", 0)) for i in range(6)]
        base = run.BASS if "_bass" in x.stem else run.GUITAR
        for n, t in run.full_events(rx):
            s = int(n.get("string"))
            out.append((t, base[s] + offs[s] + int(n.get("fret"))))
    return np.array(out, dtype=float).reshape(-1, 2)


def ref_beats(d: Path) -> tuple[np.ndarray, np.ndarray]:
    """(time of each beat in ms, whether it is a downbeat) from the reference chart."""
    xmls = sorted(d.glob("*.xml"))
    lead = next((x for x in xmls if x.stem.endswith("_lead")), None) or next(x for x in xmls if any(p in x.stem for p in PARTS))
    eb = ET.parse(lead).getroot().find("ebeats")
    return (np.array([float(e.get("time")) * 1000 for e in eb]),
            np.array([e.get("measure") is not None and int(e.get("measure")) >= 0 for e in eb]))


def tab_notes(tl: dict, bt: dict) -> np.ndarray:
    """(ms in the audio, MIDI pitch) of the tab's pitched notes at the aligned bar times, as RsBuild places
    them: linear inside the bar, up to the bar's actual end (before a gap); bars past the tail cut left out.
    The pitch is the recording's: the tab's plus the key shift the alignment found for the track."""
    st = np.array(tl["bar_starts_ms"])
    T, ends = np.array(bt["bar_times_ms"]), np.array(bt["bar_ends_ms"])
    cut, n, key = bt.get("tail_cut_bar"), len(st) - 1, bt.get("key_shift", 0)
    by_track = bt.get("track_key_shifts") or {}
    out = []
    for t, _d, midi, tr in tl["notes"]:
        if midi < 0:
            continue
        k = min(max(int(np.searchsorted(st, t, side="right") - 1), 0), n - 1)
        if cut is not None and k >= cut - 1:
            continue
        out.append((T[k] + (ends[k] - T[k]) * (t - st[k]) / (st[k + 1] - st[k]), int(midi) + by_track.get(str(int(tr)), key)))
    return np.array(out, dtype=float).reshape(-1, 2)


def window_errors(ref: np.ndarray, ours: np.ndarray, win_ms: float = 10000, max_shift: int = 3000, step: int = 10,
                  tol: float = 40, tie: float = 0.9) -> list[tuple[float, int, float]]:
    """Per 10 s window of the reference chart: (start in s, error in ms, share of its notes matched).
    For each shift of our notes, counts the reference notes that get one of ours of equal pitch within ±tol.
    The error is minus the shift that matches the most, with two cares that eval_notes.window_offsets lacks:
    - Among the shifts that match at least `tie` of the best count, the nearest to zero. In a riff that repeats
      identically, one bar away matches as well as in place, and the player sees the same notes either way.
    - Around it, the centre of the best plateau. Every shift within ±tol of the right one matches the same
      notes: taking the first one biases the error by tens of ms."""
    shifts = np.arange(-max_shift, max_shift + 1, step)
    by_pitch = {p: np.sort(ours[ours[:, 1] == p, 0]) for p in np.unique(ours[:, 1])}
    out = []
    for a in np.arange(0, ref[:, 0].max(), win_ms):
        r = ref[(ref[:, 0] >= a) & (ref[:, 0] < a + win_ms)]
        if len(r) < 8:
            continue
        hit = np.zeros((len(r), len(shifts)), dtype=bool)
        for i, (t, p) in enumerate(r):
            o = by_pitch.get(p)
            if o is None:
                continue
            lo, hi = np.searchsorted(o, [t - max_shift - tol, t + max_shift + tol])
            for x in o[lo:hi]:                       # ours + shift within tol of t  <=>  shift in [t - x ± tol]
                j0 = int(np.ceil((t - x - tol + max_shift) / step))
                j1 = int(np.floor((t - x + tol + max_shift) / step))
                hit[i, max(j0, 0):max(j1 + 1, 0)] = True
        count = hit.sum(axis=0)
        cand = np.where(count >= tie * count.max())[0]
        near = cand[np.argmin(np.abs(shifts[cand]))]
        local = np.where(np.abs(shifts - shifts[near]) <= 2 * tol)[0]
        top = local[count[local] == count[local].max()]
        out.append((float(a / 1000), -int(round(float(shifts[top].mean()))), float(count[top[0]] / len(r))))
    return out


def timeline(item: dict) -> dict:
    out = Path(item["out"])
    out.mkdir(parents=True, exist_ok=True)
    pipeline.rsbuild("timeline", item["tab"], str(out / "timeline.json"))
    return json.loads((out / "timeline.json").read_text(encoding="utf-8"))


def key_shift(tab: np.ndarray, ref: np.ndarray) -> tuple[int, float]:
    """Semitones the chart sounds above the tab (0 = same key) and how clearly: correlation of the two
    pitch-class histograms at the best of the 12 rotations, minus the second best."""
    h = lambda a: np.bincount(a[:, 1].astype(int) % 12, minlength=12).astype(float)
    z = lambda v: (v - v.mean()) / (v.std() + 1e-9)
    c = np.array([float(np.mean(z(np.roll(h(tab), k)) * z(h(ref)))) for k in range(12)])
    best = int(np.argmax(c))
    return (best if best <= 6 else best - 12), float(c[best] - np.delete(c, best).max())


def inspect_one(item: dict) -> dict:
    try:
        info = json.loads(pipeline.rsbuild("inspect-json", item["tab"]))
        tl = timeline(item)
        d = Path(item["song_dir"])
        ref = ref_notes(d)
        beats, down = ref_beats(d)
        st = np.array(tl["bar_starts_ms"])
        pitched = np.array([(t, m) for t, _d, m, _tr in tl["notes"] if m >= 0], dtype=float).reshape(-1, 2)
        shift, margin = key_shift(pitched, ref)
        return dict(item, ok=True, title=f"{info['artist']} - {info['title']}", bars=len(st) - 1,
                    tracks=len(info["tracks"]), drums=sum(1 for n in tl["notes"] if n[2] < 0), notes=len(pitched),
                    held=len(tl.get("held", [])), sync_points=len(tl.get("sync_points") or []),
                    backing=bool(tl.get("has_backing_track")),
                    # Written length of the stretch with notes against the chart's
                    tab_span_s=float(pitched[:, 0].max() - pitched[:, 0].min()) / 1000,
                    ref_span_s=float(ref[:, 0].max() - ref[:, 0].min()) / 1000,
                    ref_bars=int(down.sum()), ref_notes=len(ref), key_shift=shift, key_margin=round(margin, 2),
                    tempos=sorted({round(60000 / x) for x in np.diff(st) / np.array(tl["bar_quarters"])}))
    except Exception as e:  # noqa: BLE001 — one unreadable tab must not stop the rest
        return dict(item, ok=False, error=(getattr(e, "detail", "") or repr(e)).strip().splitlines()[-1][:160])


def evaluate(item: dict) -> dict:
    """Compares the saved alignment of a tab (timeline.json and bar_times.json in its folder) with the chart."""
    try:
        out, d = Path(item["out"]), Path(item["song_dir"])
        tl = json.loads((out / "timeline.json").read_text(encoding="utf-8"))
        res = json.loads((out / "bar_times.json").read_text(encoding="utf-8"))
        beats, _down = ref_beats(d)
        beat = float(np.median(np.diff(beats)))
        win = window_errors(ref_notes(d), tab_notes(tl, res))
        err = np.array([e for _t, e, m in win if m >= 0.3], dtype=float)
        good = np.abs(err)
        kept = (res.get("tail_cut_bar") or len(res["bar_times_ms"])) - 1
        return dict(item, ok=True, windows=win, n_windows=len(win), reliable=len(good), beat_ms=round(beat),
                    median=float(np.median(good)) if len(good) else None,
                    over100=float(np.mean(good > 100)) if len(good) else None,
                    over_half_beat=float(np.mean(good > beat / 2)) if len(good) else None,
                    bias=float(np.median(err)) if len(good) else None,
                    bars=len(res["bar_times_ms"]) - 1, gaps=res["gaps"], tail_cut_bar=res["tail_cut_bar"],
                    count_in_bars=res["count_in_bars"], tempo_ratio=res["tempo_ratio_median"],
                    odd_tempo=len(res["suspicious_bars"]) / max(kept, 1), chroma_match=res["chroma_match"],
                    key_shift=res.get("key_shift", 0),
                    # Tracks the alignment found in another key than the rest of the tab
                    odd_tracks={tr: k for tr, k in (res.get("track_key_shifts") or {}).items() if k != res.get("key_shift", 0)},
                    doubtful=res["doubtful_bars"], refine_gain=res["refine_gain"])
    except Exception as e:  # noqa: BLE001
        return dict(item, ok=False, error=repr(e)[:200])


def run_one(item: dict, kwargs: dict, transpose: int | dict = 0) -> dict:
    """Aligns one tab and evaluates it. transpose: the tab is first moved by that many semitones, as if it
    were written for another tuning ({track: semitones}: only those tracks); the alignment has to find the
    opposite key shift."""
    t0 = time.time()
    try:
        tl = timeline(item)
        out = Path(item["out"])
        if transpose:
            tl = (align._transposed(tl, 0, {int(tr): k for tr, k in transpose.items()}) if isinstance(transpose, dict)
                  else align._transposed(tl, transpose))
            (out / "timeline.json").write_text(json.dumps(tl), encoding="utf-8")
        align.align_hybrid(out / "timeline.json", run.wem_to_wav(Path(item["song_dir"])), out / "bar_times.json", **kwargs)
    except Exception as e:  # noqa: BLE001
        return dict(item, ok=False, error=(getattr(e, "detail", "") or repr(e)).strip().splitlines()[0][:200])
    return dict(evaluate(item), seconds=round(time.time() - t0))


def fmt_time(t: float) -> str:
    return f"{int(t) // 60}:{int(t) % 60:02d}"


def print_run(results: list[dict]) -> None:
    for r in sorted(results, key=lambda r: (r["song"], r["source"], r["name"])):
        if not r["ok"]:
            print(f"ERROR {r['source']:9s} {r['name'][:50]:50s} {r['error']}")
            continue
        if not r["reliable"]:
            print(f"{r['source']:9s} {r['name'][:50]:50s} no reliable window out of {r['n_windows']} (chroma match {r['chroma_match']})")
            continue
        bad = [f"{fmt_time(t)}={e:+d}" for t, e, m in r["windows"] if m >= 0.3 and abs(e) > r["beat_ms"] / 2]
        lost = [fmt_time(t) for t, _e, m in r["windows"] if m < 0.3]
        print(f"{r['source']:9s} {r['name'][:50]:50s} windows {r['reliable']:2d}/{r['n_windows']:2d} | median {r['median']:4.0f} ms | "
              f">100 ms {r['over100'] * 100:3.0f}% | >½ beat {r['over_half_beat'] * 100:3.0f}% | bias {r['bias']:+4.0f} | "
              f"tempo ×{r['tempo_ratio']:.2f} gaps {len(r['gaps'])} cut {r['tail_cut_bar']} doubtful {len(r['doubtful'])}"
              + (f" | key {r['key_shift']:+d}" if r.get("key_shift") else "")
              + (" | tracks " + " ".join(f"{tr}:{k:+d}" for tr, k in r["odd_tracks"].items()) if r.get("odd_tracks") else ""))
        if bad:
            print(f"{'':12s}off by more than ½ beat: {' '.join(bad[:14])}{' …' if len(bad) > 14 else ''}")
        if lost:
            print(f"{'':12s}unreliable: {' '.join(lost[:20])}{' …' if len(lost) > 20 else ''}")
    for source in sorted({r["source"] for r in results}):
        ok = [r for r in results if r["ok"] and r["source"] == source and r["reliable"]]
        if ok:
            good = sum(r["over_half_beat"] < 0.05 and r["reliable"] / r["n_windows"] >= 0.8 for r in ok)
            print(f"SUMMARY {source}: {len(ok)} tabs | median of medians {np.median([r['median'] for r in ok]):.0f} ms | "
                  f"windows off by more than ½ beat {np.mean([r['over_half_beat'] for r in ok]) * 100:.1f}% | "
                  f"unreliable windows {np.mean([1 - r['reliable'] / r['n_windows'] for r in ok]) * 100:.1f}% | "
                  f"good tabs (<5% off and ≥80% reliable) {good}/{len(ok)}")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("cmd", choices=["inspect", "run", "report"])
    p.add_argument("--source", help="only the tabs in samples/bench/<source>/")
    p.add_argument("--only", help="only the tabs whose file name contains this")
    p.add_argument("--workers", type=int, default=3)
    p.add_argument("--tag", default="last", help="name of the run: its files go in build/bench/real/<tag>/")
    p.add_argument("--params", default="{}", help="align_hybrid parameters as JSON")
    p.add_argument("--transpose", type=json.loads, default=0,
                   help='run: move each tab by N semitones before aligning, or only some tracks: {"1": -1}')
    a = p.parse_args()
    tabs = find_tabs(a.source, a.only, a.tag)
    with ProcessPoolExecutor(a.workers) as ex:
        if a.cmd == "report":
            print_run(list(ex.map(evaluate, tabs)))
            return
        if a.cmd == "inspect":
            for r in ex.map(inspect_one, tabs):
                if not r["ok"]:
                    print(f"ERROR {r['source']:9s} {r['name'][:46]:46s} {r['error']}")
                    continue
                print(f"{r['source']:9s} {r['name'][:46]:46s} bars {r['bars']:3d} (chart {r['ref_bars']:3d}) | "
                      f"written {r['tab_span_s']:4.0f} s (chart {r['ref_span_s']:4.0f} s, ×{r['ref_span_s'] / r['tab_span_s']:.2f}) | "
                      f"tracks {r['tracks']:2d} notes {r['notes']:5d} drums {r['drums']:5d} | key {r['key_shift']:+d} ({r['key_margin']:.2f}) | "
                      f"tempos {r['tempos'][:6]}{' sync points' if r['sync_points'] else ''}")
            return
        results = list(ex.map(run_one, tabs, [json.loads(a.params)] * len(tabs), [a.transpose] * len(tabs)))
    (OUT / a.tag / "results.json").write_text(json.dumps(results, indent=1, ensure_ascii=False), encoding="utf-8")
    print_run(results)


if __name__ == "__main__":
    main()
