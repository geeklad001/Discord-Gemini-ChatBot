"""PIL visual PNG card renderer for TRPG Character Sheets and Party Dashboards."""

from __future__ import annotations

import io
import re
from typing import Optional

from PIL import Image, ImageDraw

from core.rendering import _FONT_LOCK, draw_centered_text, load_font

_PIL_EMOJI_STRIP_RE = re.compile(
    r"[\U0001F1E0-\U0001FAFF\u2600-\u27BF\uFE0F\u200D]+"
)

_EQUIP_IGNORE_KEYS: frozenset[str] = frozenset({
    "武器", "防具", "道具", "裝備", "隨身裝備", "隨身物品",
})

_THEMES: dict[str, dict] = {
    "xianxia": {
        "id": "xianxia",
        "label": "仙俠修真 // XIANXIA",
        "bg_top": (16, 28, 34),
        "bg_bot": (10, 18, 24),
        "card_bg": (22, 38, 44),
        "border": (82, 196, 172),
        "accent": (236, 194, 98),
        "text_primary": (238, 246, 242),
        "text_muted": (154, 186, 178),
        "bar_fill": (72, 209, 176),
    },
    "cyberpunk": {
        "id": "cyberpunk",
        "label": "賽博龐克 // CYBERPUNK",
        "bg_top": (18, 12, 34),
        "bg_bot": (10, 10, 22),
        "card_bg": (26, 20, 48),
        "border": (0, 229, 255),
        "accent": (255, 42, 133),
        "text_primary": (240, 246, 255),
        "text_muted": (156, 176, 214),
        "bar_fill": (0, 229, 255),
    },
    "coc": {
        "id": "coc",
        "label": "克蘇魯神話 // CTHULHU",
        "bg_top": (18, 24, 22),
        "bg_bot": (11, 14, 14),
        "card_bg": (26, 34, 30),
        "border": (118, 152, 114),
        "accent": (198, 168, 96),
        "text_primary": (232, 236, 226),
        "text_muted": (156, 172, 154),
        "bar_fill": (116, 184, 132),
    },
    "fantasy": {
        "id": "fantasy",
        "label": "奇幻冒險 // FANTASY",
        "bg_top": (22, 24, 38),
        "bg_bot": (14, 16, 26),
        "card_bg": (32, 36, 54),
        "border": (148, 118, 234),
        "accent": (246, 196, 88),
        "text_primary": (242, 244, 252),
        "text_muted": (166, 176, 206),
        "bar_fill": (87, 216, 142),
    },
}

_AVATAR_PALETTE: tuple[tuple[int, int, int], ...] = (
    (74, 105, 189),
    (130, 88, 159),
    (44, 130, 118),
    (179, 98, 72),
    (84, 128, 74),
    (164, 122, 54),
)


def _pil_safe_text(text: object) -> str:
    """移除無法由標準 CJK TTF 字型彩繪的 Emoji，避免圖卡出現方塊亂碼。"""
    cleaned = _PIL_EMOJI_STRIP_RE.sub("", str(text or "")).strip()
    return re.sub(r"\s{2,}", " ", cleaned)


def _load_cjk_font(size: int, bold: bool = False):
    return load_font(size, bold=bold, prefer_cjk=True)


def _measure_width(draw: ImageDraw.ImageDraw, text: str, font) -> float:
    with _FONT_LOCK:
        bbox = draw.textbbox((0, 0), text, font=font)
        return float(bbox[2] - bbox[0])


def _draw_text(draw: ImageDraw.ImageDraw, xy: tuple[float, float], text: str, font, fill) -> None:
    with _FONT_LOCK:
        draw.text(xy, text, font=font, fill=fill)


def _clip_text(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> str:
    safe = _pil_safe_text(text)
    if not safe:
        return ""
    if _measure_width(draw, safe, font) <= max_width:
        return safe
    lo, hi = 0, len(safe)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        candidate = safe[:mid].rstrip() + "…"
        if _measure_width(draw, candidate, font) <= max_width:
            lo = mid
        else:
            hi = mid - 1
    return (safe[:lo].rstrip() + "…") if lo > 0 else "…"


def _draw_gradient_bg(
    draw: ImageDraw.ImageDraw,
    w: int,
    h: int,
    top_col: tuple[int, int, int],
    bot_col: tuple[int, int, int],
) -> None:
    step = 4
    denom = max(1, h - 1)
    for y in range(0, h, step):
        t = min(1.0, y / denom)
        col = tuple(int(top_col[i] + (bot_col[i] - top_col[i]) * t) for i in range(3))
        draw.rectangle([0, y, w, min(h, y + step)], fill=col)


def _extract_san_value(char: dict) -> Optional[str]:
    prof = char.get("profile") or {}
    traits = prof.get("custom_traits") or {}
    for k, v in traits.items():
        k_low = str(k or "").lower()
        if "理智" in k_low or re.search(r"(?<![a-z])san(?![a-z])", k_low):
            val = _pil_safe_text(v)
            if val:
                return val
    return None


def _hp_color(ratio: float, default_col: tuple[int, int, int]) -> tuple[int, int, int]:
    if ratio <= 0.25:
        return (235, 77, 75)
    if ratio <= 0.50:
        return (246, 185, 59)
    return default_col


def _draw_avatar_circle(
    draw: ImageDraw.ImageDraw,
    cx: float,
    cy: float,
    radius: int,
    char_name: str,
    fill_col: tuple[int, int, int],
    outline_col: tuple[int, int, int],
    text_col: tuple[int, int, int],
    font,
) -> None:
    draw.ellipse(
        [cx - radius, cy - radius, cx + radius, cy + radius],
        fill=fill_col,
        outline=outline_col,
        width=3,
    )
    safe_name = _pil_safe_text(char_name)
    initial = safe_name[0].upper() if safe_name else "?"
    draw_centered_text(draw, cx, cy, initial, font, text_col)


def _draw_pill(
    draw: ImageDraw.ImageDraw,
    x: int,
    y: int,
    text: str,
    font,
    fill_bg: tuple[int, int, int],
    outline_col: tuple[int, int, int],
    text_col: tuple[int, int, int],
    height: int = 26,
    pad_x: int = 12,
) -> int:
    safe = _pil_safe_text(text)
    if not safe:
        return 0
    tw = _measure_width(draw, safe, font)
    pw = int(tw + pad_x * 2)
    draw.rounded_rectangle(
        [x, y, x + pw, y + height],
        radius=height // 2,
        fill=fill_bg,
        outline=outline_col,
        width=1,
    )
    draw_centered_text(draw, x + pw / 2, y + height / 2, safe, font, text_col)
    return pw


def _draw_progress_bar(
    draw: ImageDraw.ImageDraw,
    x: int,
    y: int,
    w: int,
    h: int,
    ratio: float,
    track_col: tuple[int, int, int],
    fill_col: tuple[int, int, int],
) -> None:
    clamped = max(0.0, min(1.0, float(ratio)))
    rad = max(3, h // 2)
    draw.rounded_rectangle([x, y, x + w, y + h], radius=rad, fill=track_col)
    if clamped > 0.0:
        fill_w = max(h, int(w * clamped))
        fill_w = min(w, fill_w)
        draw.rounded_rectangle([x, y, x + fill_w, y + h], radius=rad, fill=fill_col)


def _draw_wrapped_pills(
    draw: ImageDraw.ImageDraw,
    x: int,
    y: int,
    max_x: int,
    items: list[str],
    font,
    fill_bg: tuple[int, int, int],
    outline_col: tuple[int, int, int],
    text_col: tuple[int, int, int],
    max_rows: int = 2,
    height: int = 26,
    gap_x: int = 8,
    gap_y: int = 8,
) -> int:
    """Draw wrapped pill badges and return bottom y coordinate."""
    cx, cy = x, y
    row = 1
    shown = 0
    clean_items = [_pil_safe_text(it) for it in items if _pil_safe_text(it)]
    for idx, txt in enumerate(clean_items):
        clipped = _clip_text(draw, txt, font, max_width=min(240, max_x - x - 20))
        tw = _measure_width(draw, clipped, font)
        pw = int(tw + 22)
        if cx + pw > max_x:
            if row >= max_rows:
                remaining = len(clean_items) - idx
                if remaining > 0:
                    more_txt = f"+{remaining}"
                    mw = int(_measure_width(draw, more_txt, font) + 20)
                    if cx + mw <= max_x:
                        _draw_pill(draw, cx, cy, more_txt, font, fill_bg, outline_col, text_col, height=height, pad_x=10)
                break
            row += 1
            cx = x
            cy += height + gap_y
        _draw_pill(draw, cx, cy, clipped, font, fill_bg, outline_col, text_col, height=height, pad_x=11)
        cx += pw + gap_x
        shown += 1
    return cy + height if shown > 0 else y


def detect_rpg_theme(char: dict) -> dict:
    """根據角色職業、特質、背景與裝備自動辨識視覺主題（修仙 / 賽博龐克 / 克蘇魯 / 奇幻）。"""
    c = char or {}
    prof = c.get("profile") or {}
    traits = prof.get("custom_traits") or {}
    parts = [
        str(c.get("char_class") or ""),
        str(c.get("location") or ""),
        str(c.get("current_quest") or ""),
        " ".join(str(x) for x in (c.get("inventory") or [])),
        str(prof.get("race") or ""),
        str(prof.get("alignment") or ""),
        str(prof.get("background") or ""),
        str(prof.get("backstory") or ""),
        " ".join(str(x) for x in (prof.get("skills") or [])),
        " ".join(f"{k} {v}" for k, v in traits.items()),
    ]
    blob = " ".join(parts)
    blob_low = blob.lower()

    if any(kw in blob_low for kw in ("修仙", "靈根", "宗門", "境界", "法寶", "修真", "道號", "築基", "金丹", "元嬰", "仙俠", "劍修", "煉氣")):
        return dict(_THEMES["xianxia"])

    if any(kw in blob_low for kw in ("賽博", "義體", "駭客", "霓虹", "cyberpunk", "netrunner", "夜之城", "電子腦", "龐克")):
        return dict(_THEMES["cyberpunk"])

    if (
        any(kw in blob_low for kw in ("克蘇魯", "理智", "調查員", "cthulhu"))
        or re.search(r"(?<![a-z])(?:san|coc)(?![a-z])", blob_low) is not None
    ):
        return dict(_THEMES["coc"])

    return dict(_THEMES["fantasy"])


def render_party_dashboard_png(chars: list[dict], notes_count: int = 0) -> bytes:
    """渲染 900px 寬度高質感小隊狀態總覽儀表板 PNG（最多顯示 6 名成員）。"""
    w = 900
    chars_list = list(chars or [])
    shown = chars_list[:6]
    theme = detect_rpg_theme(shown[0] if shown else {})

    header_y = 18
    header_h = 118
    list_top = header_y + header_h + 14
    card_h = 108
    card_gap = 12
    body_h = max(108, len(shown) * card_h + max(0, len(shown) - 1) * card_gap)
    footer_h = 44
    h = list_top + body_h + footer_h

    img = Image.new("RGB", (w, h), theme["bg_bot"])
    draw = ImageDraw.Draw(img)
    _draw_gradient_bg(draw, w, h, theme["bg_top"], theme["bg_bot"])

    # Outer frame
    draw.rounded_rectangle([12, 12, w - 12, h - 12], radius=16, fill=None, outline=theme["border"], width=2)

    # Fonts
    f_title = _load_cjk_font(23, bold=True)
    f_hdr = _load_cjk_font(19, bold=True)
    f_body = _load_cjk_font(15, bold=False)
    f_small = _load_cjk_font(13, bold=False)
    f_badge = _load_cjk_font(12, bold=True)
    f_avatar = _load_cjk_font(22, bold=True)

    # Header card
    draw.rounded_rectangle(
        [24, header_y, w - 24, header_y + header_h],
        radius=14,
        fill=theme["card_bg"],
        outline=theme["border"],
        width=2,
    )
    draw.rounded_rectangle([24, header_y, w - 24, header_y + 10], radius=14, fill=theme["accent"])
    draw.rectangle([24, header_y + 5, w - 24, header_y + 12], fill=theme["accent"])

    _draw_text(draw, (42, header_y + 20), "PARTY DASHBOARD // 冒險小隊總覽", f_title, theme["text_primary"])

    # Header right pills
    pill_theme_w = int(_measure_width(draw, _pil_safe_text(theme["label"]), f_badge) + 24)
    pill_notes_txt = f"手記 {int(notes_count)} 條"
    pill_notes_w = int(_measure_width(draw, pill_notes_txt, f_badge) + 24)
    pill_cnt_txt = f"隊員 {len(chars_list)} 人"
    pill_cnt_w = int(_measure_width(draw, pill_cnt_txt, f_badge) + 24)

    px = w - 40 - pill_theme_w
    _draw_pill(draw, px, header_y + 22, theme["label"], f_badge, theme["bg_bot"], theme["border"], theme["accent"])
    px -= pill_notes_w + 8
    _draw_pill(draw, px, header_y + 22, pill_notes_txt, f_badge, theme["bg_top"], theme["accent"], theme["text_primary"])
    px -= pill_cnt_w + 8
    _draw_pill(draw, px, header_y + 22, pill_cnt_txt, f_badge, theme["bg_top"], theme["border"], theme["text_primary"])

    loc_raw = shown[0].get("location", "未知地點") if shown else "冒險者酒館"
    quest_raw = shown[0].get("current_quest", "自由探索") if shown else "展開新冒險"
    loc_line = _clip_text(draw, f"當前地點：{loc_raw}", f_body, max_width=w - 92)
    quest_line = _clip_text(draw, f"當前目標：{quest_raw}", f_body, max_width=w - 92)
    _draw_text(draw, (42, header_y + 58), loc_line, f_body, theme["text_primary"])
    _draw_text(draw, (42, header_y + 84), quest_line, f_body, theme["text_muted"])

    if not shown:
        draw.rounded_rectangle(
            [24, list_top, w - 24, list_top + 96],
            radius=12,
            fill=theme["card_bg"],
            outline=theme["border"],
            width=1,
        )
        draw_centered_text(draw, w / 2, list_top + 48, "目前尚無已入隊的冒險者（輸入 !rpg join 加入小隊）", f_body, theme["text_muted"])
    else:
        for idx, c in enumerate(shown):
            cy = list_top + idx * (card_h + card_gap)
            draw.rounded_rectangle(
                [24, cy, w - 24, cy + card_h],
                radius=12,
                fill=theme["card_bg"],
                outline=theme["border"],
                width=1,
            )

            av_col = _AVATAR_PALETTE[idx % len(_AVATAR_PALETTE)]
            char_name = _pil_safe_text(c.get("char_name") or "冒險者") or "冒險者"
            user_name = _pil_safe_text(c.get("user_name") or "")
            char_class = _pil_safe_text(c.get("char_class") or "旅者")
            _draw_avatar_circle(
                draw,
                68,
                cy + card_h / 2,
                27,
                char_name,
                fill_col=av_col,
                outline_col=theme["accent"],
                text_col=(255, 255, 255),
                font=f_avatar,
            )

            # Right badges (AC / SAN)
            prof = c.get("profile") or {}
            ac = prof.get("ac")
            san_val = _extract_san_value(c)
            right_x = w - 40
            if san_val is not None:
                san_txt = f"SAN {san_val}"[:16]
                sw = int(_measure_width(draw, san_txt, f_badge) + 22)
                right_x -= sw
                _draw_pill(draw, right_x, cy + 14, san_txt, f_badge, (38, 30, 56), (186, 134, 240), (236, 214, 255), height=24, pad_x=11)
                right_x -= 8
            if ac is not None:
                ac_txt = f"AC {ac}"
                aw = int(_measure_width(draw, ac_txt, f_badge) + 22)
                right_x -= aw
                _draw_pill(draw, right_x, cy + 14, ac_txt, f_badge, (30, 44, 62), (96, 178, 246), (218, 240, 255), height=24, pad_x=11)
                right_x -= 8

            # Row 1: Character Name + Player Name + Class Pill
            title_max_w = max(180, right_x - 114)
            display_name = _clip_text(draw, char_name, f_hdr, max_width=min(250, title_max_w))
            _draw_text(draw, (112, cy + 13), display_name, f_hdr, theme["text_primary"])
            name_w = int(_measure_width(draw, display_name, f_hdr))

            cur_x = 112 + name_w + 10
            if user_name and cur_x + 70 < right_x:
                uname_txt = _clip_text(draw, f"({user_name})", f_small, max_width=min(160, right_x - cur_x - 10))
                _draw_text(draw, (cur_x, cy + 18), uname_txt, f_small, theme["text_muted"])
                cur_x += int(_measure_width(draw, uname_txt, f_small)) + 10

            if char_class and cur_x + 64 < right_x:
                cls_txt = _clip_text(draw, char_class, f_badge, max_width=max(40, right_x - cur_x - 24))
                _draw_pill(draw, cur_x, cy + 14, cls_txt, f_badge, theme["bg_bot"], theme["border"], theme["accent"], height=24, pad_x=10)

            # Row 2: HP progress bar
            hp = int(c.get("hp", 100) or 0)
            max_hp = max(1, int(c.get("max_hp", 100) or 100))
            hp_ratio = max(0.0, min(1.0, hp / max_hp))
            hp_col = _hp_color(hp_ratio, theme["bar_fill"])
            hp_label = f"HP {hp}/{max_hp}"
            _draw_text(draw, (112, cy + 47), hp_label, f_small, theme["text_primary"])
            _draw_progress_bar(draw, 224, cy + 49, 280, 14, hp_ratio, (42, 48, 66), hp_col)
            pct_txt = f"{int(round(hp_ratio * 100))}%"
            _draw_text(draw, (514, cy + 47), pct_txt, f_small, hp_col)

            # Row 3: Inventory preview
            inv_list = [_pil_safe_text(it) for it in (c.get("inventory") or []) if _pil_safe_text(it)]
            inv_raw = "、".join(inv_list) if inv_list else "（無隨身物品）"
            inv_line = _clip_text(draw, f"持有：{inv_raw}", f_small, max_width=w - 150)
            _draw_text(draw, (112, cy + 75), inv_line, f_small, theme["text_muted"])

    # Footer
    if len(chars_list) > 6:
        footer_txt = f"另有 {len(chars_list) - 6} 位冒險者未展開顯示（完整名單請見文字 Embed）"
    else:
        footer_txt = "TIP: 輸入 !sheet 檢視詳細角色圖卡 | !note 記錄線索 | !save <名> 存檔"
    draw_centered_text(draw, w / 2, h - 24, footer_txt, f_small, theme["text_muted"])

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def render_character_sheet_png(char: dict) -> bytes:
    """渲染 900px 寬度自適應世界觀角色卡 PNG（D&D 屬性條 / 非 D&D 特質網格 / 技能與裝備標籤）。"""
    c = char or {}
    theme = detect_rpg_theme(c)
    prof = c.get("profile") or {}
    stats_raw = prof.get("stats") or {}
    stats = {str(k).strip().upper(): v for k, v in stats_raw.items()}
    skills = [_pil_safe_text(s) for s in (prof.get("skills") or []) if _pil_safe_text(s)]
    inv = [_pil_safe_text(it) for it in (c.get("inventory") or []) if _pil_safe_text(it)]
    custom_traits = {
        _pil_safe_text(k): _pil_safe_text(v)
        for k, v in (prof.get("custom_traits") or {}).items()
        if str(k) not in _EQUIP_IGNORE_KEYS and _pil_safe_text(k) and _pil_safe_text(v)
    }
    backstory = _pil_safe_text(prof.get("backstory") or "")

    has_stats = bool(stats)
    trait_items = list(custom_traits.items())[:8]
    has_traits = bool(trait_items)

    # Pre-calculate vertical layout heights
    w = 900
    header_y = 18
    header_h = 124
    status_y = header_y + header_h + 12
    status_h = 92
    cur_y = status_y + status_h + 12

    stats_box_h = 186 if has_stats else 0
    traits_rows = (len(trait_items) + 1) // 2 if has_traits else 0
    traits_box_h = (48 + traits_rows * 44) if has_traits else 0
    narrative_box_h = 84 if (not has_stats and not has_traits) else 0

    if stats_box_h:
        stats_y = cur_y
        cur_y += stats_box_h + 12
    else:
        stats_y = 0

    if traits_box_h:
        traits_y = cur_y
        cur_y += traits_box_h + 12
    else:
        traits_y = 0

    if narrative_box_h:
        narr_y = cur_y
        cur_y += narrative_box_h + 12
    else:
        narr_y = 0

    skills_rows_est = 2 if len(skills) >= 4 else 1
    inv_rows_est = 2 if len(inv) >= 4 else 1
    gear_y = cur_y
    gear_box_h = 84 + skills_rows_est * 34 + inv_rows_est * 34
    cur_y += gear_box_h + 12

    backstory_y = 0
    backstory_h = 0
    if backstory and (has_stats or has_traits):
        backstory_y = cur_y
        backstory_h = 68
        cur_y += backstory_h + 12

    h = cur_y + 34

    img = Image.new("RGB", (w, h), theme["bg_bot"])
    draw = ImageDraw.Draw(img)
    _draw_gradient_bg(draw, w, h, theme["bg_top"], theme["bg_bot"])
    draw.rounded_rectangle([12, 12, w - 12, h - 12], radius=16, fill=None, outline=theme["border"], width=2)

    f_name = _load_cjk_font(27, bold=True)
    f_hdr = _load_cjk_font(17, bold=True)
    f_body = _load_cjk_font(15, bold=False)
    f_small = _load_cjk_font(13, bold=False)
    f_badge = _load_cjk_font(12, bold=True)
    f_avatar = _load_cjk_font(28, bold=True)

    # 1. Header Banner
    draw.rounded_rectangle(
        [24, header_y, w - 24, header_y + header_h],
        radius=14,
        fill=theme["card_bg"],
        outline=theme["border"],
        width=2,
    )
    draw.rounded_rectangle([24, header_y, w - 24, header_y + 10], radius=14, fill=theme["accent"])
    draw.rectangle([24, header_y + 5, w - 24, header_y + 12], fill=theme["accent"])

    char_name = _pil_safe_text(c.get("char_name") or "冒險者") or "冒險者"
    user_name = _pil_safe_text(c.get("user_name") or "")
    _draw_avatar_circle(
        draw,
        78,
        header_y + 68,
        36,
        char_name,
        fill_col=theme["bg_bot"],
        outline_col=theme["accent"],
        text_col=theme["accent"],
        font=f_avatar,
    )

    theme_lbl_w = int(_measure_width(draw, _pil_safe_text(theme["label"]), f_badge) + 24)
    _draw_pill(
        draw,
        w - 40 - theme_lbl_w,
        header_y + 24,
        theme["label"],
        f_badge,
        theme["bg_bot"],
        theme["border"],
        theme["accent"],
    )

    title_limit = w - 190 - theme_lbl_w
    clipped_name = _clip_text(draw, char_name, f_name, max_width=max(180, title_limit))
    _draw_text(draw, (132, header_y + 22), clipped_name, f_name, theme["text_primary"])
    name_w = int(_measure_width(draw, clipped_name, f_name))
    if user_name:
        player_lbl = _clip_text(draw, f"玩家：{user_name}", f_small, max_width=max(80, title_limit - name_w + 60))
        if 132 + name_w + 14 + _measure_width(draw, player_lbl, f_small) < w - 50 - theme_lbl_w:
            _draw_text(draw, (132 + name_w + 14, header_y + 32), player_lbl, f_small, theme["text_muted"])

    banner_parts = [f"定位：{c.get('char_class') or '冒險者'}"]
    if prof.get("race"):
        banner_parts.append(f"種族/靈根：{prof['race']}")
    if prof.get("alignment"):
        banner_parts.append(f"陣營：{prof['alignment']}")
    if prof.get("background"):
        banner_parts.append(f"出身：{prof['background']}")
    banner_line = _clip_text(draw, "  |  ".join(banner_parts), f_body, max_width=w - 172)
    _draw_text(draw, (132, header_y + 66), banner_line, f_body, theme["accent"])

    sub_line = _clip_text(draw, f"地點：{c.get('location') or '冒險者酒館'}  •  目標：{c.get('current_quest') or '自由探索'}", f_small, max_width=w - 172)
    _draw_text(draw, (132, header_y + 94), sub_line, f_small, theme["text_muted"])

    # 2. Status Row (HP bar + AC/SAN badges)
    draw.rounded_rectangle(
        [24, status_y, w - 24, status_y + status_h],
        radius=12,
        fill=theme["card_bg"],
        outline=theme["border"],
        width=1,
    )
    hp = int(c.get("hp", 100) or 0)
    max_hp = max(1, int(c.get("max_hp", 100) or 100))
    hp_ratio = max(0.0, min(1.0, hp / max_hp))
    hp_col = _hp_color(hp_ratio, theme["bar_fill"])
    _draw_text(draw, (42, status_y + 15), f"生命值 HP：{hp} / {max_hp}", f_hdr, theme["text_primary"])
    _draw_progress_bar(draw, 42, status_y + 48, 520, 20, hp_ratio, (42, 48, 66), hp_col)
    _draw_text(draw, (574, status_y + 47), f"{int(round(hp_ratio * 100))}%", f_body, hp_col)

    # AC / SAN Badges on right
    bx = w - 40
    san_val = _extract_san_value(c)
    ac = prof.get("ac")
    if san_val is not None:
        san_txt = f"理智 SAN {san_val}"[:18]
        sw = int(_measure_width(draw, san_txt, f_badge) + 24)
        bx -= sw
        _draw_pill(draw, bx, status_y + 16, san_txt, f_badge, (38, 30, 56), (186, 134, 240), (236, 214, 255), height=28)
        bx -= 10
    if ac is not None:
        ac_txt = f"防禦 AC {ac}"
        aw = int(_measure_width(draw, ac_txt, f_badge) + 24)
        bx -= aw
        _draw_pill(draw, bx, status_y + 16, ac_txt, f_badge, (30, 44, 62), (96, 178, 246), (218, 240, 255), height=28)

    # 3. 6-Stat Grid Bars (if D&D stats present)
    if has_stats:
        draw.rounded_rectangle(
            [24, stats_y, w - 24, stats_y + stats_box_h],
            radius=12,
            fill=theme["card_bg"],
            outline=theme["border"],
            width=1,
        )
        _draw_text(draw, (42, stats_y + 12), "核心能力屬性 // ABILITY SCORES", f_hdr, theme["accent"])
        stat_order = [
            ("STR", "STR 力量"),
            ("DEX", "DEX 敏捷"),
            ("CON", "CON 體質"),
            ("INT", "INT 智力"),
            ("WIS", "WIS 感知"),
            ("CHA", "CHA 魅力"),
        ]
        numeric_vals = []
        for k, _lbl in stat_order:
            try:
                numeric_vals.append(int(stats.get(k, 10)))
            except (TypeError, ValueError):
                pass
        scale_max = 5.0 if (numeric_vals and max(numeric_vals) <= 5) else 20.0

        col_w = 400
        for idx, (skey, slabel) in enumerate(stat_order):
            col_i = idx % 2
            row_i = idx // 2
            sx = 42 + col_i * (col_w + 26)
            sy = stats_y + 48 + row_i * 44
            raw_v = stats.get(skey, 10)
            try:
                val = int(raw_v)
            except (TypeError, ValueError):
                val = 10
            mod = val if val <= 5 else (val - 10) // 2
            _draw_text(draw, (sx, sy), slabel, f_body, theme["text_primary"])
            _draw_progress_bar(draw, sx + 96, sy + 4, 210, 14, val / scale_max, (42, 48, 66), theme["border"])
            _draw_text(draw, (sx + 318, sy), f"{val:>2} ({mod:+d})", f_body, theme["accent"])

    # 4. Custom Traits Grid (Xianxia, Cyberpunk, CoC, etc.)
    if has_traits:
        draw.rounded_rectangle(
            [24, traits_y, w - 24, traits_y + traits_box_h],
            radius=12,
            fill=theme["card_bg"],
            outline=theme["border"],
            width=1,
        )
        _draw_text(draw, (42, traits_y + 12), "世界觀專屬特質 // WORLD TRAITS", f_hdr, theme["accent"])
        cell_w = 398
        for idx, (tk, tv) in enumerate(trait_items):
            col_i = idx % 2
            row_i = idx // 2
            tx = 42 + col_i * (cell_w + 20)
            ty = traits_y + 46 + row_i * 44
            draw.rounded_rectangle([tx, ty, tx + cell_w, ty + 34], radius=8, fill=theme["bg_bot"], outline=(58, 68, 92), width=1)
            k_clip = _clip_text(draw, f"{tk}：", f_small, max_width=130)
            _draw_text(draw, (tx + 12, ty + 8), k_clip, f_small, theme["text_muted"])
            kw = int(_measure_width(draw, k_clip, f_small))
            v_clip = _clip_text(draw, tv, f_small, max_width=max(80, cell_w - kw - 28))
            _draw_text(draw, (tx + 14 + kw, ty + 8), v_clip, f_small, theme["text_primary"])

    # 5. Narrative fallback card when neither stats nor traits exist
    if narrative_box_h:
        draw.rounded_rectangle(
            [24, narr_y, w - 24, narr_y + narrative_box_h],
            radius=12,
            fill=theme["card_bg"],
            outline=theme["border"],
            width=1,
        )
        _draw_text(draw, (42, narr_y + 12), "角色背景與特質 // CHARACTER PROFILE", f_hdr, theme["accent"])
        desc_txt = backstory or "自由敘事冒險者 — 隨冒險歷程累積裝備、線索與成長故事。"
        _draw_text(draw, (42, narr_y + 44), _clip_text(draw, desc_txt, f_body, max_width=w - 92), f_body, theme["text_primary"])

    # 6. Skills & Inventory Pills
    draw.rounded_rectangle(
        [24, gear_y, w - 24, gear_y + gear_box_h],
        radius=12,
        fill=theme["card_bg"],
        outline=theme["border"],
        width=1,
    )
    _draw_text(draw, (42, gear_y + 12), "專長技能 / 功法法術", f_small, theme["accent"])
    if skills:
        end_sk_y = _draw_wrapped_pills(
            draw,
            42,
            gear_y + 36,
            w - 42,
            skills,
            f_badge,
            fill_bg=theme["bg_bot"],
            outline_col=theme["accent"],
            text_col=theme["text_primary"],
            max_rows=skills_rows_est,
        )
    else:
        _draw_text(draw, (42, gear_y + 38), "尚未登記特別專長（可透過 !sheet 匯入）", f_small, theme["text_muted"])
        end_sk_y = gear_y + 62

    inv_header_y = end_sk_y + 14
    _draw_text(draw, (42, inv_header_y), "隨身裝備 / 法寶 / 持有物", f_small, theme["accent"])
    if inv:
        _draw_wrapped_pills(
            draw,
            42,
            inv_header_y + 24,
            w - 42,
            inv,
            f_badge,
            fill_bg=theme["bg_top"],
            outline_col=theme["border"],
            text_col=theme["text_primary"],
            max_rows=inv_rows_est,
        )
    else:
        _draw_text(draw, (42, inv_header_y + 26), "（背包空空如也）", f_small, theme["text_muted"])

    # 7. Backstory strip (if stats/traits also shown)
    if backstory_h:
        draw.rounded_rectangle(
            [24, backstory_y, w - 24, backstory_y + backstory_h],
            radius=12,
            fill=theme["card_bg"],
            outline=(58, 68, 92),
            width=1,
        )
        _draw_text(draw, (42, backstory_y + 10), "角色傳記", f_small, theme["accent"])
        _draw_text(
            draw,
            (42, backstory_y + 34),
            _clip_text(draw, backstory, f_small, max_width=w - 92),
            f_small,
            theme["text_primary"],
        )

    draw_centered_text(
        draw,
        w / 2,
        h - 20,
        "TIP: 輸入 !sheet <人設> 更新角色卡 | !party 查看全隊儀表板 | !roll 擲骰",
        f_small,
        theme["text_muted"],
    )

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
