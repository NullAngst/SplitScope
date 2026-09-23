"""AI model registry and storage.

Models are looked up in (first match wins):
  1. <exe dir>/models         portable folder next to the executable
  2. bundled models           packed into the release by the GitHub build
  3. <user data dir>/models   where in-app downloads go

Downloads come from the Ultimate Vocal Remover public model repository on
GitHub. After a model exists locally nothing touches the network.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from .dsp.mdx import MDXParams, Cancelled

APP_NAME = "SplitScope"
UVR_URL = "https://github.com/TRvlvr/model_repo/releases/download/all_public_uvr_models/{file}"


@dataclass(frozen=True)
class ModelInfo:
    key: str
    title: str
    role: str             # vocals | karaoke | drums | bass | other | custom
    file: str
    size: int
    params: MDXParams
    secondary: str        # name for (mix - primary)
    description: str


REGISTRY: list[ModelInfo] = [
    ModelInfo("Kim_Vocal_2", "Kim Vocal 2", "vocals", "Kim_Vocal_2.onnx", 66759214,
              MDXParams(7680, 3072, 256, 1.009, "Vocals"), "Instrumental",
              "Default vocal/instrumental splitter. Strong on lead and backing vocals."),
    ModelInfo("UVR-MDX-NET-Voc_FT", "UVR MDX-Net Voc FT", "vocals", "UVR-MDX-NET-Voc_FT.onnx", 66762490,
              MDXParams(7680, 3072, 256, 1.021, "Vocals"), "Instrumental",
              "Alternative vocal model. Try it when Kim Vocal 2 leaves artifacts."),
    ModelInfo("UVR-MDX-NET-Inst_HQ_3", "UVR MDX-Net Inst HQ 3", "vocals", "UVR-MDX-NET-Inst_HQ_3.onnx", 66759214,
              MDXParams(6144, 3072, 256, 1.022, "Instrumental"), "Vocals",
              "Instrumental-focused model. Cleaner backing track, slightly more bleed in vocals."),
    ModelInfo("UVR_MDXNET_KARA_2", "UVR MDX-Net Karaoke 2", "karaoke", "UVR_MDXNET_KARA_2.onnx", 52786726,
              MDXParams(5120, 2048, 256, 1.065, "Backing vocals"), "Lead vocal",
              "Splits a vocals stem into lead vocal and backing vocals."),
    ModelInfo("kuielab_a_drums", "KUIELab MDX-Net Drums", "drums", "kuielab_a_drums.onnx", 29703204,
              MDXParams(4096, 2048, 512, 1.035, "Drums"), "No drums",
              "Drum stem from the full mix."),
    ModelInfo("kuielab_a_bass", "KUIELab MDX-Net Bass", "bass", "kuielab_a_bass.onnx", 29703204,
              MDXParams(16384, 2048, 512, 1.035, "Bass"), "No bass",
              "Bass stem from the full mix."),
    ModelInfo("kuielab_a_other", "KUIELab MDX-Net Other", "other", "kuielab_a_other.onnx", 29703204,
              MDXParams(8192, 2048, 512, 1.035, "Other (guitars/keys)"), "No other",
              "Everything that is not vocals, drums or bass: guitars, keys, synths, strings."),
]
BY_KEY = {m.key: m for m in REGISTRY}
ROLES = ["vocals", "karaoke", "drums", "bass", "other"]


def user_data_dir() -> Path:
    if sys.platform.startswith("win"):
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    d = base / APP_NAME
    d.mkdir(parents=True, exist_ok=True)
    return d


def user_models_dir() -> Path:
    d = user_data_dir() / "models"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _exe_dir() -> Path:
    if getattr(sys, "frozen", False):
        exe = Path(sys.executable).resolve().parent
        # macOS: SplitScope.app/Contents/MacOS -> folder containing the .app
        if sys.platform == "darwin" and exe.name == "MacOS":
            return exe.parent.parent.parent
        return exe
    return Path(__file__).resolve().parent.parent


def bundled_models_dir() -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base / "bundled_models"


def search_dirs() -> list[Path]:
    return [_exe_dir() / "models", bundled_models_dir(), user_models_dir()]


def find_model_file(filename: str) -> Optional[Path]:
    for d in search_dirs():
        p = d / filename
        if p.is_file() and p.stat().st_size > 1024:
            return p
    return None


def is_installed(m: ModelInfo) -> bool:
    return find_model_file(m.file) is not None


def location_label(m: ModelInfo) -> str:
    p = find_model_file(m.file)
    if p is None:
        return "Not installed"
    if p.parent == bundled_models_dir():
        return "Bundled"
    if p.parent == user_models_dir():
        return "Downloaded"
    return "Portable folder"


def download(m: ModelInfo, progress: Optional[Callable[[float], None]] = None,
             cancelled: Optional[Callable[[], bool]] = None) -> Path:
    dest = user_models_dir() / m.file
    tmp = dest.with_suffix(dest.suffix + ".part")
    url = UVR_URL.format(file=m.file)
    req = urllib.request.Request(url, headers={"User-Agent": f"{APP_NAME}"})
    with urllib.request.urlopen(req, timeout=60) as r, open(tmp, "wb") as fh:
        total = int(r.headers.get("Content-Length") or m.size or 0)
        done = 0
        while True:
            if cancelled is not None and cancelled():
                fh.close()
                tmp.unlink(missing_ok=True)
                raise Cancelled()
            chunk = r.read(1 << 16)
            if not chunk:
                break
            fh.write(chunk)
            done += len(chunk)
            if progress is not None and total:
                progress(min(1.0, done / total))
    if m.size and tmp.stat().st_size != m.size:
        got = tmp.stat().st_size
        tmp.unlink(missing_ok=True)
        raise IOError(f"Download of {m.file} is incomplete ({got} of {m.size} bytes)")
    os.replace(tmp, dest)
    return dest


def delete_download(m: ModelInfo) -> bool:
    p = user_models_dir() / m.file
    if p.is_file():
        p.unlink()
        return True
    return False


# ------------------------------------------------------------- custom models
def _custom_index_path() -> Path:
    return user_models_dir() / "custom_models.json"


def uvr_hash(path: str | Path) -> str:
    with open(path, "rb") as fh:
        try:
            fh.seek(-10000 * 1024, 2)
        except OSError:
            fh.seek(0)
        return hashlib.md5(fh.read()).hexdigest()


def lookup_uvr_params(path: str | Path) -> Optional[dict]:
    table_path = Path(__file__).resolve().parent / "resources" / "uvr_mdx_model_data.json"
    try:
        table = json.loads(table_path.read_text())
    except (OSError, ValueError):
        return None
    return table.get(uvr_hash(path))


def onnx_shape(path: str | Path) -> tuple[int, int]:
    import onnxruntime as ort

    s = ort.InferenceSession(str(path), providers=["CPUExecutionProvider"])
    shp = s.get_inputs()[0].shape
    return int(shp[2]), int(shp[3])


def load_custom_models() -> list[ModelInfo]:
    try:
        data = json.loads(_custom_index_path().read_text())
    except (OSError, ValueError):
        return []
    out = []
    for key, d in data.items():
        try:
            out.append(ModelInfo(
                key, d.get("title", key), "custom", d["file"], 0,
                MDXParams(int(d["n_fft"]), int(d["dim_f"]), int(d["dim_t"]), float(d.get("compensate", 1.0)),
                          d.get("primary", "Primary")),
                d.get("secondary", "Remainder"), "User-imported MDX-Net model."))
        except (KeyError, ValueError, TypeError):
            continue
    return out


def register_custom_model(src: str | Path, n_fft: int, primary: str, secondary: str,
                          compensate: float = 1.0) -> ModelInfo:
    src = Path(src)
    dim_f, dim_t = onnx_shape(src)
    dest = user_models_dir() / src.name
    if src.resolve() != dest.resolve():
        shutil.copy2(src, dest)
    key = "custom:" + src.stem
    try:
        data = json.loads(_custom_index_path().read_text())
    except (OSError, ValueError):
        data = {}
    data[key] = {"title": src.stem, "file": src.name, "n_fft": n_fft, "dim_f": dim_f, "dim_t": dim_t,
                 "compensate": compensate, "primary": primary, "secondary": secondary}
    _custom_index_path().write_text(json.dumps(data, indent=1))
    return [m for m in load_custom_models() if m.key == key][0]


def all_models() -> list[ModelInfo]:
    return REGISTRY + load_custom_models()


def get(key: str) -> Optional[ModelInfo]:
    for m in all_models():
        if m.key == key:
            return m
    return None


def first_installed(role: str, preferred: Optional[str] = None) -> Optional[ModelInfo]:
    if preferred:
        m = get(preferred)
        if m is not None and is_installed(m):
            return m
    for m in REGISTRY:
        if m.role == role and is_installed(m):
            return m
    return None


def remove_custom_model(key: str) -> None:
    try:
        data = json.loads(_custom_index_path().read_text())
    except (OSError, ValueError):
        return
    d = data.pop(key, None)
    _custom_index_path().write_text(json.dumps(data, indent=1))
    if d:
        p = user_models_dir() / d.get("file", "")
        if p.is_file() and not any(m.file == p.name for m in REGISTRY):
            p.unlink()


def models_for_role(role: str) -> list[ModelInfo]:
    """Installed models usable for a role (registry models of that role plus custom imports)."""
    return [m for m in all_models() if (m.role == role or m.role == "custom") and is_installed(m)]
