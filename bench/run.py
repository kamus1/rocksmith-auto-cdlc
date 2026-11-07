"""Banco de pruebas de la alineación con CDLC reales sincronizados a mano.

Para cada canción de build/bench/benchmark.json (preparada con select_v1.py o select_new_val.py + extract.fsx):
  1. Convierte el audio .wem a WAV.
  2. Construye una "tab" a tempo fijo con la misma estructura de compases y las mismas notas
     (Lead, Rhythm, Bass), olvidando los tiempos reales.
  3. Alinea con cdlc.align y compara cada inicio de compás con el del chart real.

Uso: .venv/Scripts/python bench/run.py [--set desarrollo|validación|todos] [--workers 4] [--tag nombre] [--quitar 2]
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


def build_timeline(d: Path, drop: int = 0, tag: str = "") -> tuple[Path, np.ndarray]:
    """Tab a tempo fijo desde los XML de referencia. Devuelve (timeline.json, tiempos reales de cada compás).

    drop > 0 quita esos compases de la tab hacia el 60 % de la canción (el audio los conserva): simula una
    tab con una repetición de menos que la grabación."""
    xmls = sorted(d.glob("*.xml"))
    lead = next((x for x in xmls if x.stem.endswith("_lead")), None) or next(x for x in xmls if "_rhythm" in x.stem or "_combo" in x.stem)
    r = ET.parse(lead).getroot()
    beats = np.array([float(e.get("time")) * 1000 for e in r.find("ebeats")])
    down = np.array([e.get("measure") is not None and int(e.get("measure")) >= 0 for e in r.find("ebeats")])
    q = float(np.median(np.diff(beats)))
    idx_down = np.where(down)[0]
    truth = np.append(beats[idx_down], beats[-1] + q)
    # Algunos charts marcan los compases irregulares en corcheas (7/8 con 7 beats de media negra).
    # Una tab los escribiría con su valor: cada intervalo de ~½ negra (o menos) cuenta como tal.
    # Los intervalos largos no se tocan (pueden ser ritardandos o calderones, que la tab no escribe).
    units = np.minimum(1.0, 2.0 ** np.round(np.log2(np.diff(beats) / q)))
    pos = np.concatenate([[0.0], np.cumsum(units)]) * q          # posición nominal de cada beat
    bar_starts = np.append(pos[idx_down], pos[-1] + q)
    bar_quarters = np.diff(bar_starts) / q

    def nominal(t_ms: float) -> float:
        return float(np.interp(t_ms, beats, pos))

    notes = []
    for track, x in enumerate(xmls):
        if "_lead" not in x.stem and "_rhythm" not in x.stem and "_bass" not in x.stem and "_combo" not in x.stem:
            continue
        rx = ET.parse(x).getroot()
        tun = rx.find("tuning")
        offs = [int(tun.get(f"string{i}", 0)) for i in range(6)]
        base = BASS if "_bass" in x.stem else GUITAR
        lv = max_level(rx)
        events = [(n, float(n.get("time")) * 1000) for n in lv.find("notes")]
        events += [(cn, float(c.get("time")) * 1000) for c in lv.find("chords") for cn in c.findall("chordNote")]
        for n, t in events:
            if t < beats[0] or t > beats[-1]:
                continue
            s, f = int(n.get("string")), int(n.get("fret"))
            sus = float(n.get("sustain", 0) or 0) * 1000
            t0 = nominal(t)
            notes.append([round(t0, 1), round(max(0.0, nominal(t + sus) - t0), 1), base[s] + offs[s] + f, track])
    if drop:
        j = int(len(bar_quarters) * 0.6)
        a, b = bar_starts[j], bar_starts[j + drop]
        notes = [[t - (b - a) if t >= b else t, du, m, tr] for t, du, m, tr in notes if not a <= t < b]
        bar_starts = np.concatenate([bar_starts[:j], bar_starts[j + drop:] - (b - a)])
        bar_quarters = np.concatenate([bar_quarters[:j], bar_quarters[j + drop:]])
        truth = np.concatenate([truth[:j], truth[j + drop:]])
    tl = {"bar_starts_ms": bar_starts.tolist(), "bar_quarters": bar_quarters.tolist(), "notes": notes}
    path = d / (f"timeline{tag}_drop{drop}.json" if drop else f"timeline{tag}.json")
    path.write_text(json.dumps(tl), encoding="utf-8")
    return path, truth


def run_song(song: dict, kwargs: dict, drop: int = 0) -> dict:
    d = Path(song["dir"])
    t0 = time.time()
    try:
        wav = wem_to_wav(d)
        # Archivo propio de este proceso: dos bancos a la vez no deben pisarse la tab
        tl, truth = build_timeline(d, drop, f"_{os.getpid()}")
        res = align.align_hybrid(tl, wav, d / f"bt_bench_{os.getpid()}.json", **kwargs)
        (d / f"bt_bench_{os.getpid()}.json").unlink()
        T = np.array(res["bar_times_ms"])
        n = min(len(T), len(truth))
        # Solo compases con notas: en los vacíos (count-in, intros sin la guitarra) no hay nada que alinear
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
                    bias=float(np.median(err)), gaps=res.get("gaps", []), seconds=round(time.time() - t0))
    except Exception as e:  # noqa: BLE001 — una canción rota no debe parar el banco
        return dict(song=f"{song['artist']} - {song['title']}", set=song["set"], error=repr(e)[:200])


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--set", default="desarrollo", choices=["desarrollo", "validación", "todos"])
    p.add_argument("--workers", type=int, default=4)
    p.add_argument("--tag", default="")
    p.add_argument("--quitar", type=int, default=0, help="quitar N compases de la tab hacia el 60 %% (estructura distinta)")
    p.add_argument("--params", default="{}", help='parámetros de align_hybrid en JSON, p. ej. {"onset_lag_ms": 20}')
    a = p.parse_args()
    songs = json.loads((ROOT / "build" / "bench" / "benchmark.json").read_text(encoding="utf-8"))
    slug = lambda t: __import__("re").sub(r"[^a-z0-9]+", "-", t.lower()).strip("-")
    for s in songs:
        s.setdefault("dir", str(ROOT / "build" / "bench" / "songs" / slug(s["artist"] + " " + s["title"])))
        s["group"] = ("metrónomo" if s["drift"] < 0.01 else "variable") + "/" + ("irregular" if s["meter"] >= 4 else "regular")
    if a.set != "todos":
        songs = [s for s in songs if s["set"] == a.set]
    kwargs = json.loads(a.params)
    with ProcessPoolExecutor(a.workers) as ex:
        results = list(ex.map(run_song, songs, [kwargs] * len(songs), [a.quitar] * len(songs)))
    ok = [r for r in results if "error" not in r]
    for r in sorted(ok, key=lambda r: -r["median"]):
        print(f"{r['song'][:44]:44s} {r['group']:20s} mediana {r['median']:6.0f} ms | p90 {r['p90']:6.0f} | "
              f">50ms {r['over50']*100:3.0f}% | >½beat {r['over_half_beat']*100:3.0f}% | sesgo {r['bias']:+5.0f} | huecos {r['gaps']} | {r['seconds']}s")
    for r in results:
        if "error" in r:
            print("ERROR", r["song"], r["error"])
    if ok:
        med = np.median([r["median"] for r in ok]); hb = np.mean([r["over_half_beat"] for r in ok])
        good = sum(r["over_half_beat"] < 0.05 for r in ok)
        print(f"\nRESUMEN {a.set} {a.tag} {kwargs} quitar={a.quitar}: mediana de medianas {med:.0f} ms | compases a más de ½ beat {hb*100:.1f}% "
              f"| canciones bien alineadas (<5% compases mal) {good}/{len(ok)} | con huecos {sum(bool(r['gaps']) for r in ok)}")
        out = ROOT / "build" / "bench" / f"results_{a.set}_{a.tag or int(time.time())}.json"
        out.write_text(json.dumps(results, indent=1, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
