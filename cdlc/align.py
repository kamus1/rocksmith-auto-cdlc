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
                   max_jump_half: int = 8, jump_cost: float = 0.6, ratio_cost: float = 1.5,
                   tempo_weight: float = 1.0, tempo_tolerance: float = 0.06) -> dict:
    """Rejilla de beats con programación dinámica por compases.

    Estado de cada compás: (posición de inicio en la secuencia de beats, con resolución de medio beat;
    beats detectados por negra de la tab: 0.5, 1 o 2). Lo esperado es que un compás empiece donde
    acaba el anterior con la misma relación; saltar medio beat (compases irregulares que el detector
    redondea) o cambiar de relación (secciones con otro tempo escrito) tiene coste. La emisión es la
    coincidencia de los ataques de la tab con los onsets del audio.

    Además se penaliza que la duración real (ms) de un compás se aleje de la que marca la tab al tempo
    global de la canción: así, cuando el detector se salta o inventa beats (intervalos de ½ o 2), el
    camino prefiere corregirlo con un salto antes que estirar o encoger el compás.
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

    # Relación global ms reales / ms de la tab (con una negra = un beat en las secciones principales)
    nominal = np.diff(st)
    qms = nominal / lens                                      # ms de una negra según la tab, por compás
    s_g = float(np.median(med_ib / qms))
    bt_h = beat_time(starts)                                  # tiempo de cada posición de inicio
    # Tempo local: mediana de los intervalos entre beats en ±8 beats (inmune a fallos sueltos del detector)
    loc = np.array([np.median(ib[max(0, i - 8):i + 9]) for i in range(len(ib))])
    loc_h = np.interp(starts, np.arange(len(ib)) + 0.5, loc)

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
        first_ms = beat_time(first_pos)
        cost = np.where(first_ms < start_ms - 150, np.inf, cost)
        # Preferencia suave: si la tab empieza tocando, su 1ª nota suele coincidir con el inicio del audio
        cost = cost + w_ref * np.minimum(np.abs(first_ms - start_ms) / 500.0, 2.0)
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
                # Duración real del compás k-1 si empezó en h-shift y el compás k empieza en h
                dur = np.full(H, np.nan)
                dur[shift:] = bt_h[shift:] - bt_h[:H - shift]
                # Duración esperada con el tempo local, en la misma "octava" que el tempo global
                s_loc = np.full(H, s_g)
                s_loc[shift:] = loc_h[:H - shift] / qms[k - 1]
                octave = 2.0 ** np.round(np.log2(s_loc / s_g))
                expected = nominal[k - 1] * s_loc / octave
                dev = np.log(np.maximum(dur, 1.0) / expected) / tempo_tolerance
                tempo_pen = np.nan_to_num(tempo_weight * w_ref * dev ** 2, nan=np.inf)
                for r in range(R):
                    cand = prev + jp + ratio_pen[rp, r] + tempo_pen
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


# --- Alineación híbrida: croma (qué compás) + batería por bandas (qué beat) + continuidad de tempo ---

HOP_CHROMA_S = 0.1
KICK = {35, 36}
SNARE = {37, 38, 39, 40, 31, 33, 34}
HIGH = {42, 44, 46, 49, 51, 52, 53, 55, 57, 59, 29, 30}


def _tab_chroma(tl: dict, n_frames: int) -> np.ndarray:
    C = np.zeros((12, n_frames))
    for t_ms, d_ms, midi, _tr in tl["notes"]:
        if midi < 0:
            continue
        a = int(t_ms / 1000 / HOP_CHROMA_S)
        b = max(a + 1, int(min(t_ms + d_ms, t_ms + 1000) / 1000 / HOP_CHROMA_S))
        if a < n_frames:
            C[int(midi) % 12, a:min(b, n_frames)] += 0.5
            C[int(midi) % 12, a] += 1.0
    return C


def chroma_warp(tl: dict, y: np.ndarray, band_rad: float = 0.1) -> np.ndarray:
    """Inicio (ms) de cada compás según DTW entre la croma de la tab y la croma CENS del audio.

    Solo se alinea el tramo con notas (de la primera a la última): los compases vacíos de los
    extremos (count-in, final) no tienen nada que casar y desplazarían todo. Esos compases se
    colocan después prolongando el tempo de los extremos alineados.
    """
    import librosa

    st = np.array(tl["bar_starts_ms"])
    pitched = [(n[0], n[0] + max(n[1], 1)) for n in tl["notes"] if n[2] >= 0] or [(n[0], n[0] + 1) for n in tl["notes"]]
    t_first = min(a for a, _ in pitched)
    t_last = max(b for _, b in pitched)
    f0 = int(t_first / 1000 / HOP_CHROMA_S)
    f1 = int(t_last / 1000 / HOP_CHROMA_S) + 1
    X = np.log1p(_tab_chroma(tl, f1)[:, f0:f1] * 10)
    X = librosa.util.normalize(X, norm=2, axis=0, fill=True)
    _, (s0, s1) = librosa.effects.trim(y, top_db=40)
    hop = int(round(HOP_CHROMA_S * SR))
    Y = librosa.feature.chroma_cens(y=y[s0:s1], sr=SR, hop_length=hop)
    _, wp = librosa.sequence.dtw(X=X, Y=Y, metric="cosine", backtrack=True,
                                 global_constraints=True, band_rad=band_rad)
    wp = wp[::-1]
    n_tab = f1 - f0
    t2a = np.full(n_tab, np.nan)
    for i in range(n_tab):
        js = wp[wp[:, 0] == i, 1]
        if len(js):
            t2a[i] = np.median(js) * HOP_CHROMA_S * 1000 + s0 / SR * 1000
    idx = np.arange(n_tab)
    ok = ~np.isnan(t2a)
    t2a = np.maximum.accumulate(np.interp(idx, idx[ok], t2a[ok]))
    # Tab (ms) → audio (ms) dentro del tramo con notas; fuera, se prolonga la pendiente de los extremos
    tab_ms = (idx + f0) * HOP_CHROMA_S * 1000
    edge = max(2, n_tab // 10)
    slope_a = (t2a[edge] - t2a[0]) / max(tab_ms[edge] - tab_ms[0], 1)
    slope_b = (t2a[-1] - t2a[-1 - edge]) / max(tab_ms[-1] - tab_ms[-1 - edge], 1)
    out = np.interp(st, tab_ms, t2a)
    out = np.where(st < tab_ms[0], t2a[0] + (st - tab_ms[0]) * slope_a, out)
    out = np.where(st > tab_ms[-1], t2a[-1] + (st - tab_ms[-1]) * slope_b, out)
    return np.maximum.accumulate(out)


def smooth_warp(st: np.ndarray, T: np.ndarray, window: int = 9, anchor_window: int = 15) -> np.ndarray:
    """Curva de tempo continua: mediana móvil de la relación ms reales / ms de la tab, integrada, y
    anclada localmente a T con la mediana del desfase en ±anchor_window/2 compases. Un salto suelto de
    la DTW se descarta en vez de repartirse por toda la zona."""
    from scipy.ndimage import median_filter

    ratio = np.diff(T) / np.maximum(np.diff(st), 1)
    ratio = median_filter(ratio, size=window, mode="nearest")
    S = np.concatenate([[0.0], np.cumsum(ratio * np.diff(st))])
    return S + median_filter(T - S, size=anchor_window, mode="nearest")


def drum_envelopes(y: np.ndarray) -> tuple[dict, float]:
    """Envolventes de onsets por banda: graves (bombo), medios (caja), agudos (platos) y total."""
    import librosa
    from scipy.ndimage import gaussian_filter1d

    S = librosa.feature.melspectrogram(y=y, sr=SR, hop_length=HOP, n_mels=96, fmin=30, fmax=11000)
    freqs = librosa.mel_frequencies(n_mels=96, fmin=30, fmax=11000)
    S_db = librosa.power_to_db(S)

    def env(lo, hi):
        rows = (freqs >= lo) & (freqs < hi)
        e = librosa.onset.onset_strength(S=S_db[rows], sr=SR, hop_length=HOP)
        e = gaussian_filter1d(e, 1.0)
        return np.clip(e / (np.percentile(e, 99) + 1e-9), 0, 1.5)

    envs = {"kick": env(30, 120), "snare": env(150, 2500), "high": env(5000, 11000), "all": env(30, 11000)}
    return envs, HOP / SR * 1000


def _class_onsets(tl: dict) -> list[tuple[str, np.ndarray, np.ndarray]]:
    """Ataques de la tab agrupados por la banda en la que deben sonar: (banda, tiempos, pesos)."""
    groups: dict[str, dict[float, float]] = {"kick": {}, "snare": {}, "high": {}, "all": {}}
    for t_ms, _d, midi, _tr in tl["notes"]:
        if midi < 0:
            drum = int(-midi)
            band, w = ("kick", 1.0) if drum in KICK else ("snare", 1.0) if drum in SNARE else ("high", 0.4) \
                if drum in HIGH else ("all", 0.5)
        else:
            band, w = "all", (WEIGHT_BASS if midi < 52 else WEIGHT_GUITAR) * 0.5
        key = round(t_ms, 0)
        groups[band][key] = groups[band].get(key, 0.0) + w
    out = []
    for band, g in groups.items():
        if g:
            t = np.array(sorted(g))
            out.append((band, t, np.minimum(np.array([g[x] for x in t]), 2.0)))
    return out


def _bar_part_chroma(tl: dict, st: np.ndarray, parts: int) -> np.ndarray:
    """(compases, partes, 12): croma de la tab en cada trozo del compás (notas que suenan en él)."""
    n = len(st) - 1
    out = np.zeros((n, parts, 12))
    for t_ms, d_ms, midi, _tr in tl["notes"]:
        if midi < 0:
            continue
        k = int(np.searchsorted(st, t_ms, side="right") - 1)
        if not 0 <= k < n:
            continue
        length = st[k + 1] - st[k]
        p0 = (t_ms - st[k]) / length * parts
        p1 = min(parts, p0 + max(d_ms, 1) / length * parts)
        for p in range(int(p0), int(np.ceil(p1))):
            overlap = min(p1, p + 1) - max(p0, p)
            if overlap > 0:
                out[k, p, int(midi) % 12] += overlap + (0.5 if p == int(p0) else 0)
    norm = np.linalg.norm(out, axis=2, keepdims=True)
    return np.where(norm > 0, out / np.maximum(norm, 1e-9), 0)


def align_hybrid(timeline_json: Path, wav: Path, out_json: Path, radius_beats: float = 2.5,
                 grid_ms: float = 10.0, smooth_weight: float = 0.5, onset_lag_ms: float = 40.0,
                 chroma_weight: float = 3.0, chroma_parts: int = 4) -> dict:
    """Alineación híbrida (ver docstring del módulo y docs/ALINEACION.md)."""
    import librosa

    tl = json.loads(timeline_json.read_text(encoding="utf-8"))
    st = np.array(tl["bar_starts_ms"])
    lens = np.array(tl["bar_quarters"], dtype=float)
    n = len(lens)
    y, _ = librosa.load(str(wav), sr=SR, mono=True)

    T_c = chroma_warp(tl, y)
    T_s = smooth_warp(st, T_c)

    envs, step = drum_envelopes(y)
    # El pico de la envolvente llega algo después del ataque: se compensa desplazándola hacia atrás
    lag = int(round(onset_lag_ms / step))
    scorers = {b: _Scorer(np.concatenate([e[lag:], np.zeros(lag)]) if lag > 0 else e, step) for b, e in envs.items()}
    classes = _class_onsets(tl)

    beat_ms = float(np.median(np.diff(T_s) / lens))             # duración típica de una negra en el audio
    offs = np.arange(-radius_beats * beat_ms, radius_beats * beat_ms + 0.001, grid_ms)
    m = len(offs)

    # Notas de cada compás (fracción dentro del compás) por banda
    per_bar = []
    for k in range(n):
        items = []
        for band, t, w in classes:
            sel = (t >= st[k]) & (t < st[k + 1])
            if sel.any():
                items.append((band, (t[sel] - st[k]) / (st[k + 1] - st[k]), w[sel]))
        per_bar.append(items)
    bar_w = np.array([sum(w.sum() for _, _, w in it) for it in per_bar])
    w_ref = float(np.median(bar_w[bar_w > 0])) if (bar_w > 0).any() else 1.0

    # Armonía fina: croma del audio (acumulada para promediar intervalos en O(1)) y croma de la tab por trozos
    hop_c = 512
    C_audio = librosa.feature.chroma_cqt(y=librosa.effects.harmonic(y), sr=SR, hop_length=hop_c)
    C_cum = np.concatenate([np.zeros((12, 1)), np.cumsum(C_audio, axis=1)], axis=1)
    step_c = hop_c / SR * 1000
    tab_parts = _bar_part_chroma(tl, st, chroma_parts)
    nf = C_audio.shape[1]

    def chroma_score(k: int, a: np.ndarray, b: np.ndarray) -> np.ndarray:
        if not tab_parts[k].any():
            return 0.0
        total = 0.0
        for p in range(chroma_parts):
            if not tab_parts[k, p].any():
                continue
            s_ = np.clip(((a + (b - a) * p / chroma_parts) / step_c).astype(int), 0, nf - 1)
            e_ = np.clip(((a + (b - a) * (p + 1) / chroma_parts) / step_c).astype(int), 1, nf)
            e_ = np.maximum(e_, s_ + 1)
            v = (C_cum[:, e_] - C_cum[:, s_])                       # (12, m, m)
            v = v / np.maximum(np.linalg.norm(v, axis=0, keepdims=True), 1e-9)
            total = total + np.tensordot(tab_parts[k, p], v, axes=(0, 0))
        return total / chroma_parts

    def emission(k: int) -> np.ndarray:
        """(m, m): de desplazamiento i en el inicio del compás k a j en el inicio del k+1."""
        a = T_s[k] + offs[:, None]
        b = T_s[k + 1] + offs[None, :]
        total = np.zeros((m, m))
        for band, frac, w in per_bar[k]:
            times = a[:, :, None] + (b - a)[:, :, None] * frac[None, None, :]
            total += scorers[band](times, w)
        return total + chroma_weight * w_ref * chroma_score(k, np.broadcast_to(a, (m, m)), np.broadcast_to(b, (m, m)))

    # Coste de cambiar el desplazamiento entre compases consecutivos (≈ cambio de tempo local)
    d = (offs[None, :] - offs[:, None]) / beat_ms
    trans = smooth_weight * w_ref * d ** 2

    # Restricción general: la primera nota de la tab no puede sonar antes de que empiece el audio.
    # Resuelve ambigüedades de ciclos que se repiten (p. ej. dos compases de los mismos acordes en
    # toda la canción) sin suponer que la tab empieza a la vez que el audio (puede haber una intro
    # de batería o voz que no está en la tab).
    start_ms = audio_start_ms(wav)
    first = next((k for k in range(n) if per_bar[k]), None)
    cost = np.zeros(m)
    back = np.zeros((n, m), dtype=np.int32)
    for k in range(n):
        if k == first:
            f0 = min(fr.min() for _, fr, _ in per_bar[k])
            first_ms = T_s[k] + offs + (T_s[k + 1] - T_s[k]) * f0
            cost = np.where(first_ms < start_ms - 150, np.inf, cost)
        total = cost[:, None] + trans - emission(k)
        back[k] = np.argmin(total, axis=0)
        cost = total[back[k], np.arange(m)]
    idx = np.zeros(n + 1, dtype=int)
    idx[n] = int(np.argmin(cost))
    for k in range(n - 1, -1, -1):
        idx[k] = back[k, idx[k + 1]]
    T = T_s + offs[idx]
    for k in range(1, n + 1):
        T[k] = max(T[k], T[k - 1] + 1)

    ratio = np.diff(T) / np.diff(st)
    med = float(np.median(ratio))
    result = {
        "bar_times_ms": [round(float(t), 1) for t in T],
        "tempo_ratio_median": round(med, 4),
        "suspicious_bars": [int(k + 1) for k, x in enumerate(ratio) if abs(x / med - 1) > 0.1],
        "method": "hybrid",
        "mean_abs_offset_ms": round(float(np.mean(np.abs(offs[idx]))), 1),
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
