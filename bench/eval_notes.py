"""Compara notas alineadas por nosotros con las del chart de referencia (misma altura, tiempo más cercano)."""
import json, sys, numpy as np, xml.etree.ElementTree as ET
from pathlib import Path

STD = [40, 45, 50, 55, 59, 64]

def ref_notes(xml_paths):
    out = []
    for p in xml_paths:
        r = ET.parse(p).getroot()
        tun = r.find("tuning"); off = [int(tun.get(f"string{i}", 0)) for i in range(6)]
        capo = int(r.find("capo").text) if r.find("capo") is not None and r.find("capo").text else 0
        lv = max(r.find("levels"), key=lambda l: len(l.find("notes")) + len(l.find("chords")))
        for n in lv.find("notes"):
            s, f = int(n.get("string")), int(n.get("fret"))
            out.append((float(n.get("time")) * 1000, STD[s] + off[s] + f))
        for c in lv.find("chords"):
            for n in c.findall("chordNote"):
                s, f = int(n.get("string")), int(n.get("fret"))
                out.append((float(c.get("time")) * 1000, STD[s] + off[s] + f))
    return np.array(out)

def our_notes(timeline, bar_times, tracks):
    tl = json.load(open(timeline, encoding="utf-8")); st = np.array(tl["bar_starts_ms"])
    bt = np.array(json.load(open(bar_times))["bar_times_ms"])
    pts = [(float(np.interp(n[0], st, bt)), int(n[2])) for n in tl["notes"] if int(n[3]) in tracks and n[2] >= 0]
    return np.array(pts)

def evaluate(ref, ours, label):
    errs, times = [], []
    for pitch in np.unique(ref[:, 1]):
        rt = np.sort(ref[ref[:, 1] == pitch, 0]); ot = np.sort(ours[ours[:, 1] == pitch, 0])
        if len(ot) == 0: continue
        j = np.clip(np.searchsorted(ot, rt), 1, max(1, len(ot) - 1))
        if len(ot) == 1: e = np.abs(ot[0] - rt)
        else: e = np.minimum(np.abs(ot[j] - rt), np.abs(ot[j - 1] - rt))
        errs += list(e); times += list(rt)
    errs, times = np.array(errs), np.array(times)
    per_min = [int(np.median(errs[(times >= a * 1000) & (times < (a + 60) * 1000)])) if ((times >= a * 1000) & (times < (a + 60) * 1000)).any() else None for a in range(0, 540, 60)]
    print(f"{label:34s} notas {len(errs)}: mediana {np.median(errs):5.0f} ms | <50 {np.mean(errs<50)*100:3.0f}% | >150 {np.mean(errs>150)*100:3.0f}% | por minuto {per_min}")
    return errs, times

def window_offsets(ref, ours, win_s=10, max_shift=3000, tol=40):
    """Por ventana: desplazamiento (ms) que más notas de igual altura hace coincidir (±tol) y % de coincidencias."""
    shifts = np.arange(-max_shift, max_shift + 1, 10)
    res = []
    t_end = ref[:, 0].max()
    for a in np.arange(0, t_end, win_s * 1000):
        r = ref[(ref[:, 0] >= a) & (ref[:, 0] < a + win_s * 1000)]
        if len(r) < 8: continue
        best = (-1, 0)
        o = ours[(ours[:, 0] >= a - max_shift - 1000) & (ours[:, 0] < a + win_s * 1000 + max_shift + 1000)]
        for s in shifts:
            m = 0
            for p in np.unique(r[:, 1]):
                rt = r[r[:, 1] == p, 0]; ot = np.sort(o[o[:, 1] == p, 0] + s)
                if len(ot) == 0: continue
                j = np.clip(np.searchsorted(ot, rt), 1, max(1, len(ot) - 1))
                e = np.minimum(np.abs(ot[j] - rt), np.abs(ot[j - 1] - rt)) if len(ot) > 1 else np.abs(ot[0] - rt)
                m += int((e <= tol).sum())
            if m > best[0]: best = (m, s)
        res.append((a / 1000, -best[1], best[0] / len(r)))   # error = -desplazamiento necesario
    return res

def summary(ref, ours, label):
    res = window_offsets(ref, ours)
    good = [(t, e, m) for t, e, m in res if m >= 0.3]
    errs = np.array([abs(e) for _, e, _ in good])
    line = " ".join(f"{int(t)//60}:{int(t)%60:02d}={e:+d}" + ("" if m >= 0.3 else "?") for t, e, m in res)
    print(f"{label:30s} ventanas fiables {len(good)}/{len(res)} | |error| mediana {np.median(errs):4.0f} ms | >100 ms en {np.mean(errs>100)*100:3.0f}% | >300 ms en {np.mean(errs>300)*100:3.0f}%")
    print("     " + line)
