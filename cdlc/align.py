"""Tab ↔ audio alignment: the instant in the audio where each bar of the tab starts.

align_hybrid, in two stages (details and results in docs/ALIGNMENT.md):
  1. tempo_dp: dynamic programming over each bar's start with a plausible tempo model and a harmony
     emission (tab chroma vs. audio chroma). chroma_warp (DTW) only provides the initial global
     tempo.
  2. Fine step: each bar shifts up to ±2.5 beats around that curve, comparing per-band drum hits,
     guitar and bass onsets, and harmony over chunks of the bar.
Discarded: beat_this's beat grid (skips or invents beats), chroma DTW as the alignment (skips
cycles of repeated chords), and onsets without a tempo model (ambiguous on repetitive riffs).
"""

import json
from pathlib import Path

import numpy as np

SR = 22050
HOP = 256
WEIGHT_BASS = 0.6
WEIGHT_GUITAR = 0.4


def audio_start_ms(wav: Path, top_db: float = 40.0) -> float:
    """Instant (ms) where the audio starts sounding, after the leading silence."""
    import librosa

    y, _ = librosa.load(str(wav), sr=SR, mono=True, duration=60)
    _, (start, _end) = librosa.effects.trim(y, top_db=top_db)
    return start / SR * 1000


class _Scorer:
    def __init__(self, env: np.ndarray, step_ms: float):
        self.env = env
        self.step = step_ms

    def __call__(self, times_ms: np.ndarray, w: np.ndarray) -> np.ndarray:
        """times_ms of shape (..., n) → score (...)."""
        idx = np.clip(np.rint(times_ms / self.step).astype(int), 0, len(self.env) - 1)
        return (self.env[idx] * w).sum(axis=-1)


HOP_CHROMA_S = 0.1
KICK = {35, 36}
SNARE = {37, 38, 39, 40, 31, 33, 34}
HIGH = {42, 44, 46, 49, 51, 52, 53, 55, 57, 59, 29, 30}
# Typical tab count-in: sticks, rim or hi-hat, no kick or snare
COUNT_IN = {31, 33, 37, 42, 44, 46}


def count_in_bars(tl: dict) -> int:
    """Leading bars that are just a count-in: before the first pitched note and only with COUNT_IN
    hits. Many recordings lack it (the song opens with the riff) and, if we try to match it, it drags
    the first bars along. If the recording does have it, losing it costs nothing: the next bar already
    has notes."""
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
    """Notes that sound without a new attack, [t_ms, d_ms, midi]: the stretched part of a bend and the
    continuation of a tied or long note. They only count for chroma, not as onsets."""
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
    """Start (ms) of each bar from DTW between the tab chroma and the audio's CENS chroma.

    Only the span with notes (first to last) is aligned: the empty bars at the edges (count-in,
    ending) have nothing to match and would shift everything. Those bars are placed afterwards by
    extending the tempo of the aligned edges.
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
    # Tab (ms) → audio (ms) within the span with notes; outside it, the edge slopes are extended
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
             lead_slack_bars: int | None = 2, use_held: bool = True, tail_cost: float = 0.1,
             tail_margin_ms: float = 3000.0, tail_min_bars: int = 4) -> tuple[np.ndarray, list[tuple[int, float]], int | None]:
    """Start (ms) of each bar via dynamic programming with a plausible tempo model.

    State at each bar boundary: (instant in the audio, in grid_ms steps; tempo ratio of the previous
    bar). Each bar lasts its nominal length × tempo ratio; changing the ratio between bars costs
    tempo_weight × (steps)² up to ±band steps, and a larger change (section change) costs jump_cost.
    The emission is the chroma similarity (cosine) between tab and audio along the bar, minus its
    mean over the whole audio (what a random position would score). This way it cannot skip cycles
    of repeated chords: to gain a cycle it would have to speed up unrealistically.
    T_guess (the DTW) only provides the global tempo ratio.

    The time grid has to be fine: with 100 ms frames, a 1.6 s bar can only change its duration in
    6% steps and the tempo model stops distinguishing anything.
    The first note cannot sound before start_ms (audio start) minus 150 ms, nor the last one after
    end_ms (audio end) plus 150 ms. Audio frames more than silence_db below the maximum have no
    chroma: otherwise normalized background noise resembles anything. Audio chroma arrives
    chroma_lag_ms after the attack (and the tab's is rounded down): this is compensated at the end.

    Gaps: between two bars there may be audio that is not in the tab (a repeat or a longer ending in
    the recording), at a cost of gap_cost. Without this, everything after it is pulled earlier. The
    bar before the gap is stretched up to the next one.

    Tail cut: the recording may end before the tab (a fade-out over a written ending). The path can
    then stop at a bar boundary within tail_margin_ms of the audio end, at tail_cost per dropped bar
    with notes, dropping at least tail_min_bars of them (fewer can be a quiet ending below the
    audio-end threshold that is still in the recording); otherwise the end constraint squeezes the
    whole second half of the song. The dropped bars continue at the last tempo, past the audio end.

    Returns (starts, [(bar after the gap, gap seconds)], first dropped bar or None).

    Chroma only: adding the tab's onsets was tried and it shifted whole songs by several beats
    (repetitive riffs match just as well when shifted); that is left to the fine step, with a short
    radius.
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
        return T_guess, [], None
    # Bars with emission: with some attack or some note still sounding (bend, tie)
    emit = has.copy()
    for t_ms, d_ms, midi in _held(tl, use_held):
        if midi >= 0:
            a_, b_ = np.searchsorted(st, [t_ms, t_ms + max(d_ms, 1)], side="right") - 1
            emit[max(0, a_):min(n, b_ + 1)] = True
    k0, k1 = int(np.argmax(has)), int(n - np.argmax(has[::-1]))      # bars [k0, k1) with notes

    # Tab chroma (hop_ms frames) and audio chroma interpolated to the grid, normalized
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
        # Telescoping durations: at constant tempo, the sum of bars does not accumulate rounding
        return int(round(ms * r / grid_ms))

    t_idx = np.arange(n_g)
    first_note = min(t for t, _d, midi, _tr in tl["notes"] if midi >= 0)
    cost = np.zeros((R, n_g), dtype=np.float32)                      # free start, except for the constraint
    # Empty bars at the start of the tab (intro without the guitar): the first note cannot sound before
    # they pass, except for lead_slack_bars of slack (e.g. a count-in bar added to the tab).
    # It is only a lower bound: if the tab skips the intro, the first note lands later and does no harm.
    lead_ms = 0.0 if lead_slack_bars is None else max(0.0, first_note - st[min(lead_slack_bars, n)])
    for i, r in enumerate(ratios):
        cost[i, t_idx * grid_ms + (first_note - st[k0]) * r < start_ms - 150 + lead_ms * r] = np.inf
    backs_r = np.zeros((k1 - k0, R, n_g), dtype=np.int8)
    # Gaps: g[t] = best cost of having reached the boundary before t (cumulative minimum).
    # Bits per boundary: used = reached through a gap; fresh = the minimum of g was renewed at t-1.
    used_gap = [None] * (k1 - k0)
    fresh_min = [None] * (k1 - k0)
    # Tail cut: best (cost, boundary, r, t) ending at the audio end before bar k1
    n_drop = np.cumsum(has[::-1])[::-1]                              # bars with notes from k on
    t_ms_grid = t_idx * grid_ms - chroma_lag_ms
    near_end = np.abs(t_ms_grid - end_ms) <= tail_margin_ms
    tail = None
    for j, k in enumerate(range(k0, k1)):
        if j > 0:
            prev = np.concatenate([np.full((R, 1), np.inf, dtype=np.float32), cost[:, :-1]], axis=1)
            g = np.minimum.accumulate(prev, axis=1)
            fresh = prev <= g
            via = g + np.float32(gap_cost[k] if np.ndim(gap_cost) else gap_cost)
            use = via < cost
            cost = np.where(use, via, cost)
            used_gap[j], fresh_min[j] = np.packbits(use, axis=1), np.packbits(fresh, axis=1)
        # Transition: in-band tempo ratio change (quadratic cost) or section jump
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
        # Emission: similarity of a few tab instants in bar k with the audio, from t with ratio r'
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
        # Boundary k + 1 at the audio end, dropping bars k + 1 .. k1 - 1 (the second half only)
        if (np.isfinite(end_ms) and k + 1 < k1 and n_drop[k + 1] >= tail_min_bars and k + 1 - k0 >= (k1 - k0) // 2
                and near_end.any()):
            sub = np.where(near_end[None, :], cost, np.inf)
            r_t, t_t = np.unravel_index(int(np.argmin(sub)), sub.shape)
            c = float(sub[r_t, t_t]) + tail_cost * int(n_drop[k + 1])
            if np.isfinite(c) and (tail is None or c < tail[0]):
                tail = (c, k + 1, int(r_t), int(t_t))
    # Backtrack: at each boundary, (r of the previous bar, t)
    # End constraint: the last note (in chroma time, which lags chroma_lag_ms behind) cannot
    # sound after the audio ends
    last_note = max(t for t, _d, midi, _tr in tl["notes"] if midi >= 0)
    for i, r in enumerate(ratios):
        cost[i, t_idx * grid_ms - (st[k1] - last_note) * r - chroma_lag_ms > end_ms + 150] = np.inf
    r_i, t_i = np.unravel_index(int(np.argmin(cost)), cost.shape)
    cut = None
    if tail is not None and tail[0] < float(cost[r_i, t_i]):
        _c, cut, r_i, t_i = tail
        k1 = cut
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
            # Bar k started after a gap: find where bar k-1 actually ended
            t_end = t_i
            while not bit(fresh_min[j], r_i, t_end):
                t_end -= 1
            t_end -= 1
            gaps.append((k, (t_i - t_end) * grid_ms / 1000))
            t_i = t_end
    T[k0:k1 + 1] -= chroma_lag_ms
    # Empty bars at the edges: extend the tempo of the aligned edges
    edge = max(1, min(4, (k1 - k0) // 4))
    ra = (T[k0 + edge] - T[k0]) / max(st[k0 + edge] - st[k0], 1)
    rb = (T[k1] - T[k1 - edge]) / max(st[k1] - st[k1 - edge], 1)
    T[:k0] = T[k0] + (st[:k0] - st[k0]) * ra
    T[k1 + 1:] = T[k1] + (st[k1 + 1:] - st[k1]) * rb
    return T, sorted(gaps), cut


def chroma_curve_score(tl: dict, y: np.ndarray, T: np.ndarray, use_held: bool = True,
                       grid_ms: float = 25.0, chroma_lag_ms: float = 160.0, silence_db: float = -50.0,
                       bars: int | None = None, mean: bool = False) -> float:
    """Coarse-stage chroma emission (tempo_dp's: similarity minus its mean over the whole audio) summed
    over the bars, for given bar starts T. Used to compare two curves by the harmony criterion.
    bars: only the first ones (e.g. up to the tail cut). mean: per bar with notes instead of the sum
    (how well the tab matches the audio; near 0 if they are different songs)."""
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
    total, count = 0.0, 0
    for k in range(min(n, len(T) - 1, bars or n)):
        ts = st[k] + (st[k + 1] - st[k]) * np.arange(16) / 16
        rows = np.clip(np.rint(ts / hop_ms).astype(int), 0, n_tab - 1)
        if not X[:, rows].any():
            continue
        S = X[:, rows].T @ Y
        S -= S.mean(axis=1, keepdims=True)
        at = T[k] + (ts - st[k]) * (T[k + 1] - T[k]) / (st[k + 1] - st[k]) + chroma_lag_ms
        total += float(S[np.arange(16), np.clip(np.rint(at / grid_ms).astype(int), 0, n_g - 1)].mean())
        count += 1
    return total / max(count, 1) if mean else total


def drum_envelopes(y: np.ndarray) -> tuple[dict, float]:
    """Per-band onset envelopes: lows (kick), mids (snare), highs (cymbals) and total."""
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
    """Tab onsets grouped by the band they should sound in: (band, times, weights)."""
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
    """(bars, parts, 12): tab chroma in each chunk of the bar (notes sounding in it).
    An attack counts only in its own bar; a note that keeps sounding (held) counts in every bar it crosses."""
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
                break                                       # as before: an attack only counts in its own bar
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
    """Hybrid alignment (see the module docstring and docs/ALIGNMENT.md)."""
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
    # The envelope peak arrives a bit after the attack: compensate by shifting it back
    lag = int(round(onset_lag_ms / step))
    lagged = {b: np.concatenate([e[lag:], np.zeros(lag)]) if lag > 0 else e for b, e in envs.items()}
    scorers = {b: _Scorer(e, step) for b, e in lagged.items()}
    classes = _class_onsets(tl)

    # Notes of each bar (fraction within the bar) per band
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

    # Fine harmony: audio chroma (cumulative, to average intervals in O(1)) and per-chunk tab chroma
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
        """(m, m): emission of bar k (per-band onsets + per-chunk chroma) if it starts with offset
        offs[i] over T_s and ends (before the gap, if any) with offs[j]."""
        m = len(offs)
        a = T_s[k] + offs[:, None]
        b = T_s[k + 1] - gap_ms[k] + offs[None, :]
        total = np.zeros((m, m))
        for band, frac, w in per_bar[k]:
            times = a[:, :, None] + (b - a)[:, :, None] * frac[None, None, :]
            total += scorers[band](times, w)
        return total + chroma_weight * w_ref * chroma_score(k, np.broadcast_to(a, (m, m)), np.broadcast_to(b, (m, m)))

    def start_penalty(k: int, T_s: np.ndarray, gap_ms: np.ndarray, offs: np.ndarray, beat_ms: float) -> np.ndarray:
        """General constraint: the tab's first note cannot sound before the audio starts.
        Resolves ambiguities of repeating cycles (e.g. two bars of the same chords throughout the
        song) without assuming the tab starts together with the audio (there may be an intro not in the tab).
        Finite, increasing penalty: if no offset satisfies it, the one that violates it least wins."""
        f0 = min(fr.min() for _, fr, _ in per_bar[k])
        first_ms = T_s[k] + offs + (T_s[k + 1] - gap_ms[k] - T_s[k]) * f0
        return 1e3 * w_ref * np.maximum(0, start_ms - 150 - first_ms) / beat_ms

    def fine(T_s: np.ndarray, gap_ms: np.ndarray, cut: int | None) -> tuple[np.ndarray, np.ndarray, float]:
        """Fine step over the coarse curve T_s. Bars from cut on (past the audio end) only follow
        the curve. Returns (starts, offsets, cost in w_ref units)."""
        kept = slice(0, cut)
        beat_ms = float(np.median((T_s[1:] - gap_ms - T_s[:-1])[kept] / lens[kept]))   # typical quarter-note duration
        offs = np.arange(-radius_beats * beat_ms, radius_beats * beat_ms + 0.001, grid_ms)
        m = len(offs)

        def emission(k: int) -> np.ndarray:
            return 0.0 if cut is not None and k >= cut else bar_emission(k, T_s, gap_ms, offs)

        # Cost of changing the offset between consecutive bars (≈ local tempo change)
        d = (offs[None, :] - offs[:, None]) / beat_ms
        trans = smooth_weight * w_ref * d ** 2
        # Soft anchor to the coarse curve: without it, in rhythmically ambiguous spans the fine step drifts to ±1 beat
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

    def tempo_refine(T_s: np.ndarray, gap_ms: np.ndarray, cut: int | None) -> np.ndarray:
        """Alternative to the coarse-stage curve using the fine-step emission and a second-order tempo model.

        The coarse stage decides by chroma, which on repeated patterns may prefer a slow drift of
        several beats (Yes: up to +2.8 beats over 90 bars); the fine step, anchored to it, cannot fix it
        even if its emission prefers the right curve. Here the state is (offset at the bar start, at
        the end), up to ±refine_radius_beats around T_s, and what costs is changing tempo between
        consecutive bars: refine_weight × (Δ log tempo ratio)². Keeping a constant tempo is free, even
        if it moves away from T_s. It is only a candidate: kept if the fine step over it gives a better cost."""
        kept = slice(0, cut)
        beat_ms = float(np.median((T_s[1:] - gap_ms - T_s[:-1])[kept] / lens[kept]))
        R = min(refine_radius_beats * beat_ms, refine_max_ms)
        offs = np.arange(-R, R + 0.001, refine_grid_ms)
        m = len(offs)
        L = np.maximum(np.diff(st), 1.0)
        base = T_s[1:] - gap_ms - T_s[:-1]                   # duration of each bar in the coarse stage
        dmat = offs[None, :] - offs[:, None]                 # end offset - start offset

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
            em = 0.0 if cut is not None and k >= cut else bar_emission(k, T_s, gap_ms, offs)
            C = np.where(np.isnan(r), big, best - em)
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

    # Two hypotheses: the tab has the same structure as the recording, or it is missing pieces (the gaps
    # proposed by the tempo DP). The fine step decides, since it also sees the onsets: chroma alone cannot
    # tell a chord being struck from one fading out, and in repetitive spans a gap can skip cycles.
    # Gaps are only accepted if they improve the fine cost by more than gap_select_margin each.
    T_c = chroma_warp(tl, y)
    start_ms = audio_start_ms(wav)
    end_ms = librosa.effects.trim(y, top_db=40)[1][1] / SR * 1000
    tp = dict(start_ms=start_ms, end_ms=end_ms, use_held=use_held, **(tempo_params or {}))
    T_s, gaps, cut = tempo_dp(tl, y, T_c, **tp)
    gap_ms = np.zeros(n)                                   # audio not in the tab after bar k
    for k, sec in gaps:
        gap_ms[k - 1] = sec * 1000
    T, off, score = fine(T_s, gap_ms, cut)
    score_nogap = None
    if gaps:
        T_s0, _, cut0 = tempo_dp(tl, y, T_c, **{**tp, "gap_cost": np.inf})
        T0, off0, score_nogap = fine(T_s0, np.zeros(n), cut0)
        if score_nogap <= score + gap_select_margin * len(gaps):
            T, off, gaps, gap_ms, T_s, score, cut = T0, off0, [], np.zeros(n), T_s0, score_nogap, cut0
    # Each gap is decided separately: try removing it (banning ±gap_ban_bars bars, so the DP does not
    # put it right next to it) and moving it (banning only its spot and neighbors), and keep the option
    # with the best fine cost. Otherwise a real gap can drag a false one along (previously all or none
    # were accepted), and a misplaced gap inside a repeated passage was never compared with another spot.
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
                T_b, gaps_b, cut_b = tempo_dp(tl, y, T_c, **{**tp, "gap_cost": gc})
                if len(gaps_b) > len(gaps) or [g for g, _ in gaps_b] == [g for g, _ in gaps]:
                    continue
                gm_b = np.zeros(n)
                for k, sec in gaps_b:
                    gm_b[k - 1] = sec * 1000
                T2, off2, score2 = fine(T_b, gm_b, cut_b)
                if score2 <= score + gap_select_margin * (len(gaps_b) - len(gaps)) and (best is None or score2 < best[2]):
                    best = (T2, off2, score2, T_b, gaps_b, gm_b, cut_b)
        if best is None:
            break
        T, off, score, T_s, gaps, gap_ms, cut = best

    # Intermediate stage: alternative curve with the fine-step emission; kept if it improves the fine cost
    # Criterion: fine-step gain + refine_chroma × change in coarse-stage chroma > refine_margin.
    # The fine step can fix drifts (Yes) but, given freedom, it may shift rhythmically ambiguous spans
    # by a beat (Queen, Joy Division); there the chroma gets worse or the gain is small.
    refine_gain = refine_dchroma = None
    if refine_weight > 0:
        T_m = tempo_refine(T_s, gap_ms, cut)
        T2, off2, score2 = fine(T_m, gap_ms, cut)
        refine_gain = round(score - score2, 2)
        refine_dchroma = round(chroma_curve_score(tl, y, T2, use_held) - chroma_curve_score(tl, y, T, use_held), 3)
        if refine_gain + refine_chroma * refine_dchroma > refine_margin:
            T, off, score, T_s = T2, off2, score2, T_m

    ends = np.maximum(T[1:] - gap_ms, T[:-1] + 1)          # actual end of each bar (before the gap)
    ratio = ((ends - T[:-1]) / np.diff(st))[:cut]            # dropped bars do not count
    med = float(np.median(ratio))
    result = {
        "bar_times_ms": [round(float(t), 1) for t in T],
        # Differs from bar_times_ms[k + 1] only if a gap follows the bar (audio not in the tab)
        "bar_ends_ms": [round(float(t), 1) for t in ends],
        # Coarse stage (tempo DP), before the fine step: for diagnostics
        "coarse_bar_times_ms": [round(float(t), 1) for t in T_s],
        "tempo_ratio_median": round(med, 4),
        "suspicious_bars": [int(k + 1) for k, x in enumerate(ratio) if abs(x / med - 1) > 0.1],
        "method": "hybrid",
        # Leading bars treated as a count-in (no pitched notes, only sticks or hi-hat)
        "count_in_bars": n_count_in,
        # Audio not in the tab: (bar, 1-based, that starts after the gap; seconds)
        "gaps": [(int(k + 1), round(float(sec), 2)) for k, sec in gaps],
        # First bar (1-based) past the audio end, when the recording ends before the tab (fade-out);
        # it and the following bars get no notes. None: the whole tab is in the recording
        "tail_cut_bar": None if cut is None else int(cut + 1),
        "mean_abs_offset_ms": round(float(np.mean(np.abs(off))), 1),
        # Fine-step cost (in units of the typical bar weight) with and without the proposed gaps
        "fine_cost_with_gaps": round(score, 2),
        "fine_cost_without_gaps": None if score_nogap is None else round(score_nogap, 2),
        # Fine-cost improvement from the intermediate stage (> refine_margin: it was used); None if not tried
        "refine_gain": refine_gain,
        "refine_chroma_change": refine_dchroma,
        # Mean chroma match per bar (≈ 0: the tab and the audio may be different songs)
        "chroma_match": round(chroma_curve_score(tl, y, T, use_held, bars=cut, mean=True), 3),
    }
    out_json.write_text(json.dumps(result, indent=1), encoding="utf-8")
    return result


# ---------------------------------------------------------------- Guitar Pro 8 sync points

def bar_times_from_sync_points(timeline_json: Path, wav: Path, out_json: Path, offset_ms: float) -> dict:
    """Bar times from the tab's GP8 sync points instead of aligning: each point fixes the instant of a
    position in a bar (in the embedded audio; offset_ms moves it to wav's time axis). Between two points
    the time is interpolated in proportion to the written duration (quarters), as GP plays them: one
    tempo per stretch. Before the first and after the last point, the nearest stretch's ratio to the
    written tempo carries on (the written tempo if there is a single point).
    Writes the same bar_times.json as align_hybrid (method "sync_points")."""
    import librosa

    tl = json.loads(timeline_json.read_text(encoding="utf-8"))
    st = np.array(tl["bar_starts_ms"], dtype=float)
    q = np.concatenate([[0.0], np.cumsum(tl["bar_quarters"])])          # written position of each bar start
    pts = sorted(tl.get("sync_points") or [], key=lambda p: p[0] + p[1])
    if not pts:
        raise ValueError("the timeline has no sync points")
    n = len(st) - 1
    pq = np.array([q[int(k)] + pos * (q[int(k) + 1] - q[int(k)]) for k, pos, _ in pts])
    pms = np.array([ms for _, _, ms in pts], dtype=float) + offset_ms
    pnom = np.interp(pq, q, st)                                         # written time (ms) of each point
    T = np.interp(q, pq, pms)
    if len(pts) >= 2:
        first = (pms[1] - pms[0]) / max(pnom[1] - pnom[0], 1e-9)
        last = (pms[-1] - pms[-2]) / max(pnom[-1] - pnom[-2], 1e-9)
    else:
        first = last = 1.0
    T = np.where(q < pq[0], pms[0] - (pnom[0] - st) * first, T)
    T = np.where(q > pq[-1], pms[-1] + (st - pnom[-1]) * last, T)

    y, _ = librosa.load(str(wav), sr=SR, mono=True)
    audio_ms = len(y) / SR * 1000
    past_end = [k for k in range(n) if T[k] >= audio_ms]
    cut = past_end[0] if past_end else None
    ratio = np.diff(T)[:cut] / np.diff(st)[:cut]
    result = {
        "bar_times_ms": [round(float(t), 1) for t in T],
        "bar_ends_ms": [round(float(t), 1) for t in T[1:]],
        "coarse_bar_times_ms": [round(float(t), 1) for t in T],
        "tempo_ratio_median": round(float(np.median(ratio)), 4),
        # The times come from the tab's author: tempo changes are intended, not alignment errors
        "suspicious_bars": [],
        "method": "sync_points",
        "sync_point_count": len(pts),
        "sync_offset_ms": round(float(offset_ms), 1),
        "count_in_bars": 0,
        "gaps": [],
        "tail_cut_bar": None if cut is None else int(cut + 1),
        "mean_abs_offset_ms": 0.0,
        "chroma_match": round(chroma_curve_score(tl, y, T, bars=cut, mean=True), 3),
    }
    out_json.write_text(json.dumps(result, indent=1), encoding="utf-8")
    return result


def _onset_env(path: Path, hop_s: float = 0.01) -> np.ndarray:
    """Onset strength envelope (one value per hop_s), zero mean and unit variance."""
    import librosa

    y, _ = librosa.load(str(path), sr=SR, mono=True)
    hop = int(round(hop_s * SR))
    e = librosa.onset.onset_strength(y=y, sr=SR, hop_length=hop)
    return (e - e.mean()) / (e.std() + 1e-9)


def _best_lag(a: np.ndarray, b: np.ndarray, lags: range) -> tuple[int, float]:
    """Lag (b shifted by lag frames matches a) with the highest Pearson correlation over the overlap."""
    best = (0, -1.0)
    for lag in lags:
        a0, b0 = max(0, lag), max(0, -lag)
        m = min(len(a) - a0, len(b) - b0)
        if m < 200:
            continue
        x, z = a[a0:a0 + m], b[b0:b0 + m]
        r = float(np.dot(x - x.mean(), z - z.mean()) / (m * x.std() * z.std() + 1e-9))
        if r > best[1]:
            best = (lag, r)
    return best


def audio_offset(ref: Path, wav: Path, window_s: float = 30.0, hop_s: float = 0.01) -> dict:
    """Whether wav holds the same recording as ref (the tab's embedded audio) and where: offset_ms is the
    instant in wav of ref's start. Found by cross-correlating onset envelopes, first over the whole
    files, then separately on a window at ref's start and one at its end. Same recording: both windows
    correlate well and agree on the offset (drift_ms ≈ 0; a different tempo or edit makes them drift)."""
    from scipy.signal import correlate

    a, b = _onset_env(wav, hop_s), _onset_env(ref, hop_s)
    c = correlate(a, b, mode="full", method="fft")
    lag0 = int(np.argmax(c)) - (len(b) - 1)
    w = int(window_s / hop_s)
    span = int(2 / hop_s)
    found = []
    for start in (0, max(0, len(b) - w)):
        lag, r = _best_lag(a, b[start:start + w], range(lag0 + start - span, lag0 + start + span + 1))
        found.append((lag - start, r))           # lag of ref's start, not of the window's
    (l1, r1), (l2, r2) = found
    drift_ms = (l2 - l1) * hop_s * 1000
    corr = min(r1, r2)
    same = corr >= 0.3 and abs(drift_ms) <= 60
    offset_ms = l1 * hop_s * 1000
    if same:
        offset_ms = _refine_offset(ref, wav, offset_ms, search_ms=2 * hop_s * 1000)
    return {"offset_ms": round(offset_ms, 1), "drift_ms": round(drift_ms, 1), "correlation": round(corr, 3),
            "same_recording": same}


def _refine_offset(ref: Path, wav: Path, offset_ms: float, search_ms: float, seg_s: float = 10.0) -> float:
    """Sample-accurate offset: cross-correlates the waveforms (the loudest seg_s of ref against wav) within
    ±search_ms of offset_ms. The onset envelopes only give it to one hop."""
    import librosa
    from scipy.signal import correlate

    yr, _ = librosa.load(str(ref), sr=SR, mono=True)
    yw, _ = librosa.load(str(wav), sr=SR, mono=True)
    n = int(seg_s * SR)
    if len(yr) <= n:
        return offset_ms
    energy = np.convolve(yr ** 2, np.ones(SR), mode="valid")[: len(yr) - n]
    s0 = int(np.argmax(energy[::SR // 10]) * (SR // 10))       # start of the loudest segment (100 ms steps)
    seg = yr[s0:s0 + n]
    pad = int(search_ms / 1000 * SR)
    w0 = s0 + int(round(offset_ms / 1000 * SR)) - pad
    if w0 < 0 or w0 + n + 2 * pad > len(yw):
        return offset_ms
    c = correlate(yw[w0:w0 + n + 2 * pad], seg, mode="valid", method="fft")
    return (w0 + int(np.argmax(c)) - s0) / SR * 1000
