"""Configuration, profile/env resolution, and shared formatting helpers."""

import logging
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

MAX_IMAGE_ATTACHMENT_BYTES = 10 * 1024 * 1024  # 圖片上限 10 MB
MAX_TEXT_ATTACHMENT_BYTES = 2 * 1024 * 1024    # 文字設定檔上限 2 MB
MAX_APKG_ATTACHMENT_BYTES = 25 * 1024 * 1024   # Anki 牌組上限 25 MB

DEFAULT_FALLBACK_MODELS = [
    "gemini-flash-lite-latest",
    "gemini-3.5-flash-lite",
    "gemini-3.6-flash",
]


def _parse_bool(raw: Optional[str], default: bool) -> bool:
    if raw is None or not raw.strip():
        return default
    val = raw.strip().lower()
    if val in ("true", "1", "yes", "on"):
        return True
    if val in ("false", "0", "no", "off"):
        return False
    return default


def _parse_int(raw: Optional[str], default: int) -> int:
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw.strip())
    except ValueError:
        return default


def _parse_optional_channel_id(raw: Optional[str], env_name: str, logger: Optional[logging.Logger]) -> Optional[int]:
    if not raw or not raw.strip():
        return None
    try:
        return int(raw.strip())
    except ValueError:
        if logger:
            logger.warning(f"⚠️ {env_name} 格式不正確，將略過此頻道設定。")
        return None


def configure_windows_utf8() -> None:
    """Force UTF-8 stdout/stderr on Windows consoles to prevent Emoji encoding errors."""
    if sys.platform != "win32":
        return
    for stream in (sys.stdout, sys.stderr):
        try:
            if stream and stream.encoding and stream.encoding.lower() != "utf-8":
                stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def resolve_env_file(argv: Optional[list[str]] = None) -> str:
    """Resolve target .env filename from BOT_ENV_FILE or CLI flags (--profile / --env)."""
    env_file = os.getenv("BOT_ENV_FILE")
    args = sys.argv[1:] if argv is None else argv
    for arg in args:
        if arg.startswith("--profile="):
            prof = Path(arg.split("=", 1)[1].strip()).name
            if prof:
                env_file = f".env.{prof}"
        elif arg.startswith("--env="):
            candidate = Path(arg.split("=", 1)[1].strip()).name
            if candidate:
                env_file = candidate
    return env_file or ".env"


def init_runtime_env(argv: Optional[list[str]] = None, override: bool = False) -> str:
    """Configure console encoding and load the selected .env file before reading settings."""
    configure_windows_utf8()
    env_file = resolve_env_file(argv)
    load_dotenv(dotenv_path=BASE_DIR / env_file, override=override)
    return env_file


@dataclass
class BotSettings:
    """Runtime settings and feature flags loaded after dotenv initialization."""

    env_file: str
    bot_instance_name: str
    discord_bot_token: str
    gemini_api_key: str
    target_channel_id: int
    tts_channel_id: Optional[int] = None
    general_channel_id: Optional[int] = None
    enable_japanese_mode: bool = True
    enable_flashcard_mode: bool = True
    enable_game_wiki: bool = True
    enable_safety_guardrail: bool = True
    disable_safety_settings: bool = True
    primary_model: str = "gemini-3.8-flash"
    fallback_models: list[str] = field(default_factory=lambda: list(DEFAULT_FALLBACK_MODELS))
    sliding_window_limit: int = 50
    thread_window_limit: int = 80
    system_prompt_file: str = "system_prompt.txt"
    general_prompt_file: str = "general_prompt.txt"

    @property
    def models_to_try(self) -> list[str]:
        return [self.primary_model] + [m for m in self.fallback_models if m != self.primary_model]


def load_settings(env_file: str = ".env", logger: Optional[logging.Logger] = None) -> BotSettings:
    """Build BotSettings from environment variables and validate required keys."""
    discord_token = (os.getenv("DISCORD_BOT_TOKEN") or "").strip()
    gemini_key = (os.getenv("GEMINI_API_KEY") or "").strip()
    target_raw = (os.getenv("TARGET_CHANNEL_ID") or "").strip()

    if not discord_token or not gemini_key or not target_raw:
        raise ValueError("缺少環境變數！請確認 .env 檔案中是否已設定 DISCORD_BOT_TOKEN、GEMINI_API_KEY 與 TARGET_CHANNEL_ID。")

    try:
        target_channel_id = int(target_raw)
    except ValueError as exc:
        raise ValueError("TARGET_CHANNEL_ID 必須為純數字！請檢查 .env 設定。") from exc

    fb_raw = os.getenv("FALLBACK_MODELS", "").strip()
    fallback_models = (
        [m.strip() for m in fb_raw.split(",") if m.strip()]
        if fb_raw
        else list(DEFAULT_FALLBACK_MODELS)
    )

    return BotSettings(
        env_file=env_file,
        bot_instance_name=(os.getenv("BOT_INSTANCE_NAME", "DiscordGeminiBot") or "DiscordGeminiBot").strip(),
        discord_bot_token=discord_token,
        gemini_api_key=gemini_key,
        target_channel_id=target_channel_id,
        tts_channel_id=_parse_optional_channel_id(os.getenv("TTS_CHANNEL_ID"), "TTS_CHANNEL_ID", logger),
        general_channel_id=_parse_optional_channel_id(os.getenv("GENERAL_CHANNEL_ID"), "GENERAL_CHANNEL_ID", logger),
        enable_japanese_mode=_parse_bool(os.getenv("ENABLE_JAPANESE_MODE"), default=True),
        enable_flashcard_mode=_parse_bool(os.getenv("ENABLE_FLASHCARD_MODE"), default=True),
        enable_game_wiki=_parse_bool(os.getenv("ENABLE_GAME_WIKI"), default=True),
        enable_safety_guardrail=_parse_bool(os.getenv("ENABLE_SAFETY_GUARDRAIL"), default=True),
        disable_safety_settings=_parse_bool(os.getenv("DISABLE_SAFETY_SETTINGS"), default=True),
        primary_model=(os.getenv("PRIMARY_MODEL", "gemini-3.8-flash") or "gemini-3.8-flash").strip(),
        fallback_models=fallback_models,
        sliding_window_limit=_parse_int(os.getenv("SLIDING_WINDOW_LIMIT"), default=50),
        thread_window_limit=_parse_int(os.getenv("THREAD_WINDOW_LIMIT"), default=80),
        system_prompt_file=(os.getenv("SYSTEM_PROMPT_FILE", "system_prompt.txt") or "system_prompt.txt").strip(),
        general_prompt_file=(os.getenv("GENERAL_PROMPT_FILE", "general_prompt.txt") or "general_prompt.txt").strip(),
    )


def truncate_embed_text(text: str, max_len: int = 3800) -> str:
    """Prevent exceeding Discord Embed Description (4096) or Field (1024) limits."""
    if len(text) <= max_len:
        return text
    return text[:max_len] + "\n\n*...（內容過長已自動截斷顯示）*"
