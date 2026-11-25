"""
Discord 攻略與公告頻道離線匯出工具 (REST-Only Discord Guide Exporter)

透過 `discord.Client.login()` 純 REST API 模式匯出指定頻道與討論串內容，
不建立 Gateway WebSocket 連線，因此不會中斷線上正在執行的 Bot 實例。

功能摘要：
1. 支援 ForumChannel、TextChannel、NewsChannel（Announcement）與 Thread 匯出。
2. 完整保留討論串（Active + Public Archived + Private Archived）、時間戳記 (UTC+8)、
   原文連結（含正確 guild_id 的 `https://discord.com/channels/...` jump_url）與 Embed 摘要。
3. 下載完整解析度圖片附件（`att.save()`）及非影片 Embed 之 Discord CDN 原圖，
   自動擷取寬高（`att.width`/`height` 或 `PIL.Image`）。
4. 僅擷取 YouTube 影片標題與頻道名稱（先檢查 `msg.embeds`，缺漏時改用 YouTube oEmbed API），
   絕不下載或串流影片檔案。
5. 產出每頻道一份 Markdown（`<out>/<channel_id>_<safe_label>.md`）、總索引 `<out>/_manifest.json`，
   並可透過 `--jsonl` 額外輸出精簡 UTF-8 JSONL（`<out>/<channel_id>_<safe_label>.jsonl`）。
6. 支援 `--text-only`（跳過圖片下載與 YouTube oEmbed 請求）與 `--limit <N>`（只擷取最新 N 則並按時間正序輸出）。
"""

from __future__ import annotations

import argparse
import asyncio
import datetime
import hashlib
import io
import json
import logging
import os
import re
import sys
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator, Optional

import aiohttp
import discord
from PIL import Image

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.config import configure_windows_utf8, init_runtime_env

TZ_TAIPEI = datetime.timezone(datetime.timedelta(hours=8))
DEFAULT_OUT_DIR = "data/wiki_staging/01_raw/discord_guides"
MAX_EMBED_IMAGE_BYTES = 15 * 1024 * 1024
IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp", ".gif", ".apng")
DISCORD_IMAGE_HOSTS = {"cdn.discordapp.com", "media.discordapp.net"}
WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}
MIME_TO_EXT = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/webp": ".webp",
    "image/gif": ".gif",
    "image/apng": ".png",
}

YOUTUBE_URL_RE = re.compile(
    r"(https?://(?:www\.|m\.)?(?:youtube\.com/(?:watch\?(?:[^\s<>()\"']*?&)?v=|shorts/|embed/|live/)|youtu\.be/)([a-zA-Z0-9_-]{11})(?:[^\s<>()\"']*)?)",
    re.IGNORECASE,
)
YOUTUBE_ID_RE = re.compile(
    r"(?:youtube\.com/(?:watch\?(?:[^ \n\r\t&]*&)?v=|shorts/|embed/|live/)|youtu\.be/)([a-zA-Z0-9_-]{11})",
    re.IGNORECASE,
)
MENTION_RE = re.compile(r"<(@[!&]?|#)([0-9]{15,20})>")
INVALID_FILENAME_CHARS_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')

logger = logging.getLogger("export_discord_guides")


@dataclass
class GuildContext:
    """Cached guild metadata resolved once per guild in REST-only mode."""

    guild_id: int
    guild_name: Optional[str] = None
    channel_name_map: dict[int, str] = field(default_factory=dict)
    role_name_map: dict[int, str] = field(default_factory=dict)
    active_threads: list[discord.Thread] = field(default_factory=list)


def parse_channel_spec(raw: str) -> tuple[int, Optional[str]]:
    """Parse `<channel_id>` or `<channel_id>:<label>` CLI option."""
    spec = (raw or "").strip()
    if not spec:
        raise argparse.ArgumentTypeError("頻道參數不可為空。")

    if ":" in spec:
        raw_id, raw_label = spec.split(":", 1)
        label = raw_label.strip() or None
    else:
        raw_id, label = spec, None

    try:
        channel_id = int(raw_id.strip())
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"無效的頻道 ID「{raw_id}」，格式應為 <channel_id> 或 <channel_id>:<label>。"
        ) from exc

    if channel_id <= 0:
        raise argparse.ArgumentTypeError(f"頻道 ID 必須為正整數：{channel_id}")

    return channel_id, label


def parse_positive_int(raw: str) -> int:
    """Parse a strictly positive integer CLI argument (>= 1)."""
    try:
        value = int(raw)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"無效的整數：{raw}") from exc
    if value < 1:
        raise argparse.ArgumentTypeError(f"數值必須大於或等於 1：{value}")
    return value


def build_arg_parser() -> argparse.ArgumentParser:
    """Build CLI argument parser."""
    parser = argparse.ArgumentParser(
        description="以 REST-Only 模式匯出指定 Discord 頻道與討論串內容（含原圖與 YouTube 標題）。"
    )
    parser.add_argument(
        "--profile",
        default="community",
        help="要載入的設定檔名稱（對應 .env.<profile>，預設為 community）。若同時指定 --env 則以 --env 為準。",
    )
    parser.add_argument(
        "--env",
        default=None,
        help="明確指定根目錄下的 .env.* 檔名（例如 .env.community）。",
    )
    parser.add_argument(
        "--out",
        default=DEFAULT_OUT_DIR,
        help=f"輸出目錄路徑（預設：{DEFAULT_OUT_DIR}）。",
    )
    parser.add_argument(
        "--channel",
        action="append",
        required=True,
        type=parse_channel_spec,
        dest="channels",
        help="目標頻道，格式為 <channel_id> 或 <channel_id>:<label>，可重複指定多次。",
    )
    parser.add_argument(
        "--text-only",
        action="store_true",
        default=False,
        help="僅匯出文字與中繼資料，跳過所有圖片下載與 YouTube oEmbed 請求。",
    )
    parser.add_argument(
        "--limit",
        type=parse_positive_int,
        default=None,
        help="每個頻道／討論串僅擷取最新 N 則訊息並依時間正序輸出（預設全部匯出）。",
    )
    parser.add_argument(
        "--jsonl",
        action="store_true",
        default=False,
        help="額外輸出每則訊息一行的精簡 UTF-8 JSONL（<channel_id>_<safe_label>.jsonl）。",
    )
    return parser


def resolve_output_dir(raw_out: str) -> Path:
    """Resolve output directory; keep default anchored to REPO_ROOT."""
    candidate = Path(raw_out)
    if candidate.is_absolute():
        return candidate
    if raw_out == DEFAULT_OUT_DIR:
        return REPO_ROOT / candidate
    return Path.cwd() / candidate


def sanitize_filename(raw_name: Optional[str], fallback_ext: str = ".png") -> str:
    """Sanitize attachment or embed image filename to prevent path traversal and invalid chars."""
    base = Path((raw_name or "").replace("\\", "/")).name
    cleaned = INVALID_FILENAME_CHARS_RE.sub("_", base).strip(" .")
    if not cleaned:
        return f"image{fallback_ext}"

    stem = Path(cleaned).stem.strip(" .")
    suffix = Path(cleaned).suffix[:16]
    if not stem:
        stem = "image"
    if stem.upper() in WINDOWS_RESERVED_NAMES:
        stem = f"file_{stem}"

    stem = stem[:80]
    if not suffix and fallback_ext:
        suffix = fallback_ext
    return f"{stem}{suffix}"


def sanitize_label(label: str) -> str:
    """Sanitize channel label for `<channel_id>_<safe_label>.md` filename."""
    cleaned = INVALID_FILENAME_CHARS_RE.sub("_", label or "")
    cleaned = re.sub(r"\s+", "_", cleaned)
    cleaned = re.sub(r"_+", "_", cleaned).strip(" ._")
    if not cleaned or cleaned.upper() in WINDOWS_RESERVED_NAMES:
        return "channel"
    return cleaned[:60]


def format_utc8(dt: Optional[datetime.datetime]) -> str:
    """Format an aware/naive datetime in UTC+8 (`YYYY-MM-DD HH:MM:SS +08:00`)."""
    if dt is None:
        return "未知時間"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return dt.astimezone(TZ_TAIPEI).strftime("%Y-%m-%d %H:%M:%S +08:00")


def resolve_jump_url(msg: discord.Message, fallback_guild_id: Optional[int]) -> str:
    """Build exact guild message jump URL even when msg.guild is uncached in REST-only mode."""
    guild_id = getattr(getattr(msg, "guild", None), "id", None) or fallback_guild_id
    if guild_id:
        return f"https://discord.com/channels/{guild_id}/{msg.channel.id}/{msg.id}"
    return msg.jump_url


def format_message_content(content: str, msg: discord.Message, guild_ctx: Optional[GuildContext]) -> str:
    """Resolve user/role/channel mentions without falling back to false `#deleted-channel` labels."""
    if not content:
        return ""

    mention_users = {u.id: u.display_name for u in getattr(msg, "mentions", []) or []}
    mention_roles = {r.id: r.name for r in getattr(msg, "role_mentions", []) or []}

    def _replace(match: re.Match[str]) -> str:
        prefix, raw_id = match.group(1), match.group(2)
        entity_id = int(raw_id)
        if prefix in ("@", "@!"):
            name = mention_users.get(entity_id)
            return f"@{name}" if name else f"<@{entity_id}>"
        if prefix == "@&":
            name = mention_roles.get(entity_id)
            if not name and guild_ctx:
                name = guild_ctx.role_name_map.get(entity_id)
            return f"@{name}" if name else f"<@&{entity_id}>"
        if prefix == "#":
            if guild_ctx and entity_id in guild_ctx.channel_name_map:
                return f"#{guild_ctx.channel_name_map[entity_id]}"
            return f"<#{entity_id}>"
        return match.group(0)

    return MENTION_RE.sub(_replace, content)


def is_image_attachment(att: discord.Attachment) -> bool:
    """Check whether an attachment is an image by MIME type or extension."""
    ctype = (att.content_type or "").lower()
    fname = (att.filename or "").lower()
    return ctype.startswith("image/") or fname.endswith(IMAGE_EXTENSIONS)


def inspect_image_dimensions_from_path(img_path: Path) -> tuple[Optional[int], Optional[int]]:
    """Read `(width, height)` from a saved image file via PIL."""
    try:
        with Image.open(img_path) as im:
            return int(im.width), int(im.height)
    except Exception:
        return None, None


def inspect_image_info_from_bytes(raw_bytes: bytes) -> tuple[Optional[int], Optional[int], Optional[str]]:
    """Read `(width, height, format_ext)` from in-memory image bytes via PIL."""
    try:
        with Image.open(io.BytesIO(raw_bytes)) as im:
            fmt = (im.format or "").upper()
            ext = {
                "PNG": ".png",
                "JPEG": ".jpg",
                "JPG": ".jpg",
                "WEBP": ".webp",
                "GIF": ".gif",
            }.get(fmt)
            return int(im.width), int(im.height), ext
    except Exception:
        return None, None, None


def strip_media_resize_params(url: str) -> str:
    """Remove `width`/`height` resize params from `media.discordapp.net` while preserving auth query params."""
    parsed = urllib.parse.urlsplit(url)
    host = (parsed.hostname or "").lower()
    if host != "media.discordapp.net":
        return url
    qs = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    filtered = [(k, v) for k, v in qs if k.lower() not in ("width", "height")]
    new_query = urllib.parse.urlencode(filtered)
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, new_query, parsed.fragment))


def _url_dedup_key(url: str) -> tuple[str, str]:
    """Normalize a Discord CDN URL to `(host, path)` for attachment/embed deduplication."""
    parsed = urllib.parse.urlsplit(url)
    return ((parsed.hostname or "").lower(), parsed.path)


def extract_youtube_links(text: str) -> list[dict[str, str]]:
    """Extract unique YouTube links (`watch`, `youtu.be`, `shorts`, `embed`, `live`) from text."""
    if not text:
        return []
    found: list[dict[str, str]] = []
    seen_ids: set[str] = set()
    for match in YOUTUBE_URL_RE.finditer(text):
        raw_url = match.group(1).rstrip(".,;:!?)]}」』】》>、。，！？")
        video_id = match.group(2)
        if video_id in seen_ids:
            continue
        seen_ids.add(video_id)
        found.append(
            {
                "video_id": video_id,
                "original_url": raw_url,
                "canonical_url": f"https://www.youtube.com/watch?v={video_id}",
            }
        )
    return found


def _extract_youtube_id(url: Optional[str]) -> Optional[str]:
    if not url:
        return None
    m = YOUTUBE_ID_RE.search(url)
    return m.group(1) if m else None


class YouTubeTitleResolver:
    """Resolve YouTube video titles from message embeds first, falling back to oEmbed API."""

    def __init__(self, session: aiohttp.ClientSession) -> None:
        self._session = session
        self._cache: dict[str, dict[str, Optional[str]]] = {}

    async def _fetch_oembed(self, target_url: str) -> Optional[dict[str, Optional[str]]]:
        oembed_url = "https://www.youtube.com/oembed?" + urllib.parse.urlencode(
            {"url": target_url, "format": "json"}
        )
        try:
            timeout = aiohttp.ClientTimeout(total=5)
            async with self._session.get(oembed_url, timeout=timeout) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json(content_type=None)
        except Exception as exc:
            logger.debug("YouTube oEmbed 查詢失敗 (%s): %s", target_url, exc)
            return None

        title = (data.get("title") or "").strip() or None
        author_name = (data.get("author_name") or "").strip() or None
        if not title:
            return None
        return {"title": title, "author_name": author_name}

    async def resolve_for_message(
        self,
        text_blocks: list[str],
        embeds: list[discord.Embed],
        msg_id: int,
        thread_id: Optional[int],
        jump_url: str,
    ) -> list[dict[str, Any]]:
        """Resolve YouTube metadata for all YouTube URLs detected in message text blocks."""
        links: list[dict[str, str]] = []
        seen_ids: set[str] = set()
        for text in text_blocks:
            for item in extract_youtube_links(text):
                if item["video_id"] not in seen_ids:
                    seen_ids.add(item["video_id"])
                    links.append(item)

        if not links:
            return []

        results: list[dict[str, Any]] = []
        for item in links:
            vid = item["video_id"]
            canonical_url = item["canonical_url"]
            original_url = item["original_url"]
            title: Optional[str] = None
            author_name: Optional[str] = None
            source = "unresolved"

            # 1. Check message embeds first
            for emb in embeds:
                emb_type = (getattr(emb, "type", None) or "").lower()
                if emb_type and emb_type not in ("video", "rich", "link"):
                    continue
                emb_vid = _extract_youtube_id(getattr(emb, "url", None)) or _extract_youtube_id(
                    getattr(getattr(emb, "video", None), "url", None)
                )
                if emb_vid == vid and getattr(emb, "title", None):
                    title = emb.title.strip()
                    emb_author = getattr(getattr(emb, "author", None), "name", None)
                    author_name = emb_author.strip() if emb_author else None
                    source = "embed"
                    break

            # 2. Fallback to YouTube oEmbed API (cached in memory by canonical URL)
            if not title:
                if canonical_url not in self._cache:
                    meta = await self._fetch_oembed(canonical_url)
                    if meta is None and original_url != canonical_url:
                        meta = await self._fetch_oembed(original_url)
                    self._cache[canonical_url] = meta or {"title": None, "author_name": None}

                cached = self._cache[canonical_url]
                if cached.get("title"):
                    title = cached["title"]
                    author_name = cached.get("author_name")
                    source = "oembed"

            results.append(
                {
                    "video_id": vid,
                    "url": canonical_url,
                    "original_url": original_url,
                    "title": title,
                    "author_name": author_name,
                    "source": source,
                    "msg_id": str(msg_id),
                    "thread_id": str(thread_id) if thread_id else None,
                    "jump_url": jump_url,
                }
            )
        return results


async def save_attachment_image(
    att: discord.Attachment,
    idx: int,
    msg_id: int,
    thread_id: Optional[int],
    jump_url: str,
    images_dir: Path,
    out_dir: Path,
) -> tuple[Optional[dict[str, Any]], Optional[str]]:
    """Download full-resolution attachment image via `att.save()` (capped at MAX_EMBED_IMAGE_BYTES) and record dimensions."""
    if att.size and att.size > MAX_EMBED_IMAGE_BYTES:
        return None, f"附件 `{att.filename}` 大小超過 15MB 上限 ({att.size} bytes)，已略過"

    safe_name = sanitize_filename(att.filename, fallback_ext=".png")
    target_name = f"{msg_id}_att{idx}_{safe_name}"
    images_dir.mkdir(parents=True, exist_ok=True)
    img_path = images_dir / target_name

    try:
        await att.save(img_path)
    except Exception as exc:
        if img_path.exists():
            img_path.unlink(missing_ok=True)
        return None, f"附件 `{att.filename}` 下載失敗：{exc}"

    if img_path.exists() and img_path.stat().st_size > MAX_EMBED_IMAGE_BYTES:
        actual_size = img_path.stat().st_size
        img_path.unlink(missing_ok=True)
        return None, f"附件 `{att.filename}` 大小超過 15MB 上限 ({actual_size} bytes)，已略過"

    width = att.width
    height = att.height
    if width is None or height is None:
        pil_w, pil_h = inspect_image_dimensions_from_path(img_path)
        width = width if width is not None else pil_w
        height = height if height is not None else pil_h

    rel_path = img_path.relative_to(out_dir).as_posix()
    file_size = img_path.stat().st_size if img_path.exists() else att.size
    return (
        {
            "rel_path": rel_path,
            "msg_id": str(msg_id),
            "thread_id": str(thread_id) if thread_id else None,
            "jump_url": jump_url,
            "width": width,
            "height": height,
            "source": "attachment",
            "filename": att.filename,
            "bytes": file_size,
        },
        None,
    )


async def _fetch_limited_image_bytes(
    session: aiohttp.ClientSession,
    url: str,
) -> tuple[Optional[bytes], Optional[str], Optional[str]]:
    """Fetch image bytes up to MAX_EMBED_IMAGE_BYTES. Returns `(data, content_type, error)`."""
    timeout = aiohttp.ClientTimeout(total=30, connect=10)
    try:
        async with session.get(url, timeout=timeout) as resp:
            if resp.status != 200:
                return None, None, f"HTTP {resp.status}"

            ctype = (resp.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
            if ctype and not ctype.startswith("image/") and ctype != "application/octet-stream":
                return None, ctype, f"非圖片 Content-Type ({ctype})"

            cl_raw = resp.headers.get("Content-Length")
            if cl_raw and cl_raw.isdigit() and int(cl_raw) > MAX_EMBED_IMAGE_BYTES:
                return None, ctype, f"檔案大小超過 15MB 上限 ({cl_raw} bytes)"

            buf = bytearray()
            async for chunk in resp.content.iter_chunked(65536):
                buf.extend(chunk)
                if len(buf) > MAX_EMBED_IMAGE_BYTES:
                    return None, ctype, "檔案串流大小超過 15MB 上限"
            return bytes(buf), ctype, None
    except Exception as exc:
        return None, None, str(exc)


async def download_embed_image(
    session: aiohttp.ClientSession,
    raw_url: str,
    emb_idx: int,
    kind: str,
    meta_width: Optional[int],
    meta_height: Optional[int],
    msg_id: int,
    thread_id: Optional[int],
    jump_url: str,
    images_dir: Path,
    out_dir: Path,
) -> tuple[Optional[dict[str, Any]], Optional[str]]:
    """Download a full-resolution Discord CDN embed image (capped at 15MB)."""
    full_res_url = strip_media_resize_params(raw_url)
    data, ctype, err = await _fetch_limited_image_bytes(session, full_res_url)
    if data is None and full_res_url != raw_url:
        data, ctype, err = await _fetch_limited_image_bytes(session, raw_url)

    if data is None:
        return None, f"Embed 圖片下載失敗 ({raw_url})：{err}"

    pil_w, pil_h, pil_ext = inspect_image_info_from_bytes(data)
    if pil_w is None and ctype and not ctype.startswith("image/"):
        return None, f"Embed 圖片驗證失敗 ({raw_url})：非有效影像格式"

    fallback_ext = MIME_TO_EXT.get(ctype or "", pil_ext or ".png")
    parsed_path = urllib.parse.urlsplit(raw_url).path
    raw_name = Path(parsed_path).name or f"embed_{kind}{fallback_ext}"
    safe_name = sanitize_filename(raw_name, fallback_ext=fallback_ext)
    if Path(safe_name).suffix.lower() not in IMAGE_EXTENSIONS:
        safe_name = f"{Path(safe_name).stem[:80]}{fallback_ext}"

    target_name = f"{msg_id}_emb{emb_idx}_{kind}_{safe_name}"
    images_dir.mkdir(parents=True, exist_ok=True)
    img_path = images_dir / target_name
    img_path.write_bytes(data)

    width = pil_w if pil_w is not None else meta_width
    height = pil_h if pil_h is not None else meta_height
    rel_path = img_path.relative_to(out_dir).as_posix()
    return (
        {
            "rel_path": rel_path,
            "msg_id": str(msg_id),
            "thread_id": str(thread_id) if thread_id else None,
            "jump_url": jump_url,
            "width": width,
            "height": height,
            "source": f"embed_{kind}",
            "filename": safe_name,
            "bytes": len(data),
        },
        None,
    )


async def process_message_images(
    attachments: list[discord.Attachment],
    embeds: list[discord.Embed],
    msg_id: int,
    thread_id: Optional[int],
    jump_url: str,
    images_dir: Path,
    out_dir: Path,
    session: aiohttp.ClientSession,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Download image attachments and eligible Discord CDN embed images without duplication."""
    downloaded: list[dict[str, Any]] = []
    notes: list[str] = []
    seen_cdn_keys: set[tuple[str, str]] = set()
    seen_att_ids: set[str] = set()

    for idx, att in enumerate(attachments):
        if not is_image_attachment(att):
            continue
        for url_attr in (getattr(att, "url", None), getattr(att, "proxy_url", None)):
            if url_attr:
                seen_cdn_keys.add(_url_dedup_key(url_attr))
        seen_att_ids.add(str(att.id))

        record, err = await save_attachment_image(
            att=att,
            idx=idx,
            msg_id=msg_id,
            thread_id=thread_id,
            jump_url=jump_url,
            images_dir=images_dir,
            out_dir=out_dir,
        )
        if record:
            downloaded.append(record)
        elif err:
            notes.append(err)

    for e_idx, emb in enumerate(embeds):
        emb_type = (getattr(emb, "type", None) or "").lower()
        if emb_type == "video":
            continue

        candidates = [
            ("img", getattr(emb, "image", None)),
            ("thumb", getattr(emb, "thumbnail", None)),
        ]
        for kind, media_obj in candidates:
            if not media_obj:
                continue
            raw_url = getattr(media_obj, "url", None) or getattr(media_obj, "proxy_url", None)
            if not raw_url:
                continue
            parsed = urllib.parse.urlsplit(raw_url)
            host = (parsed.hostname or "").lower()
            if parsed.scheme not in ("http", "https") or host not in DISCORD_IMAGE_HOSTS:
                continue

            dedup_key = _url_dedup_key(raw_url)
            if dedup_key in seen_cdn_keys:
                continue
            # Also avoid duplicate download if `/attachments/<ch_id>/<att_id>/...` matches an attachment
            path_parts = [p for p in parsed.path.split("/") if p]
            if len(path_parts) >= 3 and path_parts[0] == "attachments" and path_parts[2] in seen_att_ids:
                continue

            seen_cdn_keys.add(dedup_key)
            record, err = await download_embed_image(
                session=session,
                raw_url=raw_url,
                emb_idx=e_idx,
                kind=kind,
                meta_width=getattr(media_obj, "width", None),
                meta_height=getattr(media_obj, "height", None),
                msg_id=msg_id,
                thread_id=thread_id,
                jump_url=jump_url,
                images_dir=images_dir,
                out_dir=out_dir,
            )
            if record:
                downloaded.append(record)
            elif err:
                notes.append(err)

    return downloaded, notes


async def get_guild_context(
    client: discord.Client,
    guild_id: Optional[int],
    cache: dict[int, GuildContext],
) -> Optional[GuildContext]:
    """Fetch and cache guild metadata, channel names, role names, and active threads once per guild."""
    if not guild_id:
        return None
    if guild_id in cache:
        return cache[guild_id]

    ctx = GuildContext(guild_id=guild_id)
    try:
        guild = await client.fetch_guild(guild_id)
        ctx.guild_name = guild.name
        ctx.role_name_map = {r.id: r.name for r in getattr(guild, "roles", []) or []}

        try:
            channels = await guild.fetch_channels()
            ctx.channel_name_map = {c.id: c.name for c in channels if getattr(c, "name", None)}
        except (discord.Forbidden, discord.HTTPException, discord.ClientException) as exc:
            # Fallback to raw HTTP JSON if guild contains a newer Discord channel type unknown to discord.py
            try:
                raw_channels = await client.http.get_guild_channels(guild_id)
                ctx.channel_name_map = {
                    int(c["id"]): str(c["name"])
                    for c in raw_channels
                    if isinstance(c, dict) and c.get("id") and c.get("name")
                }
            except Exception:
                logger.warning("無法取得伺服器 %s 的頻道名稱清單：%s", guild_id, exc)

        try:
            ctx.active_threads = list(await guild.active_threads())
        except (discord.Forbidden, discord.HTTPException, discord.ClientException) as exc:
            logger.warning("無法取得伺服器 %s 的 active_threads：%s", guild_id, exc)
    except (discord.Forbidden, discord.NotFound, discord.HTTPException, discord.ClientException) as exc:
        logger.warning("無法取得伺服器 %s 詳細資訊：%s", guild_id, exc)

    cache[guild_id] = ctx
    return ctx


def _sort_threads_chronologically(threads: list[discord.Thread]) -> list[discord.Thread]:
    return sorted(
        threads,
        key=lambda t: (
            t.created_at or discord.utils.snowflake_time(t.id),
            t.id,
        ),
    )


async def discover_channel_threads(
    ch: discord.ForumChannel | discord.TextChannel,
    guild_ctx: Optional[GuildContext],
) -> tuple[list[discord.Thread], list[str]]:
    """Discover active and archived threads for a ForumChannel or TextChannel/NewsChannel."""
    threads_by_id: dict[int, discord.Thread] = {}
    warnings: list[str] = []

    if guild_ctx:
        for t in guild_ctx.active_threads:
            if t.parent_id == ch.id:
                threads_by_id[t.id] = t

    try:
        async for t in ch.archived_threads(limit=None):
            threads_by_id.setdefault(t.id, t)
    except (discord.Forbidden, discord.HTTPException) as exc:
        warnings.append(f"讀取公開封存討論串失敗：{exc}")

    # Only TextChannel.archived_threads accepts `private` / `joined` kwargs in discord.py 2.x
    if isinstance(ch, discord.TextChannel):
        private_ok = False
        try:
            async for t in ch.archived_threads(limit=None, private=True):
                threads_by_id.setdefault(t.id, t)
            private_ok = True
        except (discord.Forbidden, discord.HTTPException) as exc:
            logger.debug("頻道 %s 略過 private archived_threads：%s", ch.id, exc)

        if not private_ok:
            try:
                async for t in ch.archived_threads(limit=None, private=True, joined=True):
                    threads_by_id.setdefault(t.id, t)
            except (discord.Forbidden, discord.HTTPException) as exc:
                logger.debug("頻道 %s 略過 joined private archived_threads：%s", ch.id, exc)

    return _sort_threads_chronologically(list(threads_by_id.values())), warnings


def summarize_embed(emb: discord.Embed) -> list[str]:
    """Render concise human-readable Markdown lines for an embed."""
    lines: list[str] = []
    emb_type = getattr(emb, "type", None) or "rich"
    header_parts: list[str] = [f"[{emb_type}]"]
    if emb.title:
        header_parts.append(emb.title.strip())
    if emb.url:
        header_parts.append(f"({emb.url})")
    lines.append("- Embed " + " ".join(header_parts))

    emb_author = getattr(getattr(emb, "author", None), "name", None)
    if emb_author:
        lines.append(f"  - Author: {emb_author.strip()}")
    if emb.description:
        desc = emb.description.strip().replace("\r\n", "\n")
        for dl in desc.splitlines():
            lines.append(f"  > {dl}")
    for fld in getattr(emb, "fields", []) or []:
        fname = (fld.name or "").strip()
        fval = (fld.value or "").strip().replace("\r\n", " / ")
        if fname or fval:
            lines.append(f"  - **{fname}**: {fval}")
    emb_footer = getattr(getattr(emb, "footer", None), "text", None)
    if emb_footer:
        lines.append(f"  - Footer: {emb_footer.strip()}")
    return lines


async def export_message_record(
    msg: discord.Message,
    fallback_guild_id: Optional[int],
    guild_ctx: Optional[GuildContext],
    thread_id: Optional[int],
    is_starter: bool,
    images_dir: Path,
    out_dir: Path,
    session: aiohttp.ClientSession,
    yt_resolver: YouTubeTitleResolver,
    *,
    text_only: bool = False,
) -> dict[str, Any]:
    """Process a single Discord message: text, embeds, and optionally images and YouTube titles."""
    jump_url = resolve_jump_url(msg, fallback_guild_id)

    all_attachments: list[discord.Attachment] = list(msg.attachments or [])
    all_embeds: list[discord.Embed] = list(msg.embeds or [])
    text_blocks: list[str] = []

    main_text = format_message_content(msg.content or "", msg, guild_ctx)
    if main_text.strip():
        text_blocks.append(main_text)

    snapshots = list(getattr(msg, "message_snapshots", []) or [])
    for s_idx, snap in enumerate(snapshots, start=1):
        snap_text = format_message_content(getattr(snap, "content", "") or "", msg, guild_ctx)
        if snap_text.strip():
            text_blocks.append(f"[轉發訊息 #{s_idx}]\n{snap_text}")
        all_attachments.extend(getattr(snap, "attachments", []) or [])
        all_embeds.extend(getattr(snap, "embeds", []) or [])

    if text_only:
        images: list[dict[str, Any]] = []
        image_notes: list[str] = []
        youtube_items: list[dict[str, Any]] = []
    else:
        images, image_notes = await process_message_images(
            attachments=all_attachments,
            embeds=all_embeds,
            msg_id=msg.id,
            thread_id=thread_id,
            jump_url=jump_url,
            images_dir=images_dir,
            out_dir=out_dir,
            session=session,
        )

        youtube_items = await yt_resolver.resolve_for_message(
            text_blocks=text_blocks,
            embeds=all_embeds,
            msg_id=msg.id,
            thread_id=thread_id,
            jump_url=jump_url,
        )

    other_attachments: list[dict[str, Any]] = []
    for att in all_attachments:
        if not is_image_attachment(att):
            other_attachments.append(
                {
                    "filename": att.filename,
                    "content_type": att.content_type or "unknown",
                    "size": att.size,
                    "url": att.url,
                }
            )

    embed_lines: list[str] = []
    for emb in all_embeds:
        has_text = any(
            [
                emb.title,
                emb.description,
                getattr(emb, "fields", None),
                getattr(getattr(emb, "author", None), "name", None),
                getattr(getattr(emb, "footer", None), "text", None),
            ]
        )
        if has_text:
            embed_lines.extend(summarize_embed(emb))

    stickers = [s.name for s in getattr(msg, "stickers", []) or [] if getattr(s, "name", None)]
    author = msg.author
    is_bot = bool(getattr(author, "bot", False))
    raw_author_id = getattr(author, "id", None)
    author_hash = (
        hashlib.sha256(str(raw_author_id).encode("utf-8")).hexdigest()[:16]
        if raw_author_id is not None
        else "unknown"
    )
    author_label = f"{author.display_name} (@{author.name}, id={author.id})"
    if is_bot:
        author_label += " [BOT]"

    return {
        "msg_id": str(msg.id),
        "thread_id": str(thread_id) if thread_id else None,
        "author": author_label,
        "author_hash": author_hash,
        "is_bot": is_bot,
        "created_at": format_utc8(msg.created_at),
        "edited_at": format_utc8(msg.edited_at) if msg.edited_at else None,
        "pinned": bool(msg.pinned),
        "is_starter": is_starter,
        "reply_to_msg_id": (
            str(msg.reference.message_id)
            if msg.reference and msg.reference.message_id
            else None
        ),
        "jump_url": jump_url,
        "text_blocks": text_blocks,
        "embed_lines": embed_lines,
        "images": images,
        "image_notes": image_notes,
        "other_attachments": other_attachments,
        "youtube_items": youtube_items,
        "stickers": stickers,
    }


def _render_message_markdown(lines: list[str], rec: dict[str, Any]) -> None:
    """Append Markdown representation of a single message record."""
    badges: list[str] = []
    if rec["is_starter"]:
        badges.append("STARTER")
    if rec["pinned"]:
        badges.append("PINNED")
    badge_str = f" `{'` `'.join(badges)}`" if badges else ""

    edited_str = f" *(edited {rec['edited_at']})*" if rec["edited_at"] else ""
    lines.append(f"#### Message `{rec['msg_id']}`{badge_str}")
    lines.append(
        f"- **Author**: {rec['author']} | **Time**: {rec['created_at']}{edited_str}"
    )
    lines.append(f"- **Jump URL**: {rec['jump_url']}")
    if rec["reply_to_msg_id"]:
        lines.append(f"- **Replying to**: `{rec['reply_to_msg_id']}`")
    lines.append("")

    if rec["text_blocks"]:
        for block in rec["text_blocks"]:
            lines.append(block)
            lines.append("")
    elif not rec["embed_lines"] and not rec["images"] and not rec["other_attachments"]:
        lines.append("*(無文字內容)*")
        lines.append("")

    if rec["stickers"]:
        lines.append(f"- **Stickers**: {', '.join(rec['stickers'])}")
        lines.append("")

    if rec["embed_lines"]:
        lines.append("**Embeds:**")
        lines.extend(rec["embed_lines"])
        lines.append("")

    if rec["images"]:
        lines.append("**Images:**")
        for img in rec["images"]:
            dim = (
                f"{img['width']}x{img['height']}"
                if img["width"] is not None and img["height"] is not None
                else "unknown dimensions"
            )
            lines.append(
                f"- `{img['rel_path']}` ({dim}, source={img['source']}, {img['bytes']} bytes)"
            )
            lines.append(f"  ![{img['filename']}]({img['rel_path']})")
        lines.append("")

    if rec["image_notes"]:
        for note in rec["image_notes"]:
            lines.append(f"> ⚠️ {note}")
        lines.append("")

    if rec["other_attachments"]:
        lines.append("**Other Attachments:**")
        for att in rec["other_attachments"]:
            lines.append(
                f"- `{att['filename']}` ({att['content_type']}, {att['size']} bytes) — {att['url']}"
            )
        lines.append("")

    if rec["youtube_items"]:
        lines.append("**YouTube Videos (Title Only):**")
        for yt in rec["youtube_items"]:
            title_display = yt["title"] or "(無法取得標題)"
            author_part = f" — {yt['author_name']}" if yt.get("author_name") else ""
            lines.append(
                f"- [{title_display}]({yt['url']}){author_part} *(source={yt['source']})*"
            )
        lines.append("")

    lines.append("---")
    lines.append("")


def render_channel_markdown(
    channel_id: int,
    label: str,
    channel_name: str,
    channel_type: str,
    guild_id: Optional[int],
    guild_name: Optional[str],
    exported_at: str,
    available_tags: list[str],
    channel_messages: list[dict[str, Any]],
    thread_sections: list[dict[str, Any]],
    warnings: list[str],
    total_messages: int,
    total_images: int,
    total_youtube: int,
) -> str:
    """Render complete Markdown document for a single exported channel."""
    lines: list[str] = [
        f"# Discord 頻道匯出：{label} (`{channel_name}`)",
        "",
        "## 頻道中繼資料 (Channel Metadata)",
        f"- **Channel ID**: `{channel_id}`",
        f"- **Channel Mention**: `<#{channel_id}>`",
        f"- **Label**: {label}",
        f"- **Channel Name**: `{channel_name}`",
        f"- **Channel Type**: `{channel_type}`",
        f"- **Guild ID**: `{guild_id}`" + (f" (`{guild_name}`)" if guild_name else ""),
        f"- **Exported At (UTC+8)**: {exported_at}",
        (
            f"- **Stats**: {total_messages} messages | "
            f"{len(thread_sections)} threads | "
            f"{total_images} images | "
            f"{total_youtube} YouTube links"
        ),
    ]
    if available_tags:
        lines.append(f"- **Available Forum Tags**: {', '.join(available_tags)}")
    if warnings:
        lines.append("- **Warnings**:")
        for w in warnings:
            lines.append(f"  - {w}")
    lines.extend(["", "---", ""])

    if channel_messages:
        lines.append("## 頻道主體訊息 (Channel Messages)")
        lines.append("")
        for rec in channel_messages:
            _render_message_markdown(lines, rec)
    elif channel_type in ("TextChannel", "NewsChannel"):
        lines.append("## 頻道主體訊息 (Channel Messages)")
        lines.append("")
        lines.append("*(此頻道沒有主體訊息)*")
        lines.extend(["", "---", ""])

    if thread_sections:
        lines.append("## 討論串 (Threads)")
        lines.append("")
        for sec in thread_sections:
            lines.append(f"### Thread: {sec['name']} (`{sec['thread_id']}`)")
            lines.append(
                f"- **Created At (UTC+8)**: {sec['created_at']} | "
                f"**Archived**: {sec['archived']} | "
                f"**Locked**: {sec['locked']} | "
                f"**Messages**: {len(sec['messages'])}"
            )
            if sec["applied_tags"]:
                lines.append(f"- **Tags**: {', '.join(sec['applied_tags'])}")
            if sec["thread_url"]:
                lines.append(f"- **Thread URL**: {sec['thread_url']}")
            if sec["error"]:
                lines.append(f"- **Warning**: {sec['error']}")
            lines.append("")

            if not sec["messages"]:
                lines.append("*(此討論串沒有可讀取的訊息)*")
                lines.extend(["", "---", ""])
            else:
                for rec in sec["messages"]:
                    _render_message_markdown(lines, rec)

    return "\n".join(lines).rstrip() + "\n"


def _resolve_thread_tags(
    thread: discord.Thread,
    parent_channel: Optional[discord.ForumChannel],
) -> list[str]:
    """Resolve Forum thread tags using parent ForumChannel directly in REST-only mode."""
    if parent_channel is None:
        return [t.name for t in getattr(thread, "applied_tags", []) or [] if getattr(t, "name", None)]

    names: list[str] = []
    for tag_id in getattr(thread, "_applied_tags", ()) or ():
        tag = parent_channel.get_tag(int(tag_id))
        if tag and tag.name:
            names.append(tag.name)
    return names


async def _iter_history_chronological(
    history_target: Any,
    limit: Optional[int] = None,
) -> AsyncIterator[discord.Message]:
    """Yield messages chronologically; when `limit` is set, fetch newest `limit` and sort."""
    if limit is None:
        async for msg in history_target.history(limit=None, oldest_first=True):
            yield msg
        return

    batch = [msg async for msg in history_target.history(limit=limit, oldest_first=False)]
    batch.sort(
        key=lambda m: (
            m.created_at or discord.utils.snowflake_time(m.id),
            m.id,
        )
    )
    for msg in batch:
        yield msg


def _write_channel_jsonl(
    jsonl_path: Path,
    channel_messages: list[dict[str, Any]],
    thread_sections: list[dict[str, Any]],
) -> None:
    """Write compact UTF-8 JSONL records for channel and thread messages."""
    all_records: list[dict[str, Any]] = list(channel_messages)
    for sec in thread_sections:
        all_records.extend(sec["messages"])

    lines = [
        json.dumps(
            {
                "msg_id": rec["msg_id"],
                "thread_id": rec.get("thread_id"),
                "is_bot": bool(rec.get("is_bot", False)),
                "author_hash": rec.get("author_hash", "unknown"),
                "created_at": rec["created_at"],
                "reply_to_msg_id": rec["reply_to_msg_id"],
                "text": "\n".join(rec["text_blocks"]),
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        for rec in all_records
    ]
    jsonl_path.write_text(("\n".join(lines) + "\n") if lines else "", encoding="utf-8")


async def _export_thread_messages(
    thread: discord.Thread,
    parent_forum: Optional[discord.ForumChannel],
    guild_id: Optional[int],
    guild_ctx: Optional[GuildContext],
    images_dir: Path,
    out_dir: Path,
    session: aiohttp.ClientSession,
    yt_resolver: YouTubeTitleResolver,
    *,
    text_only: bool = False,
    limit: Optional[int] = None,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    """Fetch and process messages inside a single Thread chronologically."""
    records: list[dict[str, Any]] = []
    thread_images: list[dict[str, Any]] = []
    thread_youtube: list[dict[str, Any]] = []
    thread_error: Optional[str] = None

    try:
        async for msg in _iter_history_chronological(thread, limit=limit):
            rec = await export_message_record(
                msg=msg,
                fallback_guild_id=guild_id,
                guild_ctx=guild_ctx,
                thread_id=thread.id,
                is_starter=(msg.id == thread.id),
                images_dir=images_dir,
                out_dir=out_dir,
                session=session,
                yt_resolver=yt_resolver,
                text_only=text_only,
            )
            records.append(rec)
            thread_images.extend(rec["images"])
            thread_youtube.extend(rec["youtube_items"])
    except (discord.Forbidden, discord.NotFound, discord.HTTPException) as exc:
        thread_error = f"讀取討論串訊息失敗：{exc}"
        logger.warning("討論串 %s (%s) %s", thread.id, thread.name, thread_error)

    created_dt = thread.created_at or discord.utils.snowflake_time(thread.id)
    thread_url = f"https://discord.com/channels/{guild_id}/{thread.id}" if guild_id else None
    section = {
        "thread_id": str(thread.id),
        "name": thread.name,
        "created_at": format_utc8(created_dt),
        "archived": bool(getattr(thread, "archived", False)),
        "locked": bool(getattr(thread, "locked", False)),
        "applied_tags": _resolve_thread_tags(thread, parent_forum),
        "thread_url": thread_url,
        "error": thread_error,
        "messages": records,
    }
    return section, thread_images, thread_youtube


async def export_single_channel(
    client: discord.Client,
    channel_id: int,
    explicit_label: Optional[str],
    out_dir: Path,
    exported_at: str,
    guild_cache: dict[int, GuildContext],
    session: aiohttp.ClientSession,
    yt_resolver: YouTubeTitleResolver,
    *,
    text_only: bool = False,
    limit: Optional[int] = None,
    write_jsonl: bool = False,
) -> dict[str, Any]:
    """Fetch and export one Discord channel (including threads, images, and YouTube titles)."""
    fallback_label = explicit_label or f"channel_{channel_id}"
    try:
        ch = await client.fetch_channel(channel_id)
    except (discord.Forbidden, discord.NotFound, discord.HTTPException, discord.InvalidData) as exc:
        err_msg = f"{type(exc).__name__}: {exc}"
        logger.error("無法存取頻道 %s (%s)：%s", channel_id, fallback_label, err_msg)
        return {
            "channel_id": str(channel_id),
            "label": fallback_label,
            "channel_name": None,
            "channel_type": None,
            "guild_id": None,
            "status": "error",
            "error": err_msg,
            "message_count": 0,
            "thread_count": 0,
            "image_count": 0,
            "images": [],
            "youtube_titles": [],
            "output_md": None,
            "output_jsonl": None,
        }

    channel_name = getattr(ch, "name", None) or f"channel_{channel_id}"
    label = explicit_label or channel_name

    if isinstance(ch, discord.Thread):
        channel_type = "Thread"
    elif isinstance(ch, discord.ForumChannel):
        channel_type = "ForumChannel"
    elif isinstance(ch, discord.TextChannel):
        channel_type = "NewsChannel" if ch.is_news() else "TextChannel"
    else:
        channel_type = type(ch).__name__

    guild_id = getattr(getattr(ch, "guild", None), "id", None) or getattr(ch, "guild_id", None)
    guild_ctx = await get_guild_context(client, guild_id, guild_cache)

    images_dir = out_dir / "images" / str(channel_id)

    channel_messages: list[dict[str, Any]] = []
    thread_sections: list[dict[str, Any]] = []
    all_images: list[dict[str, Any]] = []
    all_youtube: list[dict[str, Any]] = []
    warnings: list[str] = []

    if isinstance(ch, discord.Thread):
        sec, imgs, yts = await _export_thread_messages(
            thread=ch,
            parent_forum=None,
            guild_id=guild_id,
            guild_ctx=guild_ctx,
            images_dir=images_dir,
            out_dir=out_dir,
            session=session,
            yt_resolver=yt_resolver,
            text_only=text_only,
            limit=limit,
        )
        thread_sections.append(sec)
        all_images.extend(imgs)
        all_youtube.extend(yts)
        if sec["error"]:
            warnings.append(sec["error"])
    elif isinstance(ch, discord.ForumChannel):
        threads, disc_warnings = await discover_channel_threads(ch, guild_ctx)
        warnings.extend(disc_warnings)
        logger.info("ForumChannel %s (%s)：共探索到 %d 個討論串", channel_id, label, len(threads))
        for thread in threads:
            sec, imgs, yts = await _export_thread_messages(
                thread=thread,
                parent_forum=ch,
                guild_id=guild_id,
                guild_ctx=guild_ctx,
                images_dir=images_dir,
                out_dir=out_dir,
                session=session,
                yt_resolver=yt_resolver,
                text_only=text_only,
                limit=limit,
            )
            thread_sections.append(sec)
            all_images.extend(imgs)
            all_youtube.extend(yts)
    elif isinstance(ch, discord.TextChannel):
        threads, disc_warnings = await discover_channel_threads(ch, guild_ctx)
        warnings.extend(disc_warnings)
        threads_by_id = {t.id: t for t in threads}

        try:
            async for msg in _iter_history_chronological(ch, limit=limit):
                if msg.thread is not None:
                    threads_by_id.setdefault(msg.thread.id, msg.thread)
                elif (
                    msg.type == discord.MessageType.thread_created
                    and msg.reference
                    and msg.reference.channel_id
                    and msg.reference.channel_id not in threads_by_id
                ):
                    try:
                        maybe_thread = await client.fetch_channel(msg.reference.channel_id)
                        if isinstance(maybe_thread, discord.Thread):
                            threads_by_id[maybe_thread.id] = maybe_thread
                    except (discord.Forbidden, discord.NotFound, discord.HTTPException):
                        pass

                rec = await export_message_record(
                    msg=msg,
                    fallback_guild_id=guild_id,
                    guild_ctx=guild_ctx,
                    thread_id=None,
                    is_starter=False,
                    images_dir=images_dir,
                    out_dir=out_dir,
                    session=session,
                    yt_resolver=yt_resolver,
                    text_only=text_only,
                )
                channel_messages.append(rec)
                all_images.extend(rec["images"])
                all_youtube.extend(rec["youtube_items"])
                if len(channel_messages) % 1000 == 0:
                    logger.info("頻道 %s (%s) 已處理 %d 則主體訊息...", channel_id, label, len(channel_messages))
        except (discord.Forbidden, discord.HTTPException) as exc:
            err_msg = f"讀取頻道歷史失敗：{exc}"
            warnings.append(err_msg)
            logger.warning("頻道 %s (%s) %s", channel_id, label, err_msg)

        sorted_threads = _sort_threads_chronologically(list(threads_by_id.values()))
        if sorted_threads:
            logger.info("%s %s (%s)：共探索到 %d 個討論串", channel_type, channel_id, label, len(sorted_threads))
        for thread in sorted_threads:
            sec, imgs, yts = await _export_thread_messages(
                thread=thread,
                parent_forum=None,
                guild_id=guild_id,
                guild_ctx=guild_ctx,
                images_dir=images_dir,
                out_dir=out_dir,
                session=session,
                yt_resolver=yt_resolver,
                text_only=text_only,
                limit=limit,
            )
            thread_sections.append(sec)
            all_images.extend(imgs)
            all_youtube.extend(yts)
    elif isinstance(ch, discord.abc.Messageable):
        try:
            async for msg in _iter_history_chronological(ch, limit=limit):
                rec = await export_message_record(
                    msg=msg,
                    fallback_guild_id=guild_id,
                    guild_ctx=guild_ctx,
                    thread_id=None,
                    is_starter=False,
                    images_dir=images_dir,
                    out_dir=out_dir,
                    session=session,
                    yt_resolver=yt_resolver,
                    text_only=text_only,
                )
                channel_messages.append(rec)
                all_images.extend(rec["images"])
                all_youtube.extend(rec["youtube_items"])
        except (discord.Forbidden, discord.HTTPException) as exc:
            warnings.append(f"讀取頻道歷史失敗：{exc}")
    else:
        err_msg = f"不支援匯出的頻道類型：{channel_type}"
        logger.error("頻道 %s (%s)：%s", channel_id, label, err_msg)
        return {
            "channel_id": str(channel_id),
            "label": label,
            "channel_name": channel_name,
            "channel_type": channel_type,
            "guild_id": str(guild_id) if guild_id else None,
            "status": "error",
            "error": err_msg,
            "message_count": 0,
            "thread_count": 0,
            "image_count": 0,
            "images": [],
            "youtube_titles": [],
            "output_md": None,
            "output_jsonl": None,
        }

    total_messages = len(channel_messages) + sum(len(sec["messages"]) for sec in thread_sections)
    available_tags = (
        [t.name for t in getattr(ch, "available_tags", []) or [] if getattr(t, "name", None)]
        if isinstance(ch, discord.ForumChannel)
        else []
    )

    safe_label = sanitize_label(label)
    md_filename = f"{channel_id}_{safe_label}.md"
    md_path = out_dir / md_filename
    md_content = render_channel_markdown(
        channel_id=channel_id,
        label=label,
        channel_name=channel_name,
        channel_type=channel_type,
        guild_id=guild_id,
        guild_name=guild_ctx.guild_name if guild_ctx else None,
        exported_at=exported_at,
        available_tags=available_tags,
        channel_messages=channel_messages,
        thread_sections=thread_sections,
        warnings=warnings,
        total_messages=total_messages,
        total_images=len(all_images),
        total_youtube=len(all_youtube),
    )
    md_path.write_text(md_content, encoding="utf-8")

    jsonl_filename: Optional[str] = None
    if write_jsonl:
        jsonl_filename = f"{channel_id}_{safe_label}.jsonl"
        _write_channel_jsonl(
            jsonl_path=out_dir / jsonl_filename,
            channel_messages=channel_messages,
            thread_sections=thread_sections,
        )

    return {
        "channel_id": str(channel_id),
        "label": label,
        "channel_name": channel_name,
        "channel_type": channel_type,
        "guild_id": str(guild_id) if guild_id else None,
        "status": "ok",
        "error": None,
        "warnings": warnings,
        "message_count": total_messages,
        "thread_count": len(thread_sections),
        "image_count": len(all_images),
        "images": all_images,
        "youtube_titles": all_youtube,
        "output_md": md_filename,
        "output_jsonl": jsonl_filename,
    }


async def run_export(args: argparse.Namespace, env_file: str, token: str) -> int:
    """Login in REST-only mode, export requested channels, and write `_manifest.json`."""
    out_dir = resolve_output_dir(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    exported_at = format_utc8(datetime.datetime.now(TZ_TAIPEI))
    text_only = bool(getattr(args, "text_only", False))
    limit = getattr(args, "limit", None)
    write_jsonl = bool(getattr(args, "jsonl", False))

    # Deduplicate repeated channel IDs while preserving CLI order
    channel_specs: list[tuple[int, Optional[str]]] = []
    seen_channel_ids: set[int] = set()
    for cid, lbl in args.channels:
        if cid in seen_channel_ids:
            logger.warning("略過重複指定的頻道 ID：%s", cid)
            continue
        seen_channel_ids.add(cid)
        channel_specs.append((cid, lbl))

    intents = discord.Intents.default()
    intents.message_content = True
    intents.guilds = True

    client = discord.Client(intents=intents)
    guild_cache: dict[int, GuildContext] = {}
    channel_entries: list[dict[str, Any]] = []

    try:
        logger.info("使用 %s 進行 REST-Only Discord 驗證登入（不啟動 Gateway）...", env_file)
        try:
            await client.login(token)
        except (discord.LoginFailure, discord.HTTPException) as exc:
            logger.error("Discord REST 登入失敗（%s）：%s", type(exc).__name__, exc)
            return 2

        http_headers = {"User-Agent": "DiscordGeminiBot-GuideExporter/1.0"}
        async with aiohttp.ClientSession(headers=http_headers) as session:
            yt_resolver = YouTubeTitleResolver(session)
            for cid, lbl in channel_specs:
                logger.info("開始匯出頻道 %s (%s)...", cid, lbl or "auto")
                entry = await export_single_channel(
                    client=client,
                    channel_id=cid,
                    explicit_label=lbl,
                    out_dir=out_dir,
                    exported_at=exported_at,
                    guild_cache=guild_cache,
                    session=session,
                    yt_resolver=yt_resolver,
                    text_only=text_only,
                    limit=limit,
                    write_jsonl=write_jsonl,
                )
                channel_entries.append(entry)
                logger.info(
                    "完成頻道 %s：status=%s, messages=%d, threads=%d, images=%d, youtube=%d",
                    cid,
                    entry["status"],
                    entry["message_count"],
                    entry["thread_count"],
                    entry["image_count"],
                    len(entry["youtube_titles"]),
                )
    finally:
        await client.close()

    manifest = {
        "exported_at": exported_at,
        "env_file": env_file,
        "text_only": text_only,
        "limit": limit,
        "jsonl": write_jsonl,
        "total_channels": len(channel_entries),
        "total_messages": sum(c["message_count"] for c in channel_entries),
        "total_threads": sum(c["thread_count"] for c in channel_entries),
        "total_images": sum(c["image_count"] for c in channel_entries),
        "channels": channel_entries,
    }
    manifest_path = out_dir / "_manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    logger.info("已寫入總索引：%s", manifest_path)

    has_success = any(c["status"] == "ok" for c in channel_entries)
    return 0 if has_success else 1


def main() -> int:
    """CLI entrypoint."""
    configure_windows_utf8()
    parser = build_arg_parser()
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    if args.env:
        env_file = init_runtime_env([f"--env={args.env}"])
    else:
        env_file = init_runtime_env([f"--profile={args.profile}"])

    token = (os.getenv("DISCORD_BOT_TOKEN") or "").strip()
    if not token:
        logger.error(
            "缺少 DISCORD_BOT_TOKEN（已嘗試載入 %s），請檢查設定檔後再試。",
            env_file,
        )
        return 2

    return asyncio.run(run_export(args, env_file=env_file, token=token))


if __name__ == "__main__":
    raise SystemExit(main())
