"""Configuration, profile/env resolution, and shared formatting helpers."""

import logging
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
APP_VERSION = "2.3.1"

MAX_IMAGE_ATTACHMENT_BYTES = 10 * 1024 * 1024  # 圖片上限 10 MB
MAX_TEXT_ATTACHMENT_BYTES = 2 * 1024 * 1024    # 文字設定檔上限 2 MB
MAX_APKG_ATTACHMENT_BYTES = 25 * 1024 * 1024   # Anki 牌組上限 25 MB
MAX_AUDIO_ATTACHMENT_BYTES = 3 * 1024 * 1024   # 語音附件上限 3 MB
MAX_AUDIO_DURATION_SECONDS = 30                # 語音訊息時長上限 30 秒

DEFAULT_PRIMARY_MODEL = "gemini-flash-lite-latest"
STRICT_FLASH_LITE_FALLBACK_MODELS = [
    "gemini-3.5-flash-lite",
    "gemini-3.1-flash-lite",
    "gemini-2.5-flash-lite",
]
DEFAULT_FALLBACK_MODELS = [
    "gemini-3.5-flash-lite",
    "gemini-3.1-flash-lite",
    "gemini-2.5-flash-lite",
    "gemini-flash-latest",
    "gemini-3.8-flash",
    "gemini-4-hydrogen",
    "gemini-2.5-flash",
]
DEFAULT_FINAL_FALLBACK_MODEL = "gemini-flash-lite-latest"
FLASH_LITE_PRESET_MODELS = (
    "gemini-flash-lite-latest",
    "gemini-3.5-flash-lite",
    "gemini-3.1-flash-lite",
    "gemini-2.5-flash-lite",
    "gemini-flash-latest",
    "gemini-3.8-flash",
    "gemini-4-hydrogen",
    "gemini-2.5-flash",
)

_RUNTIME_OVERRIDE_BOOL_KEYS: frozenset[str] = frozenset({
    "enable_japanese_mode",
    "enable_flashcard_mode",
    "enable_game_wiki",
    "enable_board_games",
    "enable_voice_tts",
    "enable_safety_guardrail",
    "tts_ai_auto_read_jp",
    "tts_ai_auto_read_general",
})
_RUNTIME_OVERRIDE_SAFE_KEYS: frozenset[str] = _RUNTIME_OVERRIDE_BOOL_KEYS | frozenset({
    "target_channel_id",
    "tts_channel_id",
    "general_channel_id",
    "primary_model",
    "fallback_models",
    "final_fallback_model",
})


def _strip_inline_comment(raw: Optional[str]) -> str:
    if raw is None:
        return ""
    return raw.split("#", 1)[0].strip()


def _parse_bool(raw: Optional[str], default: bool) -> bool:
    clean = _strip_inline_comment(raw).lower()
    if not clean:
        return default
    if clean in ("true", "1", "yes", "on"):
        return True
    if clean in ("false", "0", "no", "off"):
        return False
    return default


def _parse_int(raw: Optional[str], default: int) -> int:
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw.strip())
    except ValueError:
        return default


def _parse_float(raw: Optional[str], default: float) -> float:
    if raw is None or not raw.strip():
        return default
    try:
        return float(raw.strip())
    except ValueError:
        return default


def _parse_int_tuple(raw: Optional[str]) -> tuple[int, ...]:
    if not raw or not raw.strip():
        return ()
    result: list[int] = []
    for token in raw.replace(";", ",").replace(" ", ",").split(","):
        tok = token.strip()
        if not tok:
            continue
        try:
            val = int(tok)
            if val not in result:
                result.append(val)
        except ValueError:
            continue
    return tuple(result)


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
    env_path = BASE_DIR / env_file
    load_dotenv(dotenv_path=env_path, override=override)
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
    enable_board_games: bool = True
    enable_voice_tts: bool = True
    enable_safety_guardrail: bool = True
    disable_safety_settings: bool = True
    enable_user_voice_clone: bool = False
    primary_model: str = DEFAULT_PRIMARY_MODEL
    fallback_models: list[str] = field(default_factory=lambda: list(DEFAULT_FALLBACK_MODELS))
    final_fallback_model: str = DEFAULT_FINAL_FALLBACK_MODEL
    sliding_window_limit: int = 50
    thread_window_limit: int = 80
    system_prompt_file: str = "system_prompt.txt"
    general_prompt_file: str = "general_prompt.txt"
    tts_api_url: str = "http://127.0.0.1:9880"
    tts_api_token: str = ""
    tts_default_voice: str = "edge:zh-TW-HsiaoChenNeural"
    tts_jp_voice: str = "edge:ja-JP-NanamiNeural"
    tts_volume_boost: float = 1.25
    tts_max_chars: int = 500
    tts_f5_max_chars: int = 200
    tts_ai_auto_read_jp: bool = True
    tts_ai_auto_read_general: bool = False
    tts_clone_max_per_user: int = 2
    tts_clone_max_total: int = 40
    tts_clone_cooldown_seconds: int = 600
    tts_clone_approval_mode: str = "auto"
    bot_owner_ids: tuple[int, ...] = ()

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
    primary_model = (os.getenv("PRIMARY_MODEL", DEFAULT_PRIMARY_MODEL) or DEFAULT_PRIMARY_MODEL).strip()
    final_fb_raw = _strip_inline_comment(os.getenv("FINAL_FALLBACK_MODEL"))
    if final_fb_raw.lower() in ("none", "off", "false", "disable"):
        final_fallback_model = ""
    elif final_fb_raw:
        final_fallback_model = final_fb_raw
    else:
        final_fallback_model = primary_model if "flash-lite" in primary_model.lower() else DEFAULT_FINAL_FALLBACK_MODEL
    tts_api_raw = os.getenv("TTS_API_URL")
    tts_api_url = "http://127.0.0.1:9880" if tts_api_raw is None else tts_api_raw.strip()
    raw_clone_mode = (os.getenv("TTS_CLONE_APPROVAL_MODE", "auto") or "auto").strip().lower()
    clone_approval_mode = raw_clone_mode if raw_clone_mode in ("auto", "manual") else "auto"

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
        enable_board_games=_parse_bool(os.getenv("ENABLE_BOARD_GAMES"), default=True),
        enable_voice_tts=_parse_bool(os.getenv("ENABLE_VOICE_TTS"), default=True),
        enable_safety_guardrail=_parse_bool(os.getenv("ENABLE_SAFETY_GUARDRAIL"), default=True),
        disable_safety_settings=_parse_bool(os.getenv("DISABLE_SAFETY_SETTINGS"), default=True),
        enable_user_voice_clone=_parse_bool(
            os.getenv("ENABLE_USER_VOICE_CLONE")
            or os.getenv("ALLOW_USER_VOICE_CLONE")
            or os.getenv("ALLOW_VOICE_CLONE")
            or os.getenv("ENABLE_VOICE_CLONE"),
            default=False,
        ),
        primary_model=primary_model,
        fallback_models=fallback_models,
        final_fallback_model=final_fallback_model,
        sliding_window_limit=_parse_int(os.getenv("SLIDING_WINDOW_LIMIT"), default=50),
        thread_window_limit=_parse_int(os.getenv("THREAD_WINDOW_LIMIT"), default=80),
        system_prompt_file=(os.getenv("SYSTEM_PROMPT_FILE", "system_prompt.txt") or "system_prompt.txt").strip(),
        general_prompt_file=(os.getenv("GENERAL_PROMPT_FILE", "general_prompt.txt") or "general_prompt.txt").strip(),
        tts_api_url=tts_api_url,
        tts_api_token=(os.getenv("TTS_API_TOKEN") or "").strip(),
        tts_default_voice=(os.getenv("TTS_DEFAULT_VOICE", "edge:zh-TW-HsiaoChenNeural") or "edge:zh-TW-HsiaoChenNeural").strip(),
        tts_jp_voice=(os.getenv("TTS_JP_VOICE", "edge:ja-JP-NanamiNeural") or "edge:ja-JP-NanamiNeural").strip(),
        tts_volume_boost=max(0.2, min(3.0, _parse_float(os.getenv("TTS_VOLUME_BOOST"), default=1.25))),
        tts_max_chars=max(30, min(2000, _parse_int(os.getenv("TTS_MAX_CHARS"), default=500))),
        tts_f5_max_chars=max(20, min(1000, _parse_int(os.getenv("TTS_F5_MAX_CHARS"), default=200))),
        tts_ai_auto_read_jp=_parse_bool(os.getenv("TTS_AI_AUTO_READ_JP"), default=True),
        tts_ai_auto_read_general=_parse_bool(os.getenv("TTS_AI_AUTO_READ_GENERAL"), default=False),
        tts_clone_max_per_user=max(1, min(5, _parse_int(os.getenv("TTS_CLONE_MAX_PER_USER"), default=2))),
        tts_clone_max_total=max(1, min(200, _parse_int(os.getenv("TTS_CLONE_MAX_TOTAL"), default=40))),
        tts_clone_cooldown_seconds=max(0, min(86400, _parse_int(os.getenv("TTS_CLONE_COOLDOWN_SECONDS"), default=600))),
        tts_clone_approval_mode=clone_approval_mode,
        bot_owner_ids=_parse_int_tuple(os.getenv("BOT_OWNER_IDS")),
    )


def apply_runtime_overrides(settings: BotSettings, overrides: dict[str, str]) -> list[str]:
    """Apply safe, non-secret runtime overrides onto BotSettings and return applied keys."""
    if not overrides:
        return []

    applied: list[str] = []
    for key, raw_val in overrides.items():
        if key not in _RUNTIME_OVERRIDE_SAFE_KEYS:
            continue

        raw_str = "" if raw_val is None else str(raw_val)

        if key in _RUNTIME_OVERRIDE_BOOL_KEYS:
            clean = _strip_inline_comment(raw_str).lower()
            if clean in ("true", "1", "yes", "on", "false", "0", "no", "off"):
                current = bool(getattr(settings, key))
                setattr(settings, key, _parse_bool(raw_str, current))
                applied.append(key)
            continue

        if key == "target_channel_id":
            try:
                parsed_id = int(raw_str.strip())
            except ValueError:
                parsed_id = 0
            if parsed_id > 0:
                settings.target_channel_id = parsed_id
                applied.append(key)
            continue

        if key in ("tts_channel_id", "general_channel_id"):
            stripped = raw_str.strip()
            if stripped == "":
                setattr(settings, key, None)
                applied.append(key)
            else:
                parsed_id = _parse_optional_channel_id(stripped, key.upper(), None)
                if parsed_id is not None and parsed_id > 0:
                    setattr(settings, key, parsed_id)
                    applied.append(key)
            continue

        if key == "primary_model":
            stripped = _strip_inline_comment(raw_str)
            if stripped:
                settings.primary_model = stripped
                applied.append(key)
            continue

        if key == "final_fallback_model":
            stripped = _strip_inline_comment(raw_str)
            if stripped.lower() in ("none", "off", "false", "disable"):
                settings.final_fallback_model = ""
                applied.append(key)
            elif stripped:
                settings.final_fallback_model = stripped
                applied.append(key)
            continue

        if key == "fallback_models":
            models = [m.strip() for m in raw_str.split(",") if m.strip()]
            if models:
                settings.fallback_models = models
                applied.append(key)

    return applied


def truncate_embed_text(text: str, max_len: int = 3800) -> str:
    """Prevent exceeding Discord Embed Description (4096) or Field (1024) limits."""
    if len(text) <= max_len:
        return text
    return text[:max_len] + "\n\n*...（內容過長已自動截斷顯示）*"


def resolve_prompt_path(prompt_filename: str) -> Path:
    """Resolve prompt file under prompts/ (preferred) or legacy root path (e.g., *_personal.txt)."""
    clean_name = Path((prompt_filename or "").strip()).name
    if not clean_name:
        return BASE_DIR / "prompts" / "general_prompt.txt"
    prompts_candidate = BASE_DIR / "prompts" / clean_name
    if prompts_candidate.is_file():
        return prompts_candidate
    legacy_candidate = BASE_DIR / clean_name
    if legacy_candidate.is_file():
        return legacy_candidate
    return prompts_candidate


def is_configured_owner(settings: BotSettings, user_id: Optional[int]) -> bool:
    """Return True if user_id is explicitly listed in BOT_OWNER_IDS."""
    if user_id is None:
        return False
    try:
        return int(user_id) in settings.bot_owner_ids
    except (TypeError, ValueError):
        return False


async def is_bot_owner(bot: Any, settings: BotSettings, user: Any) -> bool:
    """Check cross-server Bot Owner authority via BOT_OWNER_IDS or Discord application owner."""
    if user is None:
        return False
    if is_configured_owner(settings, getattr(user, "id", None)):
        return True
    try:
        owner_fn = getattr(bot, "is_owner", None)
        if callable(owner_fn):
            return bool(await owner_fn(user))
    except Exception:
        return False
    return False


def resolve_bot_display_name(
    bot: Any = None,
    *,
    guild: Any = None,
    channel: Any = None,
    settings: Optional[BotSettings] = None,
    fallback: str = "機器人",
    max_len: int = 24,
) -> str:
    """Resolve the bot's display name in a guild (server nickname > global display name > instance name > fallback)."""
    resolved_guild = guild if guild is not None else getattr(channel, "guild", None)
    candidates: list[Any] = []
    if resolved_guild is not None:
        me = getattr(resolved_guild, "me", None)
        if me is not None:
            candidates.append(getattr(me, "display_name", None))
            candidates.append(getattr(me, "nick", None))
            candidates.append(getattr(me, "name", None))
    if bot is not None:
        user = getattr(bot, "user", None)
        if user is not None:
            candidates.append(getattr(user, "display_name", None))
            candidates.append(getattr(user, "name", None))
    if settings is not None:
        candidates.append(getattr(settings, "bot_instance_name", None))
    candidates.append(fallback)

    for raw in candidates:
        if isinstance(raw, str):
            clean = " ".join(raw.split()).strip()
            if clean:
                return clean[:max_len] if max_len > 0 else clean
    return fallback
