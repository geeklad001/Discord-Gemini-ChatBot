"""PIL PNG card renderers for 1in8B: birth certificate & life epitaph with 5-stat sparkline and lifespan bars."""

from __future__ import annotations

import io
import re
from typing import Optional

from PIL import Image, ImageDraw

from core.rendering import _FONT_LOCK, draw_centered_text, load_font
from engines.lifesim.constants import ERA_CONFIG, REGION_COLORS, TOTAL_COUNTRIES_COUNT
from engines.lifesim.storage import load_world_demographics

_PIL_EMOJI_STRIP_RE = re.compile(
    r"[\U0001F1E0-\U0001FAFF\u2600-\u27BF\uFE0F\u200D]+"
)


def _pil_safe_text(text: str) -> str:
    """移除無法由標準 CJK TTF 字型彩繪的 Emoji，避免圖卡出現方塊亂碼。"""
    cleaned = _PIL_EMOJI_STRIP_RE.sub("", str(text or "")).strip()
    return re.sub(r"\s{2,}", " ", cleaned)


def _load_cjk_font(size: int, bold: bool = False):
    return load_font(size, bold=bold, prefer_cjk=True)


def _draw_stat_bar(
    draw: ImageDraw.ImageDraw,
    x: int,
    y: int,
    w: int,
    label: str,
    val: int,
    color: tuple[int, int, int],
    f_lbl,
    f_val,
    bar_h: int = 15,
) -> None:
    draw.text((x, y), label, font=f_lbl, fill=(224, 228, 238))
    bar_x = x + 94
    bar_w = max(60, w - 144)
    draw.rounded_rectangle([bar_x, y + 3, bar_x + bar_w, y + 3 + bar_h], radius=7, fill=(45, 52, 68))
    clamped = max(0, min(100, int(val)))
    fill_w = max(8, int(bar_w * clamped / 100.0)) if clamped > 0 else 0
    if fill_w > 0:
        draw.rounded_rectangle([bar_x, y + 3, bar_x + fill_w, y + 3 + bar_h], radius=7, fill=color)
    draw.text((bar_x + bar_w + 10, y), f"{clamped:>3}", font=f_val, fill=(245, 248, 255))


def _lookup_effective_life_exp(run: dict) -> Optional[int]:
    try:
        world = load_world_demographics()
        code = str(run.get("country_code") or "").upper()
        era_cfg = ERA_CONFIG.get(run.get("era", "present"), {})
        le_delta = float(era_cfg.get("le_delta", 0.0))
        for c in world:
            if str(c.get("code") or "").upper() == code:
                return int(round(max(38.0, min(92.0, float(c["life_exp"]) + le_delta))))
    except Exception:
        pass
    return None


def _draw_pill_badges(
    draw: ImageDraw.ImageDraw,
    x: int,
    y: int,
    max_x: int,
    labels: list[str],
    font,
    fill_bg: tuple[int, int, int],
    outline_col: tuple[int, int, int],
    text_col: tuple[int, int, int],
) -> None:
    cx = x
    for raw_lbl in labels:
        txt = _pil_safe_text(raw_lbl)
        if not txt:
            continue
        with _FONT_LOCK:
            bbox = draw.textbbox((0, 0), txt, font=font)
            tw = bbox[2] - bbox[0]
        pill_w = tw + 18
        if cx + pill_w > max_x:
            break
        draw.rounded_rectangle([cx, y, cx + pill_w, y + 24], radius=11, fill=fill_bg, outline=outline_col, width=1)
        draw_centered_text(draw, cx + pill_w / 2, y + 12, txt, font, text_col)
        cx += pill_w + 8


def render_birth_certificate_png(run: dict) -> bytes:
    """渲染高質感全球出生證明書 PNG 圖片（含城鄉環境標籤與先天特質徽章）。"""
    w, h = 760, 480
    img = Image.new("RGB", (w, h), (22, 27, 38))
    draw = ImageDraw.Draw(img)

    accent = REGION_COLORS.get(run.get("region", ""), (88, 101, 242))
    draw.rounded_rectangle([14, 14, w - 14, h - 14], radius=18, fill=(28, 34, 48), outline=accent, width=3)
    draw.rounded_rectangle([14, 14, w - 14, 78], radius=18, fill=accent)
    draw.rectangle([14, 52, w - 14, 78], fill=accent)

    f_title = _load_cjk_font(25, bold=True)
    f_sub = _load_cjk_font(15, bold=False)
    f_big = _load_cjk_font(26, bold=True)
    f_body = _load_cjk_font(16, bold=False)
    f_small = _load_cjk_font(13, bold=False)
    f_badge = _load_cjk_font(13, bold=True)

    draw.text((34, 27), "1 IN 8 BILLION // 全球出生證明書", font=f_title, fill=(255, 255, 255))
    draw.text((w - 195, 34), f"RUN #{run['id']} | {run['country_code']}", font=f_sub, fill=(240, 245, 255))

    # Left column: Identity + Urban/Rural tag + Innate traits
    place_title = f"{run['country_name']}・{run['city']}"
    place_font = f_big if len(place_title) <= 10 else _load_cjk_font(21, bold=True)
    draw.text((36, 94), place_title, font=place_font, fill=(255, 248, 220))

    ur_key = run.get("urban_rural", "urban")
    ur_text = "都市區" if ur_key == "urban" else "鄉村區"
    ur_bg = (45, 76, 128) if ur_key == "urban" else (46, 98, 68)
    ur_outline = (116, 185, 255) if ur_key == "urban" else (111, 207, 151)
    draw.rounded_rectangle([36, 134, 108, 158], radius=10, fill=ur_bg, outline=ur_outline, width=1)
    draw_centered_text(draw, 72, 146, ur_text, f_badge, (240, 248, 255))
    draw.text(
        (118, 138),
        f"地區：{run['region']} | 出生機率：{run['birth_prob_pct']:.3f}%",
        font=f_small,
        fill=(172, 186, 212),
    )

    era_lbl = _pil_safe_text(ERA_CONFIG.get(run["era"], {}).get("label", run["era"]))
    info_lines = [
        f"姓名：{run['char_name']}（性別：{run['gender']}）",
        f"時代：{era_lbl}（{run['birth_year']}年生）",
        f"出身家境：{run['family_class']}",
    ]
    y_cursor = 172
    for line in info_lines:
        draw.text((36, y_cursor), line, font=f_body, fill=(232, 236, 246))
        y_cursor += 30

    draw.text((36, 268), "先天特質：", font=f_small, fill=(172, 186, 212))
    traits = run.get("traits") or []
    trait_names = [t.get("name", "") for t in traits if isinstance(t, dict) and t.get("name")]
    if not trait_names:
        trait_names = ["普通資質"]
    _draw_pill_badges(
        draw,
        110,
        264,
        352,
        trait_names,
        f_badge,
        fill_bg=(44, 52, 74),
        outline_col=(254, 231, 92),
        text_col=(255, 240, 178),
    )

    first_trait_desc = ""
    if traits and isinstance(traits[0], dict):
        first_trait_desc = _pil_safe_text(traits[0].get("desc", ""))[:26]
    if first_trait_desc:
        draw.text((36, 298), first_trait_desc, font=f_small, fill=(168, 180, 204))

    # PR Badge box
    pr_col = (87, 242, 135) if run["luck_pr"] >= 70 else ((254, 231, 92) if run["luck_pr"] >= 40 else (237, 66, 69))
    draw.rounded_rectangle([36, 336, 348, 442], radius=12, fill=(20, 24, 34), outline=pr_col, width=2)
    draw.text((54, 350), f"GLOBAL LUCK PR {run['luck_pr']}", font=_load_cjk_font(22, bold=True), fill=pr_col)
    life_exp = _lookup_effective_life_exp(run)
    if run.get("status") == "finished":
        age_line = f"離世年齡：{run['age']} 歲（當地均壽：{life_exp} 歲）" if life_exp else f"離世年齡：{run['age']} 歲"
    else:
        age_line = f"初始年齡：{run['age']} 歲（當地均壽：{life_exp} 歲）" if life_exp else f"初始年齡：{run['age']} 歲"
    draw.text((54, 390), age_line, font=f_small, fill=(195, 206, 224))
    draw.text((54, 412), f"全球平均預期壽命參考：73 歲", font=f_small, fill=(142, 156, 180))

    # Right column: 5 Core Stat Bars
    draw.text((382, 98), "先天與初始五維指標", font=_load_cjk_font(20, bold=True), fill=(255, 255, 255))
    stats_spec = [
        ("健康 Health", run["health"], (235, 87, 87)),
        ("快樂 Happy", run["happiness"], (242, 201, 76)),
        ("知識 Know.", run["knowledge"], (86, 204, 242)),
        ("財富 Wealth", run["wealth"], (111, 207, 151)),
        ("人際 Social", run["social"], (187, 107, 217)),
    ]
    sy = 144
    for lbl, val, col in stats_spec:
        _draw_stat_bar(draw, 382, sy, 342, lbl, val, col, f_small, f_small)
        sy += 46

    draw.rounded_rectangle([382, 378, 724, 442], radius=10, fill=(22, 27, 38), outline=(66, 78, 104), width=1)
    draw.text((398, 390), f"聯合國/世銀 197 國真實權重抽樣", font=f_small, fill=(210, 220, 240))
    draw.text((398, 412), f"模式：{_pil_safe_text(run.get('story_mode', 'fast').upper())} | 重心：均衡發展", font=f_small, fill=(154, 168, 194))

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _draw_sparkline_chart(
    draw: ImageDraw.ImageDraw,
    x0: int,
    y0: int,
    x1: int,
    y1: int,
    stat_history: list[dict],
    f_small,
) -> None:
    draw.rounded_rectangle([x0, y0, x1, y1], radius=10, fill=(18, 22, 32), outline=(60, 70, 94), width=1)
    pad_l, pad_r, pad_t, pad_b = 34, 16, 16, 24
    px0, py0 = x0 + pad_l, y0 + pad_t
    px1, py1 = x1 - pad_r, y1 - pad_b
    pw = max(10, px1 - px0)
    ph = max(10, py1 - py0)

    for grid_val in (25, 50, 75):
        gy = py1 - int(ph * grid_val / 100.0)
        draw.line([(px0, gy), (px1, gy)], fill=(38, 44, 60), width=1)
        draw.text((x0 + 6, gy - 7), f"{grid_val}", font=f_small, fill=(110, 122, 148))

    if len(stat_history) < 2:
        draw_centered_text(draw, (x0 + x1) / 2, (y0 + y1) / 2, "生命歷程較短，暫無多階段折線紀錄", f_small, (150, 162, 186))
        return

    series_spec = [
        ("health", (235, 87, 87)),
        ("happiness", (242, 201, 76)),
        ("knowledge", (86, 204, 242)),
        ("wealth", (111, 207, 151)),
        ("social", (187, 107, 217)),
    ]
    n = len(stat_history)
    ages = [int(pt.get("age", 0)) for pt in stat_history]
    min_age = ages[0]
    max_age = max(min_age + 1, ages[-1])

    for key, col in series_spec:
        pts: list[tuple[int, int]] = []
        for idx, pt in enumerate(stat_history):
            age_v = int(pt.get("age", 0))
            if max_age > min_age:
                tx = (age_v - min_age) / (max_age - min_age)
            else:
                tx = idx / max(1, n - 1)
            val = max(0, min(100, int(pt.get(key, 50))))
            px = int(px0 + tx * pw)
            py = int(py1 - (val / 100.0) * ph)
            pts.append((px, py))
        if len(pts) >= 2:
            draw.line(pts, fill=col, width=2)
            lx, ly = pts[-1]
            draw.ellipse([lx - 3, ly - 3, lx + 3, ly + 3], fill=col)

    draw.text((px0, py1 + 4), f"{min_age}歲", font=f_small, fill=(150, 162, 186))
    end_lbl = f"{ages[-1]}歲"
    with _FONT_LOCK:
        eb = draw.textbbox((0, 0), end_lbl, font=f_small)
        ew = eb[2] - eb[0]
    draw.text((px1 - ew, py1 + 4), end_lbl, font=f_small, fill=(150, 162, 186))


def _draw_lifespan_comparison(
    draw: ImageDraw.ImageDraw,
    x: int,
    y: int,
    w: int,
    run_age: int,
    country_le: Optional[int],
    f_small,
) -> None:
    draw.rounded_rectangle([x, y, x + w, y + 92], radius=10, fill=(22, 26, 36), outline=(66, 76, 100), width=1)
    draw.text((x + 12, y + 8), "壽命對比（歲）", font=_load_cjk_font(14, bold=True), fill=(255, 232, 166))
    rows = [
        ("本局享壽", int(run_age), (218, 178, 92)),
        ("該國均壽", int(country_le if country_le is not None else 73), (86, 204, 242)),
        ("全球均壽", 73, (148, 163, 184)),
    ]
    ry = y + 30
    bar_x = x + 78
    bar_w = max(80, w - 134)
    for lbl, age_val, col in rows:
        draw.text((x + 12, ry), lbl, font=f_small, fill=(208, 214, 228))
        draw.rounded_rectangle([bar_x, ry + 3, bar_x + bar_w, ry + 13], radius=5, fill=(42, 48, 64))
        fill_w = max(6, int(bar_w * max(0, min(105, age_val)) / 105.0)) if age_val > 0 else 0
        if fill_w > 0:
            draw.rounded_rectangle([bar_x, ry + 3, bar_x + fill_w, ry + 13], radius=5, fill=col)
        draw.text((bar_x + bar_w + 8, ry), f"{age_val}歲", font=f_small, fill=(240, 244, 252))
        ry += 20


def render_life_epitaph_png(
    run: dict,
    unlocked_count: int = 1,
    total_countries: int = TOTAL_COUNTRIES_COUNT,
    *,
    unlocked_countries: Optional[int] = None,
) -> bytes:
    """渲染終局人生總結紀念卡 PNG（含五維軌跡折線圖、成就稱號、跨國遷移足跡與壽命對比）。"""
    if unlocked_countries is not None:
        unlocked_count = int(unlocked_countries)

    w, h = 820, 620
    img = Image.new("RGB", (w, h), (20, 22, 30))
    draw = ImageDraw.Draw(img)

    accent = REGION_COLORS.get(run.get("region", ""), (155, 93, 229))
    draw.rounded_rectangle([14, 14, w - 14, h - 14], radius=18, fill=(28, 31, 42), outline=(218, 178, 92), width=3)
    draw.rounded_rectangle([14, 14, w - 14, 76], radius=18, fill=(44, 48, 66))
    draw.rectangle([14, 52, w - 14, 76], fill=(44, 48, 66))

    f_title = _load_cjk_font(24, bold=True)
    f_sub = _load_cjk_font(15, bold=False)
    f_hdr = _load_cjk_font(18, bold=True)
    f_body = _load_cjk_font(15, bold=False)
    f_small = _load_cjk_font(13, bold=False)
    f_badge = _load_cjk_font(12, bold=True)

    draw.text((34, 26), f"人生謝幕紀念卡 // {run['char_name']}（享壽 {run['age']} 歲）", font=f_title, fill=(255, 232, 166))
    draw.text((w - 190, 34), f"RUN #{run['id']} | PR {run['luck_pr']}", font=f_sub, fill=(214, 220, 236))

    birth_loc = f"{run['country_name']}・{run['city']}"
    cur_code = (run.get("current_country_code") or run["country_code"]).upper()
    birth_code = str(run["country_code"]).upper()
    cur_loc = f"{run.get('current_country_name') or run['country_name']}・{run.get('current_city') or run['city']}"
    if cur_code != birth_code:
        route_line = f"跨國足跡：{birth_loc} --> {cur_loc}  |  最終職業：{run['occupation']}"
    else:
        ur_txt = "都市" if run.get("urban_rural", "urban") == "urban" else "鄉村"
        route_line = f"出生地：{birth_loc}（{ur_txt}） |  最終職業：{run['occupation']}  |  出身：{run['family_class']}"
    draw.text((34, 90), route_line[:66], font=f_hdr if len(route_line) <= 34 else f_body, fill=(242, 246, 255))

    cause = _pil_safe_text(run.get("death_cause") or "走完了一生。")
    draw.text((34, 118), f"結局：{cause[:56]}", font=f_body, fill=(214, 192, 176))

    titles = list(run.get("titles") or [])
    if titles:
        draw.text((34, 148), "成就稱號：", font=f_small, fill=(255, 232, 166))
        _draw_pill_badges(
            draw,
            106,
            144,
            w - 34,
            titles[:5],
            f_badge,
            fill_bg=(52, 44, 30),
            outline_col=(218, 178, 92),
            text_col=(255, 236, 179),
        )

    # Left column: Final 5 stats + Lifespan Comparison + Country Dex
    draw.text((34, 184), "終局五維總覽", font=f_hdr, fill=accent)
    stats_spec = [
        ("健康 Health", run["health"], (235, 87, 87)),
        ("快樂 Happy", run["happiness"], (242, 201, 76)),
        ("知識 Know.", run["knowledge"], (86, 204, 242)),
        ("財富 Wealth", run["wealth"], (111, 207, 151)),
        ("人際 Social", run["social"], (187, 107, 217)),
    ]
    sy = 216
    for lbl, val, col in stats_spec:
        _draw_stat_bar(draw, 34, sy, 360, lbl, val, col, f_small, f_small, bar_h=14)
        sy += 36

    country_le = _lookup_effective_life_exp(run)
    _draw_lifespan_comparison(draw, 34, 404, 360, run["age"], country_le, f_small)

    draw.rounded_rectangle([34, 506, 394, 584], radius=10, fill=(36, 42, 58), outline=accent, width=1)
    draw.text((48, 516), f"已解鎖世界國家圖鑑：{unlocked_count} / {total_countries}", font=f_body, fill=(224, 236, 255))
    traits = run.get("traits") or []
    trait_line = "、".join(_pil_safe_text(t.get("name", "")) for t in traits if isinstance(t, dict)) or "無特殊天賦"
    rel = run.get("relationships") or {}
    fam_txt = "已婚" if rel.get("married") else ("有伴侶" if rel.get("partner") else "單身")
    if int(rel.get("children", 0) or 0) > 0:
        fam_txt += f"・子女{rel['children']}人"
    draw.text((48, 544), f"特質：{trait_line} | 家庭：{fam_txt}"[:38], font=f_small, fill=(176, 190, 216))

    # Right column: 5-stat sparkline chart + Timeline highlights
    draw.text((418, 184), "五維人生軌跡圖", font=f_hdr, fill=(255, 232, 166))
    legend = [("健", (235, 87, 87)), ("樂", (242, 201, 76)), ("知", (86, 204, 242)), ("財", (111, 207, 151)), ("人", (187, 107, 217))]
    lx = 580
    for l_txt, l_col in legend:
        draw.ellipse([lx, 191, lx + 8, 199], fill=l_col)
        draw.text((lx + 11, 187), l_txt, font=f_small, fill=(200, 210, 228))
        lx += 40

    _draw_sparkline_chart(draw, 418, 214, 786, 362, run.get("stat_history") or [], f_small)

    draw.text((418, 376), "生平重大里程碑", font=f_hdr, fill=(255, 232, 166))
    tl = run.get("timeline") or []
    sample_tl = tl[:1] + tl[-4:] if len(tl) > 5 else tl
    ty = 408
    for item in sample_tl[:5]:
        raw_line = _pil_safe_text(f"• {item.get('age', 0)}歲 {item.get('title', '')}：{item.get('detail', '')}")
        if len(raw_line) > 30:
            raw_line = raw_line[:29] + "…"
        draw.text((418, ty), raw_line, font=f_small, fill=(220, 226, 238))
        ty += 34

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
