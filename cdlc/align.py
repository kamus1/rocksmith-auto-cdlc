"""Alineación tab ↔ audio por seguimiento rítmico.

Se compara el patrón de ataques de la tab (batería, bajo y guitarras) con la envolvente de
onsets del audio. Primero se busca el inicio y el tempo de los primeros compases; después se
avanza compás a compás con una ventana deslizante que reajusta el tempo localmente.
"""

import json
from pathlib import Path

import numpy as np

SR = 22050
HOP = 256
WEIGHT_DRUMS = 1.0
WEIGHT_BASS = 0.6
WEIGHT_GUITAR = 0.4


def onset_envelope(wav: Path) -> tuple[np.ndarray, float]:
    """Envolvente de onsets normalizada y su paso temporal en ms."""
    import librosa
    from scipy.ndimage import gaussian_filter1d

    y, _ = librosa.load(str(wav), sr=SR, mono=True)
    env = librosa.onset.onset_strength(y=y, sr=SR, hop_length=HOP)
    env = gaussian_filter1d(env, 1.0)
    env = env / (np.percentile(env, 99) + 1e-9)
    return np.clip(env, 0, 1.5), HOP / SR * 1000


def tab_onsets(timeline: dict) -> tuple[np.ndarray, np.ndarray]:
    """Instantes únicos de ataque (ms nominales) y su peso según el instrumento."""
    weights: dict[float, float] = {}
    for t_ms, _d, midi, _track in timeline["notes"]:
        if midi < 0:
            w = WEIGHT_DRUMS
        elif midi < 52:  # registro de bajo
            w = WEIGHT_BASS
        else:
            w = WEIGHT_GUITAR
        key = round(t_ms, 0)
        weights[key] = weights.get(key, 0.0) + w
    times = np.array(sorted(weights))
    return times, np.minimum(np.array([weights[t] for t in times]), 2.0)


class _Scorer:
    def __init__(self, env: np.ndarray, step_ms: float):
        self.env = env
        self.step = step_ms

    def __call__(self, times_ms: np.ndarray, w: np.ndarray) -> np.ndarray:
        """times_ms con forma (..., n) → puntuación (...)."""
        idx = np.clip(np.rint(times_ms / self.step).astype(int), 0, len(self.env) - 1)
        return (self.env[idx] * w).sum(axis=-1)


def align(timeline_json: Path, wav: Path, out_json: Path, window_bars: int = 4,
          max_start_ms: float = 30000.0) -> dict:
    tl = json.loads(timeline_json.read_text(encoding="utf-8"))
    st = np.array(tl["bar_starts_ms"])
    n = len(st) - 1
    on_t, on_w = tab_onsets(tl)
    env, step = onset_envelope(wav)
    score = _Scorer(env, step)

    def window(k: int, bars: int):
        a, b = st[k], st[min(n, k + bars)]
        sel = (on_t >= a) & (on_t < b)
        return on_t[sel] - a, on_w[sel]

    # 1. Inicio y tempo: primeros 8 compases contra todo el rango de inicios posibles
    rel, w = window(0, 8)
    scales = np.arange(0.85, 1.15001, 0.005)
    starts = np.arange(0.0, max_start_ms, step)
    best = (-1.0, 0.0, 1.0)
    for s in scales:
        cand = starts[:, None] + s * rel[None, :]
        sc = score(cand, w)
        i = int(np.argmax(sc))
        if sc[i] > best[0]:
            best = (float(sc[i]), float(starts[i]), float(s))
    _, t0, s_prev = best

    # 2. Seguimiento compás a compás
    T = np.zeros(n + 1)
    T[0] = t0
    scales_used = np.zeros(n)
    deltas = np.arange(-40.0, 40.001, 5.0)
    for k in range(n):
        rel, w = window(k, window_bars)
        if len(rel) >= 3:
            ss = s_prev * np.arange(0.92, 1.08001, 0.005)
            ss = ss[(ss > 0.6) & (ss < 1.6)]
            cand = T[k] + deltas[:, None, None] + ss[None, :, None] * rel[None, None, :]
            sc = score(cand, w)
            sc = sc - 0.5 * w.sum() * np.abs(np.log(ss / s_prev))[None, :]  # preferir tempo estable
            di, si = np.unravel_index(int(np.argmax(sc)), sc.shape)
            if k > 0:
                T[k] = max(T[k - 1] + 1, T[k] + deltas[di])
            s_prev = float(ss[si])
        scales_used[k] = s_prev
        T[k + 1] = T[k] + s_prev * (st[k + 1] - st[k])

    ratio = np.diff(T) / np.diff(st)
    med = float(np.median(ratio))
    suspicious = [int(k + 1) for k, r in enumerate(ratio) if abs(r / med - 1) > 0.1]
    result = {
        "bar_times_ms": [round(float(t), 1) for t in T],
        "tempo_ratio_median": round(med, 4),
        "suspicious_bars": suspicious,
    }
    out_json.write_text(json.dumps(result, indent=1), encoding="utf-8")
    return result


def align_dp(timeline_json: Path, wav: Path, out_json: Path, radius_ms: float = 1500.0,
             grid_ms: float = 20.0, tempo_tolerance: float = 0.02, smooth_weight: float = 0.5) -> dict:
    """Alineación global por programación dinámica.

    Cada inicio de compás T_k se elige en una rejilla alrededor de la predicción a tempo constante.
    Coste de pasar de T_k a T_k+1: -(ataques del compás k que caen en onsets) + penalización por
    alejarse del tempo global. Los compases con mucha señal anclan a los ambiguos.
    """
    tl = json.loads(timeline_json.read_text(encoding="utf-8"))
    st = np.array(tl["bar_starts_ms"])
    n = len(st) - 1
    on_t, on_w = tab_onsets(tl)
    env, step = onset_envelope(wav)
    score = _Scorer(env, step)

    # Tempo global e inicio: del seguimiento voraz (robusto en el arranque)
    greedy = align(timeline_json, wav, out_json)
    g = np.array(greedy["bar_times_ms"])
    s_g = greedy["tempo_ratio_median"]
    pred = g[0] + s_g * (st - st[0])
    # Centrar la rejilla en el voraz suavizado para cubrir derivas largas
    centre = pred + np.convolve(g - pred, np.ones(9) / 9, mode="same")

    offs = np.arange(-radius_ms, radius_ms + 0.001, grid_ms)
    m = len(offs)
    bar_w = np.array([on_w[(on_t >= st[k]) & (on_t < st[k + 1])].sum() for k in range(n)])
    w_ref = float(np.median(bar_w[bar_w > 0])) if (bar_w > 0).any() else 1.0

    cost = np.zeros(m)  # coste acumulado hasta T_0 (sin preferencia)
    back = np.zeros((n, m), dtype=np.int32)
    for k in range(n):
        Tk = centre[k] + offs            # (m,)
        Tk1 = centre[k + 1] + offs       # (m,)
        nominal = s_g * (st[k + 1] - st[k])
        dur = Tk1[None, :] - Tk[:, None]  # (m, m): de i (T_k) a j (T_k+1)
        valid = dur > nominal * 0.5
        dev = np.log(np.where(valid, dur, 1.0) / nominal) / tempo_tolerance
        trans = smooth_weight * w_ref * dev ** 2
        sel = (on_t >= st[k]) & (on_t < st[k + 1])
        if sel.any():
            frac = (on_t[sel] - st[k]) / (st[k + 1] - st[k])
            times = Tk[:, None, None] + dur[:, :, None] * frac[None, None, :]
            emis = score(times, on_w[sel])
        else:
            emis = 0.0
        total = cost[:, None] + trans - emis
        total = np.where(valid, total, np.inf)
        back[k] = np.argmin(total, axis=0)
        cost = total[back[k], np.arange(m)]

    idx = np.zeros(n + 1, dtype=int)
    idx[n] = int(np.argmin(cost))
    for k in range(n - 1, -1, -1):
        idx[k] = back[k, idx[k + 1]]
    T = centre + offs[idx]

    ratio = np.diff(T) / np.diff(st)
    med = float(np.median(ratio))
    result = {
        "bar_times_ms": [round(float(t), 1) for t in T],
        "tempo_ratio_median": round(med, 4),
        "suspicious_bars": [int(k + 1) for k, r in enumerate(ratio) if abs(r / med - 1) > 0.1],
        "method": "dp",
    }
    out_json.write_text(json.dumps(result, indent=1), encoding="utf-8")
    return result


def align_beats(timeline_json: Path, wav: Path, beats_json: Path, out_json: Path) -> dict:
    """Alineación por rejilla de beats: cada negra de la tab se asigna a un beat detectado (beat_this).

    Se prueba cada beat detectado como inicio del compás 1 (y la relación beats/negra 0.5, 1 o 2) y se
    elige la combinación cuyos ataques de la tab coinciden mejor con los onsets del audio.
    """
    tl = json.loads(timeline_json.read_text(encoding="utf-8"))
    st = np.array(tl["bar_starts_ms"])
    Q = np.concatenate([[0.0], np.cumsum(tl["bar_quarters"])])  # posición de cada compás en negras
    beats = np.array(json.loads(beats_json.read_text(encoding="utf-8"))["beats_ms"])
    on_t, on_w = tab_onsets(tl)
    env, step = onset_envelope(wav)
    score = _Scorer(env, step)

    ib = np.diff(beats)
    med_ib = float(np.median(ib))
    idx = np.arange(len(beats))

    def beat_time(pos: np.ndarray) -> np.ndarray:
        """Tiempo (ms) para una posición fraccionaria en la secuencia de beats, extrapolando en los extremos."""
        t = np.interp(pos, idx, beats)
        t = np.where(pos < 0, beats[0] + pos * med_ib, t)
        return np.where(pos > len(beats) - 1, beats[-1] + (pos - (len(beats) - 1)) * med_ib, t)

    # Posición nominal (en negras) de cada ataque de la tab
    on_q = np.interp(on_t, st, Q)
    best = (-np.inf, 0, 1.0)
    for per_q in (0.5, 1.0, 2.0):  # beats detectados por cada negra de la tab
        for b0 in range(len(beats)):
            pos = b0 + on_q * per_q
            if pos[-1] > len(beats) - 1 + 8 * per_q:  # la tab no puede pasarse mucho del final
                break
            sc = float((score(beat_time(pos), on_w)))
            if sc > best[0]:
                best = (sc, b0, per_q)
    _, b0, per_q = best
    T = beat_time(b0 + Q * per_q)

    ratio = np.diff(T) / np.diff(st)
    med = float(np.median(ratio))
    beats_used = (b0 + Q[-1] * per_q) - b0
    result = {
        "bar_times_ms": [round(float(t), 1) for t in T],
        "tempo_ratio_median": round(med, 4),
        "suspicious_bars": [int(k + 1) for k, r in enumerate(ratio) if abs(r / med - 1) > 0.1],
        "method": "beats",
        "first_beat_index": int(b0),
        "beats_per_quarter": per_q,
        "irregular_beat_intervals": int(np.sum(np.abs(ib / med_ib - 1) > 0.15)),
        "beats_beyond_audio": round(float(max(0.0, b0 + beats_used - (len(beats) - 1))), 1),
    }
    out_json.write_text(json.dumps(result, indent=1), encoding="utf-8")
    return result


def onset_error(timeline_json: Path, bar_times_json: Path, wav: Path, track: int) -> dict:
    """Error entre las notas de la pista alineadas y los onsets detectados en el audio (ms)."""
    import librosa

    tl = json.loads(timeline_json.read_text(encoding="utf-8"))
    bt = np.array(json.loads(bar_times_json.read_text(encoding="utf-8"))["bar_times_ms"])
    starts = np.array(tl["bar_starts_ms"])
    y, sr = librosa.load(str(wav), sr=SR, mono=True)
    onsets = librosa.onset.onset_detect(y=y, sr=sr, units="time", backtrack=False) * 1000
    note_t = np.array(sorted({n[0] for n in tl["notes"] if int(n[3]) == track}))
    mapped = np.interp(note_t, starts, bt)
    j = np.clip(np.searchsorted(onsets, mapped), 1, len(onsets) - 1)
    err = np.minimum(np.abs(onsets[j] - mapped), np.abs(onsets[j - 1] - mapped))
    bar = np.searchsorted(starts, note_t, side="right") - 1
    return {
        "notes": int(len(mapped)),
        "median_ms": round(float(np.median(err)), 1),
        "p90_ms": round(float(np.percentile(err, 90)), 1),
        "within_50ms": round(float(np.mean(err <= 50)), 3),
        "per_bar_median_ms": {int(b): round(float(np.median(err[bar == b])), 1) for b in np.unique(bar)},
    }
