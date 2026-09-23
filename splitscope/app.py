"""Application entry point."""
from __future__ import annotations

import os
import sys


def _icon_path() -> str | None:
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    for cand in (os.path.join(base, "resources", "icon.png"),
                 os.path.join(base, "splitscope", "resources", "icon.png")):
        if os.path.exists(cand):
            return cand
    return None


def selftest(out_path: str | None) -> int:
    """Checks that a frozen build has every native piece it needs. Used by CI.

    Writes a report to out_path (GUI builds on Windows have no console) and
    returns 0 only if everything that must work does.
    """
    lines, ok = [], True

    def check(name, fn, required=True):
        nonlocal ok
        try:
            lines.append(f"PASS {name}: {fn()}")
        except Exception as e:  # report and continue
            lines.append(f"{'FAIL' if required else 'WARN'} {name}: {e!r}")
            ok = ok and not required

    import tempfile

    import numpy as np

    def sndfile():
        import soundfile as sf

        d = tempfile.mkdtemp()
        x = (np.random.rand(4410, 2).astype(np.float32) - 0.5) * 0.2
        for ext, fmt, sub in (("flac", "FLAC", "PCM_24"), ("mp3", "MP3", "MPEG_LAYER_III"), ("ogg", "OGG", "VORBIS")):
            p = os.path.join(d, "t." + ext)
            sf.write(p, x, 44100, format=fmt, subtype=sub)
            sf.read(p)
        return sf.__libsndfile_version__

    def portaudio():
        from .engine import sd, SD_ERROR

        if sd is None:
            raise RuntimeError(SD_ERROR)
        return sd.get_portaudio_version()[1]

    def onnx():
        import onnxruntime as ort

        from . import models as M

        found = [m for m in M.REGISTRY if M.is_installed(m)]
        if found:
            from .dsp.mdx import separate_file_audio

            m = min(found, key=lambda x: x.size)
            x = (np.random.rand(44100, 2).astype(np.float32) - 0.5) * 0.2
            prim, sec = separate_file_audio(x, 44100, str(M.find_model_file(m.file)), m.params)
            if prim.shape != x.shape or not np.isfinite(prim).all():
                raise RuntimeError(f"bad output shape {prim.shape}")
            return f"{ort.__version__}, ran {m.file}, found: {[x.file for x in found]}"
        return f"{ort.__version__}, no models found"

    def ffmpeg():
        from .audio_io import _ffmpeg_exe

        exe = _ffmpeg_exe()
        if not exe:
            raise RuntimeError("no ffmpeg found")
        return exe

    def gui():
        from PySide6.QtWidgets import QApplication

        from .ui import theme
        from .ui.main_window import MainWindow

        app = QApplication.instance() or QApplication(sys.argv[:1])
        theme.apply(app)
        w = MainWindow()
        w.show()
        app.processEvents()
        w.close()
        return "main window created"

    check("numpy/scipy", lambda: __import__("scipy.signal").__version__ and np.__version__)
    check("libsndfile (FLAC/MP3/OGG write+read)", sndfile)
    check("PortAudio", portaudio)
    check("ONNX Runtime", onnx)
    check("ffmpeg (for M4A/AAC import)", ffmpeg, required=False)
    check("Qt GUI", gui)
    report = "\n".join(lines) + f"\nRESULT {'OK' if ok else 'FAILED'}\n"
    if out_path:
        with open(out_path, "w", encoding="utf-8") as fh:
            fh.write(report)
    else:
        print(report)
    return 0 if ok else 1


def main() -> int:
    if "--selftest" in sys.argv:
        i = sys.argv.index("--selftest")
        out = sys.argv[i + 1] if len(sys.argv) > i + 1 else None
        return selftest(out)
    # Keep BLAS/ONNX from oversubscribing when the GUI thread is also busy.
    os.environ.setdefault("OMP_WAIT_POLICY", "PASSIVE")
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QApplication

    from . import __version__
    from .ui import theme
    from .ui.main_window import MainWindow

    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv)
    app.setApplicationName("SplitScope")
    app.setOrganizationName("SplitScope")
    app.setApplicationVersion(__version__)
    app.setDesktopFileName("splitscope")
    ic = _icon_path()
    if ic:
        app.setWindowIcon(QIcon(ic))
    theme.apply(app)
    w = MainWindow()
    w.show()
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    if args and os.path.isfile(args[0]):
        w.open_file(args[0])
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
