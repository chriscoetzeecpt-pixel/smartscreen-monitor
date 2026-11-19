"""Drawing helpers, theme and fonts shared by all pages (Pillow only)."""
from __future__ import annotations

import logging
import sys
from functools import lru_cache
from pathlib import Path

from PIL import ImageDraw, ImageFont

log = logging.getLogger(__name__)

FONT_DIR = Path(__file__).resolve().parent.parent / "fonts"

# High-contrast dark theme
BG = (8, 10, 14)
CARD = (20, 24, 32)
CARD_EDGE = (36, 42, 54)
TEXT = (240, 243, 248)
MUTED = (140, 150, 166)
DIM = (80, 88, 102)
BAR_BG = (40, 46, 58)
CPU_ACCENT = (0, 184, 255)
GPU_ACCENT = (118, 200, 40)
DISK_ACCENT = (255, 176, 32)
WEATHER_ACCENT = (255, 205, 60)
POSTS_ACCENT = (190, 140, 255)
RAM_ACCENT = (0, 200, 180)
OK = (80, 220, 120)
WARN = (255, 180, 40)
HOT = (255, 72, 72)

_WEIGHTS = {
    "regular": ("Barlow-Regular.ttf", ["segoeui.ttf", "arial.ttf", "DejaVuSans.ttf"]),
    "medium": ("Barlow-Medium.ttf", ["segoeui.ttf", "arial.ttf", "DejaVuSans.ttf"]),
    "semibold": ("Barlow-SemiBold.ttf", ["seguisb.ttf", "arialbd.ttf", "DejaVuSans-Bold.ttf"]),
    "bold": ("Barlow-Bold.ttf", ["segoeuib.ttf", "arialbd.ttf", "DejaVuSans-Bold.ttf"]),
}


@lru_cache(maxsize=64)
def font(size: int, weight: str = "regular") -> ImageFont.FreeTypeFont:
    """Bundled Barlow (SIL OFL) first, then safe Windows fonts, then Pillow's default."""
    bundled, fallbacks = _WEIGHTS.get(weight, _WEIGHTS["regular"])
    candidates = [FONT_DIR / bundled]
    if sys.platform == "win32":
        candidates += [Path(r"C:\Windows\Fonts") / f for f in fallbacks]
    candidates += [Path(f) for f in fallbacks]
    for c in candidates:
        try:
            return ImageFont.truetype(str(c), size)
        except OSError:
            continue
    log.warning("No TrueType font found, using Pillow default")
    return ImageFont.load_default(size)


def text_w(draw: ImageDraw.ImageDraw, s: str, f) -> int:
    return int(draw.textlength(s, font=f))


def text(draw, xy, s, f, fill=TEXT, anchor="la"):
    draw.text(xy, s, font=f, fill=fill, anchor=anchor)


def card(draw, rect, title=None, accent=TEXT, subtitle=None, radius=10):
    """Rounded panel with an accent strip and optional title/subtitle. Returns inner top y."""
    x0, y0, x1, y1 = rect
    draw.rounded_rectangle(rect, radius=radius, fill=CARD, outline=CARD_EDGE, width=1)
    draw.rounded_rectangle((x0, y0 + 10, x0 + 3, y1 - 10), radius=2, fill=accent)
    if title:
        text(draw, (x0 + 14, y0 + 8), title, font(17, "bold"), accent)
        if subtitle:
            tw = text_w(draw, title, font(17, "bold"))
            sub = fit(draw, subtitle, font(14, "medium"), x1 - (x0 + 14 + tw + 8) - 10)
            text(draw, (x0 + 14 + tw + 8, y0 + 11), sub, font(14, "medium"), MUTED)
        return y0 + 32
    return y0 + 8


def bar(draw, rect, frac, color, bg=BAR_BG):
    x0, y0, x1, y1 = rect
    r = (y1 - y0) // 2
    draw.rounded_rectangle(rect, radius=r, fill=bg)
    if frac is None:
        return
    frac = max(0.0, min(1.0, frac))
    if frac > 0:
        w = max(y1 - y0, int((x1 - x0) * frac))
        draw.rounded_rectangle((x0, y0, x0 + w, y1), radius=r, fill=color)


def fit(draw, s: str, f, max_w: int) -> str:
    """Truncate with an ellipsis so s fits in max_w pixels."""
    if max_w <= 0:
        return ""
    if text_w(draw, s, f) <= max_w:
        return s
    while s and text_w(draw, s + "...", f) > max_w:
        s = s[:-1]
    return s.rstrip() + "..."


def wrap(draw, s: str, f, max_w: int, max_lines: int) -> list[str]:
    """Greedy word wrap, last line ellipsised if the text does not fit."""
    words = s.split()
    lines, cur = [], ""
    for i, w in enumerate(words):
        cand = (cur + " " + w).strip()
        if text_w(draw, cand, f) <= max_w:
            cur = cand
            continue
        if cur:
            lines.append(cur)
        cur = w
        if len(lines) == max_lines:
            break
    if cur and len(lines) < max_lines:
        lines.append(cur)
    consumed = " ".join(lines).split()
    if len(consumed) < len(words) and lines:
        lines[-1] = fit(draw, lines[-1] + " " + " ".join(words[len(consumed):]), f, max_w)
    return [fit(draw, ln, f, max_w) for ln in lines[:max_lines]]


def temp_color(t: float | None, warn=70, hot=85):
    if t is None:
        return DIM
    return OK if t < warn else WARN if t < hot else HOT


def load_color(p: float | None):
    if p is None:
        return DIM
    return OK if p < 60 else WARN if p < 90 else HOT


def fmt_pct(v):
    return "n/a" if v is None else f"{v:.0f}%"


def fmt_temp(v):
    return "n/a" if v is None else f"{v:.0f}°C"


def fmt_rate(mb_s):
    """MB/s with sensible precision; switches to GB/s above 1000 MB/s (decimal units)."""
    if mb_s is None:
        return "n/a"
    if mb_s >= 1000:
        return f"{mb_s / 1000:.2f} GB/s"
    if mb_s >= 10:
        return f"{mb_s:.0f} MB/s"
    return f"{mb_s:.1f} MB/s"
