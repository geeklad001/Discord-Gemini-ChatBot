"""Markdown/CSV wiki loaders, normalized search field precomputation, and cache management."""

from __future__ import annotations

import csv
import re
import sys
import threading
from pathlib import Path
from typing import Optional

from engines.game_wiki import constants
from engines.game_wiki.constants import (
    CATEGORY_LABELS,
    _BODY_TERM_EXTRACT_RE,
    _FRONTMATTER_BLOCK_RE,
    _GENERIC_GAME_KEYWORDS,
    _HANGUL_RE,
    _HANGUL_SYLLABLE_ONLY_RE,
    _MODIFIER_CHAPTER_TOKENS,
    _SUBJECT_SPLIT_RE,
    _WHITESPACE_RE,
    logger,
)

_WIKI_LOCK = threading.RLock()

# 快取結構：依據各 .md 與 .csv 檔案的最後修改時間 (mtime) 自動熱重載 (Hot-Reload)
_WIKI_CACHE: dict = {
    "mtimes": {},
    "entries": [],          # list[dict]
    "entries_by_id": {},    # dict[str, dict]
}
_CACHE_PATHS: tuple[str, str] = ("", "")

_CSV_URL_PAREN_RE = re.compile(r"\(https?://[^\)]+\)")
_CSV_PART_SPLIT_RE = re.compile(r"[/（）()、,，]")
_BODY_TERM_SPLIT_RE = re.compile(r"[/（）()、,，&]")
_YAML_COMMENT_SPLIT_RE = re.compile(r"\s+#")


def _resolve_paths() -> tuple[Path, Path, Path]:
    """動態解析知識庫路徑，相容執行期對 engines.game_wiki_engine 的 monkeypatch。"""
    facade = sys.modules.get("engines.game_wiki_engine")
    if facade is None:
        return constants.WIKI_DIR, constants.CHAR_CSV_PATH, constants.COMMUNITY_WIKI_PATH
    wiki_dir = getattr(facade, "WIKI_DIR", constants.WIKI_DIR)
    char_csv = getattr(facade, "CHAR_CSV_PATH", constants.CHAR_CSV_PATH)
    comm_path = getattr(facade, "COMMUNITY_WIKI_PATH", constants.COMMUNITY_WIKI_PATH)
    return wiki_dir, char_csv, comm_path


def _parse_list_field(raw_val: str) -> list[str]:
    """解析 YAML 中的簡易陣列欄位，例如 [a, b, c]"""
    val = raw_val.strip()
    if val.startswith("[") and val.endswith("]"):
        val = val[1:-1]
    items = []
    for part in val.split(","):
        cleaned = part.strip().strip("'\"")
        if cleaned:
            items.append(cleaned)
    return items


def _build_entry_search_index(entry: dict) -> dict:
    """於載入階段預先計算正規化檢索欄位，避免每次搜尋對全部條目重複執行正則表達式與字串清洗。"""
    name_zh_lower = (entry.get("name_zh") or "").lower()
    name_kr_lower = (entry.get("name_kr") or "").lower()
    subject_lower = (entry.get("subject") or "").strip().lower()
    body = entry.get("body") or ""
    body_lower = body.lower()
    keywords = entry.get("keywords") or []
    aspects = entry.get("aspects") or []

    name_zh_compact = _WHITESPACE_RE.sub("", name_zh_lower)
    name_kr_compact = _WHITESPACE_RE.sub("", name_kr_lower)
    subject_compact = _WHITESPACE_RE.sub("", subject_lower)

    keywords_lower: list[str] = []
    keywords_compact: list[str] = []
    keywords_index: list[tuple[str, str, bool, bool, bool]] = []
    for kw in keywords:
        kw_l = str(kw).lower()
        kw_c = _WHITESPACE_RE.sub("", kw_l)
        keywords_lower.append(kw_l)
        keywords_compact.append(kw_c)
        if not kw_l or (len(kw_l) == 1 and ord(kw_l[0]) < 0xAC00):
            continue
        is_generic = kw_l in _GENERIC_GAME_KEYWORDS or kw_l in _MODIFIER_CHAPTER_TOKENS
        is_short_ascii = kw_l.isascii() and kw_l.isalnum() and len(kw_l) <= 3
        has_hangul = bool(_HANGUL_RE.search(kw_l))
        keywords_index.append((kw_l, kw_c, is_generic, is_short_ascii, has_hangul))

    aspects_pairs: list[tuple[str, str]] = []
    aspects_compact: list[str] = []
    for asp in aspects:
        asp_s = str(asp).strip().lower()
        if not asp_s:
            continue
        aspects_compact.append(_WHITESPACE_RE.sub("", asp_s))
        if len(asp_s) >= 2:
            aspects_pairs.append((asp_s, asp_s.replace(" ", "")))

    subject_parts: list[tuple[str, str]] = []
    if subject_lower:
        parts = [p.strip() for p in _SUBJECT_SPLIT_RE.split(subject_lower) if len(p.strip()) >= 2]
        if not parts:
            parts = [subject_lower]
        subject_parts = [(sp, sp.replace(" ", "")) for sp in parts]

    header_parts = [
        name_zh_compact,
        name_kr_compact,
        subject_compact,
        *aspects_compact,
        *(kc for kc in keywords_compact if kc),
    ]
    header_compact = " ".join(p for p in header_parts if p)

    entry["name_zh_compact"] = name_zh_compact
    entry["name_kr_compact"] = name_kr_compact
    entry["subject_compact"] = subject_compact
    entry["subject_parts"] = subject_parts
    entry["keywords_lower"] = keywords_lower
    entry["keywords_compact"] = keywords_compact
    entry["_keywords_index"] = keywords_index
    entry["aspects_pairs"] = aspects_pairs
    entry["aspects_compact"] = aspects_compact
    entry["body_lower"] = body_lower
    entry["body_no_de"] = body_lower.replace("的", "")
    entry["body_compact"] = _WHITESPACE_RE.sub("", body_lower)
    entry["header_compact"] = header_compact
    entry["is_mega_glossary"] = len(keywords) >= 35 and len(body) >= 1500
    return entry


def _parse_markdown_wiki_file(file_path: Path) -> list[dict]:
    """
    解析單一 data/game_wiki/*.md 檔案中的結構化條目。
    支援標準 YAML Frontmatter，並自動確保每個條目皆具備 Contextual Retrieval 全知脈絡標頭。
    """
    try:
        content = file_path.read_text(encoding="utf-8")
    except Exception as e:
        logger.error(f"❌ 讀取知識庫檔案失敗 ({file_path.name}): {e}")
        return []

    matches = list(_FRONTMATTER_BLOCK_RE.finditer(content))
    if not matches:
        return []

    parsed_entries: list[dict] = []
    for idx, match in enumerate(matches):
        yaml_block = match.group(1)
        body_start = match.end()
        body_end = matches[idx + 1].start() if idx + 1 < len(matches) else len(content)
        body_text = content[body_start:body_end].strip()

        meta: dict[str, str | list[str]] = {
            "id": "",
            "category": file_path.stem,
            "name_zh": "",
            "name_kr": "",
            "timeline": "",
            "series": "",
            "chapter": "",
            "subject": "",
            "aspects": [],
            "source": "",
            "updated_at": "",
            "freshness": "stable",
            "guide_url": "",
            "supersedes": [],
            "keywords": [],
            "related_ids": [],
            "source_file": file_path.name,
        }

        for line in yaml_block.splitlines():
            if ":" not in line:
                continue
            k, v = line.split(":", 1)
            k = k.strip().lower()
            v = v.strip()
            if "#" in v and "http" not in v.lower():
                v = _YAML_COMMENT_SPLIT_RE.split(v, maxsplit=1)[0].strip()
            if k in ("keywords", "related_ids", "aspects", "supersedes"):
                meta[k] = _parse_list_field(v)
            elif k in (
                "id", "category", "name_zh", "name_kr",
                "timeline", "series", "chapter", "subject",
                "source", "updated_at", "freshness", "guide_url",
            ):
                meta[k] = v.strip("'\"")

        entry_id = str(meta["id"]).strip()
        if not entry_id:
            continue

        cat = str(meta["category"]).strip()
        cat_label = CATEGORY_LABELS.get(cat, cat)
        name_zh = str(meta["name_zh"]).strip()
        name_kr = str(meta["name_kr"]).strip()
        timeline = str(meta["timeline"]).strip().lower()
        series = str(meta["series"]).strip().lower()
        chapter = str(meta["chapter"]).strip().lower()
        subject = str(meta["subject"]).strip()
        aspects = list(meta["aspects"]) if isinstance(meta["aspects"], list) else []
        source = str(meta["source"]).strip()
        updated_at = str(meta["updated_at"]).strip()
        freshness = str(meta["freshness"]).strip().lower() or "stable"
        guide_url = str(meta["guide_url"]).strip()
        supersedes = list(meta["supersedes"]) if isinstance(meta["supersedes"], list) else []

        if not body_text.startswith("【《殭屍學園》"):
            kr_part = f" ({name_kr})" if name_kr and name_kr not in name_zh else ""
            meta_tags: list[str] = []
            if timeline == "classic":
                meta_tags.append("版本：經典原版 (Classic)")
            elif timeline == "reboot":
                meta_tags.append("版本：重製版 (Reboot)")
            elif timeline == "au":
                meta_tags.append("版本：平行世界 (AU)")
            if source and updated_at:
                meta_tags.append(f"來源：{source}｜基準：{updated_at}")
            elif source:
                meta_tags.append(f"來源：{source}")
            elif updated_at:
                meta_tags.append(f"基準：{updated_at}")
            meta_suffix = f"｜{'｜'.join(meta_tags)}" if meta_tags else ""
            header = f"【《殭屍學園》[{cat_label}] — {name_zh}{kr_part}{meta_suffix}】"
            body_text = f"{header}\n{body_text}"

        raw_kws = list(meta["keywords"]) if isinstance(meta["keywords"], list) else []
        for extra in (name_zh, name_kr, subject):
            if extra:
                raw_kws.append(extra)
                for sub in _SUBJECT_SPLIT_RE.split(extra):
                    sub_clean = sub.strip()
                    if len(sub_clean) >= 2:
                        raw_kws.append(sub_clean)
        for asp in aspects:
            asp_clean = str(asp).strip()
            if len(asp_clean) >= 2:
                raw_kws.append(asp_clean)

        # 自動從內文 (body_text) 提取粗體專有名詞與引號內專有名詞加入索引，大幅提升子模式與機制的召回率
        for m_term in _BODY_TERM_EXTRACT_RE.findall(body_text):
            for t_part in m_term:
                if t_part:
                    for sub in _BODY_TERM_SPLIT_RE.split(t_part):
                        sc = sub.strip()
                        if 2 <= len(sc) <= 15:
                            raw_kws.append(sc)

        dedup_kws: list[str] = []
        seen_lower: set[str] = set()
        for kw in raw_kws:
            kw_s = kw.strip()
            if not kw_s:
                continue
            kw_l = kw_s.lower()
            if kw_l not in seen_lower:
                seen_lower.add(kw_l)
                dedup_kws.append(kw_s)

        entry = {
            "id": entry_id,
            "category": cat,
            "category_label": cat_label,
            "name_zh": name_zh,
            "name_kr": name_kr,
            "timeline": timeline,
            "series": series,
            "chapter": chapter,
            "subject": subject,
            "aspects": aspects,
            "source": source,
            "updated_at": updated_at,
            "freshness": freshness,
            "guide_url": guide_url,
            "supersedes": supersedes,
            "superseded_by": [],
            "keywords": dedup_kws,
            "related_ids": list(meta["related_ids"]) if isinstance(meta["related_ids"], list) else [],
            "body": body_text,
            "source_file": file_path.name,
        }
        parsed_entries.append(_build_entry_search_index(entry))

    return parsed_entries


def _load_character_csv_entries(csv_path: Path) -> list[dict]:
    """
    載入台服官方與社群常用角色譯名對照表 (character_translations_tw.csv)，
    讓全部 105 位角色的韓文原名、台服譯名、外觀特徵與玩家俗稱皆可被 100% 精準檢索。
    """
    if not csv_path.exists():
        return []
    entries: list[dict] = []
    try:
        with open(csv_path, "r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for idx, row in enumerate(reader, start=1):
                kr_raw = (row.get("角色名 (韓文)") or "").strip()
                zh_raw = (row.get("中文翻譯") or "").strip()
                memo = (row.get("備註 (自用)") or "").strip()
                nick = (row.get("稱呼（大眾）") or "").strip()
                role_identity = (row.get("身分") or "").strip()
                if not kr_raw:
                    continue

                # 清理如 "(台服)" 或網址註記，提取乾淨譯名供檢索與顯示
                zh_clean = _CSV_URL_PAREN_RE.sub("", zh_raw).strip()
                kws: list[str] = [kr_raw]
                for part in _CSV_PART_SPLIT_RE.split(f"{kr_raw}/{zh_clean}/{nick}/{memo}"):
                    p = part.strip()
                    if p and p not in ("台服", "韓語", "韓文", "namu", "待考", "無翻譯", "全名待考", "本名待考", "自用"):
                        if len(p) >= 2 or _HANGUL_SYLLABLE_ONLY_RE.search(p):
                            kws.append(p)

                dedup_kws: list[str] = []
                seen: set[str] = set()
                for k in kws:
                    kl = k.lower()
                    if kl not in seen:
                        seen.add(kl)
                        dedup_kws.append(k)

                disp_zh = zh_clean if "<待考" not in zh_clean else f"{kr_raw}（台服暫無官譯，俗稱：{memo or nick}）"
                body_lines = [
                    f"【《殭屍學園》[台服角色譯名與特徵對照] — {disp_zh}（韓文：{kr_raw}）】",
                    f"- **台服正式譯名**：{disp_zh}",
                ]
                if nick:
                    body_lines.append(f"- **台灣玩家常用稱呼／俗稱**：{nick}")
                if memo:
                    body_lines.append(f"- **外觀特徵與角色定位**：{memo}")
                if role_identity:
                    body_lines.append(f"- **班級／所屬身分**：{role_identity}")

                entry = {
                    "id": f"char_csv_{idx}",
                    "category": "character_dict",
                    "category_label": CATEGORY_LABELS["character_dict"],
                    "name_zh": disp_zh,
                    "name_kr": kr_raw,
                    "timeline": "common",
                    "series": "",
                    "chapter": "",
                    "subject": disp_zh,
                    "aspects": [],
                    "source": "台服角色譯名對照表",
                    "updated_at": "",
                    "freshness": "stable",
                    "guide_url": "",
                    "supersedes": [],
                    "superseded_by": [],
                    "keywords": dedup_kws,
                    "related_ids": [],
                    "body": "\n".join(body_lines),
                    "source_file": csv_path.name,
                }
                entries.append(_build_entry_search_index(entry))
    except Exception as e:
        logger.error(f"❌ 讀取角色譯名 CSV 失敗 ({csv_path.name}): {e}")
    return entries


_parse_character_csv_file = _load_character_csv_entries


def load_wiki_entries(force_reload: bool = False) -> list[dict]:
    """
    載入並快取 data/game_wiki/*.md 與 character_translations_tw.csv 所有知識庫條目。
    當偵測到任何檔案修改時間 (mtime) 變更時自動熱重載，無須重啟 Bot。
    """
    global _CACHE_PATHS
    with _WIKI_LOCK:
        wiki_dir, char_csv_path, _ = _resolve_paths()
        if not wiki_dir.exists():
            return []

        md_files = sorted(wiki_dir.glob("*.md"))
        current_mtimes: dict[str, float] = {}
        for f in md_files:
            try:
                current_mtimes[f.name] = f.stat().st_mtime
            except OSError:
                pass
        if char_csv_path.exists():
            try:
                current_mtimes[char_csv_path.name] = char_csv_path.stat().st_mtime
            except OSError:
                pass

        current_paths = (str(wiki_dir), str(char_csv_path))
        if (
            not force_reload
            and _WIKI_CACHE["entries"]
            and _CACHE_PATHS == current_paths
            and current_mtimes == _WIKI_CACHE["mtimes"]
        ):
            return list(_WIKI_CACHE["entries"])

        all_entries: list[dict] = []
        by_id: dict[str, dict] = {}
        for f in md_files:
            file_entries = _parse_markdown_wiki_file(f)
            for item in file_entries:
                all_entries.append(item)
                by_id[item["id"]] = item

        csv_entries = _load_character_csv_entries(char_csv_path)
        for item in csv_entries:
            all_entries.append(item)
            by_id[item["id"]] = item

        superseded_by_map: dict[str, list[str]] = {}
        for item in all_entries:
            for old_id in item.get("supersedes", []):
                oid_l = str(old_id).strip().lower()
                if oid_l:
                    superseded_by_map.setdefault(oid_l, []).append(item["id"])
        for item in all_entries:
            item["superseded_by"] = superseded_by_map.get(item["id"].lower(), [])

        _WIKI_CACHE["mtimes"] = current_mtimes
        _WIKI_CACHE["entries"] = all_entries
        _WIKI_CACHE["entries_by_id"] = by_id
        _CACHE_PATHS = current_paths
        logger.info(
            f"📚 已載入《殭屍學園》Contextual-LightRAG 知識庫：共 {len(all_entries)} 個條目 "
            f"({len(md_files)} 個 Markdown 檔 + {len(csv_entries)} 筆台服角色譯名)"
        )
        return list(all_entries)


def get_wiki_stats() -> dict:
    """回傳知識庫各分類統計資訊"""
    with _WIKI_LOCK:
        entries = load_wiki_entries()
        counts: dict[str, int] = {}
        active_total = 0
        superseded_count = 0
        for e in entries:
            if e.get("superseded_by"):
                superseded_count += 1
                continue
            active_total += 1
            cat = e["category"]
            counts[cat] = counts.get(cat, 0) + 1
        return {
            "total": active_total,
            "by_category": counts,
            "superseded_count": superseded_count,
        }


def list_wiki_entries_by_category(category_key: Optional[str] = None) -> list[dict]:
    """依分類回傳所有有效（未被覆寫）的知識庫條目。"""
    entries = [e for e in load_wiki_entries() if not e.get("superseded_by")]
    if category_key:
        entries = [e for e in entries if e.get("category") == category_key]
    entries.sort(key=lambda e: ((e.get("name_zh") or e["id"]), e["id"]))
    return entries
