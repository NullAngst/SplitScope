"""Generate the SplitScope icon set (PNG, ICO, ICNS, SVG) from one definition.

Run from the repo root:  python tools/make_icon.py
Needs Pillow only.
"""
from __future__ import annotations

import colorsys
import math
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "assets"
RES = ROOT / "splitscope" / "resources"

SIZE = 1024
BG_TOP = (38, 43, 56)
BG_BOT = (21, 24, 31)
N_BARS = 11
GAP = 0.055          # vertical split between the upper and lower halves
SHIFT = 0.035        # upper half moves up, lower half down


def bar_geometry(n_bars: int = N_BARS):
    """Bar rectangles in unit coordinates (x0, y0, x1, y1, rgb)."""
    bars = []
    margin, span = 0.20, 0.60
    w = span / n_bars
    for i in range(n_bars):
        t = i / (n_bars - 1)
        # spectrum-like envelope: two humps, taller in the low mids
        h = 0.12 + 0.20 * math.exp(-((t - 0.28) / 0.20) ** 2) + 0.12 * math.exp(-((t - 0.72) / 0.14) ** 2)
        hue = 0.0 + 0.78 * t                      # red -> violet, matches the analyser prism
        r, g, b = colorsys.hsv_to_rgb(hue, 0.62, 1.0)
        rgb = (int(r * 255), int(g * 255), int(b * 255))
        inset = 0.17 if n_bars > 8 else 0.14
        x0 = margin + i * w + w * inset
        x1 = margin + (i + 1) * w - w * inset
        # upper half
        bars.append((x0, 0.5 - GAP / 2 - SHIFT - h, x1, 0.5 - GAP / 2 - SHIFT, rgb))
        # lower half, slightly shorter: the "separated" layer
        bars.append((x0, 0.5 + GAP / 2 + SHIFT, x1, 0.5 + GAP / 2 + SHIFT + h * 0.72, rgb))
    return bars


def render(size: int) -> Image.Image:
    ss = 4
    S = size * ss
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    # background gradient in a rounded square
    grad = Image.new("RGBA", (S, S))
    gd = ImageDraw.Draw(grad)
    for y in range(S):
        t = y / (S - 1)
        c = tuple(int(BG_TOP[k] + (BG_BOT[k] - BG_TOP[k]) * t) for k in range(3)) + (255,)
        gd.line([(0, y), (S, y)], fill=c)
    mask = Image.new("L", (S, S), 0)
    pad = int(S * 0.04)
    ImageDraw.Draw(mask).rounded_rectangle([pad, pad, S - pad, S - pad], radius=int(S * 0.2), fill=255)
    img.paste(grad, (0, 0), mask)
    d = ImageDraw.Draw(img)
    # thin split line
    small = size <= 48
    if not small:
        y = S * 0.5
        d.line([(S * 0.16, y), (S * 0.84, y)], fill=(122, 167, 255, 150), width=max(1, int(S * 0.006)))
    for x0, y0, x1, y1, rgb in bar_geometry(7 if small else N_BARS):
        rad = int((x1 - x0) * S * 0.46)
        d.rounded_rectangle([x0 * S, y0 * S, x1 * S, y1 * S], radius=rad, fill=rgb + (255,))
    return img.resize((size, size), Image.LANCZOS)


def svg() -> str:
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {SIZE} {SIZE}">',
             '<defs><linearGradient id="bg" x1="0" y1="0" x2="0" y2="1">'
             f'<stop offset="0" stop-color="rgb{BG_TOP}"/><stop offset="1" stop-color="rgb{BG_BOT}"/>'
             '</linearGradient></defs>',
             f'<rect x="{SIZE * 0.04:.1f}" y="{SIZE * 0.04:.1f}" width="{SIZE * 0.92:.1f}" height="{SIZE * 0.92:.1f}" '
             f'rx="{SIZE * 0.2:.1f}" fill="url(#bg)"/>',
             f'<line x1="{SIZE * 0.16:.1f}" y1="{SIZE / 2}" x2="{SIZE * 0.84:.1f}" y2="{SIZE / 2}" '
             f'stroke="#7aa7ff" stroke-opacity="0.6" stroke-width="{SIZE * 0.006:.1f}"/>']
    for x0, y0, x1, y1, rgb in bar_geometry():
        w = (x1 - x0) * SIZE
        parts.append(f'<rect x="{x0 * SIZE:.1f}" y="{y0 * SIZE:.1f}" width="{w:.1f}" height="{(y1 - y0) * SIZE:.1f}" '
                     f'rx="{w / 2:.1f}" fill="#{rgb[0]:02x}{rgb[1]:02x}{rgb[2]:02x}"/>')
    parts.append("</svg>")
    return "\n".join(parts) + "\n"


def main():
    OUT.mkdir(exist_ok=True)
    RES.mkdir(exist_ok=True)
    big = render(1024)
    big.save(OUT / "icon.png")
    render(256).save(RES / "icon.png")
    ico_sizes = [16, 24, 32, 48, 64, 128, 256]
    frames = [render(n) for n in ico_sizes]
    frames[-1].save(OUT / "icon.ico", sizes=[(n, n) for n in ico_sizes], append_images=frames[:-1])
    big.save(OUT / "icon.icns")
    (OUT / "icon.svg").write_text(svg())
    print("wrote", ", ".join(p.name for p in sorted(OUT.iterdir())))


if __name__ == "__main__":
    main()
