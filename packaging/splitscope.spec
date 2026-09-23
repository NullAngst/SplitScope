# PyInstaller spec for SplitScope (one-folder build).
#
# Build from the repository root:
#     pyinstaller --noconfirm packaging/splitscope.spec
#
# Environment variables read at build time:
#     SPLITSCOPE_MODELS_DIR   folder of .onnx files to bundle (optional)
#     PORTAUDIO_LIB           Linux only: path to libportaudio.so.2 to ship
# -*- mode: python ; coding: utf-8 -*-
import glob
import os
import sys

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
sys.path.insert(0, ROOT)
IS_WIN = sys.platform.startswith("win")
IS_MAC = sys.platform == "darwin"
IS_LINUX = sys.platform.startswith("linux")

datas = [
    (os.path.join(ROOT, "splitscope", "resources"), os.path.join("splitscope", "resources")),
]
# ffmpeg is only used for formats libsndfile cannot read (M4A/AAC, WMA, video).
datas += collect_data_files("imageio_ffmpeg", subdir="binaries")
# soundfile/sounddevice ship their native libraries through the
# pyinstaller-hooks-contrib hooks, which collect them as binaries.

models_dir = os.environ.get("SPLITSCOPE_MODELS_DIR", "")
if models_dir and os.path.isdir(models_dir):
    for f in sorted(glob.glob(os.path.join(models_dir, "*.onnx"))):
        datas.append((f, "bundled_models"))
        print(f"[splitscope.spec] bundling model {os.path.basename(f)}")

binaries = []
binaries += collect_dynamic_libs("onnxruntime")
pa = os.environ.get("PORTAUDIO_LIB", "")
if IS_LINUX:
    if pa and os.path.isfile(pa):
        # Lands next to the executable; engine.py points sounddevice at it.
        binaries.append((pa, "."))
        print(f"[splitscope.spec] shipping PortAudio from {pa}")
    else:
        print("[splitscope.spec] WARNING: PORTAUDIO_LIB not set; the build will need a system PortAudio")

excludes = [
    "tkinter", "matplotlib", "IPython", "pytest", "torch", "PIL",
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineQuick",
    "PySide6.QtQuick", "PySide6.QtQml", "PySide6.QtQuick3D", "PySide6.Qt3DCore", "PySide6.Qt3DRender",
    "PySide6.QtCharts", "PySide6.QtDataVisualization", "PySide6.QtMultimedia", "PySide6.QtPdf",
    "PySide6.QtBluetooth", "PySide6.QtSql", "PySide6.QtTest", "PySide6.QtDesigner", "PySide6.QtNetwork",
    "PySide6.QtOpenGL", "PySide6.QtOpenGLWidgets", "PySide6.QtSvg", "PySide6.QtPositioning",
    "PySide6.QtLocation", "PySide6.QtSerialPort", "PySide6.QtRemoteObjects", "PySide6.QtScxml",
    "PySide6.QtSensors", "PySide6.QtTextToSpeech", "PySide6.QtWebChannel", "PySide6.QtWebSockets",
]

a = Analysis(
    [os.path.join(ROOT, "SplitScope.py")],
    pathex=[ROOT],
    binaries=binaries,
    datas=datas,
    hiddenimports=["scipy.signal", "scipy.ndimage"],
    excludes=excludes,
    noarchive=False,
)

if IS_LINUX:
    # ALSA and JACK must come from the user's system: a bundled libasound
    # cannot find the distro's ALSA plugins (PipeWire/Pulse bridges).
    def _system_only(name):
        base = os.path.basename(name)
        return base.startswith(("libasound.so", "libjack.so"))

    a.binaries = [b for b in a.binaries if not _system_only(b[0])]

# Qt pieces a widgets app never loads. The virtual-keyboard input plugin drags
# in QML/Quick; the GTK3 theme plugin drags in a private copy of GTK that can
# clash with the desktop's own. Dropping them saves roughly 60 MB.
_DROP = ("virtualkeyboard", "qt6qml", "qt6quick", "qt6pdf", "qgtk3", "qpdf", "/qml/", "\\qml\\")


def _keep(entry):
    name = entry[0].replace("\\", "/").lower()
    return not any(k.replace("\\", "/") in name for k in _DROP)


a.binaries = [b for b in a.binaries if _keep(b)]
a.datas = [d for d in a.datas if _keep(d)]

pyz = PYZ(a.pure)

icon = None
if IS_WIN:
    icon = os.path.join(ROOT, "assets", "icon.ico")
elif IS_MAC:
    icon = os.path.join(ROOT, "assets", "icon.icns")

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="SplitScope",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    icon=icon,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="SplitScope",
)

if IS_MAC:
    from splitscope import __version__ as _ver  # noqa: E402

    app = BUNDLE(
        coll,
        name="SplitScope.app",
        icon=icon,
        bundle_identifier="io.github.nullangst.splitscope",
        info_plist={
            "CFBundleShortVersionString": _ver,
            "CFBundleVersion": _ver,
            "NSHighResolutionCapable": True,
            "LSMinimumSystemVersion": "11.0",
            "CFBundleDocumentTypes": [{
                "CFBundleTypeName": "Audio",
                "CFBundleTypeRole": "Viewer",
                "LSItemContentTypes": ["public.audio"],
            }],
        },
    )
