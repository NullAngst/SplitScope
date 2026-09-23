"""Frequency-response math for the spectral chain.

Every tone-shaping stage in SplitScope is applied as a per-bin gain in the
STFT domain (zero phase). That means the curves here are magnitude
responses only, evaluated from analog prototypes so shelves and bells keep
their shape all the way to Nyquist.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Iterable

import numpy as np

EPS = 1e-12

BAND_TYPES = [
    ("bell", "Bell"),
    ("low_shelf", "Low shelf"),
    ("high_shelf", "High shelf"),
    ("low_cut", "Low cut (HPF)"),
    ("high_cut", "High cut (LPF)"),
    ("notch", "Notch"),
    ("band_pass", "Band pass"),
]
CHANNELS = [
    ("stereo", "Stereo"),
    ("mid", "Mid"),
    ("side", "Side"),
    ("left", "Left"),
    ("right", "Right"),
]
GRAPHIC_FREQS = [25, 40, 63, 100, 160, 250, 400, 630, 1000, 1600, 2500, 4000, 6300, 10000, 16000]
GRAPHIC_Q = 2.15  # roughly 2/3 octave


def db_to_lin(db):
    return np.power(10.0, np.asarray(db, dtype=np.float64) / 20.0)


def lin_to_db(x):
    return 20.0 * np.log10(np.maximum(np.asarray(x, dtype=np.float64), EPS))


# ---------------------------------------------------------------- prototypes
def _mag(num, den):
    return np.abs(num) / np.maximum(np.abs(den), EPS)


def bell(f, f0, gain_db, q):
    s = 1j * (f / f0)
    A = 10.0 ** (gain_db / 40.0)
    q = max(q, 0.05)
    return _mag(s * s + s * (A / q) + 1.0, s * s + s / (A * q) + 1.0)


def low_shelf(f, f0, gain_db, q):
    s = 1j * (f / f0)
    A = 10.0 ** (gain_db / 40.0)
    k = np.sqrt(A) / max(q, 0.05)
    return A * _mag(s * s + k * s + A, A * s * s + k * s + 1.0)


def high_shelf(f, f0, gain_db, q):
    s = 1j * (f / f0)
    A = 10.0 ** (gain_db / 40.0)
    k = np.sqrt(A) / max(q, 0.05)
    return A * _mag(A * s * s + k * s + 1.0, s * s + k * s + A)


def butter_hp(f, fc, slope_db):
    order = max(1, int(round(slope_db / 6.0)))
    r = np.maximum(f, 1e-3) / fc
    return 1.0 / np.sqrt(1.0 + r ** (-2 * order))


def butter_lp(f, fc, slope_db):
    order = max(1, int(round(slope_db / 6.0)))
    r = f / fc
    return 1.0 / np.sqrt(1.0 + r ** (2 * order))


def notch(f, f0, q):
    s = 1j * (f / f0)
    return _mag(s * s + 1.0, s * s + s / max(q, 0.05) + 1.0)


def band_pass(f, f0, q):
    s = 1j * (f / f0)
    q = max(q, 0.05)
    return _mag(s / q, s * s + s / q + 1.0)


# ---------------------------------------------------------------- EQ bands
@dataclass
class EQBand:
    type: str = "bell"
    freq: float = 1000.0
    gain: float = 0.0
    q: float = 1.0
    slope: int = 24          # dB/oct for cut filters
    channel: str = "stereo"
    enabled: bool = True

    def response(self, f: np.ndarray) -> np.ndarray:
        t = self.type
        if t == "bell":
            return bell(f, self.freq, self.gain, self.q)
        if t == "low_shelf":
            return low_shelf(f, self.freq, self.gain, self.q)
        if t == "high_shelf":
            return high_shelf(f, self.freq, self.gain, self.q)
        if t == "low_cut":
            return butter_hp(f, self.freq, self.slope)
        if t == "high_cut":
            return butter_lp(f, self.freq, self.slope)
        if t == "notch":
            return notch(f, self.freq, self.q)
        if t == "band_pass":
            return band_pass(f, self.freq, self.q)
        return np.ones_like(f)

    @property
    def uses_gain(self) -> bool:
        return self.type in ("bell", "low_shelf", "high_shelf")

    @property
    def uses_q(self) -> bool:
        return self.type in ("bell", "low_shelf", "high_shelf", "notch", "band_pass")


# ---------------------------------------------------------------- filter / focus
@dataclass
class FocusRange:
    lo: float
    hi: float


@dataclass
class FocusFilter:
    """Band-limiting 'frequency focus' used for quick instrument isolation."""

    enabled: bool = False
    ranges: list = field(default_factory=lambda: [FocusRange(150.0, 7000.0)])
    slope: int = 48
    invert: bool = False
    preset: str = "custom"

    def response(self, f: np.ndarray) -> np.ndarray:
        if not self.enabled or not self.ranges:
            return np.ones_like(f)
        m = np.zeros_like(f)
        for r in self.ranges:
            lo, hi = float(r.lo), float(r.hi)
            g = np.ones_like(f)
            if lo > 1.0:
                g *= butter_hp(f, lo, self.slope)
            if hi < 22000.0:
                g *= butter_lp(f, hi, self.slope)
            m = np.maximum(m, g)
        if self.invert:
            m = np.sqrt(np.clip(1.0 - m * m, 0.0, 1.0))
        return m


# Frequency-focus presets. These are band-limits, not true separation: they
# keep the range an instrument mostly lives in, including anything else there.
FOCUS_PRESETS: dict[str, tuple[str, list[tuple[float, float]], str]] = {
    "vocals": ("Vocal range", [(150, 7000)], "Fundamentals through sibilance; strongest when combined with Center extract."),
    "vocal_presence": ("Vocal presence", [(1800, 5000)], "Consonants and bite; good for finding a buried vocal."),
    "guitar": ("Guitar (full)", [(80, 6000)], "Amped guitar speakers roll off around 5-6 kHz."),
    "guitar_solo": ("Guitar solo focus", [(500, 4500)], "Where lead guitar lines and solos usually sit."),
    "bass": ("Bass", [(35, 450)], "Bass guitar fundamentals and low harmonics."),
    "kick": ("Kick", [(35, 140)], "Kick drum body."),
    "snare": ("Snare", [(140, 320), (2000, 6000)], "Snare body plus crack."),
    "cymbals": ("Cymbals / hats", [(6000, 20000)], "Hi-hats, rides, crashes (and vocal air)."),
    "drums": ("Drums (combined)", [(35, 320), (2000, 20000)], "Kick + snare body + cymbal range."),
    "sub": ("Sub", [(20, 60)], "Sub-bass rumble."),
    "piano_keys": ("Piano / keys", [(60, 5000)], "Most keyboard energy."),
}


# ---------------------------------------------------------------- multiband
def crossover_weights(f: np.ndarray, crossovers: Iterable[float], steepness: float = 6.0) -> np.ndarray:
    """Amplitude-complementary band weights (sum to exactly 1 at every bin).

    Built from log-frequency sigmoids; steepness is roughly the transition
    sharpness per octave. Returns shape (n_bands, n_bins).
    """
    xs = sorted(float(c) for c in crossovers)
    lf = np.log2(np.maximum(f, 1.0))
    s = [1.0 / (1.0 + np.exp(-steepness * (lf - np.log2(c)))) for c in xs]
    bands = []
    prev = np.ones_like(f)
    for si in s:
        bands.append(prev - si)
        prev = si
    bands.append(prev)
    return np.clip(np.stack(bands), 0.0, 1.0)


@dataclass
class MBBand:
    gain: float = 0.0        # dB static gain
    mute: bool = False
    solo: bool = False
    threshold: float = -20.0  # dBFS
    ratio: float = 1.0        # 1 = no compression


@dataclass
class Multiband:
    enabled: bool = False
    crossovers: list = field(default_factory=lambda: [120.0, 1000.0, 5000.0])
    bands: list = field(default_factory=lambda: [MBBand(), MBBand(), MBBand(), MBBand()])
    dynamics: bool = False
    attack_ms: float = 15.0
    release_ms: float = 150.0

    def weights(self, f):
        return crossover_weights(f, self.crossovers)

    def static_curve(self, f) -> np.ndarray:
        if not self.enabled:
            return np.ones_like(f)
        w = self.weights(f)
        any_solo = any(b.solo for b in self.bands)
        g = np.zeros_like(f)
        for wi, b in zip(w, self.bands):
            if b.mute or (any_solo and not b.solo):
                continue
            g += wi * (10.0 ** (b.gain / 20.0))
        return g


# ---------------------------------------------------------------- audition
def audition_curve(f: np.ndarray, center: float, octaves: float) -> np.ndarray:
    """Narrow band-pass used for 'hold Shift to solo a frequency band'."""
    half = 2.0 ** (octaves / 2.0)
    return butter_hp(f, center / half, 36) * butter_lp(f, center * half, 36)


def serialize(obj):
    return asdict(obj)
