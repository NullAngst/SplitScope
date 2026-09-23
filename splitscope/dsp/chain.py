"""Editable chain settings and their compiled, real-time-safe form.

The GUI edits a ChainSettings object; compile_params() turns it into an
immutable ChainParams full of precomputed per-bin arrays. The audio thread
only ever reads ChainParams, and a new one is swapped in by reference,
so there is no locking in the audio callback.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field, asdict, fields, is_dataclass
from typing import Optional

import numpy as np

from . import curves as C

N_FFT = 4096
HOP = 1024

SPATIAL_MODES = [
    ("stereo", "Stereo (unchanged)"),
    ("center", "Center extract"),
    ("remove_center", "Remove center (keep sides)"),
    ("pan_focus", "Pan focus (extract a pan position)"),
    ("pan_remove", "Pan remove (cut a pan position)"),
    ("mid", "Mid only (L+R)"),
    ("side", "Side only (L-R)"),
    ("left", "Left only"),
    ("right", "Right only"),
    ("swap", "Swap L/R"),
]
MASK_MODES = ("center", "remove_center", "pan_focus", "pan_remove")


@dataclass
class SpatialSettings:
    mode: str = "stereo"
    target: float = 0.0          # pan position -1 (L) .. +1 (R)
    width: float = 0.35          # half-width of the pan window
    phase_strict: float = 0.5    # 0 ignores phase, 1 demands in-phase content
    smoothing: float = 0.5       # temporal mask smoothing 0..0.95
    depth: float = 1.0           # 0 = no effect, 1 = full extraction/removal
    f_lo: float = 20.0           # spatial processing only inside this range
    f_hi: float = 20000.0
    mid_db: float = 0.0
    side_db: float = 0.0
    mid_mute: bool = False
    side_mute: bool = False


@dataclass
class OutputSettings:
    gain_db: float = 0.0
    balance: float = 0.0         # -1 .. +1
    mono: bool = False
    drive: float = 0.0           # tape-style saturation 0..1
    comp_enabled: bool = False
    comp_threshold: float = -18.0
    comp_ratio: float = 3.0
    comp_attack_ms: float = 10.0
    comp_release_ms: float = 120.0
    comp_makeup_db: float = 0.0
    limiter: bool = True
    ceiling_db: float = -0.3


def default_eq():
    return [
        C.EQBand("low_shelf", 80.0, 0.0, 0.707),
        C.EQBand("bell", 250.0, 0.0, 1.0),
        C.EQBand("bell", 1000.0, 0.0, 1.0),
        C.EQBand("bell", 4000.0, 0.0, 1.0),
        C.EQBand("high_shelf", 10000.0, 0.0, 0.707),
    ]


@dataclass
class ChainSettings:
    bypass: bool = False
    spatial: SpatialSettings = field(default_factory=SpatialSettings)
    focus: C.FocusFilter = field(default_factory=C.FocusFilter)
    eq_enabled: bool = True
    eq: list = field(default_factory=default_eq)
    graphic_enabled: bool = False
    graphic: list = field(default_factory=lambda: [0.0] * len(C.GRAPHIC_FREQS))
    multiband: C.Multiband = field(default_factory=C.Multiband)
    output: OutputSettings = field(default_factory=OutputSettings)
    speed: float = 1.0
    preserve_pitch: bool = True

    def copy(self) -> "ChainSettings":
        return copy.deepcopy(self)

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "ChainSettings":
        s = ChainSettings()
        _fill(s, d)
        return s


def _fill(obj, d):
    """Tolerant recursive loader (ignores unknown keys, keeps defaults)."""
    if not isinstance(d, dict):
        return
    for f in fields(obj):
        if f.name not in d:
            continue
        cur = getattr(obj, f.name)
        val = d[f.name]
        if is_dataclass(cur):
            _fill(cur, val)
        elif f.name == "eq" and isinstance(val, list):
            bands = []
            for b in val:
                nb = C.EQBand()
                _fill(nb, b)
                bands.append(nb)
            setattr(obj, f.name, bands)
        elif f.name == "ranges" and isinstance(val, list):
            setattr(obj, f.name, [C.FocusRange(float(r.get("lo", 20)), float(r.get("hi", 20000))) for r in val])
        elif f.name == "bands" and isinstance(val, list):
            bands = []
            for b in val:
                nb = C.MBBand()
                _fill(nb, b)
                bands.append(nb)
            while len(bands) < 4:
                bands.append(C.MBBand())
            setattr(obj, f.name, bands[:4])
        else:
            try:
                setattr(obj, f.name, type(cur)(val) if cur is not None and not isinstance(cur, list) else val)
            except (TypeError, ValueError):
                pass


@dataclass(frozen=True)
class ChainParams:
    bypass: bool
    mode: str
    target: float
    width: float
    phase_exp: float
    smooth: float
    depth: float
    range_w: Optional[np.ndarray]
    ms_active: bool
    g_mid: np.ndarray
    g_side: np.ndarray
    g_all: Optional[np.ndarray]
    g_left: Optional[np.ndarray]
    g_right: Optional[np.ndarray]
    mb_dyn: bool
    mb_w: Optional[np.ndarray]
    mb_thr: Optional[np.ndarray]
    mb_ratio: Optional[np.ndarray]
    mb_att: float
    mb_rel: float
    out_gain: float
    bal_l: float
    bal_r: float
    mono: bool
    drive: float
    comp: bool
    comp_thr: float
    comp_ratio: float
    comp_att: float
    comp_rel: float
    comp_makeup: float
    limiter: bool
    ceiling: float
    speed: float
    preserve_pitch: bool


def bin_freqs(sr: int) -> np.ndarray:
    f = np.fft.rfftfreq(N_FFT, 1.0 / sr)
    f[0] = f[1] * 0.5
    return f


def _coef(ms: float, sr: int) -> float:
    t = max(ms, 0.1) / 1000.0
    return float(np.exp(-HOP / (sr * t)))


def eq_curves(s: ChainSettings, f: np.ndarray) -> dict[str, np.ndarray]:
    out = {k: np.ones_like(f) for k, _ in C.CHANNELS}
    if not s.eq_enabled:
        return out
    for b in s.eq:
        if b.enabled:
            out[b.channel if b.channel in out else "stereo"] *= b.response(f)
    return out


def graphic_curve(s: ChainSettings, f: np.ndarray) -> np.ndarray:
    g = np.ones_like(f)
    if not s.graphic_enabled:
        return g
    for fc, gdb in zip(C.GRAPHIC_FREQS, s.graphic):
        if abs(gdb) > 1e-3:
            g *= C.bell(f, fc, gdb, C.GRAPHIC_Q)
    return g


def total_display_curve(s: ChainSettings, f: np.ndarray, audition=None) -> dict[str, np.ndarray]:
    """Curves for drawing: 'stereo' includes focus/graphic/multiband/output."""
    eqc = eq_curves(s, f)
    all_c = eqc["stereo"] * graphic_curve(s, f) * s.focus.response(f) * s.multiband.static_curve(f)
    all_c = all_c * (10.0 ** (s.output.gain_db / 20.0))
    eqc["stereo"] = all_c
    return eqc


def compile_params(s: ChainSettings, sr: int, audition=None) -> ChainParams:
    f = bin_freqs(sr)
    sp = s.spatial
    eqc = eq_curves(s, f)
    g_all = eqc["stereo"] * graphic_curve(s, f) * s.focus.response(f) * s.multiband.static_curve(f)
    if audition is not None:
        g_all = g_all * C.audition_curve(f, audition[0], audition[1])
    mid_lin = 0.0 if sp.mid_mute else 10.0 ** (sp.mid_db / 20.0)
    side_lin = 0.0 if sp.side_mute else 10.0 ** (sp.side_db / 20.0)
    g_mid = eqc["mid"] * mid_lin
    g_side = eqc["side"] * side_lin
    ms_active = not (np.allclose(g_mid, 1.0) and np.allclose(g_side, 1.0))

    def opt(a):
        return None if np.allclose(a, 1.0) else a.astype(np.float32)

    range_w = None
    if sp.mode != "stereo" and (sp.f_lo > 21.0 or sp.f_hi < 19999.0):
        r = np.ones_like(f)
        if sp.f_lo > 21.0:
            r *= C.butter_hp(f, sp.f_lo, 24)
        if sp.f_hi < 19999.0:
            r *= C.butter_lp(f, sp.f_hi, 24)
        range_w = r.astype(np.float32)

    mb = s.multiband
    mb_dyn = bool(mb.enabled and mb.dynamics and any(b.ratio > 1.001 for b in mb.bands))
    o = s.output
    bal = float(np.clip(o.balance, -1.0, 1.0))
    return ChainParams(
        bypass=s.bypass,
        mode=sp.mode,
        target=float(np.clip(sp.target, -1, 1)),
        width=float(np.clip(sp.width, 0.02, 2.0)),
        phase_exp=float(np.clip(sp.phase_strict, 0, 1) * 12.0),
        smooth=float(np.clip(sp.smoothing, 0.0, 0.95)),
        depth=float(np.clip(sp.depth, 0.0, 1.0)),
        range_w=range_w,
        ms_active=ms_active,
        g_mid=g_mid.astype(np.float32),
        g_side=g_side.astype(np.float32),
        g_all=opt(g_all),
        g_left=opt(eqc["left"]),
        g_right=opt(eqc["right"]),
        mb_dyn=mb_dyn,
        mb_w=mb.weights(f).astype(np.float32) if mb_dyn else None,
        mb_thr=np.array([b.threshold for b in mb.bands], np.float64) if mb_dyn else None,
        mb_ratio=np.array([max(1.0, b.ratio) for b in mb.bands], np.float64) if mb_dyn else None,
        mb_att=_coef(mb.attack_ms, sr),
        mb_rel=_coef(mb.release_ms, sr),
        out_gain=float(10.0 ** (o.gain_db / 20.0)),
        bal_l=float(min(1.0, 1.0 - bal)),
        bal_r=float(min(1.0, 1.0 + bal)),
        mono=bool(o.mono),
        drive=float(np.clip(o.drive, 0.0, 1.0)),
        comp=bool(o.comp_enabled and o.comp_ratio > 1.001),
        comp_thr=float(o.comp_threshold),
        comp_ratio=float(max(1.0, o.comp_ratio)),
        comp_att=_coef(o.comp_attack_ms, sr),
        comp_rel=_coef(o.comp_release_ms, sr),
        comp_makeup=float(10.0 ** (o.comp_makeup_db / 20.0)),
        limiter=bool(o.limiter),
        ceiling=float(10.0 ** (o.ceiling_db / 20.0)),
        speed=float(np.clip(s.speed, 0.25, 2.0)),
        preserve_pitch=bool(s.preserve_pitch),
    )
