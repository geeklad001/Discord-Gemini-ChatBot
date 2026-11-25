"""DiscordGeminiBot entrypoint: loads profile/env, initializes engines & Cogs, and runs the Discord bot."""

import logging
import os
import sys

import discord
from discord.ext import commands
from google import genai

from core.config import APP_VERSION, apply_runtime_overrides, init_runtime_env, load_settings


def _disable_windows_quickedit() -> None:
    if os.name != "nt":
        return
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        h_stdin = kernel32.GetStdHandle(-10)
        mode = ctypes.c_uint32()
        if kernel32.GetConsoleMode(h_stdin, ctypes.byref(mode)):
            kernel32.SetConsoleMode(h_stdin, (mode.value & ~0x0040) | 0x0080)
    except Exception:
        pass


_disable_windows_quickedit()
env_file = init_runtime_env()

from engines import (
    assistant_db,
    boardgame_engine,
    flashcard_engine,
    game_wiki_engine,
    lifesim_engine,
    rpg_engine,
    safety_guardrail,
    tts_engine,
)
from cogs.assistant_cog import AssistantCog
from cogs.boardgame_cog import BoardGameCog
from cogs.flashcard_cog import FlashcardCog
from cogs.lifesim_cog import LifeSimCog
from cogs.rpg_cog import RpgCog
from cogs.setup_cog import SetupCog
from cogs.tts_cog import TtsCog
from cogs.wiki_cog import WikiCog
from core.chat_pipeline import (
    handle_general_chat,
    handle_japanese_learning_chat,
    is_rpg_context,
    is_target_channel,
)

# -------------------------------------------------------------
# 1. 日誌與設定載入 (於 dotenv 載入後執行，確保多實例完全隔離)
# -------------------------------------------------------------
prelim_name = (os.getenv("BOT_INSTANCE_NAME", "DiscordGeminiBot") or "DiscordGeminiBot").strip()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(prelim_name)

try:
    settings = load_settings(env_file=env_file, logger=logger)
except ValueError as err:
    logger.error(f"❌ {err}")
    sys.exit(1)

# 初始化核心資料庫與套用 !setup SQLite 動態覆寫（於引擎建表與 Cog 載入前執行）
assistant_db.init_db()
applied_overrides = apply_runtime_overrides(settings, assistant_db.get_runtime_config_overrides())
if applied_overrides:
    logger.info(f"⚙️ 已套用 {len(applied_overrides)} 項 !setup 動態覆寫: {', '.join(applied_overrides)}")

if not settings.enable_japanese_mode:
    logger.info("🌐 日語教學模式已關閉，機器人將以繁體中文日常助理與 TRPG 冒險模式運作。")
if not settings.enable_flashcard_mode:
    logger.info("🎴 單字卡與搶答測驗功能已關閉。")
if not settings.enable_game_wiki:
    logger.info("🧟 《殭屍學園》知識庫與專屬檢索功能已關閉。")
if not settings.enable_board_games:
    logger.info("🎲 棋盤桌遊系統已關閉。")
if not settings.enable_voice_tts:
    logger.info("🔇 語音頻道 TTS 朗讀系統已關閉。")
if not settings.enable_safety_guardrail:
    logger.info("🔓 本地安全與防濫用護欄已關閉 (暢聊不受限制)。")

# 初始化其餘引擎資料表與本地知識庫
rpg_engine.init_rpg_tables()
if settings.enable_board_games:
    boardgame_engine.init_boardgame_tables()
    lifesim_engine.init_lifesim_tables()
if settings.enable_flashcard_mode:
    flashcard_engine.init_flashcard_tables()
if settings.enable_voice_tts:
    tts_engine.init_tts_tables()
if settings.enable_game_wiki:
    game_wiki_engine.load_wiki_entries()

logger.info(
    f"🚀 已成功載入設定檔: {settings.env_file} "
    f"(v{APP_VERSION} | 實例: {settings.bot_instance_name} | 資料庫: {assistant_db.get_db_path().name})"
)
if settings.tts_channel_id:
    logger.info(f"📻 已設定第二頻道 (TTS 專用日語朗讀頻道) ID: {settings.tts_channel_id}")
if settings.general_channel_id:
    logger.info(f"💬 已設定一般日常群聊頻道 ID: {settings.general_channel_id}")

# -------------------------------------------------------------
# 2. 初始化 Gemini Client 與 Discord Bot
# -------------------------------------------------------------
gemini_client = genai.Client(api_key=settings.gemini_api_key)

intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.voice_states = True

# 資安防護：禁止機器人觸發 @everyone 或 @here 群體提及轟炸
SAFE_ALLOWED_MENTIONS = discord.AllowedMentions(everyone=False, roles=False, users=True)


class GeminiBot(commands.Bot):
    """Modular Discord Bot hosting Assistant, RPG, optional Flashcard, TTS & Wiki Cogs."""

    async def setup_hook(self) -> None:
        await self.add_cog(AssistantCog(self, settings, logger, gemini_client=gemini_client))
        await self.add_cog(SetupCog(self, settings, logger, gemini_client=gemini_client))
        await self.add_cog(RpgCog(self, settings, logger, gemini_client=gemini_client))
        if settings.enable_board_games:
            await self.add_cog(BoardGameCog(self, gemini_client, settings, logger))
            await self.add_cog(LifeSimCog(self, gemini_client, settings, logger))
        if settings.enable_flashcard_mode:
            await self.add_cog(FlashcardCog(self, gemini_client, settings, logger))
        if settings.enable_game_wiki:
            await self.add_cog(WikiCog(self, gemini_client, settings, logger))
        if settings.enable_voice_tts:
            await self.add_cog(TtsCog(self, settings, logger))


bot = GeminiBot(
    command_prefix="!",
    intents=intents,
    help_command=None,
    allowed_mentions=SAFE_ALLOWED_MENTIONS,
)


@bot.event
async def on_ready():
    logger.info(f"🚀 {settings.bot_instance_name} 登入成功！已連線身分: {bot.user} (ID: {bot.user.id})")
    logger.info(
        f"👀 監聽頻道: TARGET={settings.target_channel_id}, "
        f"TTS={settings.tts_channel_id}, GENERAL={settings.general_channel_id} "
        f"| 首選模型: {settings.primary_model}"
    )


@bot.event
async def on_thread_delete(thread: discord.Thread):
    """邊界保護：當使用者在 Discord 刪除討論串時，安全記錄日誌。"""
    logger.info(f"🗑️ 偵測到討論串已被刪除: #{thread.name} (ID: {thread.id})")


@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return

    if message.content.startswith("!"):
        await bot.process_commands(message)
        return

    if settings.enable_voice_tts:
        tts_cog = bot.get_cog("TtsCog")
        if tts_cog:
            try:
                await tts_cog.handle_proxy_message(message)
            except Exception as exc:
                logger.error(f"❌ 語音代說處理失敗: {exc}", exc_info=True)

    if not is_target_channel(message.channel, settings):
        return

    flashcard_cog = bot.get_cog("FlashcardCog")
    if flashcard_cog and message.channel.id in getattr(flashcard_cog, "active_quiz_channels", ()):
        return

    if flashcard_cog and message.attachments:
        try:
            if await flashcard_cog.handle_shadowing_audio_message(message):
                return
        except Exception as exc:
            logger.error(f"❌ 日語跟讀音訊處理失敗: {exc}", exc_info=True)

    if not message.content.strip() and not message.attachments and not getattr(message, "stickers", None):
        return

    if settings.enable_safety_guardrail:
        in_rpg = is_rpg_context(message.channel, message.clean_content)
        safety_res = safety_guardrail.check_message_safety(
            text=message.clean_content,
            user_id=message.author.id,
            enable_japanese_mode=settings.enable_japanese_mode,
            is_rpg_session=in_rpg,
        )
        if not safety_res.allowed:
            logger.warning(
                f"🛡️ [SafetyGuardrail] 攔截來自 [{message.author.display_name}] 的請求 "
                f"(類別: {safety_res.category}): {message.clean_content[:100]}"
            )
            if safety_res.should_send_reply and safety_res.reply:
                await message.channel.send(safety_res.reply)
            return

    if not settings.enable_japanese_mode:
        await handle_general_chat(bot, gemini_client, settings, message, logger=logger)
        return

    is_general = False
    if settings.general_channel_id:
        if message.channel.id == settings.general_channel_id:
            is_general = True
        elif hasattr(message.channel, "parent_id") and message.channel.parent_id == settings.general_channel_id:
            is_general = True

    if is_general:
        await handle_general_chat(bot, gemini_client, settings, message, logger=logger)
    else:
        await handle_japanese_learning_chat(bot, gemini_client, settings, message, logger=logger)


if __name__ == "__main__":
    try:
        bot.run(settings.discord_bot_token)
    except discord.errors.PrivilegedIntentsRequired:
        logger.error(
            "\n" + "=" * 65 + "\n"
            "❌ 啟動失敗：尚未在 Discord 開發者後台開啟「Message Content Intent」！\n"
            "👉 請前往：https://discord.com/developers/applications/\n"
            "👉 點選你的 Bot 應用程式 -> 左側點擊「Bot」頁籤\n"
            "👉 往下滑找到「Privileged Gateway Intents」區塊\n"
            "👉 將【Message Content Intent】切換為開啟 (ON)，並按下「Save Changes」！\n"
            "👉 儲存後重新執行啟動腳本即可順利啟動。\n"
            + "=" * 65
        )
        sys.exit(1)
