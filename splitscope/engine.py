"""Playback engine.

The audio callback pulls one hop at a time from the SpectralChain, which in
turn reads the current stem mix. Everything the GUI changes (params, stem
mix, seek requests) is handed over by swapping object references, so the
callback never waits on a lock.
"""
from __future__ import annotations

import collections
import ctypes.util
import os
import sys
import uuid
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from .dsp.chain import HOP, N_FFT, ChainParams, ChainSettings, compile_params
from .dsp.processor import SpectralChain, read_padded


def _prepare_portaudio():
    """Point sounddevice at the PortAudio we ship on Linux builds."""
    if not getattr(sys, "frozen", False) or not sys.platform.startswith("linux"):
        return
    base = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    for name in ("libportaudio.so.2", "libportaudio.so"):
        cand = os.path.join(base, name)
        if os.path.exists(cand):
            orig = ctypes.util.find_library

            def patched(n, _orig=orig, _cand=cand):
                if n == "portaudio":
                    return _cand
                return _orig(n)

            ctypes.util.find_library = patched
            return


_prepare_portaudio()
try:
    import sounddevice as sd

    SD_ERROR = None
except Exception as e:  # PortAudio missing
    sd = None
    SD_ERROR = str(e)

COLORS = ["#8ab4ff", "#ff8fb1", "#7ee0b5", "#ffc46b", "#c59bff", "#6fd6ff", "#ff9e6b", "#b8e36b"]


@dataclass
class Stem:
    name: str
    audio: np.ndarray
    gain_db: float = 0.0
    mute: bool = False
    solo: bool = False
    color: str = "#8ab4ff"
    original: bool = False
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])

    @property
    def length(self) -> int:
        return int(self.audio.shape[0])


class StemMix:
    """Immutable snapshot of the audible stems (a Source for the chain)."""

    def __init__(self, stems: list[Stem]):
        any_solo = any(s.solo for s in stems)
        self.parts = []
        for s in stems:
            if s.mute or (any_solo and not s.solo):
                continue
            self.parts.append((s.audio, float(10.0 ** (s.gain_db / 20.0))))
        self.length = max((s.length for s in stems), default=0)

    def read(self, start: int, n: int) -> np.ndarray:
        if not self.parts:
            return np.zeros((n, 2), np.float32)
        out = None
        for a, g in self.parts:
            blk = read_padded(a, start, n)
            if g != 1.0:
                blk *= g
            out = blk if out is None else out + blk
        return out

    def render(self) -> np.ndarray:
        out = np.zeros((self.length, 2), np.float32)
        for a, g in self.parts:
            out[: a.shape[0]] += a * g
        return out


class AudioEngine:
    def __init__(self):
        self.sr = 44100
        self.stems: list[Stem] = []
        self.settings = ChainSettings()
        self.audition = None
        self.params: ChainParams = compile_params(self.settings, self.sr)
        self.mix = StemMix([])
        self.chain = SpectralChain(self.sr)
        self.stream = None
        self.device = None  # None = default output
        self.playing = False
        self.loop_enabled = False
        self.loop = (0, 0)
        self._seek_req: Optional[int] = None
        self._pos = 0              # source position of last emitted hop start
        self._ended = False
        self._fifo = np.zeros((0, 2), np.float32)
        self.spectra = collections.deque(maxlen=256)
        self.peak = np.zeros(2, np.float32)
        self.last_error = None
        self.monitor = 1.0         # playback-only volume, never affects exports

    # ------------------------------------------------------------ project
    @property
    def length(self) -> int:
        return self.mix.length if self.stems else 0

    def set_project(self, audio: np.ndarray, sr: int, name: str):
        self.stop_stream()
        self.sr = int(sr)
        self.stems = [Stem(name, audio, original=True, color=COLORS[0])]
        self.chain = SpectralChain(self.sr)
        self.loop = (0, audio.shape[0])
        self.loop_enabled = False
        self.update_params()
        self.update_mix()
        self._pos = 0
        self._seek_req = 0

    def add_stem(self, name: str, audio: np.ndarray) -> Stem:
        n = self.stems[0].length if self.stems else audio.shape[0]
        if audio.shape[0] < n:
            audio = np.pad(audio, ((0, n - audio.shape[0]), (0, 0)))
        s = Stem(name, np.ascontiguousarray(audio[:n], np.float32),
                 color=COLORS[len(self.stems) % len(COLORS)])
        self.stems.append(s)
        self.update_mix()
        return s

    def update_mix(self):
        self.mix = StemMix(self.stems)

    def update_params(self):
        self.params = compile_params(self.settings, self.sr, self.audition)

    # ------------------------------------------------------------ transport
    def position(self) -> float:
        """Best estimate of the source position currently audible (samples)."""
        pos = self._pos
        if self.playing and self.stream is not None:
            try:
                lat = float(self.stream.latency) + len(self._fifo) / self.sr
            except Exception:
                lat = 0.0
            pos = pos - lat * self.sr * self.params.speed
        return float(max(0, min(pos, self.length)))

    def seek(self, pos: int):
        pos = int(max(0, min(pos, max(0, self.length - 1))))
        self._seek_req = pos
        self._pos = pos
        self._ended = False
        self.spectra.clear()

    def play(self):
        if sd is None:
            raise RuntimeError(f"Audio output unavailable: {SD_ERROR}")
        if not self.stems:
            return
        if self._pos >= self.length - HOP:
            self.seek(self.loop[0] if self.loop_enabled else 0)
        if self._seek_req is None:
            self._seek_req = int(self._pos)
        if self.stream is None:
            self._open_stream()
        self._ended = False
        self.playing = True
        if not self.stream.active:
            self.stream.start()

    def pause(self):
        self.playing = False
        if self.stream is not None and self.stream.active:
            try:
                self.stream.stop()
            except Exception:
                pass
        self._fifo = np.zeros((0, 2), np.float32)
        self._seek_req = int(self._pos)

    def stop_stream(self):
        self.playing = False
        if self.stream is not None:
            try:
                self.stream.abort()
                self.stream.close()
            except Exception:
                pass
        self.stream = None
        self._fifo = np.zeros((0, 2), np.float32)

    def _open_stream(self):
        self.stream = sd.OutputStream(
            samplerate=self.sr, channels=2, dtype="float32", blocksize=HOP,
            device=self.device, latency="high", callback=self._callback,
        )

    def set_device(self, device):
        was = self.playing
        self.stop_stream()
        self.device = device
        if was:
            self.play()

    @property
    def ended(self) -> bool:
        return self._ended

    # ------------------------------------------------------------ callback
    def _render_block(self) -> np.ndarray:
        req = self._seek_req
        if req is not None:
            self._seek_req = None
            self.chain.reset(req)
        p = self.params
        mix = self.mix
        blk = self.chain.render_hop(mix, p)
        pos = self.chain.out_pos
        if self.loop_enabled and self.loop[1] - self.loop[0] > HOP and pos >= self.loop[1]:
            self.chain.reset(self.loop[0])
            blk = self.chain.render_hop(mix, p)
            pos = self.chain.out_pos
        elif pos >= mix.length:
            self._ended = True
            blk = np.zeros_like(blk)
        self._pos = pos
        if self.chain.last_post is not None:
            self.spectra.append((pos, self.chain.last_pre, self.chain.last_post, self.chain.last_gr))
        return blk

    def _callback(self, outdata, frames, time_info, status):
        try:
            if self._ended:
                outdata.fill(0)
                return
            while self._fifo.shape[0] < frames:
                self._fifo = np.concatenate((self._fifo, self._render_block()))
            out = self._fifo[:frames]
            self._fifo = self._fifo[frames:]
            if self.monitor != 1.0:
                out = out * self.monitor
            else:
                out = out.copy()
            np.clip(out, -1.0, 1.0, out=out)
            outdata[:] = out
            self.peak = np.abs(out).max(axis=0)
        except Exception as e:  # never let an exception kill the stream silently
            self.last_error = repr(e)
            outdata.fill(0)

    # ------------------------------------------------------------ analysis
    def pull_spectrum(self):
        """Newest analyser frame that is already audible (or None)."""
        if not self.spectra:
            return None
        audible = self.position()
        best = None
        while self.spectra and self.spectra[0][0] <= audible:
            best = self.spectra.popleft()
        if best is None and not self.playing:
            best = self.spectra[-1]
        return best

    def analyse_static(self, pos: int):
        """Analyser frame at a position while paused (for scrubbing)."""
        ch = SpectralChain(self.sr)
        ch.reset(max(0, int(pos) - N_FFT // 2))
        ch.render_hop(self.mix, self.params)
        return (pos, ch.last_pre, ch.last_post, ch.last_gr)


def list_output_devices():
    if sd is None:
        return []
    out = []
    try:
        hostapis = sd.query_hostapis()
        for i, d in enumerate(sd.query_devices()):
            if d["max_output_channels"] >= 2:
                api = hostapis[d["hostapi"]]["name"] if hostapis else ""
                out.append((i, f"{d['name']} ({api})"))
    except Exception:
        pass
    return out
