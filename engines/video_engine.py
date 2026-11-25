"""
DiscordGeminiBot - 影片與多模態影音處理引擎 (video_engine.py)
支援 Discord 直接上傳之短影片 (.mp4, .mov, .webm) 與 YouTube 影片 / Shorts 深度解析。
具備時長安全過濾（長度上限控管）、檔案容量防護、Live 直播識別與零下載串流傳輸。
"""

import html
import json
import os
import re
import logging
import urllib.parse
from typing import Optional
import httpx

logger = logging.getLogger("DiscordGeminiBot.video")

# -------------------------------------------------------------
# 1. 常數與安全閾值設定 (支援環境變數覆寫)
# -------------------------------------------------------------
# YouTube 影片分析長度上限（預設 180 秒 / 3 分鐘，涵蓋全規格 Shorts 與精華短片，防止 Token 爆量）
def get_max_youtube_duration_seconds() -> int:
    try:
        val = int(os.getenv("MAX_YOUTUBE_DURATION_SECONDS", "180"))
    except ValueError:
        val = 180
    return max(10, min(3600, val))


# Discord 直接上傳影片容量上限（預設 20 MB）
def get_max_video_attachment_bytes() -> int:
    try:
        val = int(os.getenv("MAX_VIDEO_ATTACHMENT_BYTES", str(20 * 1024 * 1024)))
    except ValueError:
        val = 20 * 1024 * 1024
    return max(1024 * 1024, min(50 * 1024 * 1024, val))


SUPPORTED_VIDEO_EXTENSIONS = (".mp4", ".mov", ".webm", ".mkv")
SUPPORTED_VIDEO_MIME_PREFIXES = ("video/mp4", "video/webm", "video/quicktime", "video/x-matroska")

_YOUTUBE_ID_RE = re.compile(r"[a-zA-Z0-9_-]{11}")

# 支援 YouTube 各種網址形式（一般 watch、Shorts、短網址 youtu.be、嵌入 embed、直播 live 等）
YOUTUBE_URL_REGEX = re.compile(
    r'(?:https?:\/\/)?(?:www\.|m\.)?(?:youtube\.com\/(?:watch\?(?:[^ \n\r\t]*&)?v=|shorts\/|embed\/|live\/)|youtu\.be\/)([a-zA-Z0-9_-]{11})(?![a-zA-Z0-9_-])',
    re.IGNORECASE
)


def extract_youtube_video_id(url_or_text: str) -> Optional[str]:
    """從單一網址或文字中提取 YouTube 11 位元 Video ID"""
    if not url_or_text:
        return None
    m = YOUTUBE_URL_REGEX.search(url_or_text)
    return m.group(1) if m else None


def find_all_youtube_urls(text: str) -> list[dict]:
    """
    從一段文字中搜尋所有符合的 YouTube 網址與 Video ID。
    回傳列表：[{"video_id": "...", "original_url": "...", "canonical_url": "..."}, ...]
    """
    if not text:
        return []
    results = []
    seen_ids = set()
    for m in YOUTUBE_URL_REGEX.finditer(text):
        vid = m.group(1)
        if vid not in seen_ids:
            seen_ids.add(vid)
            results.append({
                "video_id": vid,
                "original_url": m.group(0),
                "canonical_url": f"https://www.youtube.com/watch?v={vid}"
            })
    return results


def format_duration(seconds: Optional[int]) -> str:
    """將秒數轉換為直觀的「X 分 Y 秒」格式"""
    if seconds is None:
        return "未知長度"
    if seconds < 0:
        return "0 秒"
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    if h > 0:
        return f"{h} 小時 {m} 分 {s} 秒"
    if m > 0:
        return f"{m} 分 {s} 秒"
    return f"{s} 秒"


def _is_youtube_host(url: str) -> bool:
    """Ensure YouTube metadata fetch & redirects stay strictly on YouTube domains."""
    try:
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            return False
        host = parsed.hostname.lower().rstrip(".")
        return (
            host == "youtube.com"
            or host.endswith(".youtube.com")
            or host == "youtu.be"
            or host.endswith(".youtu.be")
        )
    except Exception:
        return False


def _extract_yt_initial_player_response(html_text: str) -> Optional[dict]:
    """Extract and parse the embedded ytInitialPlayerResponse JSON object safely."""
    marker_match = re.search(
        r'(?:ytInitialPlayerResponse|window\["ytInitialPlayerResponse"\])\s*=\s*\{',
        html_text,
    )
    if not marker_match:
        return None
    start_idx = marker_match.end() - 1
    try:
        decoder = json.JSONDecoder()
        obj, _ = decoder.raw_decode(html_text, start_idx)
        return obj if isinstance(obj, dict) else None
    except Exception:
        return None


async def fetch_youtube_video_info(url_or_id: str) -> dict:
    """
    非同步抓取公開 YouTube 影片的後設資料（標題、時長秒數、是否為直播）。
    無需下載影片檔，利用 HTTP 標頭與輕量頁面解析，耗時約 0.5 ~ 1.0 秒。
    回傳字典：
    {
        "success": bool,
        "video_id": str,
        "canonical_url": str,
        "title": str,
        "duration_sec": Optional[int],
        "is_live": bool,
        "error": Optional[str]
    }
    """
    raw_input = (url_or_id or "").strip()
    vid = extract_youtube_video_id(raw_input)
    if vid is None:
        if _YOUTUBE_ID_RE.fullmatch(raw_input):
            vid = raw_input
        else:
            return {
                "success": False,
                "video_id": "",
                "canonical_url": "",
                "title": "未知影片",
                "duration_sec": None,
                "is_live": False,
                "error": "無效的 YouTube 影片 ID",
            }

    canonical_url = f"https://www.youtube.com/watch?v={vid}"

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7",
    }

    # Lazy import to prevent circular dependency with core.media
    from core.media import is_safe_public_url_async

    max_html_bytes = 2 * 1024 * 1024
    max_redirects = 5
    current_url = canonical_url
    html_text = ""

    try:
        async with httpx.AsyncClient(timeout=6.0, headers=headers, follow_redirects=False) as client:
            for _ in range(max_redirects + 1):
                if not _is_youtube_host(current_url) or not await is_safe_public_url_async(current_url):
                    return {
                        "success": False,
                        "video_id": vid,
                        "canonical_url": canonical_url,
                        "title": "未知影片",
                        "duration_sec": None,
                        "is_live": False,
                        "error": "連線逾時或無法存取 YouTube 頁面",
                    }
                async with client.stream("GET", current_url, follow_redirects=False) as resp:
                    if resp.status_code in (301, 302, 303, 307, 308):
                        location = resp.headers.get("location")
                        if not location:
                            return {
                                "success": False,
                                "video_id": vid,
                                "canonical_url": canonical_url,
                                "title": "未知影片",
                                "duration_sec": None,
                                "is_live": False,
                                "error": "連線逾時或無法存取 YouTube 頁面",
                            }
                        current_url = urllib.parse.urljoin(current_url, location)
                        continue
                    if resp.status_code != 200:
                        return {
                            "success": False,
                            "video_id": vid,
                            "canonical_url": canonical_url,
                            "title": "未知影片",
                            "duration_sec": None,
                            "is_live": False,
                            "error": f"YouTube 回應狀態碼：{resp.status_code}",
                        }
                    chunks: list[bytes] = []
                    total_bytes = 0
                    async for chunk in resp.aiter_bytes():
                        if not chunk:
                            continue
                        remaining = max_html_bytes - total_bytes
                        if remaining <= 0:
                            break
                        if len(chunk) > remaining:
                            chunks.append(chunk[:remaining])
                            total_bytes += remaining
                            break
                        chunks.append(chunk)
                        total_bytes += len(chunk)
                    html_text = b"".join(chunks).decode("utf-8", errors="ignore")
                    break
            else:
                return {
                    "success": False,
                    "video_id": vid,
                    "canonical_url": canonical_url,
                    "title": "未知影片",
                    "duration_sec": None,
                    "is_live": False,
                    "error": "連線逾時或無法存取 YouTube 頁面",
                }

        player_resp = _extract_yt_initial_player_response(html_text)
        video_details = player_resp.get("videoDetails") if isinstance(player_resp, dict) else None
        if not isinstance(video_details, dict):
            video_details = None

        # 1. 抓取標題 / 直播 / 時長（優先使用 ytInitialPlayerResponse.videoDetails 避免側邊欄推薦直播污染）
        title = "未知標題"
        is_live = False
        duration_sec: Optional[int] = None

        if video_details is not None:
            raw_title = video_details.get("title")
            if isinstance(raw_title, str) and raw_title.strip():
                title = raw_title.strip()

            raw_len = video_details.get("lengthSeconds")
            if raw_len is not None and str(raw_len).strip().isdigit():
                duration_sec = int(str(raw_len).strip())

            if "isLive" in video_details:
                is_live = bool(video_details.get("isLive"))
            else:
                is_live = bool(video_details.get("isLiveContent")) and (duration_sec is None or duration_sec == 0)

        if title == "未知標題":
            m_title = (
                re.search(r'<meta property="og:title"\s+content="([^"]+)"', html_text)
                or re.search(r'<title>([^<]+)</title>', html_text)
            )
            if m_title:
                parsed_title = m_title.group(1).replace(" - YouTube", "").strip()
                if parsed_title:
                    title = parsed_title

        title = html.unescape(title)

        if video_details is None:
            if '"isLive":true' in html_text or '"isLiveBroadcast":true' in html_text or '"liveStreamability":' in html_text:
                is_live = True

        if duration_sec is None:
            if video_details is None:
                m_len = re.search(r'"lengthSeconds":"(\d+)"', html_text)
                if m_len:
                    duration_sec = int(m_len.group(1))
            if duration_sec is None:
                m_itemprop = re.search(r'itemprop="duration"\s+content="PT([^"]+)"', html_text)
                if m_itemprop:
                    dur_str = m_itemprop.group(1)
                    hours = re.search(r'(\d+)H', dur_str)
                    mins = re.search(r'(\d+)M', dur_str)
                    secs = re.search(r'(\d+)S', dur_str)
                    duration_sec = (
                        (int(hours.group(1)) * 3600 if hours else 0)
                        + (int(mins.group(1)) * 60 if mins else 0)
                        + (int(secs.group(1)) if secs else 0)
                    )
            if duration_sec is None and video_details is None:
                m_approx = re.search(r'"approxDurationMs":"(\d+)"', html_text)
                if m_approx:
                    duration_sec = int(m_approx.group(1)) // 1000

        # 若直播進行中，lengthSeconds 通常為 0
        if is_live and (duration_sec == 0 or duration_sec is None):
            duration_sec = 0

        return {
            "success": True,
            "video_id": vid,
            "canonical_url": canonical_url,
            "title": title,
            "duration_sec": duration_sec,
            "is_live": is_live,
            "error": None
        }

    except Exception as e:
        logger.warning(f"⚠️ 解析 YouTube 影片資訊失敗 ({canonical_url}): {e}")
        return {
            "success": False,
            "video_id": vid,
            "canonical_url": canonical_url,
            "title": "未知影片",
            "duration_sec": None,
            "is_live": False,
            "error": "連線逾時或無法存取 YouTube 頁面"
        }


def is_video_attachment(att) -> bool:
    """判斷 Discord 訊息附件是否為支援的影片檔案"""
    ctype = (getattr(att, "content_type", "") or "").lower()
    fname = (getattr(att, "filename", "") or "").lower()
    if any(ctype.startswith(prefix) for prefix in SUPPORTED_VIDEO_MIME_PREFIXES):
        return True
    if any(fname.endswith(ext) for ext in SUPPORTED_VIDEO_EXTENSIONS):
        return True
    return False


def get_video_mime_type(att) -> str:
    """取得影片附件合適的 MIME Type"""
    ctype = (getattr(att, "content_type", "") or "").lower()
    fname = (getattr(att, "filename", "") or "").lower()
    if ctype and any(ctype.startswith(prefix) for prefix in SUPPORTED_VIDEO_MIME_PREFIXES):
        return ctype
    if fname.endswith(".webm"):
        return "video/webm"
    if fname.endswith(".mov"):
        return "video/quicktime"
    if fname.endswith(".mkv"):
        return "video/x-matroska"
    return "video/mp4"
