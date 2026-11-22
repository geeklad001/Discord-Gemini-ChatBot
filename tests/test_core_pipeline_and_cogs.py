"""Async & unit tests for core.media, core.chat_pipeline, and Cogs wiring."""

import io
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import discord
from discord.ext import commands
from google.genai import types
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
    build_chat_history_contents,
    is_rpg_context,
    is_target_channel,
    prepare_and_generate_chat_response,
    send_synchronized_response,
)
from core.config import BotSettings
from core.llm_client import generate_with_fallback
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

        with patch("engines.rpg_engine.is_rpg_session_active", return_value=False):
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
            patch("engines.rpg_engine.is_rpg_session_active", return_value=False),
            patch("engines.rpg_engine.match_lorebook_for_text", return_value=[]),
            patch("engines.assistant_db.format_memories_for_prompt", return_value=""),
            patch("engines.boardgame_engine.get_latest_game_in_channel", return_value=None),
            patch("engines.lifesim_engine.get_active_life_run", return_value=None),
            patch("engines.game_wiki_engine.extract_image_text_pass1", new=AsyncMock()) as mock_ocr,
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

        from cogs.lifesim_cog import LifeSimCog

        await bot.add_cog(FlashcardCog(bot, client, settings, logger))
        await bot.add_cog(WikiCog(bot, client, settings, logger))
        await bot.add_cog(BoardGameCog(bot, client, settings, logger))
        await bot.add_cog(LifeSimCog(bot, client, settings, logger))
        self.assertIsNotNone(bot.get_cog("LifeSimCog"))
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

    async def test_boardgame_card_embeds_retained_in_chat_history(self):
        from core.chat_pipeline import build_chat_history_contents

        settings = _make_settings(enable_japanese_mode=True)
        bot = MagicMock()
        bot.user = SimpleNamespace(id=999, display_name="Bot")

        card_embed = discord.Embed(title="⚫ 黑白棋 (Othello)", description="目前輪到 ⚫ 小明")
        card_embed.set_footer(text="棋局 #7 | 用下方選單點選綠點")
        bot_card_msg = SimpleNamespace(
            id=1,
            author=bot.user,
            content="",
            clean_content="",
            embeds=[card_embed],
            attachments=[],
            stickers=[],
        )
        user_msg = SimpleNamespace(
            id=2,
            author=SimpleNamespace(id=123, display_name="小明"),
            content="剛才那步為什麼下那裡？",
            clean_content="剛才那步為什麼下那裡？",
            embeds=[],
            attachments=[],
            stickers=[],
        )

        async def fake_history(**kwargs):
            yield user_msg
            yield bot_card_msg

        channel = MagicMock()
        channel.id = 1001
        channel.history = fake_history
        bot.get_channel.return_value = channel

        with patch("core.chat_pipeline.get_effective_reset_timestamp", return_value=None):
            turns = await build_chat_history_contents(bot, settings, channel, user_msg, is_japanese_dual_mode=True)
        self.assertEqual(len(turns), 2)
        self.assertEqual(turns[0].role, "model")
        self.assertIn("系統已貼出", turns[0].parts[0].text)
        self.assertIn("黑白棋", turns[0].parts[0].text)
        self.assertEqual(turns[1].role, "user")

    def test_strip_hallucinated_board_card_echo(self):
        from core.chat_pipeline import _strip_hallucinated_board_card_echo

        noisy = (
            "E2 落下去，倒是不小心幫自己清出了一條路。[棋盤卡片]\n"
            "黑白棋 (Othello) — PvE (🧠 Gemini LLM)\n"
            "👉目前輪到：白棋\n"
            "📌最新動態：黑棋 落子 E2"
        )
        cleaned = _strip_hallucinated_board_card_echo(noisy)
        self.assertEqual(cleaned, "E2 落下去，倒是不小心幫自己清出了一條路。")

    def test_get_current_general_prompt_runs_with_boardgames_enabled(self):
        from core.chat_pipeline import get_current_general_prompt, get_current_system_prompt

        settings = _make_settings(enable_board_games=True)
        msg = SimpleNamespace(
            channel=SimpleNamespace(id=54321),
            author=SimpleNamespace(id=1, display_name="Tester"),
            clean_content="測試",
        )
        with (
            patch("engines.assistant_db.format_memories_for_prompt", return_value=""),
            patch("engines.rpg_engine.match_lorebook_for_text", return_value=[]),
            patch("engines.rpg_engine.is_rpg_session_active", return_value=False),
            patch("engines.boardgame_engine.get_latest_game_in_channel", return_value=None),
            patch("engines.lifesim_engine.get_active_life_run", return_value=None),
        ):
            gen_prompt = get_current_general_prompt(settings, msg, recent_context_text="大家好")
            sys_prompt = get_current_system_prompt(settings, msg, recent_context_text="hello")
        self.assertIn("台北時間", gen_prompt)
        self.assertIn("台北時間", sys_prompt)

    async def test_build_chat_history_excludes_newer_bot_reply_and_bounds_before(self):
        settings = _make_settings(enable_japanese_mode=False)
        bot = MagicMock()
        bot.user = SimpleNamespace(id=999, display_name="Bot")

        earlier_user_msg = SimpleNamespace(
            id=100,
            author=SimpleNamespace(id=10, display_name="阿明"),
            content="第一句",
            clean_content="第一句",
            embeds=[],
            attachments=[],
            stickers=[],
        )
        user_msg = SimpleNamespace(
            id=101,
            author=SimpleNamespace(id=11, display_name="mo"),
            content="第二句",
            clean_content="第二句",
            embeds=[],
            attachments=[],
            stickers=[],
        )
        later_bot_msg = SimpleNamespace(
            id=102,
            author=bot.user,
            content="插隊的 Bot 回覆",
            clean_content="插隊的 Bot 回覆",
            embeds=[],
            attachments=[],
            stickers=[],
        )

        captured_kwargs = {}

        async def fake_history(**kwargs):
            captured_kwargs.update(kwargs)
            yield later_bot_msg
            yield user_msg
            yield earlier_user_msg

        channel = MagicMock()
        channel.id = 1003
        channel.history = fake_history

        with patch("core.chat_pipeline.get_effective_reset_timestamp", return_value=None):
            turns = await build_chat_history_contents(bot, settings, channel, user_msg, is_japanese_dual_mode=False)

        self.assertEqual(captured_kwargs["before"].id, 102)
        self.assertEqual(len(turns), 2)
        self.assertEqual([t.role for t in turns], ["user", "user"])
        self.assertIn("第二句", turns[-1].parts[0].text)

    async def test_build_chat_history_appends_current_message_when_missing_from_history(self):
        settings = _make_settings(enable_japanese_mode=False)
        bot = MagicMock()
        bot.user = SimpleNamespace(id=999, display_name="Bot")

        prev_user_msg = SimpleNamespace(
            id=99,
            author=SimpleNamespace(id=10, display_name="阿明"),
            content="早安",
            clean_content="早安",
            embeds=[],
            attachments=[],
            stickers=[],
        )
        prev_bot_msg = SimpleNamespace(
            id=100,
            author=bot.user,
            content="早安啊",
            clean_content="早安啊",
            embeds=[],
            attachments=[],
            stickers=[],
        )
        current_msg = SimpleNamespace(
            id=101,
            author=SimpleNamespace(id=11, display_name="妖貓的主人"),
            content="這句還沒寫進 history",
            clean_content="這句還沒寫進 history",
            embeds=[],
            attachments=[],
            stickers=[],
        )

        async def fake_history(**kwargs):
            yield prev_bot_msg
            yield prev_user_msg

        channel = MagicMock()
        channel.id = 1003
        channel.history = fake_history

        with patch("core.chat_pipeline.get_effective_reset_timestamp", return_value=None):
            turns = await build_chat_history_contents(bot, settings, channel, current_msg, is_japanese_dual_mode=False)

        self.assertEqual([t.role for t in turns], ["user", "model", "user"])
        self.assertIn("這句還沒寫進 history", turns[-1].parts[0].text)

    async def test_build_chat_history_tts_mirror_anchor_and_failed_mirror_fallback(self):
        settings = _make_settings(enable_japanese_mode=True, target_channel_id=1001, tts_channel_id=1002)
        bot = MagicMock()
        bot.user = SimpleNamespace(id=999, display_name="Bot")

        tts_user_msg = SimpleNamespace(
            id=900,
            author=SimpleNamespace(id=22, display_name="小花"),
            content="おはようございます",
            clean_content="おはようございます",
            embeds=[],
            attachments=[],
            stickers=[],
        )
        mirror_embed = discord.Embed(description="おはようございます")
        mirror_embed.set_author(name="小花 (來自日文頻道)")
        mirror_bot_msg = SimpleNamespace(
            id=505,
            author=bot.user,
            content="",
            clean_content="",
            embeds=[mirror_embed],
            attachments=[],
            stickers=[],
        )
        later_bot_reply = SimpleNamespace(
            id=506,
            author=bot.user,
            content="插隊的模型回覆",
            clean_content="插隊的模型回覆",
            embeds=[],
            attachments=[],
            stickers=[],
        )
        prev_bot_reply = SimpleNamespace(
            id=504,
            author=bot.user,
            content="上一句機器人回覆",
            clean_content="上一句機器人回覆",
            embeds=[],
            attachments=[],
            stickers=[],
        )

        captured_kwargs = {}

        async def anchored_history(**kwargs):
            captured_kwargs.update(kwargs)
            yield later_bot_reply
            yield mirror_bot_msg
            yield prev_bot_reply

        main_channel = MagicMock()
        main_channel.id = 1001
        main_channel.history = anchored_history
        tts_channel = MagicMock()
        tts_channel.id = 1002
        bot.get_channel.side_effect = lambda cid: main_channel if cid == 1001 else tts_channel

        with patch("core.chat_pipeline.get_effective_reset_timestamp", return_value=None):
            turns = await build_chat_history_contents(
                bot,
                settings,
                tts_channel,
                tts_user_msg,
                is_japanese_dual_mode=True,
                anchor_message_id=505,
            )

        self.assertEqual(captured_kwargs["before"].id, 506)
        self.assertEqual([t.role for t in turns], ["model", "user"])
        self.assertIn("おはようございます", turns[-1].parts[0].text)

        async def failed_mirror_history(**kwargs):
            yield prev_bot_reply

        main_channel.history = failed_mirror_history
        with patch("core.chat_pipeline.get_effective_reset_timestamp", return_value=None):
            fallback_turns = await build_chat_history_contents(
                bot,
                settings,
                tts_channel,
                tts_user_msg,
                is_japanese_dual_mode=True,
                anchor_message_id=None,
            )

        self.assertEqual([t.role for t in fallback_turns], ["model", "user"])
        self.assertIn("おはようございます", fallback_turns[-1].parts[0].text)

    async def test_generate_with_fallback_sanitizes_trailing_model_turns(self):
        fake_resp = SimpleNamespace(text="收到！")
        mock_gen = AsyncMock(return_value=fake_resp)
        fake_client = SimpleNamespace(aio=SimpleNamespace(models=SimpleNamespace(generate_content=mock_gen)))

        original_contents = [
            types.Content(role="user", parts=[types.Part.from_text(text="你好")]),
            types.Content(role="model", parts=[types.Part.from_text(text="多餘的模型回合")]),
        ]
        config = types.GenerateContentConfig()

        resp, used_model = await generate_with_fallback(
            client=fake_client,
            models_to_try=["gemini-flash-lite-latest"],
            contents=original_contents,
            config=config,
            context_label="Test",
        )

        self.assertEqual(resp.text, "收到！")
        self.assertEqual(used_model, "gemini-flash-lite-latest")
        self.assertEqual(len(original_contents), 2)
        passed_contents = mock_gen.call_args.kwargs["contents"]
        self.assertEqual(len(passed_contents), 1)
        self.assertEqual(passed_contents[0].role, "user")

        mock_gen.reset_mock()
        with self.assertRaises(ValueError):
            await generate_with_fallback(
                client=fake_client,
                models_to_try=["gemini-flash-lite-latest"],
                contents=[types.Content(role="model", parts=[types.Part.from_text(text="只有模型回合")])],
                config=config,
                context_label="Test",
            )
        mock_gen.assert_not_called()

    async def test_wiki_search_and_browse_views_render_valid_embeds(self):
        from cogs.wiki_cog import WikiBrowseView, WikiOverviewView, WikiSearchView
        from engines import game_wiki_engine

        game_wiki_engine.load_wiki_entries(force_reload=True)
        res = game_wiki_engine.search_wiki_interactive("椅子")
        self.assertEqual(res["mode"], "results")

        entries = (res["results"] + res["related_entries"])[:25]
        view = WikiSearchView(
            query="椅子",
            entries=entries,
            focused_snippets=res["focused_snippets"],
            dynamic_notes=res["dynamic_notes"],
            related_ids={r["id"] for r in res["related_entries"]},
        )
        embed = view.build_embed()
        self.assertLessEqual(len(embed.description or ""), 4096)
        self.assertIn("체어맨", embed.description)
        for fld in embed.fields:
            self.assertLessEqual(len(fld.value), 1024)
        for child in view.children:
            if isinstance(child, discord.ui.Select):
                self.assertGreaterEqual(len(child.options), 1)
                self.assertLessEqual(len(child.options), 25)
                for opt in child.options:
                    self.assertLessEqual(len(opt.label), 100)
                    if opt.description:
                        self.assertLessEqual(len(opt.description), 100)

        # Toggle expanded full view on focused mega-glossary and test full-text subpage
        fake_interaction = MagicMock()
        fake_interaction.response = MagicMock()
        fake_interaction.response.edit_message = AsyncMock()
        await view._on_toggle_full(fake_interaction)
        self.assertTrue(view.expanded_full)
        expanded_embed = view.build_embed()
        self.assertLessEqual(len(expanded_embed.description or ""), 4096)
        self.assertIn("全文第 1/", expanded_embed.description)

        # Test WikiBrowseView and WikiOverviewView
        browse_view = WikiBrowseView(category_key="character_dict", page=0)
        browse_embed = browse_view.build_embed()
        self.assertLessEqual(len(browse_embed.description or ""), 4096)
        self.assertIn("第 1 /", browse_embed.footer.text)
        await browse_view._on_next(fake_interaction)
        self.assertEqual(browse_view.page, 1)

        overview_view = WikiOverviewView()
        self.assertTrue(any(isinstance(c, discord.ui.Select) for c in overview_view.children))
        fake_msg = MagicMock()
        fake_msg.edit = AsyncMock()
        overview_view.message = fake_msg
        select_cat_interaction = MagicMock()
        select_cat_interaction.data = {"values": ["character_dict"]}
        select_cat_interaction.response = MagicMock()
        select_cat_interaction.response.edit_message = AsyncMock()
        await overview_view._on_select_category(select_cat_interaction)
        self.assertTrue(overview_view.is_finished())
        self.assertIsNone(overview_view.message)
        await overview_view.on_timeout()
        fake_msg.edit.assert_not_awaited()

        browse_view.message = fake_msg
        open_entry_interaction = MagicMock()
        open_entry_interaction.data = {"values": ["0"]}
        open_entry_interaction.response = MagicMock()
        open_entry_interaction.response.edit_message = AsyncMock()
        await browse_view._on_open_entry(open_entry_interaction)
        self.assertTrue(browse_view.is_finished())
        self.assertIsNone(browse_view.message)
        await browse_view.on_timeout()
        fake_msg.edit.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
