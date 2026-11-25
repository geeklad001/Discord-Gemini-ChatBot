"""Text sanitization, duration-weighted truncation, and sentence chunking for TTS."""

from __future__ import annotations

import re
from typing import Optional

try:
    import emoji as _emoji_lib  # type: ignore
except ImportError:
    _emoji_lib = None

_UNICODE_EMOJI_RE = re.compile(
    r"[\U0001F000-\U0001FAFF\u2600-\u27BF\u2300-\u23FF\uFE0F\u200D]"
)
_SPEECH_CJK_CHAR_RE = re.compile(r"[\u3040-\u30FF\u3100-\u312F\u4E00-\u9FFF\uAC00-\uD7AF]")
_SPEECH_LATIN_CHAR_RE = re.compile(r"[A-Za-z]")


def is_latin_dominant(text: str) -> bool:
    """Return True when text is predominantly English/Latin rather than CJK."""
    s = str(text or "")
    latin_cnt = len(_SPEECH_LATIN_CHAR_RE.findall(s))
    cjk_cnt = len(_SPEECH_CJK_CHAR_RE.findall(s))
    return latin_cnt >= 12 and latin_cnt > cjk_cnt * 2


def _char_speech_units(ch: str) -> float:
    return 1.0 if _SPEECH_CJK_CHAR_RE.match(ch) else 0.22


def truncate_for_tts(text: str, max_units: int) -> str:
    """Truncate text using duration-weighted speech units (1.0 CJK, 0.22 ASCII) on clean boundaries."""
    s = (text or "").strip()
    if not s:
        return ""
    limit = max(10, int(max_units))
    units = 0.0
    cut_idx: Optional[int] = None
    for i, ch in enumerate(s):
        units += _char_speech_units(ch)
        if units > limit + 1e-9:
            cut_idx = i
            break
    if cut_idx is None:
        return s.rstrip("，,、 ")

    prefix = s[:cut_idx]
    min_sent_idx = int(len(prefix) * 0.70)
    best_sent = -1
    for m in re.finditer(r"[。！？!?]|(?:\.(?=\s|$))", prefix):
        if m.end() >= min_sent_idx:
            best_sent = m.end()
    if best_sent > 0:
        return prefix[:best_sent].strip().rstrip("，,、 ")

    if cut_idx < len(s) and re.match(r"[A-Za-z0-9']", prefix[-1:] or "") and re.match(r"[A-Za-z0-9']", s[cut_idx]):
        last_ws = max(prefix.rfind(" "), prefix.rfind("\t"), prefix.rfind("\n"))
        if last_ws > 0:
            prefix = prefix[:last_ws]

    return prefix.strip().rstrip("，,、 ")


def _split_oversized_piece(piece: str, limit: int) -> list[str]:
    """Split an oversized clause cleanly without cutting English words in the middle."""
    rem = (piece or "").strip()
    if not rem:
        return []
    out: list[str] = []
    while len(rem) > limit:
        window = rem[: limit + 1]
        ws = max(window.rfind(" "), window.rfind("\t"))
        if ws >= limit // 3:
            part = rem[:ws].strip()
            rem = rem[ws + 1 :].strip()
        else:
            part = rem[:limit].strip()
            rem = rem[limit:].strip()
        if part:
            out.append(part)
    if rem:
        out.append(rem)
    return out


def clean_text_for_tts(raw_text: str, max_chars: int = 500) -> str:
    """Clean Discord markup, URLs, mentions, code blocks, and emojis for natural speech."""
    if not raw_text:
        return ""
    text = raw_text

    # Remove fenced code blocks and unwrap inline code backticks
    text = re.sub(r"```[\s\S]*?```", " ", text)
    text = re.sub(r"`([^`]*)`", r"\1", text)

    # Remove URLs and Discord custom emojis / mentions
    text = re.sub(r"https?://\S+", "", text)
    text = re.sub(r"<a?:[a-zA-Z0-9_]+:[0-9]+>", "", text)
    text = re.sub(r"<@!?[0-9]+>", "", text)
    text = re.sub(r"<@&[0-9]+>", "", text)
    text = re.sub(r"<#[0-9]+>", "", text)

    # Remove Unicode emojis without touching CJK / Japanese Kana / Hangul
    if _emoji_lib is not None:
        text = _emoji_lib.replace_emoji(text, replace="")
    else:
        text = _UNICODE_EMOJI_RE.sub("", text)

    # Strip Discord markdown prefixes & characters
    text = re.sub(r"^\s*>\s?", "", text, flags=re.MULTILINE)
    text = re.sub(r"[*_~|`]", "", text)

    # Remove decorative CJK brackets and tildes
    for ch in ("「", "」", "『", "』", "《", "》", "【", "】"):
        text = text.replace(ch, "")
    text = text.replace("～", " ").replace("~", " ")

    # Collapse repeated punctuation while preserving script-appropriate marks and decimals
    text = re.sub(r"!+", "!", text)
    text = re.sub(r"！+", "！", text)
    text = re.sub(r"\?+", "?", text)
    text = re.sub(r"？+", "？", text)
    text = re.sub(r",+", ",", text)
    text = re.sub(r"，+", "，", text)
    text = re.sub(r"。+", "。", text)
    text = re.sub(r"(?:\.{2,}|…+)", "。", text)

    # Collapse whitespace
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return ""

    # Skip pure-punctuation strings (preserve ASCII, CJK, Kana, Bopomofo, Hangul)
    if not re.search(r"[0-9A-Za-z\u3040-\u30FF\u3100-\u312F\u4E00-\u9FFF\uAC00-\uD7AF]", text):
        return ""

    return truncate_for_tts(text, max_chars)


def split_into_speech_chunks(
    text: str,
    max_chunk_chars: int = 45,
    *,
    first_chunk_max_chars: Optional[int] = None,
) -> list[str]:
    """Split sanitized text into natural sentence chunks for low-latency streaming playback."""
    cleaned = (text or "").strip()
    if not cleaned:
        return []
    limit = max(15, int(max_chunk_chars))
    first_limit = (
        max(15, min(limit, int(first_chunk_max_chars)))
        if first_chunk_max_chars is not None
        else limit
    )
    if len(cleaned) <= first_limit:
        return [cleaned]

    # Split on primary CJK and English sentence boundaries (preserving decimals like 3.14)
    raw_parts = re.split(r"(?<=[。！？；\n])|(?<=[.!?;])(?=\s|$)", cleaned)
    sentences = [p.strip() for p in raw_parts if p and p.strip()]
    if not sentences:
        sentences = [cleaned]

    chunks: list[str] = []
    current = ""
    for sent in sentences:
        # Never merge a second sentence into the very first chunk so Chunk 0 starts playing ASAP
        if not chunks and current:
            chunks.append(current)
            current = ""

        active_limit = first_limit if not chunks else limit
        if len(sent) > active_limit:
            if current:
                chunks.append(current)
                current = ""
            sub_parts = re.split(r"(?<=[，、：])|(?<=[,:;])(?=\s|$)", sent)
            sub_buf = ""
            for sp in sub_parts:
                sp = sp.strip()
                if not sp:
                    continue
                cur_sub_limit = first_limit if not chunks else limit
                if len(sp) > cur_sub_limit:
                    if sub_buf:
                        chunks.append(sub_buf)
                        sub_buf = ""
                        cur_sub_limit = first_limit if not chunks else limit
                    if cur_sub_limit < limit:
                        first_pieces = _split_oversized_piece(sp, cur_sub_limit)
                        if first_pieces:
                            chunks.append(first_pieces[0])
                            rest = sp[len(first_pieces[0]) :].strip()
                            if rest:
                                chunks.extend(_split_oversized_piece(rest, limit))
                    else:
                        chunks.extend(_split_oversized_piece(sp, limit))
                    continue
                sep = " " if (sub_buf and (sub_buf[-1].isascii() or sp[0].isascii())) else ""
                candidate = f"{sub_buf}{sep}{sp}" if sub_buf else sp
                if len(candidate) > cur_sub_limit:
                    if sub_buf:
                        chunks.append(sub_buf)
                    sub_buf = sp
                else:
                    sub_buf = candidate
            if sub_buf:
                chunks.append(sub_buf)
            continue

        candidate = f"{current} {sent}".strip() if current else sent
        if current and len(candidate) > active_limit:
            chunks.append(current)
            current = sent
        else:
            current = candidate

    if current:
        chunks.append(current)
    return [c for c in chunks if c]
