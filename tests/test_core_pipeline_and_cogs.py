"""Async & unit tests for core.media, core.chat_pipeline, and Cogs wiring."""

import io
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import discord
from discord.ext import commands
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from cogs.assistant_cog import AssistantCog
from cogs.boardgame_cog import BoardGameCog
from cogs.flashcard_cog import FlashcardCog, check_quiz_answer_match
from cogs.rpg_cog import RpgCog
from cogs.wiki_cog import WikiCog
from core.chat_pipeline import (
    is_rpg_context,
    is_target_channel,
    prepare_and_generate_chat_response,
    send_synchronized_response,
)
from core.config import BotSettings
from core.media import convert_image_or_gif_for_gemini, is_gif_or_media_share_url, is_safe_public_url


def _make_settings(**overrides) -> BotSettings:
    defaults = dict(
        env_file=".env",
        bot_instance_name="TestBot",
        discord_bot_token="tok",
        gemini_api_key="key",
        target_channel_id=1001,
        tts_channel_id=1002,
        general_channel_id=1003,
        enable_japanese_mode=True,
        enable_flashcard_mode=True,
        enable_game_wiki=True,
        enable_safety_guardrail=True,
        disable_safety_settings=True,
    )
    defaults.update(overrides)
    return BotSettings(**defaults)


class TestMediaAndPipeline(unittest.IsolatedAsyncioTestCase):
    def test_ssrf_checks(self):
        self.assertFalse(is_safe_public_url("http://127.0.0.1:8080/admin"))
        self.assertFalse(is_safe_public_url("http://192.168.1.1/"))
        self.assertFalse(is_safe_public_url("http://localhost/secret"))
        self.assertFalse(is_safe_public_url("file:///etc/passwd"))
        self.assertTrue(is_gif_or_media_share_url("https://tenor.com/view/cat-123"))

    def test_gif_and_lottie_conversion(self):
        lottie_bytes = b'{"v":"5.5.7","fr":60}'
        b, mime, anim = convert_image_or_gif_for_gemini(lottie_bytes, "application/json", "sticker.json")
        self.assertIsNone(b)
        self.assertFalse(anim)

        frames = [Image.new("RGBA", (32, 32), (255, i * 80, 0, 255)) for i in range(3)]
        buf = io.BytesIO()
        frames[0].save(buf, format="GIF", save_all=True, append_images=frames[1:], duration=100, loop=0)
        conv_b, conv_mime, is_anim = convert_image_or_gif_for_gemini(buf.getvalue(), "image/gif", "anim.gif")
        self.assertIsNotNone(conv_b)
        self.assertEqual(conv_mime, "image/png")
        self.assertTrue(is_anim)

    def test_channel_and_rpg_context_detection(self):
        settings = _make_settings()
        ch = SimpleNamespace(id=1001)
        thread = SimpleNamespace(id=9999, parent_id=1003)
        other = SimpleNamespace(id=5555)
        self.assertTrue(is_target_channel(ch, settings))
        self.assertTrue(is_target_channel(thread, settings))
        self.assertFalse(is_target_channel(other, settings))

        with patch("rpg_engine.is_rpg_session_active", return_value=False):
            self.assertTrue(is_rpg_context(SimpleNamespace(id=1), "我想玩文字冒險"))
            self.assertFalse(is_rpg_context(SimpleNamespace(id=1), "今天天氣真好"))

    async def test_send_synchronized_response_protects_tts(self):
        settings = _make_settings()
        main_ch = AsyncMock()
        main_ch.id = 1001
        tts_ch = AsyncMock()
        tts_ch.id = 1002
        bot = MagicMock()
        bot.get_channel.side_effect = lambda cid: main_ch if cid == 1001 else tts_ch

        await send_synchronized_response(bot, settings, main_ch, "[JP]こんにちは\n[ZH]你好")
        main_ch.send.assert_awaited_once()
        tts_ch.send.assert_awaited_once_with(content="こんにちは")

        main_ch.reset_mock()
        tts_ch.reset_mock()
        await send_synchronized_response(bot, settings, main_ch, "已完成待辦事項記錄")
        main_ch.send.assert_awaited_once()
        tts_ch.send.assert_not_called()

    async def test_pipeline_skips_wiki_ocr_when_disabled(self):
        settings = _make_settings(enable_japanese_mode=False, enable_flashcard_mode=False, enable_game_wiki=False)
        msg = MagicMock()
        msg.channel = SimpleNamespace(id=1001)
        msg.clean_content = "看這張圖片"
        msg.attachments = [SimpleNamespace(content_type="image/png")]
        fake_resp = SimpleNamespace(text="這張圖看起來很棒。", usage_metadata=None)
        fake_client = MagicMock()

        with (
            patch("rpg_engine.is_rpg_session_active", return_value=False),
            patch("rpg_engine.match_lorebook_for_text", return_value=[]),
            patch("assistant_db.format_memories_for_prompt", return_value=""),
            patch("game_wiki_engine.extract_image_text_pass1", new=AsyncMock()) as mock_ocr,
            patch("core.chat_pipeline.generate_with_fallback", new=AsyncMock(return_value=(fake_resp, "gemini-flash-lite-latest"))) as mock_gen,
        ):
            reply, used = await prepare_and_generate_chat_response(
                gemini_client=fake_client,
                settings=settings,
                message=msg,
                contents=[],
                system_prompt_fn=lambda s, m, ocr_text="", recent_context_text="": "prompt",
                base_temperature=0.7,
                context_label="Test",
            )
            mock_ocr.assert_not_called()
            called_config = mock_gen.call_args.kwargs["config"]
            tool_names = {fn.__name__ for fn in called_config.tools}
            self.assertNotIn("lookup_game_wiki", tool_names)
            self.assertNotIn("add_flashcard_card", tool_names)
            self.assertEqual(reply, "這張圖看起來很棒。")
            self.assertEqual(used, "gemini-flash-lite-latest")

    async def test_cogs_registration_under_flags(self):
        intents = discord.Intents.none()
        bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)
        settings = _make_settings(enable_flashcard_mode=False, enable_game_wiki=False)
        logger = MagicMock()
        client = MagicMock()

        assistant_cog = AssistantCog(bot, settings, logger)
        await bot.add_cog(assistant_cog)
        await bot.add_cog(RpgCog(bot, settings, logger))
        self.assertIsNotNone(bot.get_command("help"))
        self.assertIsNotNone(bot.get_command("reload"))
        self.assertIsNotNone(bot.get_command("rpg"))
        self.assertIsNone(bot.get_command("card"))
        self.assertIsNone(bot.get_command("wiki"))

        await bot.add_cog(FlashcardCog(bot, client, settings, logger))
        await bot.add_cog(WikiCog(bot, client, settings, logger))
        await bot.add_cog(BoardGameCog(bot, client, settings, logger))
        self.assertIsNotNone(bot.get_command("card"))
        self.assertIsNotNone(bot.get_command("wiki"))
        self.assertIsNotNone(bot.get_command("play"))
        self.assertIsNotNone(bot.get_command("m"))
        self.assertIsNone(bot.get_command("resign"))
        await bot.close()

    def test_quiz_answer_match(self):
        card = {"front": "桜", "reading": "さくら", "back": "櫻花 / 櫻樹"}
        self.assertTrue(check_quiz_answer_match("さくら", card))
        self.assertTrue(check_quiz_answer_match("櫻花", card))
        self.assertFalse(check_quiz_answer_match("蘋果", card))


if __name__ == "__main__":
    unittest.main()
