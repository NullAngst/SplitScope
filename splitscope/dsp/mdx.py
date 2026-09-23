"""MDX-Net source separation running ONNX models with onnxruntime.

This is a NumPy port of the inference loop used by Ultimate Vocal Remover
(UVR) for its MDX-Net models. No PyTorch is required: the STFT/iSTFT are
implemented here to match torch.stft/torch.istft (center=True, reflect pad,
periodic Hann window), which is what the models were trained against.

Model input layout: (batch, 4, dim_f, dim_t) =
    [left.real, left.imag, right.real, right.imag] for the first dim_f bins.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable, Optional

import numpy as np

HOP = 1024
MODEL_SR = 44100


class Cancelled(Exception):
    """Raised when a running job is cancelled by the user."""


@dataclass(frozen=True)
class MDXParams:
    n_fft: int
    dim_f: int
    dim_t: int          # number of STFT frames per chunk (2 ** mdx_dim_t_set)
    compensate: float
    primary_stem: str


def _periodic_hann(n: int) -> np.ndarray:
    return (0.5 - 0.5 * np.cos(2.0 * np.pi * np.arange(n) / n)).astype(np.float32)


class MDXRunner:
    """Runs one MDX-Net ONNX model over a stereo signal at 44.1 kHz."""

    def __init__(self, model_path: str, params: MDXParams, threads: int = 0):
        import onnxruntime as ort

        opts = ort.SessionOptions()
        if threads > 0:
            opts.intra_op_num_threads = threads
        opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        self.session = ort.InferenceSession(
            model_path, sess_options=opts, providers=["CPUExecutionProvider"]
        )
        self.input_name = self.session.get_inputs()[0].name
        self.output_name = self.session.get_outputs()[0].name
        self.p = params
        self.n_bins = params.n_fft // 2 + 1
        self.chunk = HOP * (params.dim_t - 1)
        self.trim = params.n_fft // 2
        self.window = _periodic_hann(params.n_fft)

    # ---- STFT matching torch.stft(center=True, pad_mode="reflect") ----
    def _stft(self, x: np.ndarray) -> np.ndarray:
        """x: (2, chunk) -> (4, dim_f, dim_t) float32."""
        n_fft = self.p.n_fft
        pad = n_fft // 2
        xp = np.pad(x, ((0, 0), (pad, pad)), mode="reflect")
        frames = np.lib.stride_tricks.sliding_window_view(xp, n_fft, axis=1)[:, ::HOP]
        frames = frames[:, : self.p.dim_t]
        spec = np.fft.rfft(frames * self.window, axis=-1)  # (2, T, bins)
        spec = spec[:, :, : self.p.dim_f].transpose(0, 2, 1)  # (2, F, T)
        out = np.empty((4, self.p.dim_f, self.p.dim_t), dtype=np.float32)
        out[0] = spec[0].real
        out[1] = spec[0].imag
        out[2] = spec[1].real
        out[3] = spec[1].imag
        return out

    def _istft(self, s: np.ndarray) -> np.ndarray:
        """s: (4, dim_f, dim_t) -> (2, chunk) float32."""
        n_fft = self.p.n_fft
        T = s.shape[-1]
        spec = np.zeros((2, self.n_bins, T), dtype=np.complex64)
        spec[0, : self.p.dim_f] = s[0] + 1j * s[1]
        spec[1, : self.p.dim_f] = s[2] + 1j * s[3]
        frames = np.fft.irfft(spec.transpose(0, 2, 1), n=n_fft, axis=-1) * self.window
        total = n_fft + HOP * (T - 1)
        y = np.zeros((2, total), dtype=np.float64)
        wsum = np.zeros(total, dtype=np.float64)
        w2 = self.window.astype(np.float64) ** 2
        for i in range(T):
            a = i * HOP
            y[:, a : a + n_fft] += frames[:, i]
            wsum[a : a + n_fft] += w2
        y /= np.where(wsum > 1e-11, wsum, 1.0)
        start = n_fft // 2
        return y[:, start : start + self.chunk].astype(np.float32)

    def _run(self, spec: np.ndarray, denoise: bool) -> np.ndarray:
        spec = spec.copy()
        spec[:, :3, :] = 0.0
        feed = spec[None]
        if denoise:
            pos = self.session.run([self.output_name], {self.input_name: feed})[0][0]
            neg = self.session.run([self.output_name], {self.input_name: -feed})[0][0]
            return 0.5 * pos - 0.5 * neg
        return self.session.run([self.output_name], {self.input_name: feed})[0][0]

    def separate(
        self,
        mix: np.ndarray,
        overlap: float = 0.25,
        denoise: bool = False,
        progress: Optional[Callable[[float], None]] = None,
        cancelled: Optional[Callable[[], bool]] = None,
    ) -> np.ndarray:
        """mix: (N, 2) float32 at 44.1 kHz. Returns primary stem (N, 2)."""
        x = np.ascontiguousarray(mix.T, dtype=np.float32)
        n = x.shape[1]
        chunk, trim = self.chunk, self.trim
        gen = chunk - 2 * trim
        pad = gen + trim - (n % gen)
        mixture = np.concatenate(
            (np.zeros((2, trim), np.float32), x, np.zeros((2, pad), np.float32)), axis=1
        )
        total = mixture.shape[1]
        step = max(1, int((1.0 - overlap) * chunk))
        result = np.zeros((2, total), dtype=np.float64)
        divider = np.zeros((2, total), dtype=np.float64)
        starts = list(range(0, total, step))
        for k, start in enumerate(starts):
            if cancelled is not None and cancelled():
                raise Cancelled()
            end = min(start + chunk, total)
            actual = end - start
            part = mixture[:, start:end]
            if actual < chunk:
                part = np.concatenate((part, np.zeros((2, chunk - actual), np.float32)), axis=1)
            out = self._istft(self._run(self._stft(part), denoise))
            if overlap > 0:
                win = np.hanning(actual)
                result[:, start:end] += out[:, :actual] * win
                divider[:, start:end] += win
            else:
                result[:, start:end] += out[:, :actual]
                divider[:, start:end] += 1.0
            if progress is not None:
                progress((k + 1) / len(starts))
        result /= np.maximum(divider, 1e-8)
        source = result[:, trim : trim + n] * self.p.compensate
        return source.T.astype(np.float32)


def resample(x: np.ndarray, sr_from: int, sr_to: int) -> np.ndarray:
    """Polyphase resample along axis 0."""
    if sr_from == sr_to:
        return x
    from scipy.signal import resample_poly

    g = math.gcd(int(sr_from), int(sr_to))
    return resample_poly(x, sr_to // g, sr_from // g, axis=0).astype(np.float32)


def separate_file_audio(
    audio: np.ndarray,
    sr: int,
    model_path: str,
    params: MDXParams,
    denoise: bool = False,
    progress: Optional[Callable[[float], None]] = None,
    cancelled: Optional[Callable[[], bool]] = None,
) -> tuple[np.ndarray, np.ndarray]:
    """Returns (primary, secondary) at the input sample rate.

    secondary = mix - primary, so the two always sum back to the input.
    """
    runner = MDXRunner(model_path, params)
    x = resample(audio, sr, MODEL_SR)
    primary = runner.separate(x, denoise=denoise, progress=progress, cancelled=cancelled)
    primary = resample(primary, MODEL_SR, sr)
    n = audio.shape[0]
    if primary.shape[0] < n:
        primary = np.pad(primary, ((0, n - primary.shape[0]), (0, 0)))
    primary = primary[:n]
    secondary = (audio - primary).astype(np.float32)
    return primary, secondary
