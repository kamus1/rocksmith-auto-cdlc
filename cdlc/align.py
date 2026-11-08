"""Alineación tab ↔ audio: en qué instante del audio empieza cada compás de la tab.

align_hybrid, en dos etapas (detalles y resultados en docs/ALINEACION.md):
  1. tempo_dp: programación dinámica sobre el inicio de cada compás con un modelo de tempo plausible
     y emisión de armonía (croma de la tab frente a la del audio). chroma_warp (DTW) solo da el tempo
     global de partida.
  2. Paso fino: cada compás se desplaza hasta ±2,5 beats sobre esa curva, comparando los golpes de
     batería por bandas, los ataques de guitarras y bajo y la armonía por trozos del compás.
Se descartaron: la rejilla de beats de beat_this (se salta o inventa beats), la DTW de croma como
alineación (se salta ciclos de acordes repetidos) y los ataques sin un modelo de tempo (ambiguos en
riffs repetitivos).
"""

import json
from pathlib import Path

import numpy as np

SR = 22050
HOP = 256
WEIGHT_BASS = 0.6
WEIGHT_GUITAR = 0.4


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


HOP_CHROMA_S = 0.1
KICK = {35, 36}
SNARE = {37, 38, 39, 40, 31, 33, 34}
HIGH = {42, 44, 46, 49, 51, 52, 53, 55, 57, 59, 29, 30}
# Cuenta de entrada típica de una tab: baquetas, aro o charles, sin bombo ni caja
COUNT_IN = {31, 33, 37, 42, 44, 46}


def count_in_bars(tl: dict) -> int:
    """Compases iniciales que son solo cuenta de entrada: antes de la primera nota con altura y solo con
    golpes de COUNT_IN. Muchas grabaciones no la tienen (la canción arranca con el riff) y, si se intenta
    casar, arrastra los primeros compases. Si la grabación sí la tiene, perderla no cuesta nada: el compás
    siguiente ya tiene notas."""
    st = tl["bar_starts_ms"]
    pitched = [t for t, _d, midi, _tr in tl["notes"] if midi >= 0]
    if not pitched:
        return 0
    first = min(pitched)
    k = 0
    while k + 1 < len(st) and st[k + 1] <= first:
        drums = [-midi for t, _d, midi, _tr in tl["notes"] if st[k] <= t < st[k + 1] and midi < 0]
        if drums and not set(drums) <= COUNT_IN:
            break
        k += 1
    return k


def _held(tl: dict, use_held: bool = True) -> list:
    """Notas que suenan sin ataque nuevo, [t_ms, d_ms, midi]: la parte estirada de un bend y la continuación
    de una nota ligada o larga. Solo cuentan para la croma, no como ataques."""
    return tl.get("held", []) if use_held else []


def _tab_chroma(tl: dict, n_frames: int, use_held: bool = True) -> np.ndarray:
    C = np.zeros((12, n_frames))
    segments = [(t, d, m, True) for t, d, m, _tr in tl["notes"]] + [(t, d, m, False) for t, d, m in _held(tl, use_held)]
    for t_ms, d_ms, midi, attack in segments:
        if midi < 0:
            continue
        a = int(t_ms / 1000 / HOP_CHROMA_S)
        b = max(a + 1, int(min(t_ms + d_ms, t_ms + 1000) / 1000 / HOP_CHROMA_S))
        if a < n_frames:
            C[int(midi) % 12, a:min(b, n_frames)] += 0.5
            if attack:
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


def tempo_dp(tl: dict, y: np.ndarray, T_guess: np.ndarray, ratio_span: float = 0.35, ratio_step: float = 0.015,
             tempo_weight: float = 0.002, band: int = 3, jump_cost: float = 1.0, grid_ms: float = 25.0,
             samples: int = 16, start_ms: float = 0.0, chroma_lag_ms: float = 160.0,
             gap_cost: float | np.ndarray = 0.2, end_ms: float = np.inf, silence_db: float = -50.0,
             lead_slack_bars: int | None = 2, use_held: bool = True) -> tuple[np.ndarray, list[tuple[int, float]]]:
    """Inicio (ms) de cada compás por programación dinámica con un modelo de tempo plausible.

    Estado en cada frontera de compás: (instante en el audio, en pasos de grid_ms; relación de tempo
    del compás anterior). Cada compás dura su longitud nominal × relación de tempo; cambiar la relación
    entre compases cuesta tempo_weight × (pasos)² hasta ±band pasos, y un cambio mayor (cambio de
    sección), jump_cost. La emisión es la similitud de croma (coseno) entre la tab y el audio a lo largo
    del compás, menos su media en todo el audio (lo que puntuaría una posición al azar). Así no puede
    saltarse ciclos de acordes repetidos: para ganar un ciclo tendría que acelerar de forma irreal.
    T_guess (la DTW) solo da la relación de tempo global.

    La rejilla temporal tiene que ser fina: con frames de 100 ms, un compás de 1,6 s solo puede
    cambiar de duración en pasos del 6 % y el modelo de tempo deja de distinguir nada.
    La primera nota no puede sonar antes de start_ms (inicio del audio) menos 150 ms, ni la última después
    de end_ms (fin del audio) más 150 ms. Los frames de audio a más de silence_db por debajo del máximo
    no tienen croma: si no, el ruido de fondo normalizado se parece a cualquier cosa. La croma del audio
    llega chroma_lag_ms después del ataque (y la de la tab se redondea hacia abajo): se compensa al final.

    Huecos: entre dos compases puede haber audio que no está en la tab (una repetición o un final más
    largo en la grabación), con coste gap_cost. Sin esto, todo lo que viene después se adelanta. El
    compás anterior al hueco se estira hasta el siguiente. Devuelve (inicios, [(compás tras el hueco,
    segundos de hueco)]).

    Solo usa croma: se probó añadir los ataques de la tab y desplazaba canciones enteras varios beats
    (con riffs repetitivos casan igual de bien desplazados); eso queda para el paso fino, con radio corto.
    """
    import librosa

    st = np.array(tl["bar_starts_ms"])
    n = len(st) - 1
    hop_ms = HOP_CHROMA_S * 1000
    has = np.zeros(n, dtype=bool)
    for t_ms, _d, midi, _tr in tl["notes"]:
        k = int(np.searchsorted(st, t_ms, side="right") - 1)
        if 0 <= k < n and midi >= 0:
            has[k] = True
    if not has.any():
        return T_guess, []
    # Compases con emisión: con algún ataque o con alguna nota que sigue sonando (bend, ligadura)
    emit = has.copy()
    for t_ms, d_ms, midi in _held(tl, use_held):
        if midi >= 0:
            a_, b_ = np.searchsorted(st, [t_ms, t_ms + max(d_ms, 1)], side="right") - 1
            emit[max(0, a_):min(n, b_ + 1)] = True
    k0, k1 = int(np.argmax(has)), int(n - np.argmax(has[::-1]))      # compases [k0, k1) con notas

    # Croma de la tab (frames de hop_ms) y del audio interpolada a la rejilla, normalizadas
    n_tab = int(st[-1] / hop_ms) + 2
    X = librosa.util.normalize(np.log1p(_tab_chroma(tl, n_tab, use_held) * 10), norm=2, axis=0, fill=False)
    Y = librosa.feature.chroma_cqt(y=y, sr=SR, hop_length=int(round(HOP_CHROMA_S * SR)))
    Y = np.log1p(Y * 10)
    rms = librosa.feature.rms(y=y, hop_length=int(round(HOP_CHROMA_S * SR)))[0][:Y.shape[1]]
    db = 20 * np.log10(rms / (rms.max() + 1e-12) + 1e-12)
    Y[:, :len(rms)][:, db < silence_db] = 0
    n_g = int((Y.shape[1] - 1) * hop_ms / grid_ms) + 1
    pos = np.arange(n_g) * grid_ms / hop_ms
    i0 = np.minimum(pos.astype(int), Y.shape[1] - 2)
    fr = (pos - i0)[None, :]
    Y = librosa.util.normalize(Y[:, i0] * (1 - fr) + Y[:, i0 + 1] * fr, norm=2, axis=0, fill=False).astype(np.float32)

    rel = np.diff(T_guess) / np.maximum(np.diff(st), 1)
    r0 = float(np.median(rel[k0:k1]))
    n_r = int(round(np.log1p(ratio_span) / np.log1p(ratio_step)))
    ratios = r0 * (1 + ratio_step) ** np.arange(-n_r, n_r + 1)
    R = len(ratios)

    def cells(ms: float, r: float) -> int:
        # Duraciones telescópicas: con tempo constante, la suma de compases no acumula redondeos
        return int(round(ms * r / grid_ms))

    t_idx = np.arange(n_g)
    first_note = min(t for t, _d, midi, _tr in tl["notes"] if midi >= 0)
    cost = np.zeros((R, n_g), dtype=np.float32)                      # inicio libre, salvo la restricción
    # Compases vacíos al principio de la tab (intro sin la guitarra): la primera nota no puede sonar antes
    # de que pasen, salvo lead_slack_bars de margen (un compás de cuenta añadido a la tab, por ejemplo).
    # Solo es un límite inferior: si la tab se salta la intro, la primera nota cae más tarde y no estorba.
    lead_ms = 0.0 if lead_slack_bars is None else max(0.0, first_note - st[min(lead_slack_bars, n)])
    for i, r in enumerate(ratios):
        cost[i, t_idx * grid_ms + (first_note - st[k0]) * r < start_ms - 150 + lead_ms * r] = np.inf
    backs_r = np.zeros((k1 - k0, R, n_g), dtype=np.int8)
    # Huecos: g[t] = mejor coste de haber llegado a la frontera antes de t (mínimo acumulado).
    # Bits por frontera: used = se llega por un hueco; fresh = el mínimo de g se renovó en t-1.
    used_gap = [None] * (k1 - k0)
    fresh_min = [None] * (k1 - k0)
    for j, k in enumerate(range(k0, k1)):
        if j > 0:
            prev = np.concatenate([np.full((R, 1), np.inf, dtype=np.float32), cost[:, :-1]], axis=1)
            g = np.minimum.accumulate(prev, axis=1)
            fresh = prev <= g
            via = g + np.float32(gap_cost[k] if np.ndim(gap_cost) else gap_cost)
            use = via < cost
            cost = np.where(use, via, cost)
            used_gap[j], fresh_min[j] = np.packbits(use, axis=1), np.packbits(fresh, axis=1)
        # Transición: cambio de relación de tempo en banda (coste cuadrático) o salto de sección
        best = np.full((R, n_g), np.inf, dtype=np.float32)
        br = np.zeros((R, n_g), dtype=np.int8)
        for dlt in range(-band, band + 1):
            lo, hi = max(0, dlt), min(R, R + dlt)                    # r' = r + dlt
            cand = cost[lo - dlt:hi - dlt] + np.float32(tempo_weight * dlt ** 2)
            better = cand < best[lo:hi]
            best[lo:hi] = np.where(better, cand, best[lo:hi])
            br[lo:hi] = np.where(better, (np.arange(lo, hi) - dlt)[:, None], br[lo:hi])
        jr = np.argmin(cost, axis=0)
        jc = cost[jr, t_idx] + np.float32(jump_cost)
        better = jc[None, :] < best
        best = np.where(better, jc[None, :], best)
        br = np.where(better, jr[None, :].astype(np.int8), br)
        # Emisión: similitud de unos instantes de la tab en el compás k con el audio, desde t con relación r'
        if emit[k]:
            ts = st[k] + (st[k + 1] - st[k]) * np.arange(samples) / samples
            rows = np.clip(np.rint(ts / hop_ms).astype(int), 0, n_tab - 1)
            Sk = X[:, rows].T.astype(np.float32) @ Y                  # (samples, n_g)
            Sk -= Sk.mean(axis=1, keepdims=True)
        new = np.full((R, n_g), np.inf, dtype=np.float32)
        for i, r in enumerate(ratios):
            val = best[i]
            if emit[k]:
                em = np.zeros(n_g, dtype=np.float32)
                for s_, t_ in enumerate(ts):
                    o = cells(t_ - st[k], r)
                    em[:n_g - o] += Sk[s_, o:]
                val = val - em / samples
            dur = cells(st[k + 1], r) - cells(st[k], r)
            if 0 < dur < n_g:
                new[i, dur:] = val[:n_g - dur]
        backs_r[j] = br
        cost = new
    # Retroceso: en cada frontera, (r del compás anterior, t)
    # Restricción de fin: la última nota (en tiempo de croma, que va chroma_lag_ms por detrás) no puede
    # sonar después de que termine el audio
    last_note = max(t for t, _d, midi, _tr in tl["notes"] if midi >= 0)
    for i, r in enumerate(ratios):
        cost[i, t_idx * grid_ms - (st[k1] - last_note) * r - chroma_lag_ms > end_ms + 150] = np.inf
    r_i, t_i = np.unravel_index(int(np.argmin(cost)), cost.shape)
    T = np.zeros(n + 1)
    T[k1] = t_i * grid_ms
    gaps = []
    bit = lambda a, r, t: (a[r, t >> 3] >> (7 - (t & 7))) & 1
    for j in range(k1 - k0 - 1, -1, -1):
        k = k0 + j
        t_i -= cells(st[k + 1], ratios[r_i]) - cells(st[k], ratios[r_i])
        T[k] = t_i * grid_ms
        r_i = int(backs_r[j, r_i, t_i])
        if j > 0 and bit(used_gap[j], r_i, t_i):
            # El compás k empezó tras un hueco: se busca dónde terminó de verdad el compás k-1
            t_end = t_i
            while not bit(fresh_min[j], r_i, t_end):
                t_end -= 1
            t_end -= 1
            gaps.append((k, (t_i - t_end) * grid_ms / 1000))
            t_i = t_end
    T[k0:k1 + 1] -= chroma_lag_ms
    # Compases vacíos de los extremos: se prolonga el tempo de los extremos alineados
    edge = max(1, min(4, (k1 - k0) // 4))
    ra = (T[k0 + edge] - T[k0]) / max(st[k0 + edge] - st[k0], 1)
    rb = (T[k1] - T[k1 - edge]) / max(st[k1] - st[k1 - edge], 1)
    T[:k0] = T[k0] + (st[:k0] - st[k0]) * ra
    T[k1 + 1:] = T[k1] + (st[k1 + 1:] - st[k1]) * rb
    return T, sorted(gaps)


def chroma_curve_score(tl: dict, y: np.ndarray, T: np.ndarray, use_held: bool = True,
                       grid_ms: float = 25.0, chroma_lag_ms: float = 160.0, silence_db: float = -50.0) -> float:
    """Emisión de croma de la etapa gruesa (la de tempo_dp: similitud menos su media en todo el audio) sumada
    sobre los compases, para unos inicios de compás T dados. Sirve para comparar dos curvas con el criterio
    de la armonía."""
    import librosa

    st = np.array(tl["bar_starts_ms"])
    n = len(st) - 1
    hop_ms = HOP_CHROMA_S * 1000
    n_tab = int(st[-1] / hop_ms) + 2
    X = librosa.util.normalize(np.log1p(_tab_chroma(tl, n_tab, use_held) * 10), norm=2, axis=0, fill=False)
    hop = int(round(HOP_CHROMA_S * SR))
    Y = np.log1p(librosa.feature.chroma_cqt(y=y, sr=SR, hop_length=hop) * 10)
    rms = librosa.feature.rms(y=y, hop_length=hop)[0][:Y.shape[1]]
    Y[:, :len(rms)][:, 20 * np.log10(rms / (rms.max() + 1e-12) + 1e-12) < silence_db] = 0
    n_g = int((Y.shape[1] - 1) * hop_ms / grid_ms) + 1
    pos = np.arange(n_g) * grid_ms / hop_ms
    i0 = np.minimum(pos.astype(int), Y.shape[1] - 2)
    fr = (pos - i0)[None, :]
    Y = librosa.util.normalize(Y[:, i0] * (1 - fr) + Y[:, i0 + 1] * fr, norm=2, axis=0, fill=False)
    total = 0.0
    for k in range(min(n, len(T) - 1)):
        ts = st[k] + (st[k + 1] - st[k]) * np.arange(16) / 16
        rows = np.clip(np.rint(ts / hop_ms).astype(int), 0, n_tab - 1)
        if not X[:, rows].any():
            continue
        S = X[:, rows].T @ Y
        S -= S.mean(axis=1, keepdims=True)
        at = T[k] + (ts - st[k]) * (T[k + 1] - T[k]) / (st[k + 1] - st[k]) + chroma_lag_ms
        total += float(S[np.arange(16), np.clip(np.rint(at / grid_ms).astype(int), 0, n_g - 1)].mean())
    return total


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


def _bar_part_chroma(tl: dict, st: np.ndarray, parts: int, use_held: bool = True) -> np.ndarray:
    """(compases, partes, 12): croma de la tab en cada trozo del compás (notas que suenan en él).
    Un ataque cuenta solo en su compás; una nota que sigue sonando (held) cuenta en todos los que cruza."""
    n = len(st) - 1
    out = np.zeros((n, parts, 12))
    segments = [(t, d, m, True) for t, d, m, _tr in tl["notes"]] + [(t, d, m, False) for t, d, m in _held(tl, use_held)]
    for t_ms, d_ms, midi, attack in segments:
        if midi < 0:
            continue
        k = int(np.searchsorted(st, t_ms, side="right") - 1)
        end = t_ms + max(d_ms, 1)
        while 0 <= k < n and st[k] < end:
            length = st[k + 1] - st[k]
            p0 = max(0.0, (t_ms - st[k]) / length * parts)
            p1 = min(parts, (end - st[k]) / length * parts)
            for p in range(int(p0), int(np.ceil(p1))):
                overlap = min(p1, p + 1) - max(p0, p)
                if overlap > 0:
                    out[k, p, int(midi) % 12] += overlap + (0.5 if attack and p == int(p0) and st[k] <= t_ms else 0)
            if attack:
                break                                       # como antes: un ataque solo cuenta en su compás
            k += 1
    norm = np.linalg.norm(out, axis=2, keepdims=True)
    return np.where(norm > 0, out / np.maximum(norm, 1e-9), 0)


def align_hybrid(timeline_json: Path, wav: Path, out_json: Path, radius_beats: float = 2.5,
                 grid_ms: float = 10.0, smooth_weight: float = 0.5, onset_lag_ms: float = 40.0,
                 chroma_weight: float = 3.0, chroma_parts: int = 4, anchor_weight: float = 0.3,
                 gap_select_margin: float = 0.0, anchor_ref_ms: float | None = 450.0,
                 use_held: bool = True, per_gap_select: bool = True, gap_ban_bars: int = 4,
                 refine_weight: float = 1000.0, refine_radius_beats: float = 4.0, refine_max_ms: float = 2000.0,
                 refine_grid_ms: float = 40.0, refine_margin: float = 1.5, refine_chroma: float = 20.0,
                 tempo_params: dict | None = None) -> dict:
    """Alineación híbrida (ver docstring del módulo y docs/ALINEACION.md)."""
    import librosa

    tl = json.loads(timeline_json.read_text(encoding="utf-8"))
    st = np.array(tl["bar_starts_ms"])
    lens = np.array(tl["bar_quarters"], dtype=float)
    n_count_in = count_in_bars(tl)
    if n_count_in:
        tl["notes"] = [nt for nt in tl["notes"] if nt[0] >= st[n_count_in]]
    n = len(lens)
    y, _ = librosa.load(str(wav), sr=SR, mono=True)

    envs, step = drum_envelopes(y)
    # El pico de la envolvente llega algo después del ataque: se compensa desplazándola hacia atrás
    lag = int(round(onset_lag_ms / step))
    lagged = {b: np.concatenate([e[lag:], np.zeros(lag)]) if lag > 0 else e for b, e in envs.items()}
    scorers = {b: _Scorer(e, step) for b, e in lagged.items()}
    classes = _class_onsets(tl)

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
    first = next((k for k in range(n) if per_bar[k]), None)

    # Armonía fina: croma del audio (acumulada para promediar intervalos en O(1)) y croma de la tab por trozos
    hop_c = 512
    C_audio = librosa.feature.chroma_cqt(y=librosa.effects.harmonic(y), sr=SR, hop_length=hop_c)
    C_cum = np.concatenate([np.zeros((12, 1)), np.cumsum(C_audio, axis=1)], axis=1)
    step_c = hop_c / SR * 1000
    tab_parts = _bar_part_chroma(tl, st, chroma_parts, use_held)
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

    def bar_emission(k: int, T_s: np.ndarray, gap_ms: np.ndarray, offs: np.ndarray) -> np.ndarray:
        """(m, m): emisión del compás k (ataques por banda + croma por trozos) si empieza con el desplazamiento
        offs[i] sobre T_s y termina (antes del hueco, si lo hay) con offs[j]."""
        m = len(offs)
        a = T_s[k] + offs[:, None]
        b = T_s[k + 1] - gap_ms[k] + offs[None, :]
        total = np.zeros((m, m))
        for band, frac, w in per_bar[k]:
            times = a[:, :, None] + (b - a)[:, :, None] * frac[None, None, :]
            total += scorers[band](times, w)
        return total + chroma_weight * w_ref * chroma_score(k, np.broadcast_to(a, (m, m)), np.broadcast_to(b, (m, m)))

    def start_penalty(k: int, T_s: np.ndarray, gap_ms: np.ndarray, offs: np.ndarray, beat_ms: float) -> np.ndarray:
        """Restricción general: la primera nota de la tab no puede sonar antes de que empiece el audio.
        Resuelve ambigüedades de ciclos que se repiten (p. ej. dos compases de los mismos acordes en toda la
        canción) sin suponer que la tab empieza a la vez que el audio (puede haber una intro sin la tab).
        Penalización finita y creciente: si ningún desplazamiento la cumple, gana el que menos la viola."""
        f0 = min(fr.min() for _, fr, _ in per_bar[k])
        first_ms = T_s[k] + offs + (T_s[k + 1] - gap_ms[k] - T_s[k]) * f0
        return 1e3 * w_ref * np.maximum(0, start_ms - 150 - first_ms) / beat_ms

    def fine(T_s: np.ndarray, gap_ms: np.ndarray) -> tuple[np.ndarray, np.ndarray, float]:
        """Paso fino sobre la curva gruesa T_s. Devuelve (inicios, desplazamientos, coste en unidades de w_ref)."""
        beat_ms = float(np.median((T_s[1:] - gap_ms - T_s[:-1]) / lens))   # duración típica de una negra
        offs = np.arange(-radius_beats * beat_ms, radius_beats * beat_ms + 0.001, grid_ms)
        m = len(offs)

        def emission(k: int) -> np.ndarray:
            return bar_emission(k, T_s, gap_ms, offs)

        # Coste de cambiar el desplazamiento entre compases consecutivos (≈ cambio de tempo local)
        d = (offs[None, :] - offs[:, None]) / beat_ms
        trans = smooth_weight * w_ref * d ** 2
        # Ancla suave a la curva gruesa: sin ella, en tramos de ritmo ambiguo el paso fino se va a ±1 beat
        anchor = anchor_weight * w_ref * (offs / min(beat_ms, anchor_ref_ms or beat_ms)) ** 2

        cost = np.zeros(m)
        back = np.zeros((n, m), dtype=np.int32)
        for k in range(n):
            if k == first:
                cost = cost + start_penalty(k, T_s, gap_ms, offs, beat_ms)
            total = cost[:, None] + trans - emission(k)
            back[k] = np.argmin(total, axis=0)
            cost = total[back[k], np.arange(m)] + anchor
        idx = np.zeros(n + 1, dtype=int)
        idx[n] = int(np.argmin(cost))
        for k in range(n - 1, -1, -1):
            idx[k] = back[k, idx[k + 1]]
        T = T_s + offs[idx]
        for k in range(1, n + 1):
            T[k] = max(T[k], T[k - 1] + 1)
        return T, offs[idx], float(cost[idx[n]]) / w_ref

    def tempo_refine(T_s: np.ndarray, gap_ms: np.ndarray) -> np.ndarray:
        """Curva alternativa a la etapa gruesa con la emisión del paso fino y un modelo de tempo de segundo orden.

        La etapa gruesa decide con la croma, que en patrones repetidos puede preferir una deriva lenta de
        varios beats (Yes: hasta +2,8 beats en 90 compases); el paso fino, anclado a ella, no puede corregirla
        aunque su emisión prefiera la curva buena. Aquí el estado es (desplazamiento al inicio del compás,
        al final), hasta ±refine_radius_beats sobre T_s, y lo que cuesta es cambiar de tempo entre compases
        consecutivos: refine_weight × (Δ log relación de tempo)². Mantener un tempo constante no cuesta,
        aunque se aleje de T_s. Solo es un candidato: se queda si el paso fino sobre él da mejor coste."""
        beat_ms = float(np.median((T_s[1:] - gap_ms - T_s[:-1]) / lens))
        R = min(refine_radius_beats * beat_ms, refine_max_ms)
        offs = np.arange(-R, R + 0.001, refine_grid_ms)
        m = len(offs)
        L = np.maximum(np.diff(st), 1.0)
        base = T_s[1:] - gap_ms - T_s[:-1]                   # duración de cada compás en la etapa gruesa
        dmat = offs[None, :] - offs[:, None]                 # desplazamiento final - inicial

        def log_ratio(k: int) -> np.ndarray:
            dur = base[k] + dmat
            return np.where(dur > 10, np.log(np.maximum(dur, 1e-3) / L[k]), np.nan)

        big = 1e12
        r_prev = log_ratio(0)
        C = np.where(np.isnan(r_prev), big, -bar_emission(0, T_s, gap_ms, offs))
        if first == 0:
            C = C + start_penalty(0, T_s, gap_ms, offs, beat_ms)[:, None]
        backs = np.zeros((n, m, m), dtype=np.int16)
        lam = refine_weight * w_ref
        for k in range(1, n):
            if k == first:
                C = C + start_penalty(k, T_s, gap_ms, offs, beat_ms)[None, :]
            r = log_ratio(k)
            change = np.nan_to_num((r[None, :, :] - r_prev[:, :, None]) ** 2, nan=1e6)   # (i, j, l)
            tot = C[:, :, None] + lam * change
            bi = np.argmin(tot, axis=0)                                                    # (j, l)
            best = np.take_along_axis(tot, bi[None], axis=0)[0]
            C = np.where(np.isnan(r), big, best - bar_emission(k, T_s, gap_ms, offs))
            backs[k] = bi
            r_prev = r
        j, l = np.unravel_index(int(np.argmin(C)), C.shape)
        idx = np.zeros(n + 1, dtype=int)
        idx[n], idx[n - 1] = l, j
        for k in range(n - 1, 0, -1):
            idx[k - 1] = backs[k, idx[k], idx[k + 1]]
        T = T_s + offs[idx]
        for k in range(1, n + 1):
            T[k] = max(T[k], T[k - 1] + 1)
        return T

    # Dos hipótesis: la tab tiene la misma estructura que la grabación, o le faltan trozos (los huecos
    # que propone la DP de tempo). Decide el paso fino, que además ve los ataques: la croma sola no
    # distingue un acorde que ataca de uno que se apaga, y en tramos repetitivos un hueco puede saltarse
    # ciclos. Los huecos solo se aceptan si mejoran el coste fino en más de gap_select_margin cada uno.
    T_c = chroma_warp(tl, y)
    start_ms = audio_start_ms(wav)
    end_ms = librosa.effects.trim(y, top_db=40)[1][1] / SR * 1000
    tp = dict(start_ms=start_ms, end_ms=end_ms, use_held=use_held, **(tempo_params or {}))
    T_s, gaps = tempo_dp(tl, y, T_c, **tp)
    gap_ms = np.zeros(n)                                   # audio sin tab tras el compás k
    for k, sec in gaps:
        gap_ms[k - 1] = sec * 1000
    T, off, score = fine(T_s, gap_ms)
    score_nogap = None
    if gaps:
        T_s0, _ = tempo_dp(tl, y, T_c, **{**tp, "gap_cost": np.inf})
        T0, off0, score_nogap = fine(T_s0, np.zeros(n))
        if score_nogap <= score + gap_select_margin * len(gaps):
            T, off, gaps, gap_ms, T_s, score = T0, off0, [], np.zeros(n), T_s0, score_nogap
    # Cada hueco se decide por separado: se prueba a quitarlo (prohibiendo ±gap_ban_bars compases, para que
    # la DP no lo ponga al lado) y a moverlo (prohibiendo solo su sitio y los vecinos), y se queda la opción
    # con mejor coste fino. Si no, un hueco real puede arrastrar a uno falso (antes se aceptaban todos o
    # ninguno), y un hueco mal colocado dentro de un pasaje repetido nunca se comparaba con otro sitio.
    base_cost = tp.get("gap_cost", 0.2)
    tried = set()
    for _round in range(4):
        if not (per_gap_select and gaps):
            break
        best = None
        for k_gap, _sec in gaps:
            for ban in (1, gap_ban_bars):
                if (k_gap, ban) in tried:
                    continue
                tried.add((k_gap, ban))
                gc = np.full(n + 1, float(base_cost))
                gc[max(0, k_gap - ban):k_gap + ban + 1] = np.inf
                T_b, gaps_b = tempo_dp(tl, y, T_c, **{**tp, "gap_cost": gc})
                if len(gaps_b) > len(gaps) or [g for g, _ in gaps_b] == [g for g, _ in gaps]:
                    continue
                gm_b = np.zeros(n)
                for k, sec in gaps_b:
                    gm_b[k - 1] = sec * 1000
                T2, off2, score2 = fine(T_b, gm_b)
                if score2 <= score + gap_select_margin * (len(gaps_b) - len(gaps)) and (best is None or score2 < best[2]):
                    best = (T2, off2, score2, T_b, gaps_b, gm_b)
        if best is None:
            break
        T, off, score, T_s, gaps, gap_ms = best

    # Etapa intermedia: curva alternativa con la emisión del paso fino; se queda si mejora el coste fino
    # Criterio: ganancia del paso fino + refine_chroma × cambio de la croma de la etapa gruesa > refine_margin.
    # El paso fino sabe corregir derivas (Yes) pero, con libertad, puede desplazar un beat tramos de ritmo
    # ambiguo (Queen, Joy Division); ahí la croma empeora o la ganancia es pequeña.
    refine_gain = refine_dchroma = None
    if refine_weight > 0:
        T_m = tempo_refine(T_s, gap_ms)
        T2, off2, score2 = fine(T_m, gap_ms)
        refine_gain = round(score - score2, 2)
        refine_dchroma = round(chroma_curve_score(tl, y, T2, use_held) - chroma_curve_score(tl, y, T, use_held), 3)
        if refine_gain + refine_chroma * refine_dchroma > refine_margin:
            T, off, score, T_s = T2, off2, score2, T_m

    ends = np.maximum(T[1:] - gap_ms, T[:-1] + 1)          # fin real de cada compás (antes del hueco)
    ratio = (ends - T[:-1]) / np.diff(st)
    med = float(np.median(ratio))
    result = {
        "bar_times_ms": [round(float(t), 1) for t in T],
        # Solo distinto de bar_times_ms[k + 1] si tras el compás hay un hueco (audio que no está en la tab)
        "bar_ends_ms": [round(float(t), 1) for t in ends],
        # Etapa gruesa (DP de tempo), antes del paso fino: para diagnóstico
        "coarse_bar_times_ms": [round(float(t), 1) for t in T_s],
        "tempo_ratio_median": round(med, 4),
        "suspicious_bars": [int(k + 1) for k, x in enumerate(ratio) if abs(x / med - 1) > 0.1],
        "method": "hybrid",
        # Compases iniciales tratados como cuenta de entrada (sin notas con altura, solo baquetas o charles)
        "count_in_bars": n_count_in,
        # Audio que no está en la tab: (compás, 1-based, que empieza tras el hueco; segundos)
        "gaps": [(int(k + 1), round(float(sec), 2)) for k, sec in gaps],
        "mean_abs_offset_ms": round(float(np.mean(np.abs(off))), 1),
        # Coste del paso fino (en unidades del peso típico de un compás) con y sin los huecos propuestos
        "fine_cost_with_gaps": round(score, 2),
        "fine_cost_without_gaps": None if score_nogap is None else round(score_nogap, 2),
        # Mejora del coste fino con la etapa intermedia (> refine_margin: se usó); None si no se probó
        "refine_gain": refine_gain,
        "refine_chroma_change": refine_dchroma,
    }
    out_json.write_text(json.dumps(result, indent=1), encoding="utf-8")
    return result




