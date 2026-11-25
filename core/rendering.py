"""Shared PIL font loading and text rendering utilities for Board Games and Life Sim cards."""

import threading
from functools import lru_cache
from PIL import ImageDraw, ImageFont

_FONT_LOCK = threading.Lock()

_LATIN_BOLD_CANDIDATES = (
    "C:/Windows/Fonts/segoeuib.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
    "C:/Windows/Fonts/arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
)

_CJK_BOLD_CANDIDATES = (
    "C:/Windows/Fonts/msjhbd.ttc",
    "C:/Windows/Fonts/msjh.ttc",
    "C:/Windows/Fonts/NotoSansTC-VF.ttf",
    "C:/Windows/Fonts/segoeuib.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
)

_CJK_REGULAR_CANDIDATES = (
    "C:/Windows/Fonts/msjh.ttc",
    "C:/Windows/Fonts/NotoSansTC-VF.ttf",
    "C:/Windows/Fonts/segoeuib.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
)


@lru_cache(maxsize=64)
def _load_font_cached(size: int, bold: bool = False, prefer_cjk: bool = False) -> ImageFont.ImageFont:
    if prefer_cjk:
        candidates = _CJK_BOLD_CANDIDATES if bold else _CJK_REGULAR_CANDIDATES
    else:
        candidates = _LATIN_BOLD_CANDIDATES
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            continue
    return ImageFont.load_default()


def load_font(size: int, bold: bool = False, prefer_cjk: bool = False) -> ImageFont.ImageFont:
    """Load an available TrueType font cleanly with cross-platform fallback and caching."""
    with _FONT_LOCK:
        return _load_font_cached(size, bold, prefer_cjk)


load_font.cache_clear = _load_font_cached.cache_clear
load_font.cache_info = _load_font_cached.cache_info


def draw_centered_text(draw: ImageDraw.ImageDraw, cx: float, cy: float, text: str, font, fill) -> None:
    """Draw text visually centered on (cx, cy)."""
    with _FONT_LOCK:
        try:
            bbox = draw.textbbox((0, 0), text, font=font)
            tw = bbox[2] - bbox[0]
            th = bbox[3] - bbox[1]
            draw.text((cx - tw / 2 - bbox[0], cy - th / 2 - bbox[1]), text, font=font, fill=fill)
        except Exception:
            draw.text((cx - len(text) * 4, cy - 6), text, font=font, fill=fill)
