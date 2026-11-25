"""Wiki search, multi-factor ranking, LightRAG 1-hop expansion, and focused excerpts."""

from __future__ import annotations

import difflib
import re
from typing import Optional

from engines.game_wiki.constants import (
    _CATEGORY_QUERY_ALIASES,
    _CJK_HANGUL_CHAR_RE,
    _DELIM_SPLIT_RE,
    _GENERIC_GAME_KEYWORDS,
    _MODIFIER_CHAPTER_TOKENS,
    _QUERY_SYNONYMS_LOWER,
    _SHORT_QUALIFIER_STOP_CHARS,
    _WHITESPACE_RE,
)
from engines.game_wiki.loader import (
    _WIKI_CACHE,
    _WIKI_LOCK,
    _build_entry_search_index,
    list_wiki_entries_by_category,
    load_wiki_entries,
)
from engines.game_wiki.nlp import (
    _analyze_dynamic_room_patterns,
    _boundary_pattern,
    _detect_query_coordinates,
    _extract_chinese_subterms,
    _strip_korean_particles,
    _strip_query_filler,
    _token_in_text,
)

_TABLE_SEP_RE = re.compile(r"[\|\s:\-]+")
_BULLET_LINE_RE = re.compile(r"^[-*]\s+")
_SUB_BULLET_LINE_RE = re.compile(r"^\s+[-*]\s+")
_BOLD_TEXT_RE = re.compile(r"\*\*(.+?)\*\*")


def _prepare_search_query(query_text: str, allow_bare_chapter_disambig: bool) -> dict:
    """萃取核心查詢詞、同義詞群、韓服房名解析與三維座標消歧義提醒。"""
    text_lower = query_text.lower()
    text_compact = _WHITESPACE_RE.sub("", text_lower)
    core_query = _strip_query_filler(text_lower)
    core_tokens = [
        t for t in _DELIM_SPLIT_RE.split(core_query)
        if len(t) >= 2 and t not in _GENERIC_GAME_KEYWORDS and t not in _MODIFIER_CHAPTER_TOKENS
    ]
    query_subterms = [
        st for st in _extract_chinese_subterms(core_query)
        if st.lower() not in _MODIFIER_CHAPTER_TOKENS
    ]

    token_groups: list[tuple[str, ...]] = []
    expanded_alias_set: set[str] = set()
    for ct in core_tokens:
        syns = _QUERY_SYNONYMS_LOWER.get(ct, ())
        token_groups.append(tuple(dict.fromkeys((ct,) + syns)))
        expanded_alias_set.update(syns)

    if not expanded_alias_set and len(text_compact) <= 8:
        for syn_k, syn_vals in _QUERY_SYNONYMS_LOWER.items():
            if syn_k in text_lower:
                expanded_alias_set.update(syn_vals)
                if not token_groups:
                    token_groups.append((syn_k,) + syn_vals)

    short_qualifiers: list[str] = []
    if token_groups:
        for raw_tok in _DELIM_SPLIT_RE.split(core_query):
            rt = raw_tok.strip()
            if len(rt) == 1 and rt not in _SHORT_QUALIFIER_STOP_CHARS and not rt.isascii():
                short_qualifiers.append(rt)

    dynamic_notes, extra_boost_keywords = _analyze_dynamic_room_patterns(query_text)
    coords = _detect_query_coordinates(query_text)
    if (
        allow_bare_chapter_disambig
        and coords["chapters"]
        and not coords["series"]
        and not coords["timelines"]
    ):
        ch_list = "、".join(sorted(c.upper() for c in coords["chapters"]))
        dynamic_notes.append(
            f"🔀 **【跨系列與新舊版本章節消歧義提醒（偵測到未指明系列的章節詞：{ch_list}）】**：\n"
            f"  * 在《殭屍學園》中，`{ch_list}` 同時存在於多個完全不同的系列與時間線：\n"
            "    1. **經典原版《進擊的殭屍 (진격의 좀비)》** EP.0~EP.3（舊版主線故事）\n"
            "    2. **10 週年重製版《Reboot (리부트)》** 1部《破滅的裂隙》/ 2部《絕望的種子》/ 3部《鬥志的火種》（劇情與關卡重新編排）\n"
            "    3. **《英雄模式 (Heroes / H1~H4)》**（反派／實驗體視角副本）\n"
            "    4. **《Division (디비전 / D1~D3)》**（第二季主線副本）\n"
            "  * **回答原則**：在群友還沒明確講出是哪一個系列或新舊版之前，**嚴禁自行猜測並混拼不同系列的劇情或攻略**！請先簡短列出上述常見選項並反問對方想查哪一個（或請他補充模式名稱）。"
        )

    return {
        "text_lower": text_lower,
        "text_compact": text_compact,
        "core_tokens": core_tokens,
        "query_subterms": query_subterms,
        "token_groups": token_groups,
        "expanded_alias_set": expanded_alias_set,
        "short_qualifiers": short_qualifiers,
        "dynamic_notes": dynamic_notes,
        "extra_boost_keywords": extra_boost_keywords,
        "coords": coords,
    }


def _score_entry_keywords(entry: dict, query_ctx: dict) -> tuple[float, float, int, bool]:
    """比對條目 ID、預計算關鍵字與同義詞群組分數。"""
    text_lower = query_ctx["text_lower"]
    text_compact = query_ctx["text_compact"]
    core_tokens = query_ctx["core_tokens"]
    token_groups = query_ctx["token_groups"]
    expanded_alias_set = query_ctx["expanded_alias_set"]
    extra_boost_keywords = query_ctx["extra_boost_keywords"]

    specific_score = 0.0
    generic_score = 0.0
    matched_specific_kws = 0
    exact_kw_matched = False
    direct_kw_hit_count = 0
    matched_partial_groups: set[int] = set()

    entry_id_l = entry["id"].lower()
    if entry_id_l in text_lower:
        specific_score += 15.0
        matched_specific_kws += 1
        if entry_id_l == text_lower.strip():
            exact_kw_matched = True

    kw_index = entry.get("_keywords_index")
    if kw_index is None:
        _build_entry_search_index(entry)
        kw_index = entry["_keywords_index"]

    for kw_l, kw_compact, is_generic_kw, is_short_ascii, has_hangul in kw_index:
        if kw_l in extra_boost_keywords:
            specific_score += 12.0
            matched_specific_kws += 1
            if kw_l == text_lower or kw_compact == text_compact:
                exact_kw_matched = True
            continue

        if is_short_ascii:
            if (
                _boundary_pattern(kw_l, True).search(text_lower)
                or kw_l in core_tokens
                or kw_l in expanded_alias_set
            ):
                if is_generic_kw:
                    generic_score += 0.5
                else:
                    if (
                        kw_l == text_lower
                        or kw_compact == text_compact
                        or (text_compact in _QUERY_SYNONYMS_LOWER and kw_l in expanded_alias_set)
                    ):
                        exact_kw_matched = True
                    direct_kw_hit_count += 1
                    specific_score += 12.0 if direct_kw_hit_count <= 3 else 2.0
                    matched_specific_kws += 1
            continue

        is_direct_hit = (
            kw_l in text_lower
            or (len(kw_compact) >= 2 and kw_compact in text_compact)
            or kw_l in expanded_alias_set
            or kw_compact in expanded_alias_set
        )
        if is_direct_hit:
            if is_generic_kw:
                generic_score += 0.5
            else:
                if (
                    kw_l == text_lower
                    or kw_compact == text_compact
                    or (
                        text_compact in _QUERY_SYNONYMS_LOWER
                        and (kw_l in expanded_alias_set or kw_compact in expanded_alias_set)
                    )
                ):
                    exact_kw_matched = True
                weight = 14.0 if has_hangul else (8.0 + min(len(kw_l), 6) * 0.8)
                direct_kw_hit_count += 1
                specific_score += weight if direct_kw_hit_count <= 3 else min(weight, 2.0)
                matched_specific_kws += 1
        elif not is_generic_kw and token_groups:
            for g_idx, aliases in enumerate(token_groups):
                if g_idx not in matched_partial_groups and any(_token_in_text(a, kw_compact) for a in aliases):
                    matched_partial_groups.add(g_idx)
                    specific_score += 7.0
                    matched_specific_kws += 1

    return specific_score, generic_score, matched_specific_kws, exact_kw_matched


def _score_entry_body_fallback(entry: dict, query_ctx: dict) -> tuple[float, int]:
    """關鍵字未直接命中時，以核心查詢詞與中文斷詞片段掃描預先小寫化的內文。"""
    core_tokens = query_ctx["core_tokens"]
    query_subterms = query_ctx["query_subterms"]
    expanded_alias_set = query_ctx["expanded_alias_set"]
    if not (core_tokens or query_subterms or expanded_alias_set):
        return 0.0, 0

    specific_score = 0.0
    matched_specific_kws = 0
    body_l = entry["body_lower"]
    body_no_de = entry["body_no_de"]

    for group in query_ctx["token_groups"]:
        for alias in group:
            if len(alias) >= 2 and (
                _token_in_text(alias, body_l)
                or _token_in_text(alias.replace("的", ""), body_no_de)
            ):
                specific_score += 6.5
                matched_specific_kws += 1
                break

    if specific_score == 0.0 and query_subterms:
        matched_long_subterms = 0
        matched_short_subterms = 0
        for st in query_subterms:
            st_l = st.lower()
            if st_l in body_l or st_l.replace("的", "") in body_no_de:
                if len(st_l) >= 3:
                    matched_long_subterms += 1
                else:
                    matched_short_subterms += 1
        # 命中至少 1 個 3 字以上具體詞或同時命中 2 個以上 2 字詞
        if matched_long_subterms >= 1 or matched_short_subterms >= 2:
            specific_score += matched_long_subterms * 7.5 + matched_short_subterms * 3.5
            matched_specific_kws += matched_long_subterms + matched_short_subterms

    return specific_score, matched_specific_kws


def _apply_coordinate_and_aspect_adjustments(
    entry: dict,
    specific_score: float,
    matched_specific_kws: int,
    query_ctx: dict,
) -> tuple[float, int]:
    """套用 YAML V2 三維座標鎖定 (series × timeline × chapter) 與 subject + aspects 加權。"""
    coords = query_ctx["coords"]
    entry_series = entry.get("series") or ""
    entry_chapter = entry.get("chapter") or ""
    entry_timeline = entry.get("timeline") or ""

    if coords["series"] and entry_series:
        if entry_series in coords["series"]:
            if coords["chapters"] and entry_chapter in coords["chapters"]:
                specific_score += 22.0
                matched_specific_kws += 2
            elif coords["chapters"] and entry_chapter and entry_chapter != "all" and entry_chapter not in coords["chapters"]:
                specific_score *= 0.45
            elif not coords["chapters"]:
                specific_score += 8.0
                matched_specific_kws += 1
        elif coords["chapters"] and entry_chapter in coords["chapters"]:
            specific_score *= 0.2

    if coords["timelines"] and entry_timeline in ("classic", "reboot"):
        if entry_timeline in coords["timelines"]:
            specific_score += 14.0
            matched_specific_kws += 1
        else:
            specific_score *= 0.35

    subject_parts = entry.get("subject_parts")
    if specific_score > 0 and subject_parts:
        text_lower = query_ctx["text_lower"]
        text_compact = query_ctx["text_compact"]
        if any(sp in text_lower or sp_ns in text_compact for sp, sp_ns in subject_parts):
            aspects_pairs = entry.get("aspects_pairs") or ()
            if any(asp in text_lower or asp_ns in text_compact for asp, asp_ns in aspects_pairs):
                specific_score = specific_score * 1.8 + 12.0
                matched_specific_kws += 1

    return specific_score, matched_specific_kws


def _apply_title_coverage_and_glossary_adjustments(
    entry: dict,
    specific_score: float,
    exact_kw_matched: bool,
    query_ctx: dict,
) -> float:
    """套用精準匹配、標題加權、多詞覆蓋率與巨型總表降噪調整。"""
    if specific_score <= 0:
        return specific_score

    text_compact = query_ctx["text_compact"]
    token_groups = query_ctx["token_groups"]
    short_qualifiers = query_ctx["short_qualifiers"]
    name_zh_compact = entry["name_zh_compact"]
    name_kr_compact = entry["name_kr_compact"]
    subject_compact = entry["subject_compact"]
    is_mega_glossary = entry["is_mega_glossary"]

    if exact_kw_matched:
        specific_score += 12.0 if is_mega_glossary else 22.0
    if not is_mega_glossary and len(text_compact) >= 2:
        if text_compact in (name_zh_compact, name_kr_compact, subject_compact):
            specific_score += 28.0
        elif (
            _token_in_text(text_compact, name_zh_compact)
            or _token_in_text(text_compact, subject_compact)
            or _token_in_text(text_compact, name_kr_compact)
        ):
            specific_score += 14.0

    if len(token_groups) >= 2 or (len(token_groups) == 1 and short_qualifiers):
        header_compact = entry["header_compact"]
        body_compact = entry["body_compact"]

        header_covered = 0
        any_covered = 0
        for aliases in token_groups:
            in_header = any(_token_in_text(a, header_compact) for a in aliases)
            in_body = in_header or any(_token_in_text(a, body_compact) for a in aliases)
            if in_header:
                header_covered += 1
            if in_body:
                any_covered += 1

        for sq in short_qualifiers:
            if sq in header_compact:
                specific_score += 14.0
            elif sq in body_compact:
                specific_score += 6.0

        if len(token_groups) >= 2:
            if any_covered == len(token_groups):
                specific_score += 30.0 * (any_covered - 1)
                if header_covered == len(token_groups):
                    specific_score += 18.0
            elif any_covered >= 2:
                specific_score += 14.0 * (any_covered - 1)
            else:
                specific_score *= 0.55

    if (
        is_mega_glossary
        and not exact_kw_matched
        and not _token_in_text(text_compact, name_zh_compact)
    ):
        specific_score *= 0.5

    return specific_score


def _score_single_entry(entry: dict, query_ctx: dict) -> float:
    """計算單筆條目對查詢的總相關分數；若未命中專有分數則回傳 0.0。"""
    specific_score, generic_score, matched_specific_kws, exact_kw_matched = _score_entry_keywords(
        entry, query_ctx
    )
    if specific_score == 0.0:
        specific_score, matched_specific_kws = _score_entry_body_fallback(entry, query_ctx)

    specific_score, matched_specific_kws = _apply_coordinate_and_aspect_adjustments(
        entry, specific_score, matched_specific_kws, query_ctx
    )
    specific_score = _apply_title_coverage_and_glossary_adjustments(
        entry, specific_score, exact_kw_matched, query_ctx
    )

    if specific_score <= 0:
        return 0.0

    total_score = specific_score + generic_score
    if entry.get("category") == "community_contrib":
        total_score += 8.0
    if matched_specific_kws >= 2:
        total_score += min(matched_specific_kws, 5) * 2.5
    return total_score


def _expand_lightrag_relations(
    slang_hits: list[dict],
    lore_hits: list[dict],
    by_id: dict[str, dict],
) -> list[dict]:
    """LightRAG 1-hop 實體關聯圖譜展開（若關聯條目已被取代則自動導向最新版本）。"""
    if not (slang_hits or lore_hits):
        return []

    selected_ids = {e["id"] for e in slang_hits} | {e["id"] for e in lore_hits}
    candidate_rel_ids: list[str] = []

    for primary_entry in (lore_hits[:2] + slang_hits[:2]):
        for rel_id in primary_entry.get("related_ids", []):
            if rel_id not in by_id:
                continue
            target_rel = by_id[rel_id]
            if target_rel.get("superseded_by"):
                rep_id = target_rel["superseded_by"][-1]
                if rep_id in by_id and not by_id[rep_id].get("superseded_by"):
                    rel_id = rep_id
                else:
                    continue
            if rel_id not in selected_ids and rel_id not in candidate_rel_ids:
                candidate_rel_ids.append(rel_id)

    return [by_id[rel_id] for rel_id in candidate_rel_ids[:2]]


def search_wiki_entries(
    query_text: str,
    max_slang: int = 8,
    max_lore: int = 4,
    expand_lightrag: bool = True,
    allow_bare_chapter_disambig: bool = True,
) -> dict:
    """
    混合式精準檢索 + LightRAG 1-hop 實體關聯展開 + CRAG 精準度評分 + 三維座標消歧義：
    1. 區分「泛用遊戲大類詞（如 殭屍學園、좀고、PVE）」與「跨系列章節修飾詞（如 EP1、第一章）」，
       單憑命中泛用詞或孤立章節詞不計入有效專有分數，防止跨系列撞車與拿無關條目餵給小模型。
    2. 支援 YAML V2 座標鎖定（series × timeline × chapter）、subject+aspects 雙層加權，以及 supersedes 軟屏蔽。
    3. 回傳 max_specific_score 供上層判定是否應直接觸發「不知道就直說 (Selective Abstention)」拒答閘門。
    """
    with _WIKI_LOCK:
        entries = load_wiki_entries()
        by_id = dict(_WIKI_CACHE.get("entries_by_id", {}))
    if not entries or not query_text or not query_text.strip():
        return {
            "slang_hits": [],
            "lore_hits": [],
            "ranked_hits": [],
            "related_hits": [],
            "dynamic_notes": [],
            "max_specific_score": 0.0,
        }

    query_ctx = _prepare_search_query(query_text, allow_bare_chapter_disambig)
    scored_slang: list[tuple[float, dict]] = []
    scored_lore: list[tuple[float, dict]] = []
    max_specific_score = 0.0

    for entry in entries:
        if entry.get("superseded_by"):
            continue
        total_score = _score_single_entry(entry, query_ctx)
        if total_score > 0:
            if total_score > max_specific_score:
                max_specific_score = total_score
            if entry["category"] == "slang":
                scored_slang.append((total_score, entry))
            else:
                scored_lore.append((total_score, entry))

    scored_slang.sort(key=lambda x: x[0], reverse=True)
    scored_lore.sort(key=lambda x: x[0], reverse=True)

    slang_hits = [item[1] for item in scored_slang[:max_slang]]
    lore_hits = [item[1] for item in scored_lore[:max_lore]]

    all_scored = sorted(
        scored_slang + scored_lore,
        key=lambda x: (-x[0], x[1]["id"]),
    )
    ranked_hits: list[dict] = []
    seen_ranked_ids: set[str] = set()
    max_ranked = max(max_slang + max_lore, 12)
    for _, item in all_scored:
        if item["id"] not in seen_ranked_ids:
            seen_ranked_ids.add(item["id"])
            ranked_hits.append(item)
            if len(ranked_hits) >= max_ranked:
                break

    related_hits = _expand_lightrag_relations(slang_hits, lore_hits, by_id) if expand_lightrag else []

    return {
        "slang_hits": slang_hits,
        "lore_hits": lore_hits,
        "ranked_hits": ranked_hits,
        "related_hits": related_hits,
        "dynamic_notes": query_ctx["dynamic_notes"],
        "max_specific_score": max_specific_score,
    }


def _extract_focus_search_terms(query_text: str) -> tuple[str, list[str]]:
    """從查詢字串抽取用於長表段落摘錄的精簡檢索詞組。"""
    q_lower = (query_text or "").strip().lower()
    q_compact = _WHITESPACE_RE.sub("", q_lower)
    core_q = _strip_query_filler(q_lower)

    raw_terms: list[str] = []
    if q_lower:
        raw_terms.append(q_lower)
    if q_compact and q_compact != q_lower:
        raw_terms.append(q_compact)
    for tok in _DELIM_SPLIT_RE.split(core_q):
        t = tok.strip()
        if not t:
            continue
        raw_terms.append(t)
        raw_terms.extend(_QUERY_SYNONYMS_LOWER.get(t, ()))
        stem = _strip_korean_particles(t)
        if stem and stem != t:
            raw_terms.append(stem)
    for st in _extract_chinese_subterms(core_q):
        raw_terms.append(st.lower())
    if not raw_terms and q_compact:
        raw_terms.append(q_compact)

    search_terms: list[str] = []
    seen_terms: set[str] = set()
    for term in raw_terms:
        tl = term.strip().lower()
        if not tl or tl in seen_terms:
            continue
        if len(tl) == 1 and tl != q_compact and (tl in _SHORT_QUALIFIER_STOP_CHARS or tl.isascii()):
            continue
        if len(tl) >= 2 or tl == q_compact or (not tl.isascii() and tl not in _SHORT_QUALIFIER_STOP_CHARS):
            seen_terms.add(tl)
            search_terms.append(tl)

    return q_lower, search_terms


def _split_body_into_blocks(lines: list[str], start_idx: int) -> list[dict]:
    """將長篇條目拆解為可獨立挑選的「項目塊」(bullet block 或 markdown table row)。"""
    blocks: list[dict] = []
    current_section = ""
    current_table_header: list[str] = []
    i = start_idx
    n = len(lines)
    while i < n:
        ln = lines[i]
        stripped = ln.strip()
        if not stripped:
            current_table_header = []
            i += 1
            continue
        if stripped.startswith("### "):
            current_section = ln
            current_table_header = []
            i += 1
            continue
        if (
            stripped.startswith("|")
            and i + 1 < n
            and _TABLE_SEP_RE.fullmatch(lines[i + 1].strip())
        ):
            current_table_header = [ln, lines[i + 1]]
            i += 2
            continue
        if stripped.startswith("|") and not _TABLE_SEP_RE.fullmatch(stripped):
            blocks.append({
                "idx": len(blocks),
                "section": current_section,
                "table_header": list(current_table_header),
                "text": ln,
            })
            i += 1
            continue
        if _BULLET_LINE_RE.match(ln):
            current_table_header = []
            buf = [ln]
            j = i + 1
            while j < n:
                nxt = lines[j]
                if nxt.startswith(("  ", "\t")) or _SUB_BULLET_LINE_RE.match(nxt):
                    buf.append(nxt)
                    j += 1
                else:
                    break
            blocks.append({
                "idx": len(blocks),
                "section": current_section,
                "table_header": [],
                "text": "\n".join(buf),
            })
            i = j
            continue
        current_table_header = []
        blocks.append({
            "idx": len(blocks),
            "section": current_section,
            "table_header": [],
            "text": ln,
        })
        i += 1
    return blocks


def extract_focused_entry_body(
    entry: dict,
    query_text: str,
    max_chars: int = 1200,
) -> tuple[str, bool]:
    """
    對長篇總表條目（如 4,200 字武器總表或 3,000 字黑話總表）精準萃取與查詢直接相關的子條目與表格列，
    避免使用者一查單詞就看到整頁被截斷的無關清單。回傳 (excerpt_or_full, was_focused)。
    """
    body = (entry.get("body") or "").strip()
    if len(body) <= max_chars:
        return body, False

    q_lower, search_terms = _extract_focus_search_terms(query_text)
    lines = body.splitlines()
    header_line = ""
    start_idx = 0
    if lines and lines[0].startswith("【《殭屍學園》"):
        header_line = lines[0]
        start_idx = 1

    blocks = _split_body_into_blocks(lines, start_idx)
    scored_blocks: list[tuple[float, int, dict]] = []
    for blk in blocks:
        blk_l = blk["text"].lower()
        bold_text = " ".join(_BOLD_TEXT_RE.findall(blk_l))
        score = 0.0
        distinct_hits = 0
        for term in search_terms:
            if _token_in_text(term, bold_text):
                score += 6.0 if term == q_lower else 5.0
                distinct_hits += 1
            elif _token_in_text(term, blk_l):
                score += 2.5 if term == q_lower else 2.0
                distinct_hits += 1
        if distinct_hits > 0:
            score += distinct_hits * 1.5
            scored_blocks.append((score, -blk["idx"], blk))

    if scored_blocks:
        scored_blocks.sort(key=lambda x: (x[0], x[1]), reverse=True)
        budget = max(300, max_chars - 100)
        used = len(header_line) + (1 if header_line else 0)
        chosen: list[dict] = []
        seen_sections: set[str] = set()

        for _, _, blk in scored_blocks:
            extra = len(blk["text"]) + 1
            if blk["section"] and blk["section"] not in seen_sections:
                extra += len(blk["section"]) + 2
            if blk["table_header"]:
                extra += sum(len(h) + 1 for h in blk["table_header"])
            if chosen and used + extra > budget:
                continue
            chosen.append(blk)
            used += extra
            if blk["section"]:
                seen_sections.add(blk["section"])
            if used >= budget:
                break

        chosen.sort(key=lambda b: b["idx"])
        out_lines: list[str] = []
        if header_line:
            out_lines.append(header_line)
        last_section = None
        last_table_header: Optional[list[str]] = None
        for blk in chosen:
            if blk["section"] and blk["section"] != last_section:
                if out_lines:
                    out_lines.append("")
                out_lines.append(blk["section"])
                last_section = blk["section"]
                last_table_header = None
            if blk["table_header"] and blk["table_header"] != last_table_header:
                out_lines.extend(blk["table_header"])
                last_table_header = blk["table_header"]
            elif not blk["table_header"]:
                last_table_header = None
            out_lines.append(blk["text"])

        out_lines.append(
            f"\n*(🔎 已從長篇總表精選 {len(chosen)}/{len(scored_blocks)} 條直接相關項目；全文共 {len(body)} 字)*"
        )
        return "\n".join(out_lines).strip(), True

    cut_limit = max(200, max_chars - 60)
    snippet = body[:cut_limit]
    nl_pos = snippet.rfind("\n")
    if nl_pos > cut_limit // 2:
        snippet = snippet[:nl_pos]
    return f"{snippet.rstrip()}\n\n*(📄 長篇條目預覽，全文共 {len(body)} 字)*", True


def suggest_wiki_entries(query_text: str, max_suggestions: int = 6) -> list[dict]:
    """針對錯字或模糊搜尋詞提供相近條目建議。"""
    q_compact = _WHITESPACE_RE.sub("", (query_text or "").strip().lower())
    if not q_compact:
        return []

    q_chars = set(_CJK_HANGUL_CHAR_RE.findall(q_compact))
    entries = [e for e in load_wiki_entries() if not e.get("superseded_by")]
    scored: list[tuple[float, dict]] = []

    for entry in entries:
        kw_compact_list = entry.get("keywords_compact")
        if kw_compact_list is None:
            candidates_compact = [
                _WHITESPACE_RE.sub("", (entry.get("name_zh") or "").lower()),
                _WHITESPACE_RE.sub("", (entry.get("name_kr") or "").lower()),
                _WHITESPACE_RE.sub("", (entry.get("subject") or "").lower()),
                *(_WHITESPACE_RE.sub("", str(k).lower()) for k in entry.get("keywords", [])[:15]),
            ]
        else:
            candidates_compact = [
                entry["name_zh_compact"],
                entry["name_kr_compact"],
                entry["subject_compact"],
                *kw_compact_list[:15],
            ]

        best_seq = 0.0
        for c_comp in candidates_compact:
            if not c_comp:
                continue
            ratio = difflib.SequenceMatcher(None, q_compact, c_comp).ratio()
            if ratio > best_seq:
                best_seq = ratio

        overlap_score = 0.0
        if q_chars:
            cand_blob = " ".join(candidates_compact[:12])
            cand_chars = set(_CJK_HANGUL_CHAR_RE.findall(cand_blob))
            if cand_chars:
                overlap_score = (len(q_chars & cand_chars) / max(len(q_chars), 1)) * 0.78

        sim = max(best_seq, overlap_score)
        if sim >= 0.32:
            len_penalty = min(len(entry.get("body", "")), 4000) * 1e-5
            scored.append((sim - len_penalty, entry))

    scored.sort(key=lambda x: (-x[0], x[1]["id"]))
    return [item[1] for item in scored[:max_suggestions]]


def search_wiki_interactive(query_text: str, max_results: int = 20) -> dict:
    """
    專供 Discord `!wiki` 互動指令使用的查詢介面：
    支援分類目錄直達 (`角色` / `黑話`)、精準排名前綴、單字/短詞備援、長表重點摘錄與錯字推薦，
    且完全不影響對話 RAG 的 CRAG 拒答防線。
    """
    q = (query_text or "").strip()
    if not q:
        return {
            "mode": "empty",
            "query": "",
            "category_key": None,
            "results": [],
            "related_entries": [],
            "dynamic_notes": [],
            "focused_snippets": {},
        }

    q_compact = _WHITESPACE_RE.sub("", q.lower())
    cat_key = _CATEGORY_QUERY_ALIASES.get(q_compact)
    if cat_key:
        cat_entries = list_wiki_entries_by_category(cat_key)
        return {
            "mode": "category",
            "query": q,
            "category_key": cat_key,
            "results": cat_entries,
            "related_entries": [],
            "dynamic_notes": [],
            "focused_snippets": {},
        }

    res = search_wiki_entries(q, max_slang=12, max_lore=15, expand_lightrag=True)
    results = list(res.get("ranked_hits") or [])[:max_results]
    related_entries = [
        r for r in res.get("related_hits", [])
        if not r.get("superseded_by") and all(r["id"] != x["id"] for x in results)
    ]
    dynamic_notes = list(res.get("dynamic_notes") or [])

    if not results:
        q_lower = q.lower()
        active_entries = [e for e in load_wiki_entries() if not e.get("superseded_by")]
        fallback_scored: list[tuple[float, dict]] = []
        for entry in active_entries:
            score = 0.0
            kws_l = entry.get("keywords_lower") or [k.lower() for k in entry.get("keywords", [])]
            body_l = entry.get("body_lower") or entry["body"].lower()
            name_zh_c = entry.get("name_zh_compact") or _WHITESPACE_RE.sub("", entry["name_zh"].lower())
            if q_lower in kws_l:
                score += 20.0
            if entry["id"].lower().startswith(f"{q_lower}_") or entry["id"].lower() == q_lower:
                score += 18.0
            if f"**{q_lower}**" in body_l:
                score += 12.0
            elif len(q_compact) >= 2 and q_compact in name_zh_c:
                score += 10.0
            if score > 0:
                fallback_scored.append((score, entry))
        fallback_scored.sort(key=lambda x: (-x[0], x[1]["id"]))
        results = [item[1] for item in fallback_scored[:max_results]]

    if results:
        focused_snippets = {
            e["id"]: extract_focused_entry_body(e, q, max_chars=1200)
            for e in (results + related_entries)
        }
        return {
            "mode": "results",
            "query": q,
            "category_key": None,
            "results": results,
            "related_entries": related_entries,
            "dynamic_notes": dynamic_notes,
            "focused_snippets": focused_snippets,
        }

    if dynamic_notes:
        return {
            "mode": "results",
            "query": q,
            "category_key": None,
            "results": [],
            "related_entries": [],
            "dynamic_notes": dynamic_notes,
            "focused_snippets": {},
        }

    suggestions = suggest_wiki_entries(q, max_suggestions=6)
    if suggestions:
        focused_snippets = {
            e["id"]: extract_focused_entry_body(e, q, max_chars=1200)
            for e in suggestions
        }
        return {
            "mode": "suggestions",
            "query": q,
            "category_key": None,
            "results": suggestions,
            "related_entries": [],
            "dynamic_notes": [],
            "focused_snippets": focused_snippets,
        }

    return {
        "mode": "empty",
        "query": q,
        "category_key": None,
        "results": [],
        "related_entries": [],
        "dynamic_notes": [],
        "focused_snippets": {},
    }
