"""Japanese speaking shadowing session state, prompt builder, JSON parser, and Gemini evaluator."""

from __future__ import annotations

import json
import logging
import math
import re
import threading
import time
from typing import Any, Optional

from google.genai import types

from core.config import BotSettings
from core.llm_client import generate_with_fallback, get_safety_settings

_shadowing_sessions: dict[tuple[int, int], dict[str, Any]] = {}
_shadowing_lock = threading.Lock()

_CODE_FENCE_START_RE = re.compile(r"^\s*```(?:json)?\s*", re.IGNORECASE)
_CODE_FENCE_END_RE = re.compile(r"\s*```\s*$")

_DEFAULT_FALLBACK_FIELDS: dict[str, str] = {
    "transcript": "（未取得清晰日語轉寫）",
    "pitch_and_rhythm": "音調與節奏大致清楚，建議持續留意長短音與語尾停頓。",
    "strengths": "勇敢開口練習就是最棒的進步！",
    "improvements": "可對照標準發音多聽幾次，注意重音高低變化與咬字清晰度。",
    "encouragement_jp": "いい調子ですね！この調子で頑張りましょう！",
}


def _prune_expired_sessions_locked(now: float) -> None:
    expired = [k for k, v in _shadowing_sessions.items() if float(v.get("expires_at", 0.0)) <= now]
    for k in expired:
        _shadowing_sessions.pop(k, None)


def start_shadowing_session(
    channel_id: int,
    user_id: int,
    *,
    target_jp: str,
    reading: str = "",
    pitch: str = "",
    meaning: str = "",
    example_jp: str = "",
    card_id: Optional[int] = None,
    ttl_seconds: float = 90.0,
) -> dict[str, Any]:
    """啟動或重設使用者的單頻道日語跟讀練習會話，並自動清理過期紀錄。"""
    target_clean = str(target_jp or "").strip()[:300]
    if not target_clean:
        raise ValueError("跟讀目標日語句子不可為空。")

    now = time.monotonic()
    ttl = max(0.0, float(ttl_seconds))
    session: dict[str, Any] = {
        "channel_id": int(channel_id),
        "user_id": int(user_id),
        "target_jp": target_clean,
        "reading": str(reading or "").strip()[:300],
        "pitch": str(pitch or "").strip()[:60],
        "meaning": str(meaning or "").strip()[:400],
        "example_jp": str(example_jp or "").strip()[:400],
        "card_id": int(card_id) if card_id is not None else None,
        "created_at": now,
        "expires_at": now + ttl,
    }
    key = (int(channel_id), int(user_id))
    with _shadowing_lock:
        _prune_expired_sessions_locked(now)
        _shadowing_sessions[key] = session
        return dict(session)


def get_active_shadowing_session(channel_id: int, user_id: int) -> Optional[dict[str, Any]]:
    """取得目前尚未過期的跟讀練習會話（若已逾時則回傳 None）。"""
    now = time.monotonic()
    key = (int(channel_id), int(user_id))
    with _shadowing_lock:
        _prune_expired_sessions_locked(now)
        sess = _shadowing_sessions.get(key)
        return dict(sess) if sess is not None else None


def clear_shadowing_session(channel_id: int, user_id: int) -> Optional[dict[str, Any]]:
    """消費並移除使用者的跟讀練習會話。"""
    now = time.monotonic()
    key = (int(channel_id), int(user_id))
    with _shadowing_lock:
        _prune_expired_sessions_locked(now)
        sess = _shadowing_sessions.pop(key, None)
        return dict(sess) if sess is not None else None


def format_score_bar(score: int, width: int = 10) -> str:
    """產生直覺的進度條字串（例如 ███░░░░░░░ 30/100）。"""
    clamped = max(0, min(100, int(score)))
    filled = max(0, min(width, round((clamped / 100.0) * width)))
    return ("█" * filled) + ("░" * (width - filled)) + f" {clamped}/100"


def build_shadowing_eval_prompt(session: dict[str, Any]) -> str:
    """建構日語跟讀發音評測 Prompt（要求模型僅回傳結構化 JSON）。"""
    target_jp = str(session.get("target_jp") or "").strip()
    reading = str(session.get("reading") or "").strip()
    pitch = str(session.get("pitch") or "").strip()
    meaning = str(session.get("meaning") or "").strip()

    ref_lines = [f"- 目標日語：{target_jp}"]
    if reading:
        ref_lines.append(f"- 假名讀音：{reading}")
    if pitch:
        ref_lines.append(f"- 音調標記 (アクセント)：{pitch}")
    if meaning:
        ref_lines.append(f"- 中文意思：{meaning}")

    return (
        "你是一位溫柔、專業且聽力敏銳的日語發音教練。請仔細聆聽附帶的使用者口說音訊，並對照以下目標日語進行發音評測：\n"
        + "\n".join(ref_lines)
        + "\n\n"
        "評測原則：\n"
        "1. 先真實轉寫你在音訊中實際聽到的日語發音（不可未聽就照抄目標句子）。\n"
        "2. 評估發音清晰度、高低音調 (ピッチアクセント)、長短音、促音 (っ)、撥音 (ん) 與語速節奏自然度。\n"
        "3. 若音訊幾乎無聲、只有雜訊或完全不是目標日語，請誠實給予較低分數（0~35 分）並溫柔提示重錄。\n\n"
        "請嚴格只回傳一個 JSON 物件（不要加上其他多餘文字），格式如下：\n"
        "{\n"
        '  "score": 85,\n'
        '  "transcript": "實際聽到的日語轉寫",\n'
        '  "pitch_and_rhythm": "（繁體中文）音調高低、長短音、促音與語速節奏評析（40~80字）",\n'
        '  "strengths": "（繁體中文）這次唸得很好的優點（30~60字）",\n'
        '  "improvements": "（繁體中文）具體可微調的發音建議或進階自然度提示（30~70字）",\n'
        '  "encouragement_jp": "（日語）一句親切自然的日語鼓勵短評"\n'
        "}"
    )


def _clamp_score(raw_score: Any, default: int = 75) -> int:
    if isinstance(raw_score, bool) or raw_score is None:
        return default
    if isinstance(raw_score, (int, float)):
        if not math.isfinite(float(raw_score)):
            return default
        return max(0, min(100, int(round(float(raw_score)))))
    if isinstance(raw_score, str):
        m = re.search(r"-?\d+(?:\.\d+)?", raw_score.strip())
        if m:
            try:
                val = float(m.group(0))
                if math.isfinite(val):
                    return max(0, min(100, int(round(val))))
            except ValueError:
                pass
    return default


def _strip_code_fences(raw_text: str) -> str:
    cleaned = _CODE_FENCE_START_RE.sub("", str(raw_text or "").strip())
    cleaned = _CODE_FENCE_END_RE.sub("", cleaned).strip()
    return cleaned


def parse_shadowing_eval_response(raw_text: str) -> dict[str, Any]:
    """解析 Gemini 回傳的跟讀評測 JSON，並對 markdown fence 與純文字降級回覆提供安全防呆。"""
    stripped = _strip_code_fences(raw_text)
    parsed: Optional[dict[str, Any]] = None

    if stripped:
        try:
            obj = json.loads(stripped)
            if isinstance(obj, dict):
                parsed = obj
        except Exception:
            m = re.search(r"\{.*\}", stripped, re.DOTALL)
            if m:
                try:
                    obj = json.loads(m.group(0))
                    if isinstance(obj, dict):
                        parsed = obj
                except Exception:
                    parsed = None

    if parsed is not None:
        result: dict[str, Any] = {
            "score": _clamp_score(parsed.get("score"), default=75),
        }
        for key, fallback_val in _DEFAULT_FALLBACK_FIELDS.items():
            val = str(parsed.get(key) or "").strip()[:400]
            result[key] = val or fallback_val
        return result

    if stripped:
        return {
            "score": 75,
            "transcript": "（無法解析逐字轉寫，請參考下方評語）",
            "pitch_and_rhythm": stripped[:350],
            "strengths": "勇敢開口跟讀練習就是最棒的進步！",
            "improvements": stripped[:400],
            "encouragement_jp": "いい調子ですね！この調子で頑張りましょう！",
        }

    return {
        "score": 75,
        **_DEFAULT_FALLBACK_FIELDS,
    }


async def evaluate_shadowing_audio(
    gemini_client,
    settings: BotSettings,
    session: dict[str, Any],
    audio_bytes: bytes,
    mime_type: str,
    logger: Optional[logging.Logger] = None,
) -> dict[str, Any]:
    """送出音訊至 Gemini Flash-Lite 進行跟讀發音評測並回傳標準化評分字典。"""
    contents = [
        types.Part.from_text(text=build_shadowing_eval_prompt(session)),
        types.Part.from_bytes(data=audio_bytes, mime_type=mime_type),
    ]
    config = types.GenerateContentConfig(
        temperature=0.25,
        response_mime_type="application/json",
        safety_settings=get_safety_settings(settings.disable_safety_settings),
    )
    resp, _ = await generate_with_fallback(
        client=gemini_client,
        models_to_try=settings.models_to_try,
        contents=contents,
        config=config,
        timeout=25.0,
        context_label="日語跟讀發音評測",
        logger=logger,
        final_fallback_model=settings.final_fallback_model,
    )
    return parse_shadowing_eval_response(getattr(resp, "text", "") or "")
