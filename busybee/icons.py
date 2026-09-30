"""Tray/window icons, drawn at runtime so no image files need to ship."""

from __future__ import annotations

import math
from functools import lru_cache

from PIL import Image, ImageDraw

from .tracker import State

HONEY = (245, 176, 20, 255)
GREY = (160, 160, 160, 255)
STRIPE = (45, 35, 20, 255)
GREEN = (46, 160, 67, 255)
ORANGE = (230, 110, 30, 255)
WHITE = (255, 255, 255, 255)

_SUPERSAMPLE = 4


def _hexagon(cx: float, cy: float, r: float) -> list[tuple[float, float]]:
    return [(cx + r * math.cos(math.radians(60 * i - 30)), cy + r * math.sin(math.radians(60 * i - 30))) for i in range(6)]


def _bee_hexagon(s: int, color: tuple[int, int, int, int]) -> Image.Image:
    mask = Image.new("L", (s, s), 0)
    ImageDraw.Draw(mask).polygon(_hexagon(s / 2, s / 2, s * 0.49), fill=255)

    body = Image.new("RGBA", (s, s), color)
    stripes = ImageDraw.Draw(body)
    for top in (0.30, 0.56):  # two bee stripes
        stripes.rectangle([0, s * top, s, s * (top + 0.13)], fill=STRIPE)

    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    img.paste(body, (0, 0), mask)
    return img


@lru_cache(maxsize=None)
def make_app_icon(size: int = 256) -> Image.Image:
    """Badge-free honey bee used for the window and taskbar button."""
    s = size * _SUPERSAMPLE
    return _bee_hexagon(s, HONEY).resize((size, size), Image.LANCZOS)


@lru_cache(maxsize=None)
def make_icon(state: State, size: int = 64) -> Image.Image:
    s = size * _SUPERSAMPLE
    img = _bee_hexagon(s, GREY if state is State.IDLE else HONEY)

    if state is not State.IDLE:
        draw = ImageDraw.Draw(img)
        r = s * 0.25
        cx, cy = s - r - s * 0.01, s - r - s * 0.01
        draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=GREEN if state is State.RUNNING else ORANGE, outline=WHITE, width=int(s * 0.03))
        if state is State.RUNNING:
            t = r * 0.5
            draw.polygon([(cx - t * 0.7, cy - t), (cx - t * 0.7, cy + t), (cx + t, cy)], fill=WHITE)
        else:
            w, h = r * 0.22, r * 0.5
            for dx in (-r * 0.25, r * 0.25):
                draw.rectangle([cx + dx - w / 2, cy - h, cx + dx + w / 2, cy + h], fill=WHITE)

    return img.resize((size, size), Image.LANCZOS)
