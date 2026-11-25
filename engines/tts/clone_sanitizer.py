"""User voice clone quarantine storage, transcript/name validation, and FFmpeg sanitizer."""

from __future__ import annotations

import asyncio
import logging
import re
import shutil
import tempfile
import uuid
import wave
from pathlib import Path

from engines import assistant_db

logger = logging.getLogger("DiscordGeminiBot.tts")

CLONE_MAX_UPLOAD_BYTES = 5 * 1024 * 1024
CLONE_ALLOWED_EXTENSIONS = (".wav", ".mp3", ".m4a", ".ogg", ".flac")
CLONE_MIN_DURATION_SEC = 3.0
CLONE_MAX_DURATION_SEC = 15.0
CLONE_MIN_PROMPT_CHARS = 5
CLONE_MAX_PROMPT_CHARS = 120


def get_pending_clones_dir() -> Path:
    """Resolve quarantine directory dynamically so profile/test DATA_DIR overrides always apply."""
    path = (assistant_db.DATA_DIR / "pending_clones").resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def save_pending_clone_wav(wav_bytes: bytes) -> Path:
    """Persist FFmpeg-sanitized WAV bytes into quarantine storage with a random filename."""
    pending_dir = get_pending_clones_dir()
    target = (pending_dir / f"clone_{uuid.uuid4().hex}.wav").resolve()
    if not target.is_relative_to(pending_dir):
        raise ValueError("暫存音檔路徑異常")
    target.write_bytes(wav_bytes)
    return target


def delete_pending_clone_wav(path_str: str) -> None:
    if not path_str:
        return
    pending_dir = get_pending_clones_dir()
    try:
        candidate = Path(path_str).resolve()
        if candidate.is_relative_to(pending_dir) and candidate.is_file():
            candidate.unlink(missing_ok=True)
    except OSError:
        pass


def sanitize_clone_voice_name(raw_name: str, fallback: str = "個人聲線") -> str:
    cleaned = re.sub(r"[\x00-\x1f\x7f`*_~|<>@\\]", "", str(raw_name or ""))
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if not cleaned:
        cleaned = re.sub(r"[\x00-\x1f\x7f`*_~|<>@\\]", "", str(fallback or "個人聲線"))
        cleaned = re.sub(r"\s+", " ", cleaned).strip() or "個人聲線"
    return cleaned[:24].strip()


def validate_clone_prompt_text(raw_text: str) -> tuple[bool, str]:
    """Validate and clean reference audio transcript (5-120 speakable chars, no mentions/control chars)."""
    text = re.sub(r"<@!?\d+>|<#\d+>|<@&\d+>", "", str(raw_text or ""))
    text = re.sub(r"[\x00-\x1f\x7f`*_~|]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) < CLONE_MIN_PROMPT_CHARS:
        return False, f"請附上參考音檔中實際說出的逐字稿（至少 {CLONE_MIN_PROMPT_CHARS} 字，例如：`大家好今天天氣真不錯`）。"
    if len(text) > CLONE_MAX_PROMPT_CHARS:
        return False, f"參考音檔逐字稿過長（目前 {len(text)} 字，上限 {CLONE_MAX_PROMPT_CHARS} 字）。"
    if not re.search(r"[0-9A-Za-z\u3040-\u30FF\u3100-\u312F\u4E00-\u9FFF\uAC00-\uD7AF]", text):
        return False, "逐字稿必須包含有效語音文字（不可只有符號）。"
    return True, text


def _prepare_clone_tmpdir(raw_bytes: bytes, ext: str) -> tuple[Path, Path, Path]:
    tmp_root = Path(tempfile.mkdtemp(prefix="dgbot_clone_"))
    in_path = tmp_root / f"input{ext}"
    out_path = tmp_root / "sanitized.wav"
    try:
        in_path.write_bytes(raw_bytes)
    except Exception:
        shutil.rmtree(tmp_root, ignore_errors=True)
        raise
    return tmp_root, in_path, out_path


def _validate_and_read_sanitized_wav(out_path: Path, filename: str) -> tuple[bytes, float]:
    if not out_path.is_file():
        raise ValueError("無法解析此音訊檔案，請確認檔案未損毀且為有效音檔。")
    try:
        with wave.open(str(out_path), "rb") as wf:
            frames = wf.getnframes()
            rate = wf.getframerate()
            duration = float(frames) / float(rate) if rate > 0 else 0.0
    except wave.Error as exc:
        logger.warning(f"⚠️ 轉檔後 WAV 驗證失敗 (file={filename}): {exc}")
        raise ValueError("無法驗證音訊內容，請重新匯出或更換音檔後再試。") from exc

    if duration < CLONE_MIN_DURATION_SEC:
        raise ValueError(
            f"音檔過短（{duration:.1f} 秒），請提供 {CLONE_MIN_DURATION_SEC:.0f}～{CLONE_MAX_DURATION_SEC:.0f} 秒的清晰人聲。"
        )
    if duration > CLONE_MAX_DURATION_SEC:
        raise ValueError(
            f"音檔超過長度限制（需介於 {CLONE_MIN_DURATION_SEC:.0f}～{CLONE_MAX_DURATION_SEC:.0f} 秒），請先裁切後再上傳。"
        )

    wav_bytes = out_path.read_bytes()
    if len(wav_bytes) <= 1024 or len(wav_bytes) > CLONE_MAX_UPLOAD_BYTES:
        raise ValueError("淨化後音檔大小異常，請重新錄製短音檔。")
    return wav_bytes, round(duration, 2)


async def sanitize_clone_audio_bytes(raw_bytes: bytes, filename: str) -> tuple[bytes, float]:
    """Re-encode uploaded audio via FFmpeg into clean 24kHz mono s16 WAV and verify 3-15s duration."""
    if not raw_bytes:
        raise ValueError("上傳的音訊檔案是空的。")
    if len(raw_bytes) > CLONE_MAX_UPLOAD_BYTES:
        raise ValueError(f"音訊檔案過大（上限 {CLONE_MAX_UPLOAD_BYTES // (1024 * 1024)} MB）。")

    ext = Path(filename or "").suffix.lower()
    if ext not in CLONE_ALLOWED_EXTENSIONS:
        allowed_str = " / ".join(CLONE_ALLOWED_EXTENSIONS)
        raise ValueError(f"不支援的音訊格式 `{ext or '無副檔名'}`，僅接受 {allowed_str}。")

    tmp_root, in_path, out_path = await asyncio.to_thread(_prepare_clone_tmpdir, raw_bytes, ext)
    try:
        try:
            proc = await asyncio.create_subprocess_exec(
                "ffmpeg",
                "-nostdin",
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-protocol_whitelist",
                "file",
                "-i",
                str(in_path),
                "-vn",
                "-sn",
                "-dn",
                "-map_metadata",
                "-1",
                "-ac",
                "1",
                "-ar",
                "24000",
                "-sample_fmt",
                "s16",
                "-t",
                "16",
                str(out_path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError as exc:
            raise RuntimeError("主機未安裝 `ffmpeg`，無法安全驗證與轉檔音訊。") from exc

        try:
            _, stderr_data = await asyncio.wait_for(proc.communicate(), timeout=15.0)
        except asyncio.TimeoutError as exc:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(proc.communicate(), timeout=2.0)
            except Exception:
                pass
            raise ValueError("音訊轉檔超時，請改傳 3～15 秒的標準短音檔。") from exc

        if proc.returncode != 0:
            err_hint = stderr_data.decode("utf-8", errors="ignore").strip()[:300]
            logger.warning(f"⚠️ FFmpeg 音訊轉檔失敗 (rc={proc.returncode}, file={filename}): {err_hint or 'no stderr'}")
            raise ValueError("無法解析此音訊檔案，請確認檔案未損毀且為有效音檔。")

        return await asyncio.to_thread(_validate_and_read_sanitized_wav, out_path, filename)
    finally:
        try:
            await asyncio.to_thread(shutil.rmtree, tmp_root, True)
        except Exception:
            shutil.rmtree(tmp_root, ignore_errors=True)
