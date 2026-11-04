"""Model-free separation (always available, no downloads, no AI).

Every function here builds soft time-frequency masks from the signal itself
and returns stems that sum back to the input exactly (the last stem of each
split is computed as input minus the others), so nothing is lost and any
split can be undone by unmuting both halves.

Methods:
  * REPET-SIM (Rafii and Pardo, 2012). Pop music repeats: a chorus chord,
    a drum loop, a riff. For every frame, find the frames elsewhere in the
    song that look most like it and take their median. What survives the
    median is the repeating background; what does not (a lead vocal, a solo,
    a fill) is the foreground.
  * HPSS (FitzGerald, 2010; Driedger et al., 2014). A median along time
    keeps sustained partials, a median along frequency keeps broadband
    hits. With a margin above 1 a third "residual" part takes whatever is
    neither clearly sustained nor clearly percussive.
  * Multi-stage HPSS (FitzGerald and Gainza, 2010). A voice sung with
    vibrato looks smeared (percussive) through a very long window but
    sustained (harmonic) through a short one. Two passes with different
    window sizes split steady instruments, voice/leads and drums.
  * Center weighting: the same inter-channel pan/phase test the live chain
    uses for center extraction.
  * Mid/side, low/high split.

These are estimates. They have no idea what a "voice" is, only what repeats,
what sustains and where it sits in the stereo field. Expect bleed.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass

import numpy as np
from scipy.ndimage import median_filter
from scipy.signal import butter, sosfiltfilt

from . import curves as C
from .chain import ChainSettings, compile_params
from .mdx import Cancelled
from .processor import ArraySource, render_offline

EPS = 1e-10


# ------------------------------------------------------------------ settings
@dataclass
class DSPSettings:
    """User-tunable parameters for the model-free methods."""

    # REPET-SIM
    rep_neighbours: int = 40        # similar frames whose median forms the background
    rep_min_gap_s: float = 1.0      # neighbours must be at least this far away in time
    rep_strength: float = 1.0       # >1 keeps the foreground cleaner but quieter
    # vocal estimate (REPET foreground x center x band x harmonic)
    voc_center: float = 0.5         # 0 ignores stereo position, 1 keeps only center
    voc_width: float = 0.35         # pan window counted as "center"
    voc_lo: float = 100.0           # vocal band, Hz
    voc_hi: float = 12000.0
    voc_transients: float = 0.3     # 0 keeps drum hits in the vocal, 1 suppresses them
    # HPSS
    hpss_time_s: float = 0.2        # median length along time
    hpss_freq_hz: float = 500.0     # median length along frequency
    hpss_margin: float = 1.0        # >1 gives a third residual stem
    drum_margin: float = 2.0        # margin used for the drum estimate
    bass_cutoff: float = 200.0      # Hz, top of the bass estimate

    def key(self) -> tuple:
        return tuple(asdict(self).values())

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d) -> "DSPSettings":
        s = DSPSettings()
        if isinstance(d, dict):
            for k, v in d.items():
                if hasattr(s, k):
                    try:
                        setattr(s, k, type(getattr(s, k))(v))
                    except (TypeError, ValueError):
                        pass
        return s


# ------------------------------------------------------------------ helpers
def _phase(progress, a: float, b: float, label: str = ""):
    """Maps a sub-step's 0..1 progress into [a, b] of the caller's bar."""
    if progress is None:
        return None
    return lambda f: progress(a + (b - a) * min(1.0, max(0.0, f)))


def _check(cancelled):
    if cancelled is not None and cancelled():
        raise Cancelled()


def _scaled(n: int, sr: int) -> int:
    """FFT sizes are tuned for 44.1/48 kHz; keep the same time span at other rates."""
    k = int(round(math.log2(max(sr, 8000) / 44100.0)))
    return int(max(256, n * 2 ** k))


def _odd(x: float) -> int:
    n = max(3, int(round(x)))
    return n | 1


def _sqrt_hann(n: int) -> np.ndarray:
    return np.sqrt(0.5 - 0.5 * np.cos(2 * np.pi * np.arange(n) / n)).astype(np.float32)


class Stft:
    """Block-wise stereo STFT with 75 % overlap.

    Holds the padded signal, never the whole complex spectrogram: analysis
    keeps a mono magnitude (about 100 MB for 5 minutes), and synthesis
    recomputes each block, so long files stay inside a sane memory budget.
    """

    def __init__(self, audio: np.ndarray, sr: int, n_fft: int):
        self.sr = int(sr)
        self.n = int(audio.shape[0])
        self.n_fft = int(n_fft)
        self.hop = self.n_fft // 4
        self.bins = self.n_fft // 2 + 1
        self.w = _sqrt_hann(self.n_fft)
        self.pad = self.n_fft
        x = np.ascontiguousarray(audio.T, dtype=np.float32)
        tail = self.n_fft + (-(self.n + 2 * self.pad)) % self.hop
        self.x = np.pad(x, ((0, 0), (self.pad, self.pad + tail)))
        self.n_frames = (self.x.shape[1] - self.n_fft) // self.hop + 1
        acc = np.zeros(self.n_fft + 3 * self.hop)
        for k in range(4):
            acc[k * self.hop : k * self.hop + self.n_fft] += self.w.astype(np.float64) ** 2
        self.norm = np.float32(1.0 / acc[3 * self.hop : 4 * self.hop].mean())
        self.freqs = np.fft.rfftfreq(self.n_fft, 1.0 / self.sr).astype(np.float32)
        self._frames = np.lib.stride_tricks.sliding_window_view(self.x, self.n_fft, axis=1)[:, :: self.hop]

    def ranges(self, size: int):
        for f0 in range(0, self.n_frames, size):
            yield f0, min(self.n_frames, f0 + size)

    def block(self, f0: int, f1: int) -> np.ndarray:
        """Complex spectra, shape (2, frames, bins)."""
        return np.fft.rfft(self._frames[:, f0:f1] * self.w, axis=-1).astype(np.complex64)

    def magnitude(self, progress=None, cancelled=None, block: int = 512) -> np.ndarray:
        """Mono magnitude (mean of |L| and |R|), shape (frames, bins), float32."""
        V = np.empty((self.n_frames, self.bins), dtype=np.float32)
        for f0, f1 in self.ranges(block):
            _check(cancelled)
            S = self.block(f0, f1)
            V[f0:f1] = 0.5 * (np.abs(S[0]) + np.abs(S[1]))
            if progress is not None:
                progress(f1 / self.n_frames)
        return V

    def synth(self, mask_fn, n_out: int, progress=None, cancelled=None, block: int = 512) -> list:
        """Applies mask_fn(f0, f1, S) -> list of n_out masks (each broadcastable
        to S) and overlap-adds the masked spectra. Returns n_out (n, 2) arrays."""
        outs = [np.zeros_like(self.x) for _ in range(n_out)]
        wn = self.w * self.norm
        n_fft, hop = self.n_fft, self.hop
        for f0, f1 in self.ranges(block):
            _check(cancelled)
            S = self.block(f0, f1)
            masks = mask_fn(f0, f1, S)
            for o, m in zip(outs, masks):
                y = np.fft.irfft(S * m, n=n_fft, axis=-1).astype(np.float32) * wn  # (2, F, n_fft)
                # Frames k, k+4, k+8 ... do not overlap: add each phase as one flat run.
                for k in range(4):
                    sel = y[:, k::4]
                    cnt = sel.shape[1]
                    if cnt == 0:
                        continue
                    s0 = (f0 + k) * hop
                    o[:, s0 : s0 + cnt * n_fft] += sel.reshape(2, cnt * n_fft)
            if progress is not None:
                progress(f1 / self.n_frames)
        return [np.ascontiguousarray(o[:, self.pad : self.pad + self.n].T) for o in outs]


def _softmask(target: np.ndarray, other: np.ndarray, power: float = 2.0) -> np.ndarray:
    t = target ** power
    return (t / (t + other ** power + EPS)).astype(np.float32)


def hpss_masks(V: np.ndarray, st: Stft, time_s: float, freq_hz: float, margin: float = 1.0,
               power: float = 2.0):
    """Harmonic and percussive masks from a (frames, bins) magnitude.
    With margin 1 they sum to 1; with margin > 1 the rest is residual."""
    kt = _odd(time_s * st.sr / st.hop)
    kf = _odd(freq_hz * st.n_fft / st.sr)
    H = median_filter(V, size=(kt, 1), mode="nearest")
    P = median_filter(V, size=(1, kf), mode="nearest")
    if margin <= 1.0:
        mh = _softmask(H, P, power)
        return mh, (1.0 - mh)
    return _softmask(H, margin * P, power), _softmask(P, margin * H, power)


def center_weight(SL: np.ndarray, SR: np.ndarray, width: float = 0.35, phase_strict: float = 0.5) -> np.ndarray:
    """1 for content panned center and in phase, falling to 0 off-center.
    Same test as the live chain's center extraction."""
    aL = np.abs(SL)
    aR = np.abs(SR)
    pan = (aR - aL) / (aL + aR + EPS)
    m = np.clip(1.0 - np.abs(pan) / max(width, 0.02), 0.0, 1.0)
    m = m * m * (3.0 - 2.0 * m)
    if phase_strict > 0.0:
        cosphi = np.real(SL * np.conj(SR)) / (aL * aR + EPS)
        pw = np.clip(0.5 * (cosphi + 1.0), 0.0, 1.0) ** (phase_strict * 12.0)
        ap = np.abs(pan)
        m = m * (ap + (1.0 - ap) * pw)
    return m.astype(np.float32)


def band_weight(f: np.ndarray, lo: float, hi: float, slope_db: float = 24.0) -> np.ndarray:
    w = np.ones_like(f, dtype=np.float64)
    if lo > 0:
        w *= C.butter_hp(f.astype(np.float64), lo, slope_db)
    if hi < f[-1]:
        w *= C.butter_lp(f.astype(np.float64), hi, slope_db)
    return w.astype(np.float32)


# ------------------------------------------------------------------ HPSS
def hpss(audio: np.ndarray, sr: int, kernel_t=None, kernel_f=None, margin: float = 1.0,
         progress=None, cancelled=None, n_fft: int | None = None,
         time_s: float = 0.2, freq_hz: float = 500.0, three_way: bool = False):
    """Median-filter harmonic/percussive separation.

    Returns (harmonic, percussive) that sum to the input, or with three_way
    (harmonic, percussive, residual). kernel_t / kernel_f are accepted for
    backwards compatibility and ignored; use time_s / freq_hz, which mean
    the same thing at every FFT size and sample rate.
    """
    st = Stft(audio, sr, n_fft or _scaled(2048, sr))
    V = st.magnitude(_phase(progress, 0.0, 0.35), cancelled)
    _check(cancelled)
    mh, mp = hpss_masks(V, st, time_s, freq_hz, margin)
    del V
    if progress is not None:
        progress(0.5)
    if three_way and margin > 1.0:
        h, p = st.synth(lambda f0, f1, S: (mh[f0:f1][None], mp[f0:f1][None]), 2,
                        _phase(progress, 0.5, 1.0), cancelled)
        r = (audio - h - p).astype(np.float32)
        return h, p, r
    (h,) = st.synth(lambda f0, f1, S: (mh[f0:f1][None],), 1, _phase(progress, 0.5, 1.0), cancelled)
    return h, (audio - h).astype(np.float32)


def multistage(audio: np.ndarray, sr: int, s: DSPSettings, progress=None, cancelled=None):
    """Two-pass HPSS. Returns (sustained, voice, hits); they sum to the input.

    Pass 1, ~370 ms window: steady pitched instruments are harmonic; voice
    (vibrato, glides) and drums are percussive. Pass 2 on that percussive
    part, ~23 ms window: voice and leads are harmonic, drums percussive.
    Voice content below the vocal band goes back to the sustained stem.
    """
    _, rest = hpss(audio, sr, n_fft=_scaled(16384, sr), time_s=0.4, freq_hz=50.0,
                   progress=_phase(progress, 0.0, 0.55), cancelled=cancelled)
    voice, hits = hpss(rest, sr, n_fft=_scaled(1024, sr), time_s=0.1, freq_hz=700.0,
                       progress=_phase(progress, 0.55, 0.95), cancelled=cancelled)
    if s.voc_lo > 20.0:
        _, voice = split_low(voice, sr, s.voc_lo)  # the low part rejoins "sustained" below
    if progress is not None:
        progress(1.0)
    voice = voice.astype(np.float32)
    hits = hits.astype(np.float32)
    sustained = (audio - voice - hits).astype(np.float32)
    return sustained, voice, hits


# ------------------------------------------------------------------ REPET-SIM
def _band_pool(freqs: np.ndarray, n_bands: int = 96, lo: float = 40.0, hi: float = 12000.0) -> np.ndarray:
    hi = min(hi, float(freqs[-1]) * 0.98)
    edges = np.geomspace(lo, hi, n_bands + 1)
    idx = np.clip(np.searchsorted(edges, freqs) - 1, -1, n_bands)
    W = np.zeros((freqs.size, n_bands), dtype=np.float32)
    ok = (idx >= 0) & (idx < n_bands)
    W[np.nonzero(ok)[0], idx[ok]] = 1.0
    return W


def repeating_spectrogram(V: np.ndarray, st: Stft, s: DSPSettings, progress=None, cancelled=None) -> np.ndarray:
    """REPET-SIM background estimate: per frame, the median of its most
    similar frames (cosine similarity on log-spaced band energies), limited
    to the frame's own magnitude."""
    F = V.shape[0]
    feat = np.sqrt(V @ _band_pool(st.freqs))
    feat /= np.linalg.norm(feat, axis=1, keepdims=True) + EPS
    gap = int(round(s.rep_min_gap_s * st.sr / st.hop))
    k = int(max(3, s.rep_neighbours))
    # Short clips: shrink the gap and neighbour count until candidates exist.
    while gap > 0 and F - (2 * gap + 1) < k:
        gap //= 2
    k = max(1, min(k, F - (2 * gap + 1)))
    if F <= 2:
        return V.copy()
    R = np.empty_like(V)
    cols = np.arange(F)
    blk = 128
    for b0 in range(0, F, blk):
        _check(cancelled)
        b1 = min(F, b0 + blk)
        sims = feat[b0:b1] @ feat.T
        rows = np.arange(b0, b1)[:, None]
        sims[np.abs(cols[None, :] - rows) <= gap] = -np.inf
        nb = np.argpartition(-sims, k - 1, axis=1)[:, :k]
        R[b0:b1] = np.median(V[nb], axis=1)
        if progress is not None:
            progress(b1 / F)
    np.minimum(R, V, out=R)
    return R


def repet_sim(audio: np.ndarray, sr: int, s: DSPSettings, progress=None, cancelled=None):
    """Returns (foreground, background); they sum to the input."""
    st = Stft(audio, sr, _scaled(4096, sr))
    V = st.magnitude(_phase(progress, 0.0, 0.2), cancelled)
    R = repeating_spectrogram(V, st, s, _phase(progress, 0.2, 0.75), cancelled)
    mf = _softmask(V - R, max(s.rep_strength, 0.1) * R)
    del V, R
    (fg,) = st.synth(lambda f0, f1, S: (mf[f0:f1][None],), 1, _phase(progress, 0.75, 1.0), cancelled)
    return fg, (audio - fg).astype(np.float32)


def vocals_estimate(audio: np.ndarray, sr: int, s: DSPSettings, progress=None, cancelled=None):
    """Best model-free vocal estimate SplitScope has. The vocal mask is the
    product of four cues, each a soft 0..1 weight per time-frequency bin:

      non-repeating (REPET-SIM foreground)
      x center-panned (blend set by voc_center)
      x inside the vocal band
      x not a transient (blend set by voc_transients)

    Returns (vocals, instrumental); they sum to the input."""
    st = Stft(audio, sr, _scaled(4096, sr))
    V = st.magnitude(_phase(progress, 0.0, 0.15), cancelled)
    R = repeating_spectrogram(V, st, s, _phase(progress, 0.15, 0.6), cancelled)
    mf = _softmask(V - R, max(s.rep_strength, 0.1) * R)
    del R
    _check(cancelled)
    if s.voc_transients > 0.0:
        mh, _ = hpss_masks(V, st, s.hpss_time_s, s.hpss_freq_hz)
        mf *= (1.0 - s.voc_transients) + s.voc_transients * mh
        del mh
    del V
    mf *= band_weight(st.freqs, s.voc_lo, s.voc_hi)[None, :]
    if progress is not None:
        progress(0.7)
    c_amt = float(np.clip(s.voc_center, 0.0, 1.0))

    def masks(f0, f1, S):
        m = mf[f0:f1]
        if c_amt > 0.0:
            m = m * ((1.0 - c_amt) + c_amt * center_weight(S[0], S[1], s.voc_width))
        return (m[None],)

    (voc,) = st.synth(masks, 1, _phase(progress, 0.7, 1.0), cancelled)
    return voc, (audio - voc).astype(np.float32)


def drums_estimate(audio: np.ndarray, sr: int, s: DSPSettings, progress=None, cancelled=None) -> np.ndarray:
    """Percussive part of the input with a margin, so only clear hits count."""
    _, p, _ = hpss(audio, sr, margin=max(1.01, s.drum_margin), time_s=s.hpss_time_s, freq_hz=s.hpss_freq_hz,
                   three_way=True, progress=progress, cancelled=cancelled)
    return p


def bass_estimate(audio: np.ndarray, sr: int, s: DSPSettings, progress=None, cancelled=None) -> np.ndarray:
    """Sustained, low, mostly-center content. Long window for low-frequency
    resolution, so a kick drum (short, broadband) mostly stays out."""
    st = Stft(audio, sr, _scaled(8192, sr))
    V = st.magnitude(_phase(progress, 0.0, 0.4), cancelled)
    mh, _ = hpss_masks(V, st, 0.3, 120.0)
    del V
    mh *= band_weight(st.freqs, 25.0, s.bass_cutoff)[None, :]

    def masks(f0, f1, S):
        return ((mh[f0:f1] * (0.5 + 0.5 * center_weight(S[0], S[1], 0.5, 0.0)))[None],)

    (bass,) = st.synth(masks, 1, _phase(progress, 0.5, 1.0), cancelled)
    return bass


# ------------------------------------------------------------------ simple splits
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
