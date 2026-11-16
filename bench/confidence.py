"""Per-bar confidence against real errors, on the benchmark songs.

  collect: aligns each song (as run.py) and saves, per bar, the shifted emissions and the real error
           in build/bench/confidence/<song>.npz
  report:  how well a confidence measure separates the bars off by more than ½ beat

Usage: .venv/bin/python bench/confidence.py collect [--set dev] [--workers 4]
       .venv/bin/python bench/confidence.py report [--set dev]
"""

import argparse
import json
import os
import re
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "bench"))
from cdlc import align  # noqa: E402
import run  # noqa: E402

OUT = ROOT / "build" / "bench" / "confidence"


def slug(t: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", t.lower()).strip("-")


def songs(which: str) -> list[dict]:
    out = []
    for s in json.loads((ROOT / "build" / "bench" / "benchmark.json").read_text(encoding="utf-8")):
        s.setdefault("dir", str(ROOT / "build" / "bench" / "songs" / slug(s["artist"] + " " + s["title"])))
        if which == "all" or s["set"] == which:
            out.append(s)
    return out


def collect_song(song: dict) -> str:
    d = Path(song["dir"])
    name = slug(song["artist"] + " " + song["title"])
    try:
        wav = run.wem_to_wav(d)
        tl, truth = run.build_timeline(d, 0, f"_{os.getpid()}")
        tmp = d / f"bt_conf_{os.getpid()}.json"
        res = align.align_hybrid(tl, wav, tmp, shift_emissions_out=OUT / f"{name}.npz")
        tmp.unlink()
        tlj = json.loads(tl.read_text())
        tl.unlink()
        T = np.array(res["bar_times_ms"])
        st = np.array(tlj["bar_starts_ms"])
        n = len(st) - 1
        has = np.zeros(n, dtype=bool)
        for note in tlj["notes"]:
            k = int(np.searchsorted(st, note[0], side="right") - 1)
            if 0 <= k < n:
                has[k] = True
        m = min(n, len(truth))
        err = np.full(n, np.nan)
        err[:m] = T[:m] - truth[:m]
        z = dict(np.load(OUT / f"{name}.npz"))
        np.savez_compressed(OUT / f"{name}.npz", **z, err=err, has=has, set=song["set"])
        return f"{name}: ok"
    except Exception as e:  # noqa: BLE001
        return f"{name}: ERROR {e!r}"[:200]


def load(which: str) -> list[tuple[str, dict]]:
    out = []
    for s in songs(which):
        p = OUT / f"{slug(s['artist'] + ' ' + s['title'])}.npz"
        if p.exists():
            z = dict(np.load(p))
            if "err" in z:
                out.append((p.stem, z))
    return out


def report(which: str) -> None:
    """For each threshold and minimum run length: share of the bad bars (off by more than ½ beat)
    inside a reported run, share of reported bars that are bad, share of runs touching a bad bar
    (±1 bar), and clean songs (no bad bar) with some run."""
    data = load(which)
    print(f"{len(data)} songs, {sum(int((z['has'] & (np.abs(np.nan_to_num(z['err'])) > z['beat_ms'] / 2)).sum()) for _, z in data)} bad bars")
    conf = {name: align.bar_confidence(z["E"], z["shifts"], float(z["beat_ms"])) for name, z in data}
    for min_bars in (3, 4, 5):
        for thr in (-0.02, 0.0, 0.01, 0.02, 0.03, 0.05):
            n_bad = caught = n_rep = rep_bad = n_runs = hits = clean = false_clean = 0
            for name, z in data:
                bad = z["has"] & (np.abs(np.nan_to_num(z["err"])) > z["beat_ms"] / 2)
                runs = align.doubtful_runs(conf[name], thr, min_bars)
                inrun = np.zeros(len(bad), dtype=bool)
                for a, b in runs:
                    inrun[a - 1:b] = True
                inrun &= z["has"]
                n_bad += bad.sum()
                caught += (bad & inrun).sum()
                n_rep += inrun.sum()
                rep_bad += (bad & inrun).sum()
                n_runs += len(runs)
                hits += sum(bool(bad[max(0, a - 2):b + 1].any()) for a, b in runs)
                if not bad.any():
                    clean += 1
                    false_clean += bool(runs)
            print(f"runs >= {min_bars} bars, lift < {thr:+.2f}: catches {caught / max(n_bad, 1) * 100:4.0f}% of the bad bars | "
                  f"{rep_bad / max(n_rep, 1) * 100:4.0f}% of the reported bars are bad | {hits}/{n_runs} runs touch a bad bar | "
                  f"clean songs with a run: {false_clean}/{clean}")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("cmd", choices=["collect", "report"])
    p.add_argument("--set", default="dev")
    p.add_argument("--workers", type=int, default=4)
    a = p.parse_args()
    if a.cmd == "collect":
        OUT.mkdir(parents=True, exist_ok=True)
        with ProcessPoolExecutor(a.workers) as ex:
            for line in ex.map(collect_song, songs(a.set)):
                print(line, flush=True)
    else:
        report(a.set)


if __name__ == "__main__":
    main()
