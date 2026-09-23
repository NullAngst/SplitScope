"""Audio import/export.

libsndfile (via soundfile) handles WAV, FLAC, OGG/Vorbis, Opus-in-OGG,
MP3 and AIFF directly. Anything else (M4A/AAC, WMA, video files) goes
through ffmpeg: the copy bundled by imageio-ffmpeg if present, otherwise
one found on PATH.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass

import numpy as np
import soundfile as sf

IMPORT_FILTER = (
    "Audio files (*.wav *.flac *.mp3 *.ogg *.oga *.opus *.aif *.aiff *.m4a *.aac *.wma *.alac *.mp4 *.mkv *.webm);;"
    "All files (*)"
)

EXPORT_FORMATS = {
    # label: (extension, soundfile format, subtype)
    "WAV 16-bit": ("wav", "WAV", "PCM_16"),
    "WAV 24-bit": ("wav", "WAV", "PCM_24"),
    "WAV 32-bit float": ("wav", "WAV", "FLOAT"),
    "FLAC 16-bit": ("flac", "FLAC", "PCM_16"),
    "FLAC 24-bit": ("flac", "FLAC", "PCM_24"),
    "MP3 (VBR high quality)": ("mp3", "MP3", "MPEG_LAYER_III"),
    "OGG Vorbis": ("ogg", "OGG", "VORBIS"),
}


@dataclass
class LoadedAudio:
    audio: np.ndarray  # (n, 2) float32
    sr: int
    channels_in_file: int
    path: str


def _ffmpeg_exe() -> str | None:
    try:
        import imageio_ffmpeg

        exe = imageio_ffmpeg.get_ffmpeg_exe()
        if exe and os.path.exists(exe):
            return exe
    except Exception:
        pass
    return shutil.which("ffmpeg")


def _to_stereo(a: np.ndarray) -> tuple[np.ndarray, int]:
    if a.ndim == 1:
        a = a[:, None]
    ch = a.shape[1]
    if ch == 1:
        a = np.repeat(a, 2, axis=1)
    elif ch > 2:
        a = a[:, :2]
    return np.ascontiguousarray(a, dtype=np.float32), ch


def _load_ffmpeg(path: str) -> LoadedAudio:
    exe = _ffmpeg_exe()
    if exe is None:
        raise RuntimeError(
            "This format needs ffmpeg, and none was found. Install ffmpeg or convert the file to WAV/FLAC/MP3."
        )
    probe_sr = 44100
    # ask ffmpeg to keep the native rate: decode once to WAV in memory
    cmd = [exe, "-v", "error", "-i", path, "-vn", "-ac", "2", "-c:a", "pcm_f32le", "-f", "wav", "-"]
    flags = 0
    if sys.platform.startswith("win"):
        flags = 0x08000000  # CREATE_NO_WINDOW
    proc = subprocess.run(cmd, capture_output=True, creationflags=flags)
    if proc.returncode != 0 or not proc.stdout:
        msg = proc.stderr.decode(errors="replace").strip().splitlines()
        raise RuntimeError("ffmpeg could not decode the file: " + (msg[-1] if msg else "unknown error"))
    import io

    data, sr = sf.read(io.BytesIO(proc.stdout), dtype="float32", always_2d=True)
    a, _ = _to_stereo(data)
    return LoadedAudio(a, int(sr or probe_sr), 2, path)


def load(path: str) -> LoadedAudio:
    try:
        data, sr = sf.read(path, dtype="float32", always_2d=True)
        a, ch = _to_stereo(data)
        if a.shape[0] == 0:
            raise RuntimeError("File contains no audio")
        return LoadedAudio(a, int(sr), ch, path)
    except (sf.LibsndfileError, RuntimeError, TypeError) as e:
        try:
            return _load_ffmpeg(path)
        except RuntimeError as e2:
            raise RuntimeError(f"{e2}\n(libsndfile said: {e})") from None


def save(path: str, audio: np.ndarray, sr: int, fmt_label: str, normalize: bool = False) -> None:
    ext, fmt, subtype = EXPORT_FORMATS[fmt_label]
    a = np.asarray(audio, dtype=np.float32)
    peak = float(np.max(np.abs(a))) if a.size else 0.0
    if normalize and peak > 0:
        a = a * (10 ** (-1.0 / 20.0) / peak)
    elif subtype != "FLOAT" and peak > 1.0:
        a = np.clip(a, -1.0, 1.0)
    kwargs = {}
    if fmt == "MP3":
        kwargs["compression_level"] = 0.1
        kwargs["bitrate_mode"] = "VARIABLE"
        if sr not in (32000, 44100, 48000):
            from .dsp.mdx import resample

            a = resample(a, sr, 44100)
            sr = 44100
    if fmt == "OGG":
        kwargs["compression_level"] = 0.2
    try:
        sf.write(path, a, sr, format=fmt, subtype=subtype, **kwargs)
    except TypeError:
        sf.write(path, a, sr, format=fmt, subtype=subtype)


def ensure_ext(path: str, fmt_label: str) -> str:
    ext = EXPORT_FORMATS[fmt_label][0]
    root, cur = os.path.splitext(path)
    if cur.lower().lstrip(".") != ext:
        return path + "." + ext
    return path
