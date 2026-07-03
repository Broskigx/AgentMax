#!/usr/bin/env python3
"""Generate assets/icon.ico for AgentMax from scratch using Pillow."""

from __future__ import annotations

from pathlib import Path

try:
    from PIL import Image, ImageDraw
except ImportError:
    print("Pillow required: py -m pip install Pillow")
    raise

OUT = Path(__file__).resolve().parent.parent / "assets" / "icon.ico"
OUT.parent.mkdir(exist_ok=True)

PURPLE = (108, 99, 255, 255)
PURPLE2 = (74, 58, 210, 255)
BG_DARK = (15, 17, 23, 255)
WHITE = (255, 255, 255, 255)
WHITE_DIM = (220, 220, 255, 200)


def _rounded_rect(draw: ImageDraw.ImageDraw, xy: tuple, radius: int, fill: tuple) -> None:
    x0, y0, x1, y1 = xy
    draw.rectangle([x0 + radius, y0, x1 - radius, y1], fill=fill)
    draw.rectangle([x0, y0 + radius, x1, y1 - radius], fill=fill)
    draw.ellipse([x0, y0, x0 + 2 * radius, y0 + 2 * radius], fill=fill)
    draw.ellipse([x1 - 2 * radius, y0, x1, y0 + 2 * radius], fill=fill)
    draw.ellipse([x0, y1 - 2 * radius, x0 + 2 * radius, y1], fill=fill)
    draw.ellipse([x1 - 2 * radius, y1 - 2 * radius, x1, y1], fill=fill)


def _make_frame(size: int) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    pad = max(1, size // 12)
    r = max(2, size // 5)

    # ── Background rounded square ──────────────────────────────────────────
    _rounded_rect(draw, (pad, pad, size - pad, size - pad), r, PURPLE2)

    # Subtle inner glow (lighter purple rectangle, centered)
    gpad = size // 4
    draw.ellipse(
        [gpad, gpad // 2, size - gpad, size - gpad // 2],
        fill=(130, 120, 255, 60),
    )

    # ── Draw stylised "A" ─────────────────────────────────────────────────
    # We draw two diagonal strokes and one crossbar using polygons.
    cx = size / 2
    top = size * 0.18
    bot = size * 0.82
    sw = max(1, size * 0.10)  # stroke width
    half = size * 0.34  # half-width of the base

    def poly(pts: list) -> None:
        draw.polygon(pts, fill=WHITE)

    # Left leg
    poly(
        [
            (cx - half, bot),
            (cx - half + sw, bot),
            (cx + sw * 0.3, top),
            (cx - sw * 0.3, top),
        ]
    )

    # Right leg
    poly(
        [
            (cx + half, bot),
            (cx + half - sw, bot),
            (cx - sw * 0.3, top),
            (cx + sw * 0.3, top),
        ]
    )

    # Crossbar
    cross_y = size * 0.57
    cross_h = sw * 0.75
    cross_w = half * 0.68
    poly(
        [
            (cx - cross_w, cross_y - cross_h / 2),
            (cx + cross_w, cross_y - cross_h / 2),
            (cx + cross_w, cross_y + cross_h / 2),
            (cx - cross_w, cross_y + cross_h / 2),
        ]
    )

    return img


def build_ico() -> None:
    sizes = [16, 24, 32, 48, 64, 128, 256]
    frames = [_make_frame(s) for s in sizes]
    frames[0].save(
        str(OUT),
        format="ICO",
        sizes=[(s, s) for s in sizes],
        append_images=frames[1:],
    )
    print(f"Icon written -> {OUT}")


if __name__ == "__main__":
    build_ico()
