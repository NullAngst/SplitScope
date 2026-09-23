"""Model-free separation tools (always available, no downloads).

These are signal-processing estimates, not learned separation:
  * HPSS splits sustained (harmonic) from transient (percussive) energy.
    Drums dominate the percussive part, but picked/strummed attacks and
    consonants land there too.
  * Center/sides uses the same inter-channel masks as the live chain.
"""
from __future__ import annotations

import numpy as np
from scipy.ndimage import median_filter
from scipy.signal import butter, sosfiltfilt

from .chain import ChainSettings, compile_params
from .processor import ArraySource, render_offline
from .mdx import Cancelled

HN = 2048
HH = 512


def _win(n):
    return np.sqrt(0.5 - 0.5 * np.cos(2 * np.pi * np.arange(n) / n)).astype(np.float32)


def hpss(audio: np.ndarray, sr: int, kernel_t: int = 31, kernel_f: int = 31, margin: float = 1.0,
         progress=None, cancelled=None) -> tuple[np.ndarray, np.ndarray]:
    """Median-filter harmonic/percussive separation (Fitzgerald 2010) with
    soft Wiener-style masks. Processed in blocks of frames to bound memory.
    Returns (harmonic, percussive), each (n, 2); they sum to the input."""
    n = audio.shape[0]
    w = _win(HN)
    pad = HN
    x = np.pad(audio.T.astype(np.float32), ((0, 0), (pad, pad + HN)))
    n_frames = (x.shape[1] - HN) // HH + 1
    # window-squared overlap normalisation
    acc = np.zeros(HN + 3 * HH)
    for k in range(4):
        acc[k * HH : k * HH + HN] += w.astype(np.float64) ** 2
    norm = np.float32(1.0 / acc[3 * HH : 4 * HH].mean())

    harm = np.zeros_like(x)
    block = 1500
    margin_frames = kernel_t // 2 + 1
    for b0 in range(0, n_frames, block):
        if cancelled is not None and cancelled():
            raise Cancelled()
        b1 = min(n_frames, b0 + block)
        a0 = max(0, b0 - margin_frames)
        a1 = min(n_frames, b1 + margin_frames)
        idx = np.arange(a0, a1) * HH
        frames = np.stack([x[:, i : i + HN] for i in idx], axis=1) * w  # (2, F, HN)
        S = np.fft.rfft(frames, axis=-1)  # (2, F, bins)
        mag = np.abs(S).mean(axis=0).T.astype(np.float32)  # (bins, F)
        H = median_filter(mag, size=(1, kernel_t), mode="nearest")
        P = median_filter(mag, size=(kernel_f, 1), mode="nearest")
        H2 = H * H
        P2 = (P * margin) ** 2
        mask_h = (H2 / (H2 + P2 + 1e-12)).T  # (F, bins)
        core = slice(b0 - a0, b1 - a0)
        Sh = S[:, core] * mask_h[core][None]
        yh = np.fft.irfft(Sh, n=HN, axis=-1).astype(np.float32) * (w * norm)
        for j, i in enumerate(idx[core]):
            harm[:, i : i + HN] += yh[:, j]
        if progress is not None:
            progress(b1 / n_frames)
    harm = harm[:, pad : pad + n].T
    perc = audio - harm
    return np.ascontiguousarray(harm, dtype=np.float32), np.ascontiguousarray(perc, dtype=np.float32)


def mid_side(audio: np.ndarray):
    m = 0.5 * (audio[:, 0] + audio[:, 1])
    s = 0.5 * (audio[:, 0] - audio[:, 1])
    return np.stack([m, m], 1).astype(np.float32), np.stack([s, -s], 1).astype(np.float32)


def center_sides(audio: np.ndarray, sr: int, strict: float = 0.6, progress=None, cancelled=None):
    """Spectral center extraction. Returns (center, sides); they sum to the input."""
    s = ChainSettings()
    s.eq_enabled = False
    s.output.limiter = False
    s.spatial.mode = "center"
    s.spatial.phase_strict = strict
    s.spatial.smoothing = 0.4
    center = render_offline(ArraySource(audio), compile_params(s, sr), sr, progress=progress, cancelled=cancelled)
    sides = (audio - center).astype(np.float32)
    return center, sides


def split_low(audio: np.ndarray, sr: int, cutoff: float = 200.0):
    """Zero-phase 4th-order low/high split. Returns (low, rest)."""
    sos = butter(4, cutoff, "low", fs=sr, output="sos")
    low = sosfiltfilt(sos, audio, axis=0).astype(np.float32)
    return low, (audio - low).astype(np.float32)
