"""Download the models that get bundled into release builds.

Usage:  python tools/fetch_models.py DEST_DIR MODEL_KEY [MODEL_KEY ...]
Keys are the registry keys in splitscope/models.py, e.g. Kim_Vocal_2.
Sizes are checked against the registry so a truncated download fails the build.
"""
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from splitscope.models import BY_KEY, UVR_URL  # noqa: E402


def fetch(key: str, dest: Path) -> None:
    m = BY_KEY.get(key)
    if m is None:
        sys.exit(f"Unknown model key '{key}'. Known: {', '.join(BY_KEY)}")
    out = dest / m.file
    if out.is_file() and out.stat().st_size == m.size:
        print(f"{m.file}: already present")
        return
    url = UVR_URL.format(file=m.file)
    for attempt in range(1, 4):
        try:
            print(f"{m.file}: downloading ({m.size / 1e6:.0f} MB), attempt {attempt}")
            req = urllib.request.Request(url, headers={"User-Agent": "SplitScope-build"})
            tmp = out.with_suffix(".part")
            with urllib.request.urlopen(req, timeout=120) as r, open(tmp, "wb") as fh:
                while chunk := r.read(1 << 20):
                    fh.write(chunk)
            if tmp.stat().st_size != m.size:
                raise IOError(f"size {tmp.stat().st_size} != expected {m.size}")
            tmp.replace(out)
            return
        except Exception as e:
            print(f"  failed: {e}")
            time.sleep(5 * attempt)
    sys.exit(f"Could not download {m.file}")


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    dest = Path(sys.argv[1])
    dest.mkdir(parents=True, exist_ok=True)
    for key in sys.argv[2:]:
        fetch(key, dest)


if __name__ == "__main__":
    main()
