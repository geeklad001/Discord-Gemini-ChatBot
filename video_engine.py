"""
DiscordGeminiBot - 影片與多模態影音處理引擎 (video_engine.py)
支援 Discord 直接上傳之短影片 (.mp4, .mov, .webm) 與 YouTube 影片 / Shorts 深度解析。
具備時長安全過濾（長度上限控管）、檔案容量防護、Live 直播識別與零下載串流傳輸。
"""

import os
import re
import logging
from typing import Optional
import httpx

logger = logging.getLogger("DiscordGeminiBot.video")

# -------------------------------------------------------------
# 1. 常數與安全閾值設定 (支援環境變數覆寫)
# -------------------------------------------------------------
# YouTube 影片分析長度上限（預設 180 秒 / 3 分鐘，涵蓋全規格 Shorts 與精華短片，防止 Token 爆量）
def get_max_youtube_duration_seconds() -> int:
    try:
        return int(os.getenv("MAX_YOUTUBE_DURATION_SECONDS", "180"))
    except ValueError:
        return 180

# Discord 直接上傳影片容量上限（預設 20 MB）
def get_max_video_attachment_bytes() -> int:
    try:
        return int(os.getenv("MAX_VIDEO_ATTACHMENT_BYTES", str(20 * 1024 * 1024)))
    except ValueError:
        return 20 * 1024 * 1024

SUPPORTED_VIDEO_EXTENSIONS = (".mp4", ".mov", ".webm", ".mkv")
SUPPORTED_VIDEO_MIME_PREFIXES = ("video/mp4", "video/webm", "video/quicktime", "video/x-matroska")

# 支援 YouTube 各種網址形式（一般 watch、Shorts、短網址 youtu.be、嵌入 embed 等）
YOUTUBE_URL_REGEX = re.compile(
    r'(?:https?:\/\/)?(?:www\.|m\.)?(?:youtube\.com\/(?:watch\?(?:[^ \n\r\t]*&)?v=|shorts\/|embed\/)|youtu\.be\/)([a-zA-Z0-9_-]{11})',
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
    vid = extract_youtube_video_id(url_or_id) or url_or_id
    canonical_url = f"https://www.youtube.com/watch?v={vid}"

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7",
    }

    try:
        async with httpx.AsyncClient(timeout=6.0, headers=headers, follow_redirects=True) as client:
            resp = await client.get(canonical_url)
            if resp.status_code != 200:
                return {
                    "success": False,
                    "video_id": vid,
                    "canonical_url": canonical_url,
                    "title": "未知影片",
                    "duration_sec": None,
                    "is_live": False,
                    "error": f"YouTube 回應狀態碼：{resp.status_code}"
                }
            html = resp.text

        # 1. 抓取標題
        title = "未知標題"
        m_title = (
            re.search(r'<meta property="og:title"\s+content="([^"]+)"', html) or
            re.search(r'<title>([^<]+)</title>', html)
        )
        if m_title:
            title = m_title.group(1).replace(" - YouTube", "").strip()

        # 2. 判斷是否為直播 (Live Stream)
        is_live = False
        if '"isLive":true' in html or '"isLiveBroadcast":true' in html or '"liveStreamability":' in html:
            is_live = True

        # 3. 抓取時長秒數 (lengthSeconds 或 itemprop="duration")
        duration_sec = None
        m_len = re.search(r'"lengthSeconds":"(\d+)"', html)
        if m_len:
            duration_sec = int(m_len.group(1))
        else:
            m_itemprop = re.search(r'itemprop="duration"\s+content="PT([^"]+)"', html)
            if m_itemprop:
                dur_str = m_itemprop.group(1)
                hours = re.search(r'(\d+)H', dur_str)
                mins = re.search(r'(\d+)M', dur_str)
                secs = re.search(r'(\d+)S', dur_str)
                duration_sec = (
                    (int(hours.group(1)) * 3600 if hours else 0) +
                    (int(mins.group(1)) * 60 if mins else 0) +
                    (int(secs.group(1)) if secs else 0)
                )
            else:
                m_approx = re.search(r'"approxDurationMs":"(\d+)"', html)
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
            "error": str(e)
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
