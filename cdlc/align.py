"""Alineación tab ↔ audio por rejilla de beats.

Cada negra de la tab se asigna a un beat detectado por beat_this; el beat que corresponde al
compás 1 se elige comparando los ataques de la tab (batería, bajo y guitarras) con la envolvente
de onsets del audio. Se descartaron por imprecisos: downbeats de beat_this (fallan con compases
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
