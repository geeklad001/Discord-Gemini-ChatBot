"""Edge-TTS voice catalog, prosody clamping/formatting, and FFmpeg WAV pitch shifting."""

from __future__ import annotations

import asyncio
import io
import logging
import re
import subprocess
import wave
from typing import Any, Optional

logger = logging.getLogger("DiscordGeminiBot.tts")

DEFAULT_EDGE_VOICE = "edge:zh-TW-HsiaoChenNeural"
DEFAULT_JP_EDGE_VOICE = "edge:ja-JP-NanamiNeural"

MANAGED_VOICE_ID_RE = re.compile(r"^u_[0-9]{1,25}_s[1-5]$")
EDGE_PROSODY_TTL_SECONDS = 600.0

# -------------------------------------------------------------
# Built-in 0-VRAM Edge-TTS Voice Catalog (16 Neural Voices)
# -------------------------------------------------------------
EDGE_VOICES: dict[str, dict[str, str]] = {
    "edge:zh-TW-HsiaoChenNeural": {
        "id": "edge:zh-TW-HsiaoChenNeural",
        "short_name": "zh-TW-HsiaoChenNeural",
        "label": "🇹🇼 曉臻 (台灣女聲・預設)",
        "description": "自然親切的台灣國語女聲",
        "category": "zh-TW",
    },
    "edge:zh-TW-HsiaoYuNeural": {
        "id": "edge:zh-TW-HsiaoYuNeural",
        "short_name": "zh-TW-HsiaoYuNeural",
        "label": "🇹🇼 曉雨 (台灣女聲・溫婉)",
        "description": "柔和沉穩的台灣國語女聲",
        "category": "zh-TW",
    },
    "edge:zh-TW-YunJheNeural": {
        "id": "edge:zh-TW-YunJheNeural",
        "short_name": "zh-TW-YunJheNeural",
        "label": "🇹🇼 雲哲 (台灣男聲・清晰)",
        "description": "台灣國語男聲（不穩時自動以穩定男聲備援）",
        "category": "zh-TW",
    },
    "edge:zh-CN-XiaoxiaoNeural": {
        "id": "edge:zh-CN-XiaoxiaoNeural",
        "short_name": "zh-CN-XiaoxiaoNeural",
        "label": "🇨🇳 曉曉 (普通話女聲・情感)",
        "description": "情感豐富、適合對話與朗讀",
        "category": "zh-CN",
    },
    "edge:zh-CN-XiaoyiNeural": {
        "id": "edge:zh-CN-XiaoyiNeural",
        "short_name": "zh-CN-XiaoyiNeural",
        "label": "🇨🇳 曉伊 (普通話女聲・活潑)",
        "description": "活潑輕快的年輕女聲",
        "category": "zh-CN",
    },
    "edge:zh-CN-YunxiNeural": {
        "id": "edge:zh-CN-YunxiNeural",
        "short_name": "zh-CN-YunxiNeural",
        "label": "🇨🇳 雲希 (普通話男聲・故事)",
        "description": "清亮有故事感的青年男聲",
        "category": "zh-CN",
    },
    "edge:zh-CN-YunxiaNeural": {
        "id": "edge:zh-CN-YunxiaNeural",
        "short_name": "zh-CN-YunxiaNeural",
        "label": "🇨🇳 雲夏 (普通話・溫和少年)",
        "description": "溫和年輕感聲線",
        "category": "zh-CN",
    },
    "edge:zh-CN-YunjianNeural": {
        "id": "edge:zh-CN-YunjianNeural",
        "short_name": "zh-CN-YunjianNeural",
        "label": "🇨🇳 雲健 (普通話男聲・旁白)",
        "description": "低沉有力的旁白／熱血男聲",
        "category": "zh-CN",
    },
    "edge:zh-CN-YunyangNeural": {
        "id": "edge:zh-CN-YunyangNeural",
        "short_name": "zh-CN-YunyangNeural",
        "label": "🇨🇳 雲揚 (普通話男聲・播報)",
        "description": "專業新聞播報風格男聲",
        "category": "zh-CN",
    },
    "edge:zh-CN-liaoning-XiaobeiNeural": {
        "id": "edge:zh-CN-liaoning-XiaobeiNeural",
        "short_name": "zh-CN-liaoning-XiaobeiNeural",
        "label": "🇨🇳 曉北 (東北話女聲・趣味)",
        "description": "帶東北腔調的趣味女聲",
        "category": "zh-CN",
    },
    "edge:zh-CN-shaanxi-XiaoniNeural": {
        "id": "edge:zh-CN-shaanxi-XiaoniNeural",
        "short_name": "zh-CN-shaanxi-XiaoniNeural",
        "label": "🇨🇳 曉妮 (陝西話女聲・趣味)",
        "description": "帶陝西腔調的趣味女聲",
        "category": "zh-CN",
    },
    "edge:zh-HK-HiuGaaiNeural": {
        "id": "edge:zh-HK-HiuGaaiNeural",
        "short_name": "zh-HK-HiuGaaiNeural",
        "label": "🇭🇰 曉佳 (粵語女聲・標準)",
        "description": "標準親切的香港粵語女聲",
        "category": "zh-HK",
    },
    "edge:zh-HK-HiuMaanNeural": {
        "id": "edge:zh-HK-HiuMaanNeural",
        "short_name": "zh-HK-HiuMaanNeural",
        "label": "🇭🇰 曉曼 (粵語女聲・柔和)",
        "description": "柔和自然的香港粵語女聲",
        "category": "zh-HK",
    },
    "edge:zh-HK-WanLungNeural": {
        "id": "edge:zh-HK-WanLungNeural",
        "short_name": "zh-HK-WanLungNeural",
        "label": "🇭🇰 雲龍 (粵語男聲・穩重)",
        "description": "穩重清晰的香港粵語男聲",
        "category": "zh-HK",
    },
    "edge:ja-JP-NanamiNeural": {
        "id": "edge:ja-JP-NanamiNeural",
        "short_name": "ja-JP-NanamiNeural",
        "label": "🇯🇵 七海 (日語女聲・標準)",
        "description": "自然流暢的標準日語女聲",
        "category": "ja-JP",
    },
    "edge:ja-JP-KeitaNeural": {
        "id": "edge:ja-JP-KeitaNeural",
        "short_name": "ja-JP-KeitaNeural",
        "label": "🇯🇵 圭太 (日語男聲・沉穩)",
        "description": "沉穩清晰的標準日語男聲",
        "category": "ja-JP",
    },
}

# Microsoft Edge-TTS server rejects non-zero pitch/rate adjustments on certain voices (returns NoAudioReceived).
EDGE_PROSODY_LIMITED_VOICES: frozenset[str] = frozenset({"zh-TW-YunJheNeural"})
EDGE_FALLBACK_VOICE_MAP: dict[str, str] = {
    "zh-TW-YunJheNeural": "zh-CN-YunxiNeural",
}
EDGE_MALE_VOICES: frozenset[str] = frozenset(
    {
        "zh-TW-YunJheNeural",
        "zh-CN-YunxiNeural",
        "zh-CN-YunxiaNeural",
        "zh-CN-YunjianNeural",
        "zh-CN-YunyangNeural",
        "zh-HK-WanLungNeural",
        "ja-JP-KeitaNeural",
    }
)


def is_edge_prosody_limited(voice_id: str) -> bool:
    """Return whether an Edge-TTS voice does not support non-zero pitch/rate adjustments."""
    raw = (voice_id or "").strip()
    if not raw or raw.startswith("f5:"):
        return False
    short = raw[5:] if raw.startswith("edge:") else raw
    return short in EDGE_PROSODY_LIMITED_VOICES


def clamp_rate_pct(rate_pct: int) -> int:
    return max(-50, min(50, int(rate_pct)))


def clamp_pitch_hz(pitch_hz: int) -> int:
    return max(-30, min(30, int(pitch_hz)))


def format_edge_rate(rate_pct: int) -> str:
    return f"{clamp_rate_pct(rate_pct):+d}%"


def format_edge_pitch(pitch_hz: int) -> str:
    return f"{clamp_pitch_hz(pitch_hz):+d}Hz"


def rate_pct_to_f5_speed(rate_pct: int) -> float:
    clamped = clamp_rate_pct(rate_pct)
    return round(max(0.7, min(1.5, 1.0 + clamped / 100.0)), 2)


def pitch_hz_to_factor(pitch_hz: int) -> float:
    """Convert pitch_hz (-30..+30) to a frequency shift multiplier (~±4.5 semitones)."""
    clamped = clamp_pitch_hz(pitch_hz)
    if clamped == 0:
        return 1.0
    semitones = (clamped / 30.0) * 4.5
    return round(2.0 ** (semitones / 12.0), 6)


def _shift_wav_pitch_sync(wav_bytes: bytes, pitch_hz: int) -> bytes:
    """Shift pitch of WAV audio bytes via in-memory FFmpeg asetrate+atempo without changing duration."""
    clamped = clamp_pitch_hz(pitch_hz)
    if clamped == 0 or not wav_bytes:
        return wav_bytes
    try:
        with wave.open(io.BytesIO(wav_bytes), "rb") as wf_in:
            sr = int(wf_in.getframerate() or 24000)
            channels = int(wf_in.getnchannels() or 1)
    except Exception:
        return wav_bytes
    if sr < 8000 or sr > 96000:
        sr = 24000
    if channels not in (1, 2):
        channels = 1

    factor = pitch_hz_to_factor(clamped)
    shifted_sr = max(4000, int(round(sr * factor)))
    tempo_comp = max(0.5, min(2.0, 1.0 / factor))
    af_chain = f"asetrate={shifted_sr},atempo={tempo_comp:.6f},aresample={sr}"

    try:
        proc = subprocess.run(
            [
                "ffmpeg",
                "-nostdin",
                "-hide_banner",
                "-loglevel",
                "error",
                "-f",
                "wav",
                "-i",
                "pipe:0",
                "-af",
                af_chain,
                "-ac",
                str(channels),
                "-ar",
                str(sr),
                "-f",
                "s16le",
                "pipe:1",
            ],
            input=wav_bytes,
            capture_output=True,
            timeout=10,
            check=False,
        )
        if proc.returncode != 0 or not proc.stdout:
            return wav_bytes
        out_buf = io.BytesIO()
        with wave.open(out_buf, "wb") as wf_out:
            wf_out.setnchannels(channels)
            wf_out.setsampwidth(2)
            wf_out.setframerate(sr)
            wf_out.writeframes(proc.stdout)
        return out_buf.getvalue()
    except Exception as exc:
        logger.warning(f"⚠️ F5-TTS 音高微調處理略過 (pitch={clamped:+d}Hz): {exc}")
        return wav_bytes


async def apply_wav_pitch_shift(wav_bytes: bytes, pitch_hz: int) -> bytes:
    """Asynchronously shift WAV pitch without altering duration when pitch_hz != 0."""
    if clamp_pitch_hz(pitch_hz) == 0 or not wav_bytes:
        return wav_bytes
    return await asyncio.to_thread(_shift_wav_pitch_sync, wav_bytes, pitch_hz)


def normalize_edge_short_name(voice_id: str, fallback: str = DEFAULT_EDGE_VOICE) -> str:
    raw = (voice_id or "").strip()
    if raw in EDGE_VOICES:
        return EDGE_VOICES[raw]["short_name"]
    prefixed = f"edge:{raw}"
    if prefixed in EDGE_VOICES:
        return EDGE_VOICES[prefixed]["short_name"]
    fb = fallback if fallback in EDGE_VOICES else DEFAULT_EDGE_VOICE
    return EDGE_VOICES[fb]["short_name"]


def resolve_edge_voice_query(query: str) -> Optional[str]:
    """Match a user query against EDGE_VOICES by exact ID, short_name, or substring."""
    q = (query or "").strip().lower()
    if not q:
        return None
    for vid, meta in EDGE_VOICES.items():
        if q in (vid.lower(), meta["short_name"].lower()):
            return vid
    for vid, meta in EDGE_VOICES.items():
        haystack = f"{meta['short_name']} {meta['label']} {meta['description']}".lower()
        if q in haystack:
            return vid
    return None


def get_voice_display_label(voice_id: str, f5_voices: Optional[list[dict[str, Any]]] = None) -> str:
    vid = (voice_id or "").strip() or DEFAULT_EDGE_VOICE
    if vid in EDGE_VOICES:
        return EDGE_VOICES[vid]["label"]
    prefixed = f"edge:{vid}"
    if prefixed in EDGE_VOICES:
        return EDGE_VOICES[prefixed]["label"]
    if vid.startswith("f5:"):
        raw_id = vid[3:]
        if f5_voices:
            for fv in f5_voices:
                if fv.get("id") == vid or fv.get("raw_id") == raw_id:
                    return f"🎙️ {fv.get('name', raw_id)} (F5-TTS)"
        return f"🎙️ {raw_id} (F5-TTS)"
    return vid
