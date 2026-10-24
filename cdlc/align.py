"""Alineación tab ↔ audio por rejilla de beats.

Cada negra de la tab se asigna a beats detectados por beat_this. align_beats_dp elige por
programación dinámica dónde empieza cada compás (resolución de medio beat) y cuántos beats hay por
negra, comparando los ataques de la tab (batería, bajo y guitarras) con la envolvente de onsets del
audio; refine_local ajusta después cada inicio de compás unos milisegundos. Se descartaron por imprecisos: downbeats de beat_this (fallan con compases
irregulares), DTW sobre croma (se pierde con guitarras distorsionadas) y seguimiento rítmico
compás a compás, voraz o con Viterbi (ambiguo en riffs repetitivos).
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


def audio_start_ms(wav: Path, top_db: float = 40.0) -> float:
    """Instante (ms) en que empieza a sonar el audio, tras el silencio inicial."""
    import librosa

    y, _ = librosa.load(str(wav), sr=SR, mono=True, duration=60)
    _, (start, _end) = librosa.effects.trim(y, top_db=top_db)
    return start / SR * 1000


class _Scorer:
    def __init__(self, env: np.ndarray, step_ms: float):
        self.env = env
        self.step = step_ms

    def __call__(self, times_ms: np.ndarray, w: np.ndarray) -> np.ndarray:
        """times_ms con forma (..., n) → puntuación (...)."""
        idx = np.clip(np.rint(times_ms / self.step).astype(int), 0, len(self.env) - 1)
        return (self.env[idx] * w).sum(axis=-1)


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


def align_beats_dp(timeline_json: Path, wav: Path, beats_json: Path, out_json: Path,
                   max_jump_half: int = 4, jump_cost: float = 0.6, ratio_cost: float = 1.5) -> dict:
    """Rejilla de beats con programación dinámica por compases.

    Estado de cada compás: (posición de inicio en la secuencia de beats, con resolución de medio beat;
    beats detectados por negra de la tab: 0.5, 1 o 2). Lo esperado es que un compás empiece donde
    acaba el anterior con la misma relación; saltar medio beat (compases irregulares que el detector
    redondea) o cambiar de relación (secciones con otro tempo escrito) tiene coste. La emisión es la
    coincidencia de los ataques de la tab con los onsets del audio.
    """
    tl = json.loads(timeline_json.read_text(encoding="utf-8"))
    st = np.array(tl["bar_starts_ms"])
    lens = np.array(tl["bar_quarters"], dtype=float)
    n = len(lens)
    beats = np.array(json.loads(beats_json.read_text(encoding="utf-8"))["beats_ms"])
    on_t, on_w = tab_onsets(tl)
    env, step = onset_envelope(wav)
    score = _Scorer(env, step)

    ib = np.diff(beats)
    med_ib = float(np.median(ib))
    nb = len(beats)

    def beat_time(pos: np.ndarray) -> np.ndarray:
        t = np.interp(pos, np.arange(nb), beats)
        t = np.where(pos < 0, beats[0] + pos * med_ib, t)
        return np.where(pos > nb - 1, beats[-1] + (pos - (nb - 1)) * med_ib, t)

    ratios = np.array([0.5, 1.0, 2.0])
    R = len(ratios)
    H = 2 * (nb + 8)                     # posiciones de inicio en medios beats: -8 .. nb (beats)
    pos_half = np.arange(H) - 16         # índice → medio beat (desde -8 beats)
    starts = pos_half / 2.0              # posición en beats

    # Notas de cada compás como fracción del compás
    bar_notes = []
    for k in range(n):
        sel = (on_t >= st[k]) & (on_t < st[k + 1])
        bar_notes.append(((on_t[sel] - st[k]) / (st[k + 1] - st[k]), on_w[sel]))
    bar_w = np.array([w.sum() for _, w in bar_notes])
    w_ref = float(np.median(bar_w[bar_w > 0])) if (bar_w > 0).any() else 1.0

    def emission(k: int) -> np.ndarray:
        frac, w = bar_notes[k]
        if len(frac) == 0:
            return np.zeros((R, H))
        span = ratios[:, None] * lens[k]                     # (R,1) beats que ocupa el compás
        pos = starts[None, :, None] + span[:, :, None] * frac[None, None, :]
        return score(beat_time(pos), w)                      # (R, H)

    jumps = np.arange(-max_jump_half, max_jump_half + 1)
    jump_pen = np.where(jumps == 0, 0.0, jump_cost * w_ref * (1 + np.abs(jumps) / 2))
    ratio_pen = np.where(np.eye(R, dtype=bool), 0.0, ratio_cost * w_ref)

    cost = -emission(0)                                       # (R, H): coste acumulado con el compás 0 en ese estado
    # La primera nota de la tab no puede sonar antes que el audio (los riffs repetitivos puntúan casi
    # igual desplazados unos compases, y sin esto la tab puede empezar dentro del silencio inicial)
    start_ms = audio_start_ms(wav)
    first = next((k for k in range(n) if len(bar_notes[k][0])), None)
    if first is not None:
        q0 = float(lens[:first].sum()) + bar_notes[first][0][0] * lens[first]   # negras hasta la 1ª nota
        first_pos = starts[None, :] + ratios[:, None] * q0
        cost = np.where(beat_time(first_pos) < start_ms - 150, np.inf, cost)
    back = np.zeros((n, R, H, 2), dtype=np.int32)             # (ratio previo, posición previa)
    for k in range(1, n):
        # Donde acabaría el compás k-1 desde cada estado, en medios beats
        best = np.full((R, H), np.inf)
        arg_r = np.zeros((R, H), dtype=np.int32)
        arg_h = np.zeros((R, H), dtype=np.int32)
        for rp in range(R):
            adv = int(round(ratios[rp] * lens[k - 1] * 2))    # medios beats que avanza el compás previo
            for j, jp in zip(jumps, jump_pen):
                shift = adv + j
                prev = np.full(H, np.inf)
                if 0 < shift < H:
                    prev[shift:] = cost[rp, :H - shift]
                elif shift <= 0:
                    continue
                for r in range(R):
                    cand = prev + jp + ratio_pen[rp, r]
                    better = cand < best[r]
                    best[r] = np.where(better, cand, best[r])
                    arg_r[r] = np.where(better, rp, arg_r[r])
                    arg_h[r] = np.where(better, np.arange(H) - shift, arg_h[r])
        back[k, :, :, 0] = arg_r
        back[k, :, :, 1] = arg_h
        cost = best - emission(k)

    r, h = np.unravel_index(int(np.argmin(cost)), cost.shape)
    path = [(r, h)]
    for k in range(n - 1, 0, -1):
        r, h = back[k, r, h]
        path.append((r, h))
    path.reverse()

    T = np.zeros(n + 1)
    for k, (r, h) in enumerate(path):
        T[k] = beat_time(np.array(starts[h]))
    r_last, h_last = path[-1]
    T[n] = beat_time(np.array(starts[h_last] + ratios[r_last] * lens[-1]))

    jumps_taken = sum(1 for k in range(1, n)
                      if path[k][1] != path[k - 1][1] + round(ratios[path[k - 1][0]] * lens[k - 1] * 2))
    ratio_changes = [(k + 1, float(ratios[path[k][0]])) for k in range(1, n) if path[k][0] != path[k - 1][0]]
    ratio = np.diff(T) / np.diff(st)
    med = float(np.median(ratio))
    result = {
        "bar_times_ms": [round(float(t), 1) for t in T],
        "tempo_ratio_median": round(med, 4),
        "suspicious_bars": [int(k + 1) for k, x in enumerate(ratio) if abs(x / med - 1) > 0.1],
        "method": "beats_dp",
        "first_beat_index": float(starts[path[0][1]]),
        "beats_per_quarter_start": float(ratios[path[0][0]]),
        "ratio_changes": ratio_changes,
        "half_beat_jumps": jumps_taken,
        "irregular_beat_intervals": int(np.sum(np.abs(ib / med_ib - 1) > 0.15)),
    }
    out_json.write_text(json.dumps(result, indent=1), encoding="utf-8")
    return result


def refine_local(timeline_json: Path, wav: Path, bar_times_json: Path, out_json: Path,
                 radius_ms: float = 60.0, smooth: int = 5) -> dict:
    """Ajuste fino: mueve cada inicio de compás hasta ±radius_ms para que los ataques de los dos compases
    que lo rodean caigan en onsets del audio; los ajustes se suavizan con la mediana de `smooth` compases."""
    from scipy.ndimage import median_filter

    tl = json.loads(timeline_json.read_text(encoding="utf-8"))
    st = np.array(tl["bar_starts_ms"])
    data = json.loads(bar_times_json.read_text(encoding="utf-8"))
    T = np.array(data["bar_times_ms"])
    on_t, on_w = tab_onsets(tl)
    env, step = onset_envelope(wav)
    score = _Scorer(env, step)
    n = len(T) - 1
    deltas = np.arange(-radius_ms, radius_ms + 0.001, 5.0)

    shift = np.zeros(n + 1)
    for k in range(n + 1):
        a, b = st[max(0, k - 1)], st[min(n, k + 1)]
        sel = (on_t >= a) & (on_t < b)
        if sel.sum() < 3:
            continue
        mapped = np.interp(on_t[sel], st, T)
        # Desplazamiento que decae linealmente desde el inicio del compás k hacia los vecinos
        lo, mid, hi = T[max(0, k - 1)], T[k], T[min(n, k + 1)]
        wgt = np.where(mapped < mid, (mapped - lo) / max(mid - lo, 1), (hi - mapped) / max(hi - mid, 1))
        wgt = np.clip(wgt, 0, 1)
        cand = mapped[None, :] + deltas[:, None] * wgt[None, :]
        sc = score(cand, on_w[sel])
        shift[k] = deltas[int(np.argmax(sc))]
    shift = median_filter(shift, size=smooth, mode="nearest")
    T2 = T + shift
    for k in range(1, len(T2)):
        T2[k] = max(T2[k], T2[k - 1] + 1)
    data = dict(data, bar_times_ms=[round(float(t), 1) for t in T2], method=data.get("method", "") + "+refine",
                refine_mean_abs_shift_ms=round(float(np.mean(np.abs(shift))), 1))
    out_json.write_text(json.dumps(data, indent=1), encoding="utf-8")
    return data


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
