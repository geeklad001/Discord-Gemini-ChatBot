"""
《殭屍學園》離線知識庫防幻覺檢驗與發布工具 (Wiki Staging Linter & Promoter)

提供可重用的 Python API 與 CLI 子命令：
1. `check`：檢查 `.review.md` 或 `.md` 知識卡片的 YAML V2 必填欄位、重複 ID、
   `gemini-flash-lite-latest` 潔淨字數上限、泛用／章節修飾詞污染、中韓混雜錯字、
   `character_translations_tw.csv` 台服譯名對齊，以及未解決之 `> ⚠️` 人工確認標記。
2. `promote`：通過 Linter 驗證後，自動剝除 `status: pending_review` 與 `> 🔍` / `> ⚠️`
   審核輔助行，依 `id` 安全合併至 `data/game_wiki/*.md` 並觸發快取熱重載。
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from engines.game_wiki_engine import (
    CHAR_CSV_PATH,
    WIKI_DIR,
    _GENERIC_GAME_KEYWORDS,
    _MODIFIER_CHAPTER_TOKENS,
    _parse_list_field,
    load_wiki_entries,
)

DEFAULT_STAGING_REVIEW_DIR = REPO_ROOT / "data" / "wiki_staging" / "02_review"
DEFAULT_MAX_BODY_CHARS = 450
WARN_BODY_CHARS = 380
REQUIRED_YAML_FIELDS = ("id", "category", "name_zh", "keywords")
LIST_YAML_FIELDS = {"keywords", "related_ids", "aspects", "supersedes", "resource_links"}
KNOWN_YAML_KEYS = {
    "id",
    "category",
    "timeline",
    "series",
    "chapter",
    "subject",
    "aspects",
    "name_zh",
    "name_kr",
    "source",
    "updated_at",
    "freshness",
    "supersedes",
    "status",
    "guide_url",
    "resource_links",
    "keywords",
    "related_ids",
}

_YAML_KV_LINE_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*\s*:")
_REVIEW_MARKER_LINE_RE = re.compile(r"^\s*>\s*(?:\*\*)?(?:🔍|\u26a0\ufe0f?)")
_UNRESOLVED_REVIEW_LINE_RE = re.compile(r"^\s*>\s*(?:\*\*)?\u26a0\ufe0f?")
_MIXED_ZH_KR_RE = re.compile(
    r"[\u4e00-\u9fff]+[\uac00-\ud7a3]+|[\uac00-\ud7a3]+[\u4e00-\u9fff]+"
)
_CONTEXTUAL_HEADER_RE = re.compile(r"^【《殭屍學園》[^\r\n]*】\s*")


def _is_yaml_block(candidate_lines: list[str]) -> bool:
    """判斷兩個 `---` 分隔線之間的內容是否為 YAML Frontmatter 區塊。"""
    non_empty = [
        ln.strip()
        for ln in candidate_lines
        if ln.strip() and not ln.strip().startswith("#")
    ]
    if not non_empty:
        return False

    has_known_key = False
    for ln in non_empty:
        if ln.startswith("-") and not _YAML_KV_LINE_RE.match(ln):
            # 容許辨識多行 YAML 清單（後續 lint 會再標記不相容問題），但不得誤判普通 Markdown 列表
            continue
        if not _YAML_KV_LINE_RE.match(ln):
            return False
        key = ln.split(":", 1)[0].strip().lower()
        if key in KNOWN_YAML_KEYS:
            has_known_key = True

    return has_known_key


def _parse_yaml_lines(yaml_lines: list[str]) -> dict[str, Any]:
    """解析 YAML Frontmatter 各欄位（與 `game_wiki_engine.py` 規則一致）。"""
    meta: dict[str, Any] = {}
    for line in yaml_lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or ":" not in line:
            continue
        k, v = line.split(":", 1)
        k = k.strip().lower()
        v = v.strip()
        if "#" in v and "http" not in v.lower():
            v = re.split(r"\s+#", v, maxsplit=1)[0].strip()
        if k in LIST_YAML_FIELDS:
            meta[k] = _parse_list_field(v)
        else:
            meta[k] = v.strip("'\"")
    return meta


def parse_review_entries(content: str) -> list[dict[str, Any]]:
    """
    解析 Markdown / `.review.md` 檔案中的 `--- ... ---` YAML Frontmatter 區塊與內文。

    回傳每筆條目的字典，保留原始 `yaml_lines` 順序、解析後 `meta` 字典、
    常用頂層欄位 (`id`, `category`, `name_zh`, `keywords`, ...) 與 `body`。
    """
    clean_content = (content or "").lstrip("\ufeff")
    lines = clean_content.splitlines()
    delim_indices = [idx for idx, ln in enumerate(lines) if ln.strip() == "---"]
    if len(delim_indices) < 2:
        return []

    yaml_spans: list[tuple[int, int]] = []
    pos = 0
    while pos + 1 < len(delim_indices):
        start_idx = delim_indices[pos]
        end_idx = delim_indices[pos + 1]
        if _is_yaml_block(lines[start_idx + 1 : end_idx]):
            yaml_spans.append((start_idx, end_idx))
            pos += 2
        else:
            pos += 1

    entries: list[dict[str, Any]] = []
    for idx, (start_idx, end_idx) in enumerate(yaml_spans):
        yaml_lines = list(lines[start_idx + 1 : end_idx])
        body_start = end_idx + 1
        body_end = (
            yaml_spans[idx + 1][0] if idx + 1 < len(yaml_spans) else len(lines)
        )
        body_text = "\n".join(lines[body_start:body_end]).strip()
        meta = _parse_yaml_lines(yaml_lines)

        entries.append(
            {
                "id": str(meta.get("id", "")).strip(),
                "category": str(meta.get("category", "")).strip(),
                "name_zh": str(meta.get("name_zh", "")).strip(),
                "name_kr": str(meta.get("name_kr", "")).strip(),
                "timeline": str(meta.get("timeline", "")).strip(),
                "series": str(meta.get("series", "")).strip(),
                "chapter": str(meta.get("chapter", "")).strip(),
                "subject": str(meta.get("subject", "")).strip(),
                "aspects": (
                    list(meta.get("aspects", []))
                    if isinstance(meta.get("aspects"), list)
                    else []
                ),
                "source": str(meta.get("source", "")).strip(),
                "updated_at": str(meta.get("updated_at", "")).strip(),
                "freshness": str(meta.get("freshness", "")).strip(),
                "supersedes": (
                    list(meta.get("supersedes", []))
                    if isinstance(meta.get("supersedes"), list)
                    else []
                ),
                "status": str(meta.get("status", "")).strip(),
                "guide_url": str(meta.get("guide_url", "")).strip(),
                "resource_links": (
                    list(meta.get("resource_links", []))
                    if isinstance(meta.get("resource_links"), list)
                    else []
                ),
                "keywords": (
                    list(meta.get("keywords", []))
                    if isinstance(meta.get("keywords"), list)
                    else []
                ),
                "related_ids": (
                    list(meta.get("related_ids", []))
                    if isinstance(meta.get("related_ids"), list)
                    else []
                ),
                "meta": meta,
                "yaml_lines": yaml_lines,
                "body": body_text,
                "start_line": start_idx + 1,
            }
        )

    return entries


def strip_review_markers(body: str) -> str:
    """
    移除所有以 `> 🔍`、`> ⚠️`（含 `> **🔍` / `> **⚠️` 變體）開頭的審核輔助行，
    並清理多餘空白行。不影響內文中的 `- **⚠️ 防誤判提醒**` 行。
    """
    kept_lines: list[str] = []
    for line in (body or "").splitlines():
        if _REVIEW_MARKER_LINE_RE.match(line):
            continue
        kept_lines.append("" if not line.strip() else line)

    cleaned = "\n".join(kept_lines)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def _strip_contextual_header(cleaned_body: str) -> str:
    """扣除開頭的 `【《殭屍學園》...】` 全知脈絡標頭，供評估純內文字數上限。"""
    text = (cleaned_body or "").strip()
    if text.startswith("【《殭屍學園》"):
        text = _CONTEXTUAL_HEADER_RE.sub("", text, count=1).strip()
    return text


def _clean_tw_translation(raw_zh: str) -> str:
    """清除譯名欄位中的網址與來源備註標籤，保留乾淨台服譯名或 `<待考>` 標記。"""
    cleaned = re.sub(r"\(\s*https?://[^)]+\)", "", raw_zh or "")
    cleaned = re.sub(
        r"\(\s*(?:台服|namu|韓服中文|韓語|韓文)\s*\)",
        "",
        cleaned,
    )
    cleaned = re.sub(r"\s{2,}", " ", cleaned)
    cleaned = re.sub(r"\s*/\s*", " / ", cleaned)
    return cleaned.strip()


def load_character_translation_map(
    csv_path: Path | None = None,
) -> dict[str, dict[str, str]]:
    """
    讀取 `data/game_wiki/character_translations_tw.csv`，
    建立 `{韓文角色名: {"tw_name": ..., "nick": ..., "role": ...}}` 對照字典。
    """
    target_csv = Path(csv_path) if csv_path is not None else CHAR_CSV_PATH
    if not target_csv.exists():
        return {}

    char_map: dict[str, dict[str, str]] = {}
    with open(target_csv, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            kr_name = (row.get("角色名 (韓文)") or "").strip()
            if not kr_name:
                continue
            zh_raw = (row.get("中文翻譯") or "").strip()
            nick = (row.get("稱呼（大眾）") or "").strip()
            role = (row.get("身分") or "").strip()
            char_map[kr_name] = {
                "tw_name": _clean_tw_translation(zh_raw),
                "nick": nick,
                "role": role,
            }
    return char_map


def _extract_kr_aliases(kr_name: str) -> list[str]:
    """拆解如 `맥스 (Max)` 或 `기자P / 표지희` 之類的韓文鍵為可比對別名。"""
    aliases: list[str] = []
    for part in kr_name.split("/"):
        p = part.strip()
        if len(p) >= 2:
            aliases.append(p)
        no_paren = re.sub(r"\s*\([^)]*\)", "", p).strip()
        if len(no_paren) >= 2 and no_paren not in aliases:
            aliases.append(no_paren)
    return aliases


def _extract_valid_tw_names(tw_name: str) -> list[str]:
    """從 `tw_name` 提取不含 `<待考` 標記的有效繁中譯名清單。"""
    valid: list[str] = []
    for part in (tw_name or "").split("/"):
        p = part.strip()
        if not p or "<待考" in p or (p.startswith("<") and p.endswith(">")):
            continue
        if p.startswith("(") and p.endswith(")"):
            p = p[1:-1].strip()
        if p:
            valid.append(p)
    return valid


def lint_wiki_file(
    file_path: Path,
    max_body_chars: int = DEFAULT_MAX_BODY_CHARS,
    csv_path: Path | None = None,
) -> dict[str, Any]:
    """
    檢驗單一 `.review.md` 或 `.md` 知識庫檔案是否符合 YAML V2 與防幻覺規範。

    回傳字典：
    `{"file": str, "entry_count": int, "errors": list[str],
      "warnings": list[str], "unresolved_reviews": int, "ok": bool}`
    """
    path = Path(file_path)
    errors: list[str] = []
    warnings: list[str] = []

    if not path.exists():
        errors.append(f"找不到指定檔案：{path}")
        return {
            "file": str(path),
            "entry_count": 0,
            "errors": errors,
            "warnings": warnings,
            "unresolved_reviews": 0,
            "ok": False,
        }

    try:
        content = path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        errors.append(f"讀取檔案失敗 ({path})：{exc}")
        return {
            "file": str(path),
            "entry_count": 0,
            "errors": errors,
            "warnings": warnings,
            "unresolved_reviews": 0,
            "ok": False,
        }

    unresolved_reviews = sum(
        1 for line in content.splitlines() if _UNRESOLVED_REVIEW_LINE_RE.match(line)
    )
    entries = parse_review_entries(content)
    if not entries:
        errors.append("檔案中未解析到任何有效的 `--- ... ---` YAML 條目區塊。")
        return {
            "file": str(path),
            "entry_count": 0,
            "errors": errors,
            "warnings": warnings,
            "unresolved_reviews": unresolved_reviews,
            "ok": False,
        }

    char_map = load_character_translation_map(csv_path)
    seen_ids: set[str] = set()

    for idx, entry in enumerate(entries, start=1):
        meta = entry["meta"]
        entry_id = entry["id"]
        label = entry_id or f"第 {idx} 筆 (行 {entry['start_line']})"

        # 1. YAML 必填欄位檢查
        if not entry["yaml_lines"] or not re.match(r"^id:\s*\S", entry["yaml_lines"][0]):
            errors.append(
                f"[{label}] YAML 第一行（`---` 緊接的下一行）必須為 `id: ...`，否則 `game_wiki_engine` 將無法解析此卡片"
            )
        for req_field in REQUIRED_YAML_FIELDS:
            val = meta.get(req_field)
            if not val:
                errors.append(f"[{label}] 缺少或空白必填 YAML 欄位：`{req_field}`")

        # 檢查是否存在 game_wiki_engine 不支援的多行 `- item` YAML 清單
        for yline in entry["yaml_lines"]:
            ystr = yline.strip()
            if ystr.startswith("-") and ":" not in ystr:
                errors.append(
                    f"[{label}] YAML 不支援多行 `- item` 清單語法（{ystr}），請改用單行 `[a, b]` 格式"
                )
                break

        # 2. 檔案內重複 ID 檢查
        if entry_id:
            eid_lower = entry_id.lower()
            if eid_lower in seen_ids:
                errors.append(f"[{entry_id}] 檔案內出現重複的條目 ID：`{entry_id}`")
            else:
                seen_ids.add(eid_lower)

        # 3. 潔淨內文字數檢查（扣除脈絡標頭與 > 🔍 / > ⚠️ 審核輔助行）
        body_no_review = strip_review_markers(entry["body"])
        clean_body = _strip_contextual_header(body_no_review)
        body_len = len(clean_body)
        if body_len == 0:
            errors.append(f"[{label}] 扣除標頭與審核標記後，卡片內文為空")
        elif body_len > max_body_chars:
            errors.append(
                f"[{label}] 潔淨內文共 {body_len} 字，超過硬性上限 {max_body_chars} 字（請拆分為多張微卡片）"
            )
        elif body_len > WARN_BODY_CHARS:
            warnings.append(
                f"[{label}] 潔淨內文共 {body_len} 字，超過建議值 {WARN_BODY_CHARS} 字（接近上限 {max_body_chars}）"
            )

        # 4. keywords 泛用詞與孤立章節修飾詞污染檢查
        keywords = entry["keywords"]
        if keywords:
            flagged_kws = [
                kw
                for kw in keywords
                if kw.strip().lower() in _GENERIC_GAME_KEYWORDS
                or kw.strip().lower() in _MODIFIER_CHAPTER_TOKENS
            ]
            if flagged_kws:
                warnings.append(
                    f"[{label}] `keywords` 含有泛用詞或孤立章節修飾詞：{', '.join(flagged_kws)}"
                )

        # 5. 中韓混雜錯字檢查（中文與韓文直接黏連）
        mixed_tokens = _MIXED_ZH_KR_RE.findall(clean_body)
        if mixed_tokens:
            unique_mixed = list(dict.fromkeys(mixed_tokens))
            errors.append(
                f"[{label}] 內文偵測到中韓黏連錯字／漏空白：{', '.join(unique_mixed)}"
            )

        # 6. 台服角色譯名對齊檢查
        if char_map and clean_body:
            clean_body_lower = clean_body.lower()
            for kr_name, info in char_map.items():
                valid_tw_names = _extract_valid_tw_names(info.get("tw_name", ""))
                if not valid_tw_names:
                    continue

                matched_alias = ""
                for alias in _extract_kr_aliases(kr_name):
                    if len(alias) < 2:
                        continue
                    if re.search(
                        rf"(?<![\uac00-\ud7a3]){re.escape(alias)}(?![\uac00-\ud7a3])",
                        clean_body,
                        re.IGNORECASE,
                    ):
                        matched_alias = alias
                        break

                if matched_alias and not any(
                    tw.lower() in clean_body_lower for tw in valid_tw_names
                ):
                    warnings.append(
                        f"[{label}] 內文出現韓文角色名「{matched_alias}」，但未對齊台服譯名「{' / '.join(valid_tw_names)}」"
                    )

        # 7. guide_url 連結格式驗證
        guide_url = entry.get("guide_url", "")
        if guide_url:
            if not guide_url.startswith(("http://", "https://")) or any(
                ch.isspace() for ch in guide_url
            ):
                errors.append(
                    f"[{label}] `guide_url` 格式無效（須為 http:// 或 https:// 開頭且不含空白之有效網址）：`{guide_url}`"
                )
            elif "discord.com" in guide_url.lower() and not guide_url.startswith(
                (
                    "https://discord.com/channels/",
                    "https://ptb.discord.com/channels/",
                    "https://canary.discord.com/channels/",
                )
            ):
                errors.append(
                    f"[{label}] `guide_url` 若為 Discord 連結，須為標準訊息/討論串跳轉連結（`https://discord.com/channels/...`）：`{guide_url}`"
                )

        # 8. resource_links 多連結導覽驗證
        for item in entry.get("resource_links", []):
            item_str = str(item).strip()
            m = re.search(r"(https?://\S+)$", item_str)
            url = m.group(1) if m else ""
            if (
                not m
                or not url.startswith(("http://", "https://"))
                or any(ch.isspace() for ch in url)
            ):
                errors.append(
                    f"[{label}] `resource_links` 包含無效網址（須為 `標籤｜https://...` 或有效 http(s) 網址）：`{item}`"
                )
            else:
                prefix = item_str[: m.start()].strip().rstrip("|｜:：- ").strip()
                if "discord.com" in url.lower() and not url.startswith(
                    (
                        "https://discord.com/channels/",
                        "https://ptb.discord.com/channels/",
                        "https://canary.discord.com/channels/",
                    )
                ):
                    errors.append(
                        f"[{label}] `resource_links` 若為 Discord 連結，須為標準訊息/討論串跳轉連結（`https://discord.com/channels/...`）：`{item}`"
                    )
                if not prefix:
                    warnings.append(
                        f"[{label}] `resource_links` 建議加上用途標籤（如 `🎬 標題｜https://...`）：`{item}`"
                    )

    return {
        "file": str(path),
        "entry_count": len(entries),
        "errors": errors,
        "warnings": warnings,
        "unresolved_reviews": unresolved_reviews,
        "ok": len(errors) == 0,
    }


def _build_promoted_yaml_lines(yaml_lines: list[str]) -> list[str]:
    """
    清理 YAML 行：
    1. 移除 `status: pending_review` 行。
    2. 確保 `id:` 位於第一行，以符合 `game_wiki_engine._parse_markdown_wiki_file` 解析要求。
    """
    id_line: str | None = None
    other_lines: list[str] = []

    for line in yaml_lines:
        stripped = line.strip()
        if ":" in line and not stripped.startswith("#"):
            k, v = line.split(":", 1)
            key = k.strip().lower()
            val = v.strip()
            if "#" in val and "http" not in val.lower():
                val = re.split(r"\s+#", val, maxsplit=1)[0].strip()
            val = val.strip("'\"").lower()
            if key == "status" and val == "pending_review":
                continue
            if key == "id" and id_line is None:
                id_line = line
                continue
        other_lines.append(line)

    return ([id_line] + other_lines) if id_line is not None else other_lines


def _format_entry_block(yaml_lines: list[str], body: str) -> str:
    """將清理後的 YAML 行與內文格式化為標準知識庫條目區塊。"""
    yaml_text = "\n".join(yaml_lines).strip("\n")
    body_text = body.strip()
    return f"---\n{yaml_text}\n---\n{body_text}"


def promote_review_file(
    review_path: Path,
    target_path: Path,
    force: bool = False,
    max_body_chars: int = DEFAULT_MAX_BODY_CHARS,
    csv_path: Path | None = None,
) -> dict[str, Any]:
    """
    驗證 `.review.md` 草稿並正式入庫至 `target_path`：
    - 若存在 Linter Error，或仍有 `> ⚠️` 未解決標記且未指定 `force=True`，拒絕發布。
    - 剝除 `status: pending_review` 與 `> 🔍` / `> ⚠️` 審核行。
    - 依 `id` 覆蓋更新既有條目或附加新條目，並於寫入 `data/game_wiki/` 時觸發熱重載。
    """
    r_path = Path(review_path)
    t_path = Path(target_path)

    lint_result = lint_wiki_file(
        r_path,
        max_body_chars=max_body_chars,
        csv_path=csv_path,
    )
    errors = list(lint_result["errors"])
    warnings = list(lint_result["warnings"])
    unresolved = int(lint_result["unresolved_reviews"])

    if errors:
        return {
            "success": False,
            "promoted_count": 0,
            "target_path": str(t_path),
            "errors": errors,
            "warnings": warnings,
        }

    if unresolved > 0 and not force:
        errors.append(
            f"檔案尚有 {unresolved} 筆 `> ⚠️` 待人工確認標記尚未結案；請審核並移除該行後再執行，或加上 `--force` 強制入庫。"
        )
        return {
            "success": False,
            "promoted_count": 0,
            "target_path": str(t_path),
            "errors": errors,
            "warnings": warnings,
        }

    if unresolved > 0 and force:
        warnings.append(
            f"已使用 `--force` 略過 {unresolved} 筆 `> ⚠️` 待人工確認標記並自動剝除。"
        )

    review_entries = parse_review_entries(r_path.read_text(encoding="utf-8-sig"))
    promoted_blocks: list[tuple[str, str]] = []
    for entry in review_entries:
        clean_yaml = _build_promoted_yaml_lines(entry["yaml_lines"])
        clean_body = strip_review_markers(entry["body"])
        promoted_blocks.append((entry["id"], _format_entry_block(clean_yaml, clean_body)))

    promoted_by_id = {eid.lower(): block for eid, block in promoted_blocks}
    consumed_ids: set[str] = set()

    preamble = ""
    final_blocks: list[str] = []

    if t_path.exists():
        existing_content = t_path.read_text(encoding="utf-8-sig").lstrip("\ufeff")
        existing_entries = parse_review_entries(existing_content)
        if existing_entries:
            lines = existing_content.splitlines()
            first_yaml_line = existing_entries[0]["start_line"] - 1
            if first_yaml_line > 0:
                preamble = "\n".join(lines[:first_yaml_line]).strip()
            for ex in existing_entries:
                ex_id_l = ex["id"].lower()
                if ex_id_l in promoted_by_id:
                    final_blocks.append(promoted_by_id[ex_id_l])
                    consumed_ids.add(ex_id_l)
                else:
                    final_blocks.append(
                        _format_entry_block(ex["yaml_lines"], ex["body"])
                    )
        elif existing_content.strip():
            preamble = existing_content.strip()

    for eid, block in promoted_blocks:
        eid_l = eid.lower()
        if eid_l not in consumed_ids:
            final_blocks.append(block)
            consumed_ids.add(eid_l)

    t_path.parent.mkdir(parents=True, exist_ok=True)
    sections: list[str] = []
    if preamble:
        sections.append(preamble)
    sections.extend(final_blocks)
    output_text = "\n\n".join(sections) + "\n"
    t_path.write_text(output_text, encoding="utf-8")

    try:
        if t_path.parent.resolve() == WIKI_DIR.resolve():
            load_wiki_entries(force_reload=True)
    except Exception as exc:
        warnings.append(f"寫入完成，但觸發 `load_wiki_entries(force_reload=True)` 時發生警告：{exc}")

    return {
        "success": True,
        "promoted_count": len(promoted_blocks),
        "target_path": str(t_path),
        "errors": [],
        "warnings": warnings,
    }


def _resolve_target_path(target_arg: str) -> Path:
    """若 `--target` 為純檔名（如 `evolution_mode.md`），預設解析至 `data/game_wiki/`。"""
    p = Path(target_arg)
    if len(p.parts) == 1:
        return WIKI_DIR / p.name
    return p


def _print_lint_report(res: dict[str, Any]) -> None:
    status_tag = "PASS" if res["ok"] else "FAIL"
    print(
        f"[{status_tag}] {res['file']} "
        f"(entries={res['entry_count']}, unresolved_reviews={res['unresolved_reviews']}, "
        f"errors={len(res['errors'])}, warnings={len(res['warnings'])})"
    )
    for err in res["errors"]:
        print(f"  ERROR: {err}")
    for warn in res["warnings"]:
        print(f"  WARN : {warn}")


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="《殭屍學園》知識庫離線審核稿 Linter 與入庫發布工具"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    check_parser = subparsers.add_parser(
        "check",
        help="檢查 .review.md 或 .md 檔案（未指定路徑時預設掃描 data/wiki_staging/02_review/*.review.md）",
    )
    check_parser.add_argument(
        "paths",
        nargs="*",
        help="待檢查的檔案路徑清單",
    )
    check_parser.add_argument(
        "--max-body-chars",
        type=int,
        default=DEFAULT_MAX_BODY_CHARS,
        help=f"單卡潔淨內文字數硬性上限（預設 {DEFAULT_MAX_BODY_CHARS}）",
    )

    promote_parser = subparsers.add_parser(
        "promote",
        help="驗證 .review.md 並清除審核輔助行後合併發布至目標 Markdown 檔",
    )
    promote_parser.add_argument(
        "review_file",
        help="來源 .review.md 檔案路徑",
    )
    promote_parser.add_argument(
        "--target",
        required=True,
        help="目標檔案（若只給檔名如 evolution_mode.md，將自動寫入 data/game_wiki/）",
    )
    promote_parser.add_argument(
        "--force",
        action="store_true",
        help="即使尚有 `> ⚠️` 待人工確認標記也強制剝除並入庫",
    )
    promote_parser.add_argument(
        "--max-body-chars",
        type=int,
        default=DEFAULT_MAX_BODY_CHARS,
        help=f"單卡潔淨內文字數硬性上限（預設 {DEFAULT_MAX_BODY_CHARS}）",
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    parser = _build_arg_parser()
    args = parser.parse_args(argv)

    if args.command == "check":
        if args.paths:
            target_files = [Path(p) for p in args.paths]
        else:
            target_files = (
                sorted(DEFAULT_STAGING_REVIEW_DIR.glob("*.review.md"))
                if DEFAULT_STAGING_REVIEW_DIR.exists()
                else []
            )
            if not target_files:
                print(
                    f"未在 {DEFAULT_STAGING_REVIEW_DIR} 找到任何 `*.review.md` 檔案。"
                )
                return 0

        has_error = False
        for fpath in target_files:
            res = lint_wiki_file(fpath, max_body_chars=args.max_body_chars)
            _print_lint_report(res)
            if not res["ok"]:
                has_error = True
        return 1 if has_error else 0

    if args.command == "promote":
        target_path = _resolve_target_path(args.target)
        res = promote_review_file(
            Path(args.review_file),
            target_path,
            force=args.force,
            max_body_chars=args.max_body_chars,
        )
        for warn in res["warnings"]:
            print(f"  WARN : {warn}")
        if not res["success"]:
            for err in res["errors"]:
                print(f"  ERROR: {err}", file=sys.stderr)
            return 1
        print(
            f"[PROMOTED] 已將 {res['promoted_count']} 筆淨化卡片寫入 {res['target_path']}"
        )
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
