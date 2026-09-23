"""Real-time spectral processor (STFT, 4096-point frames, 75% overlap).

One SpectralChain instance renders audio one hop (1024 samples) at a time
from a Source. The exact same code renders exports offline, so what you
hear is what gets written.

Signal flow per frame:
    source -> [speed: phase vocoder or varispeed] -> FFT
      -> spatial stage (center/pan masks or matrix modes)
      -> mid/side gains + M/S EQ
      -> stereo EQ, graphic EQ, focus filter, multiband gains, L/R EQ
      -> multiband dynamics -> master compressor -> balance/mono
      -> iFFT/overlap-add -> saturation -> output gain -> limiter
"""
from __future__ import annotations

from typing import Protocol

import numpy as np

from .chain import N_FFT, HOP, ChainParams, MASK_MODES

BINS = N_FFT // 2 + 1
EPS = 1e-12


class Source(Protocol):
    length: int

    def read(self, start: int, n: int) -> np.ndarray:  # (n, 2) float32, zeros outside
        ...


class ArraySource:
    """Source over a (n, 2) array."""

    def __init__(self, audio: np.ndarray):
        self.audio = audio
        self.length = int(audio.shape[0])

    def read(self, start: int, n: int) -> np.ndarray:
        return read_padded(self.audio, start, n)


def read_padded(a: np.ndarray, start: int, n: int) -> np.ndarray:
    out = np.zeros((n, 2), dtype=np.float32)
    s0 = max(start, 0)
    s1 = min(start + n, a.shape[0])
    if s1 > s0:
        out[s0 - start : s1 - start] = a[s0:s1]
    return out


def _sqrt_hann(n: int) -> np.ndarray:
    return np.sqrt(0.5 - 0.5 * np.cos(2 * np.pi * np.arange(n) / n)).astype(np.float32)


class SpectralChain:
    def __init__(self, sr: int):
        self.sr = int(sr)
        self.win = _sqrt_hann(N_FFT)
        # overlap-add normalisation for analysis*synthesis windows at 75% overlap
        acc = np.zeros(N_FFT + 3 * HOP)
        for k in range(4):
            acc[k * HOP : k * HOP + N_FFT] += self.win.astype(np.float64) ** 2
        self.ola_norm = float(1.0 / acc[3 * HOP : 4 * HOP].mean())
        # dB offset so a full-scale sine reads ~0 dBFS on the analyser
        self.spec_ref_db = float(20.0 * np.log10(self.win.sum() / 2.0))
        self.pow_to_ms = float(2.0 / (N_FFT * np.sum(self.win.astype(np.float64) ** 2)))
        self.reset(0)

    # ------------------------------------------------------------ state
    def reset(self, start: int) -> None:
        self.ola = np.zeros((2, N_FFT), dtype=np.float32)
        self.pos = float(start + HOP)  # end of next analysis window (source samples)
        self.preroll = N_FFT // HOP - 1
        self.mask_prev = None
        self.pv_phase = None
        self.mb_env = None
        self.comp_env = 0.0
        self.lim_gain = 1.0
        self.out_pos = int(start)
        self.last_pre = None
        self.last_post = None
        self.last_gr = None  # gain reduction per band (dB) for meters

    # ------------------------------------------------------------ input
    def _read_frame(self, src: Source, p: ChainParams):
        """Returns (X, ref_X or None) complex arrays shape (2, BINS)."""
        e = self.pos
        if p.speed != 1.0 and not p.preserve_pitch:
            # varispeed: resample the analysis window (pitch follows speed)
            span = N_FFT * p.speed
            t0 = e - span
            i0 = int(np.floor(t0)) - 1
            n = int(np.ceil(span)) + 4
            raw = src.read(i0, n)
            t = t0 + np.arange(N_FFT) * p.speed - i0
            idx = np.clip(t.astype(np.int64), 0, n - 2)
            fr = (t - idx).astype(np.float32)[:, None]
            x = raw[idx] * (1.0 - fr) + raw[idx + 1] * fr
            return np.fft.rfft(self.win * x.T, axis=1), None
        start = int(round(e)) - N_FFT
        if p.speed != 1.0:
            raw = src.read(start - HOP, N_FFT + HOP)
            X = np.fft.rfft(self.win * raw[HOP:].T, axis=1)
            Xr = np.fft.rfft(self.win * raw[:N_FFT].T, axis=1)
            return X, Xr
        raw = src.read(start, N_FFT)
        return np.fft.rfft(self.win * raw.T, axis=1), None

    def _phase_vocoder(self, X, Xr):
        """Pitch-preserving time stretch using a reference frame one
        synthesis hop earlier (exact per-bin phase advance, no unwrapping).
        Phase is propagated on L+R and the same rotation is applied to both
        channels so the stereo image stays intact."""
        M = X[0] + X[1]
        Mr = Xr[0] + Xr[1]
        weak = np.abs(M) < 1e-6 * (np.abs(X[0]) + np.abs(X[1]) + EPS)
        M = np.where(weak, X[0], M)
        Mr = np.where(weak, Xr[0], Mr)
        ang = np.angle(M)
        if self.pv_phase is None:
            self.pv_phase = ang
            return X
        self.pv_phase = self.pv_phase + (ang - np.angle(Mr))
        self.pv_phase = np.mod(self.pv_phase + np.pi, 2 * np.pi) - np.pi
        rot = np.exp(1j * (self.pv_phase - ang)).astype(np.complex64)
        return X * rot

    # ------------------------------------------------------------ spatial
    def _mask(self, XL, XR, p: ChainParams):
        aL = np.abs(XL)
        aR = np.abs(XR)
        tot = aL + aR + EPS
        pan = (aR - aL) / tot
        if p.mode in ("center", "remove_center"):
            target = 0.0
        else:
            target = p.target
        d = np.abs(pan - target) / p.width
        m = np.clip(1.0 - d, 0.0, 1.0)
        m = m * m * (3.0 - 2.0 * m)
        if p.phase_exp > 0.0:
            cosphi = np.real(XL * np.conj(XR)) / (aL * aR + EPS)
            pw = np.clip(0.5 * (cosphi + 1.0), 0.0, 1.0) ** p.phase_exp
            ap = np.abs(pan)
            m = m * (ap + (1.0 - ap) * pw)
        m = np.convolve(m, (0.25, 0.5, 0.25), mode="same")
        if self.mask_prev is not None and p.smooth > 0:
            m = p.smooth * self.mask_prev + (1.0 - p.smooth) * m
        self.mask_prev = m
        if p.mode in ("remove_center", "pan_remove"):
            m = 1.0 - m
        if p.depth < 1.0:
            m = 1.0 - p.depth * (1.0 - m)
        if p.range_w is not None:
            m = 1.0 - p.range_w * (1.0 - m)
        return m.astype(np.float32)

    def _spatial(self, XL, XR, p: ChainParams):
        mode = p.mode
        if mode == "stereo":
            return XL, XR
        if mode in MASK_MODES:
            m = self._mask(XL, XR, p)
            return XL * m, XR * m
        if mode == "mid":
            M = 0.5 * (XL + XR)
            YL, YR = M, M
        elif mode == "side":
            S = 0.5 * (XL - XR)
            YL, YR = S, -S
        elif mode == "left":
            YL, YR = XL, XL
        elif mode == "right":
            YL, YR = XR, XR
        elif mode == "swap":
            YL, YR = XR, XL
        else:
            return XL, XR
        r = p.range_w
        if p.depth < 1.0:
            r = p.depth if r is None else r * p.depth
        if r is not None:
            YL = r * YL + (1.0 - r) * XL
            YR = r * YR + (1.0 - r) * XR
        return YL, YR

    # ------------------------------------------------------------ dynamics
    def _gain_computer(self, level_db, thr, ratio):
        over = level_db - thr
        knee = 6.0
        gr = np.where(
            over <= -knee / 2,
            0.0,
            np.where(
                over >= knee / 2,
                over * (1.0 - 1.0 / ratio),
                (1.0 - 1.0 / ratio) * (over + knee / 2) ** 2 / (2 * knee),
            ),
        )
        return gr

    def _smooth_env(self, prev, target, att, rel):
        coef = np.where(target > prev, att, rel)
        return coef * prev + (1.0 - coef) * target

    # ------------------------------------------------------------ frame
    def _frame(self, src: Source, p: ChainParams) -> np.ndarray:
        X, Xr = self._read_frame(src, p)
        if Xr is not None:
            X = self._phase_vocoder(X, Xr)
        else:
            self.pv_phase = None
        XL, XR = X[0], X[1]
        pre = 0.5 * (XL.real ** 2 + XL.imag ** 2 + XR.real ** 2 + XR.imag ** 2)

        if p.bypass:
            YL, YR = XL, XR
            self.last_gr = None
        else:
            YL, YR = self._spatial(XL, XR, p)
            if p.ms_active:
                M = 0.5 * (YL + YR) * p.g_mid
                S = 0.5 * (YL - YR) * p.g_side
                YL, YR = M + S, M - S
            if p.g_all is not None:
                YL = YL * p.g_all
                YR = YR * p.g_all
            if p.g_left is not None:
                YL = YL * p.g_left
            if p.g_right is not None:
                YR = YR * p.g_right

            gr_list = None
            if p.mb_dyn:
                pw = 0.5 * (np.abs(YL) ** 2 + np.abs(YR) ** 2)
                band_ms = (p.mb_w @ pw) * self.pow_to_ms
                lvl = 10.0 * np.log10(band_ms + 1e-14)
                target = self._gain_computer(lvl, p.mb_thr, p.mb_ratio)
                if self.mb_env is None or self.mb_env.shape != target.shape:
                    self.mb_env = np.zeros_like(target)
                self.mb_env = self._smooth_env(self.mb_env, target, p.mb_att, p.mb_rel)
                gains = 10.0 ** (-self.mb_env / 20.0)
                g = (gains[:, None] * p.mb_w).sum(axis=0).astype(np.float32)
                YL = YL * g
                YR = YR * g
                gr_list = list(self.mb_env)
            if p.comp:
                pw = 0.5 * (np.abs(YL) ** 2 + np.abs(YR) ** 2)
                lvl = 10.0 * np.log10(pw.sum() * self.pow_to_ms + 1e-14)
                target = float(self._gain_computer(lvl, p.comp_thr, p.comp_ratio))
                self.comp_env = float(self._smooth_env(self.comp_env, target, p.comp_att, p.comp_rel))
                cg = (10.0 ** (-self.comp_env / 20.0)) * p.comp_makeup
                YL = YL * cg
                YR = YR * cg
                gr_list = (gr_list or []) + [self.comp_env]
            self.last_gr = gr_list
            if p.mono:
                m = 0.5 * (YL + YR)
                YL, YR = m, m
            if p.bal_l != 1.0:
                YL = YL * p.bal_l
            if p.bal_r != 1.0:
                YR = YR * p.bal_r

        post = 0.5 * (YL.real ** 2 + YL.imag ** 2 + YR.real ** 2 + YR.imag ** 2)
        y = np.fft.irfft(np.stack((YL, YR)), n=N_FFT, axis=1).astype(np.float32)
        self.ola += y * (self.win * self.ola_norm)
        out = self.ola[:, :HOP].copy()
        self.ola[:, :-HOP] = self.ola[:, HOP:]
        self.ola[:, -HOP:] = 0.0
        self.out_pos = int(round(self.pos)) - N_FFT
        self.pos += HOP * p.speed
        self.last_pre = pre
        self.last_post = post
        return out

    def _post(self, out: np.ndarray, p: ChainParams) -> np.ndarray:
        if p.bypass:
            return out
        if p.drive > 0.0:
            k = 1.0 + 7.0 * p.drive
            out = np.tanh(k * out) / np.tanh(k) * (1.0 / (1.0 + 0.6 * p.drive))
        if p.out_gain != 1.0:
            out = out * p.out_gain
        if p.limiter:
            peak = float(np.max(np.abs(out)))
            target = min(1.0, p.ceiling / peak) if peak > 0 else 1.0
            if target < self.lim_gain:
                g_new = target
            else:
                g_new = self.lim_gain + (target - self.lim_gain) * 0.08
            ramp = np.linspace(self.lim_gain, g_new, out.shape[1], dtype=np.float32)
            out = out * ramp
            self.lim_gain = g_new
            np.clip(out, -p.ceiling, p.ceiling, out=out)
        return out

    def render_hop(self, src: Source, p: ChainParams) -> np.ndarray:
        """Returns (HOP, 2) float32."""
        out = self._frame(src, p)
        while self.preroll > 0:
            self.preroll -= 1
            out = self._frame(src, p)
        return self._post(out, p).T

    def spectrum_db(self, pw):
        return 10.0 * np.log10(pw + 1e-14) - self.spec_ref_db


def render_offline(src: Source, params: ChainParams, sr: int, start: int = 0, end: int | None = None,
                   progress=None, cancelled=None) -> np.ndarray:
    """Render [start, end) of the source through the chain. Speed changes
    the output length when != 1."""
    if end is None:
        end = src.length
    speed = params.speed
    total = int(np.ceil((end - start) / speed))
    chain = SpectralChain(sr)
    chain.reset(start)
    out = np.zeros((total, 2), dtype=np.float32)
    w = 0
    step = 0
    n_steps = max(1, total // HOP)
    while w < total:
        if cancelled is not None and step % 64 == 0 and cancelled():
            from .mdx import Cancelled
            raise Cancelled()
        blk = chain.render_hop(src, params)
        k = min(HOP, total - w)
        out[w : w + k] = blk[:k]
        w += k
        step += 1
        if progress is not None and step % 64 == 0:
            progress(min(1.0, step / n_steps))
    return out
