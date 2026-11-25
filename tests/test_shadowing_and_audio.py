"""Unit & async tests for native audio attachments, shadowing engine, FlashcardStudyView/ShadowingResultView, and FlashcardCog."""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import discord
from discord.ext import commands
from google.genai import types

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from cogs.flashcard_cog import FlashcardCog
from cogs.flashcard_views import FlashcardStudyView, ShadowingResultView
from cogs.tts_cog import TtsCog
from core.config import (
    MAX_AUDIO_ATTACHMENT_BYTES,
    MAX_AUDIO_DURATION_SECONDS,
    BotSettings,
)
from core.media import (
    build_current_message_media_parts,
    get_audio_mime_type,
    is_audio_attachment,
    load_bounded_audio_attachment,
)
from engines import assistant_db, flashcard_engine, rpg_engine, shadowing_engine, tts_engine


def _make_settings(**overrides) -> BotSettings:
    defaults = dict(
        env_file=".env",
        bot_instance_name="ShadowTestBot",
        discord_bot_token="tok",
        gemini_api_key="key",
        target_channel_id=1001,
        tts_channel_id=1002,
        general_channel_id=1003,
        enable_japanese_mode=True,
        enable_flashcard_mode=True,
        enable_voice_tts=True,
        enable_safety_guardrail=True,
        disable_safety_settings=True,
    )
    defaults.update(overrides)
    return BotSettings(**defaults)


def _assert_discord_view_limits(testcase: unittest.TestCase, view: discord.ui.View) -> None:
    """Validate Discord UI limits: <=25 items, rows 0..4, and <=5 width units per row."""
    testcase.assertLessEqual(len(view.children), 25)
    row_units: dict[int, int] = {}
    for item in view.children:
        row = item.row if item.row is not None else 0
        testcase.assertGreaterEqual(row, 0)
        testcase.assertLessEqual(row, 4)
        width = 5 if isinstance(item, discord.ui.Select) else 1
        row_units[row] = row_units.get(row, 0) + width
    for row, units in row_units.items():
        testcase.assertLessEqual(units, 5, msg=f"Row {row} exceeded Discord width limit ({units} > 5)")


class _AsyncTypingCtx:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class TestAudioMediaHelpers(unittest.IsolatedAsyncioTestCase):
    def test_is_audio_attachment_and_mime_resolution(self):
        self.assertTrue(is_audio_attachment(SimpleNamespace(filename="voice-message.ogg", content_type="audio/ogg; codecs=opus")))
        self.assertTrue(is_audio_attachment(SimpleNamespace(filename="clip.m4a", content_type="")))
        self.assertTrue(is_audio_attachment(SimpleNamespace(filename="clip.mp3", content_type="application/octet-stream")))
        self.assertTrue(is_audio_attachment(SimpleNamespace(filename="clip.opus", content_type=None)))
        self.assertTrue(is_audio_attachment(SimpleNamespace(filename="clip.wav", content_type=None)))
        self.assertTrue(is_audio_attachment(SimpleNamespace(filename="voice.webm", content_type="audio/webm")))

        self.assertFalse(is_audio_attachment(SimpleNamespace(filename="video.webm", content_type="video/webm")))
        self.assertFalse(is_audio_attachment(SimpleNamespace(filename="movie.mp4", content_type="video/mp4")))
        self.assertFalse(is_audio_attachment(SimpleNamespace(filename="photo.png", content_type="image/png")))
        self.assertFalse(is_audio_attachment(SimpleNamespace(filename="notes.txt", content_type="text/plain")))
        self.assertFalse(is_audio_attachment(None))

        self.assertEqual(
            get_audio_mime_type(SimpleNamespace(filename="voice-message.ogg", content_type="audio/ogg; codecs=opus")),
            "audio/ogg",
        )
        self.assertEqual(
            get_audio_mime_type(SimpleNamespace(filename="voice.m4a", content_type="")),
            "audio/mp4",
        )
        self.assertEqual(
            get_audio_mime_type(SimpleNamespace(filename="voice.mp3", content_type="application/octet-stream")),
            "audio/mpeg",
        )
        self.assertEqual(
            get_audio_mime_type(SimpleNamespace(filename="unknown.bin", content_type="")),
            "audio/ogg",
        )

    async def test_load_bounded_audio_attachment_limits(self):
        long_att = SimpleNamespace(
            filename="voice-message.ogg",
            content_type="audio/ogg; codecs=opus",
            duration=MAX_AUDIO_DURATION_SECONDS + 5.2,
            size=1024,
            read=AsyncMock(return_value=b"OggS"),
        )
        data, mime, err = await load_bounded_audio_attachment(long_att)
        self.assertIsNone(data)
        self.assertIsNone(mime)
        self.assertIn("超過單次上限", err)
        long_att.read.assert_not_awaited()

        big_att = SimpleNamespace(
            filename="big.mp3",
            content_type="audio/mpeg",
            duration=10.0,
            size=MAX_AUDIO_ATTACHMENT_BYTES + 1024,
            read=AsyncMock(return_value=b"MP3"),
        )
        data2, mime2, err2 = await load_bounded_audio_attachment(big_att)
        self.assertIsNone(data2)
        self.assertIsNone(mime2)
        self.assertIn("MB 上限", err2)
        big_att.read.assert_not_awaited()

        empty_att = SimpleNamespace(
            filename="empty.wav",
            content_type="audio/wav",
            duration=2.0,
            size=0,
            read=AsyncMock(return_value=b""),
        )
        data3, _, err3 = await load_bounded_audio_attachment(empty_att)
        self.assertIsNone(data3)
        self.assertIn("為空", err3)

        valid_att = SimpleNamespace(
            filename="voice-message.ogg",
            content_type="audio/ogg; codecs=opus",
            duration=8.5,
            size=512,
            read=AsyncMock(return_value=b"OggS_VALID_AUDIO"),
        )
        data4, mime4, err4 = await load_bounded_audio_attachment(valid_att)
        self.assertEqual(data4, b"OggS_VALID_AUDIO")
        self.assertEqual(mime4, "audio/ogg")
        self.assertIsNone(err4)
        valid_att.read.assert_awaited_once()

    async def test_build_current_message_media_parts_audio_flag_and_video_webm(self):
        audio_att1 = SimpleNamespace(
            filename="voice-message.ogg",
            content_type="audio/ogg; codecs=opus",
            duration=5.0,
            size=200,
            read=AsyncMock(return_value=b"OGG1"),
        )
        audio_att2 = SimpleNamespace(
            filename="second.mp3",
            content_type="audio/mpeg",
            duration=5.0,
            size=200,
            read=AsyncMock(return_value=b"MP3_2"),
        )
        video_webm_att = SimpleNamespace(
            filename="clip.webm",
            content_type="video/webm",
            size=1024,
            read=AsyncMock(return_value=b"WEBM_VID"),
        )
        msg = SimpleNamespace(
            content="",
            clean_content="",
            attachments=[audio_att1, audio_att2, video_webm_att],
            embeds=[],
            stickers=[],
        )

        # Disabled -> no audio read at all, video/webm still loaded as video
        parts_disabled = await build_current_message_media_parts(msg, context_label="General", enable_audio=False)
        audio_att1.read.assert_not_awaited()
        audio_att2.read.assert_not_awaited()
        self.assertTrue(
            any(getattr(p, "inline_data", None) and p.inline_data.mime_type == "video/webm" for p in parts_disabled)
        )
        self.assertFalse(
            any(getattr(p, "inline_data", None) and p.inline_data.mime_type == "audio/ogg" for p in parts_disabled)
        )

        # Enabled -> loads at most 1 audio attachment AND preserves video/webm as video
        video_webm_att.read.reset_mock()
        parts_enabled = await build_current_message_media_parts(msg, context_label="JP", enable_audio=True)
        audio_att1.read.assert_awaited_once()
        audio_att2.read.assert_not_awaited()
        audio_parts = [
            p for p in parts_enabled
            if getattr(p, "inline_data", None) and str(p.inline_data.mime_type).startswith("audio/")
        ]
        video_parts = [
            p for p in parts_enabled
            if getattr(p, "inline_data", None) and p.inline_data.mime_type == "video/webm"
        ]
        self.assertEqual(len(audio_parts), 1)
        self.assertEqual(audio_parts[0].inline_data.data, b"OGG1")
        self.assertEqual(audio_parts[0].inline_data.mime_type, "audio/ogg")
        self.assertEqual(len(video_parts), 1)
        combined_txt = "\n".join(p.text for p in parts_enabled if getattr(p, "text", None))
        self.assertIn("口說回饋", combined_txt)

        # Oversized/long audio injects friendly reject text without reading bytes
        long_att = SimpleNamespace(
            filename="long.ogg",
            content_type="audio/ogg",
            duration=45.0,
            size=300,
            read=AsyncMock(return_value=b"LONG"),
        )
        msg_long = SimpleNamespace(
            content="",
            clean_content="",
            attachments=[long_att],
            embeds=[],
            stickers=[],
        )
        parts_long = await build_current_message_media_parts(msg_long, context_label="JP", enable_audio=True)
        long_att.read.assert_not_awaited()
        long_txt = "\n".join(p.text for p in parts_long if getattr(p, "text", None))
        self.assertIn("超過單次上限", long_txt)


class TestShadowingEngine(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        with shadowing_engine._shadowing_lock:
            shadowing_engine._shadowing_sessions.clear()

    def tearDown(self):
        with shadowing_engine._shadowing_lock:
            shadowing_engine._shadowing_sessions.clear()

    def test_session_lifecycle_and_expiry(self):
        with patch("engines.shadowing_engine.time.monotonic", return_value=100.0):
            sess = shadowing_engine.start_shadowing_session(
                1001,
                42,
                target_jp="桜",
                reading="さくら",
                pitch="0",
                meaning="櫻花",
                card_id=7,
                ttl_seconds=90,
            )
            self.assertEqual(sess["target_jp"], "桜")
            self.assertEqual(sess["reading"], "さくら")
            self.assertEqual(sess["expires_at"], 190.0)

        with patch("engines.shadowing_engine.time.monotonic", return_value=150.0):
            active = shadowing_engine.get_active_shadowing_session(1001, 42)
            self.assertIsNotNone(active)
            self.assertEqual(active["card_id"], 7)

        # Still isolated by channel & user
        with patch("engines.shadowing_engine.time.monotonic", return_value=150.0):
            self.assertIsNone(shadowing_engine.get_active_shadowing_session(1001, 99))
            self.assertIsNone(shadowing_engine.get_active_shadowing_session(2002, 42))

        # Expired at >= 190.0
        with patch("engines.shadowing_engine.time.monotonic", return_value=191.0):
            self.assertIsNone(shadowing_engine.get_active_shadowing_session(1001, 42))

        # Clear active session
        with patch("engines.shadowing_engine.time.monotonic", return_value=200.0):
            shadowing_engine.start_shadowing_session(1001, 42, target_jp="猫", ttl_seconds=90)
            popped = shadowing_engine.clear_shadowing_session(1001, 42)
            self.assertIsNotNone(popped)
            self.assertEqual(popped["target_jp"], "猫")
            self.assertIsNone(shadowing_engine.get_active_shadowing_session(1001, 42))

    def test_parse_shadowing_eval_response_json_fences_clamp_and_prose_fallback(self):
        fenced = (
            "```json\n"
            '{"score": 145, "transcript": "さくら", "pitch_and_rhythm": "標準", '
            '"strengths": "清晰", "improvements": "很棒", "encouragement_jp": "すごい！"}\n'
            "```"
        )
        parsed = shadowing_engine.parse_shadowing_eval_response(fenced)
        self.assertEqual(parsed["score"], 100)
        self.assertEqual(parsed["transcript"], "さくら")
        self.assertEqual(parsed["encouragement_jp"], "すごい！")

        negative_score = '{"score": -12, "transcript": "ねこ"}'
        parsed_neg = shadowing_engine.parse_shadowing_eval_response(negative_score)
        self.assertEqual(parsed_neg["score"], 0)
        self.assertTrue(parsed_neg["strengths"])
        self.assertTrue(parsed_neg["improvements"])
        self.assertTrue(parsed_neg["pitch_and_rhythm"])
        self.assertTrue(parsed_neg["encouragement_jp"])

        prose = "整體唸得很不錯，さ的母音很飽滿，建議尾音再稍微降下來一點。"
        parsed_prose = shadowing_engine.parse_shadowing_eval_response(prose)
        self.assertEqual(parsed_prose["score"], 75)
        self.assertIn("さ的母音很飽滿", parsed_prose["pitch_and_rhythm"])
        self.assertEqual(
            set(parsed_prose.keys()),
            {"score", "transcript", "pitch_and_rhythm", "strengths", "improvements", "encouragement_jp"},
        )

    async def test_evaluate_shadowing_audio_mocked_gemini(self):
        settings = _make_settings()
        session = {
            "target_jp": "桜",
            "reading": "さくら",
            "pitch": "0",
            "meaning": "櫻花",
        }
        fake_resp = SimpleNamespace(
            text='{"score": 91, "transcript": "さくら", "pitch_and_rhythm": "平板型很準", "strengths": "自然清晰", "improvements": "無明顯問題", "encouragement_jp": "完璧ですね！"}'
        )
        mock_gen = AsyncMock(return_value=fake_resp)
        fake_client = SimpleNamespace(
            aio=SimpleNamespace(models=SimpleNamespace(generate_content=mock_gen))
        )

        res = await shadowing_engine.evaluate_shadowing_audio(
            fake_client,
            settings,
            session,
            b"FAKE_OGG_BYTES",
            "audio/ogg",
        )
        self.assertEqual(res["score"], 91)
        self.assertEqual(res["transcript"], "さくら")
        mock_gen.assert_awaited_once()
        passed_contents = mock_gen.call_args.kwargs["contents"]
        self.assertEqual(len(passed_contents), 2)
        self.assertIn("桜", passed_contents[0].text)
        self.assertEqual(passed_contents[1].inline_data.data, b"FAKE_OGG_BYTES")
        self.assertEqual(passed_contents[1].inline_data.mime_type, "audio/ogg")


class TestFlashcardViewsAndCogShadowing(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.orig_db_dir = assistant_db.DB_DIR
        self.orig_data_dir = assistant_db.DATA_DIR
        self.orig_imports_dir = assistant_db.IMPORTS_DIR
        self.orig_media_dir = assistant_db.MEDIA_DIR
        assistant_db.DB_DIR = Path(self.tmpdir.name)
        assistant_db.DATA_DIR = Path(self.tmpdir.name)
        assistant_db.IMPORTS_DIR = Path(self.tmpdir.name) / "imports"
        assistant_db.MEDIA_DIR = Path(self.tmpdir.name) / "media"
        flashcard_engine.IMPORTS_DIR = assistant_db.IMPORTS_DIR
        flashcard_engine.MEDIA_DIR = assistant_db.MEDIA_DIR
        assistant_db._initialized_dbs.clear()
        tts_engine._initialized_tts_dbs.clear()
        flashcard_engine._initialized_dbs.clear()
        rpg_engine._initialized_rpg_dbs.clear()
        with shadowing_engine._shadowing_lock:
            shadowing_engine._shadowing_sessions.clear()
        self.env_patch = patch.dict(os.environ, {"DB_NAME": "test_shadow.db"})
        self.env_patch.start()
        assistant_db.init_db()
        flashcard_engine.init_flashcard_tables()

    def tearDown(self):
        self.env_patch.stop()
        with shadowing_engine._shadowing_lock:
            shadowing_engine._shadowing_sessions.clear()
        assistant_db.DB_DIR = self.orig_db_dir
        assistant_db.DATA_DIR = self.orig_data_dir
        assistant_db.IMPORTS_DIR = self.orig_imports_dir
        assistant_db.MEDIA_DIR = self.orig_media_dir
        flashcard_engine.IMPORTS_DIR = self.orig_imports_dir
        flashcard_engine.MEDIA_DIR = self.orig_media_dir
        assistant_db._initialized_dbs.clear()
        tts_engine._initialized_tts_dbs.clear()
        flashcard_engine._initialized_dbs.clear()
        rpg_engine._initialized_rpg_dbs.clear()
        self.tmpdir.cleanup()

    async def test_flashcard_study_view_layout_and_on_shadow(self):
        deck = flashcard_engine.get_or_create_deck("跟讀測試牌組")
        card = {
            "id": 10,
            "front": "桜",
            "reading": "さくら",
            "pitch": "0",
            "back": "櫻花",
            "example_jp": "桜が満開です。",
            "example_zh": "櫻花盛開了。",
            "audio_filename": "",
        }
        bot = MagicMock()

        for jp_mode in (True, False):
            settings = _make_settings(enable_japanese_mode=jp_mode)
            for flipped in (False, True):
                view = FlashcardStudyView(
                    bot,
                    MagicMock(),
                    settings,
                    MagicMock(),
                    user_id=123,
                    deck_id=deck["id"],
                    card=card,
                    is_flipped=flipped,
                )
                _assert_discord_view_limits(self, view)
                shadow_btns = [c for c in view.children if getattr(c, "custom_id", None) == "fc_shadow"]
                if jp_mode:
                    self.assertEqual(len(shadow_btns), 1)
                    self.assertEqual(shadow_btns[0].row, 2)
                else:
                    self.assertEqual(len(shadow_btns), 0)

        # Clicking on_shadow unflipped uses front + reveals reading gently in session/embed
        settings_jp = _make_settings(enable_japanese_mode=True)
        view_unflipped = FlashcardStudyView(
            bot, MagicMock(), settings_jp, MagicMock(), user_id=123, deck_id=deck["id"], card=card, is_flipped=False
        )
        inter = MagicMock()
        inter.channel = SimpleNamespace(id=1001)
        inter.user = SimpleNamespace(id=123)
        inter.response = AsyncMock()
        await view_unflipped.on_shadow(inter)
        sess_unflipped = shadowing_engine.get_active_shadowing_session(1001, 123)
        self.assertEqual(sess_unflipped["target_jp"], "桜")
        self.assertEqual(sess_unflipped["reading"], "さくら")
        sent_embed = inter.response.send_message.call_args.kwargs["embed"]
        self.assertIn("さくら", sent_embed.description)

        # Clicking on_shadow flipped with example_jp practices example_jp
        view_flipped = FlashcardStudyView(
            bot, MagicMock(), settings_jp, MagicMock(), user_id=123, deck_id=deck["id"], card=card, is_flipped=True
        )
        inter.response.reset_mock()
        await view_flipped.on_shadow(inter)
        sess_flipped = shadowing_engine.get_active_shadowing_session(1001, 123)
        self.assertEqual(sess_flipped["target_jp"], "桜が満開です。")

    async def test_shadowing_result_view_tts_and_retry(self):
        settings = _make_settings(enable_voice_tts=True)
        tts_cog = MagicMock(spec=TtsCog)
        tts_cog.synthesize_ephemeral_jp_audio = AsyncMock(return_value=(b"EDGE_MP3_BYTES", "mp3"))
        bot = MagicMock()
        bot.get_cog.return_value = tts_cog

        session_data = {
            "target_jp": "桜が満開です。",
            "reading": "さくら",
            "pitch": "0",
            "meaning": "櫻花盛開了。",
            "example_jp": "桜が満開です。",
            "card_id": 10,
        }
        view = ShadowingResultView(settings=settings, owner_id=42, session_data=session_data, bot=bot)
        _assert_discord_view_limits(self, view)

        # Non-owner blocked
        other_inter = MagicMock()
        other_inter.user = SimpleNamespace(id=999)
        other_inter.response = AsyncMock()
        self.assertFalse(await view.interaction_check(other_inter))
        other_inter.response.send_message.assert_awaited_once()

        # Owner clicks listen TTS -> attaches tts_preview.mp3 ephemerally
        owner_inter = MagicMock()
        owner_inter.user = SimpleNamespace(id=42)
        owner_inter.guild = SimpleNamespace(id=500)
        owner_inter.channel = SimpleNamespace(id=1001)
        owner_inter.response = AsyncMock()
        owner_inter.followup = AsyncMock()
        await view.on_listen_tts.callback(owner_inter)
        owner_inter.response.defer.assert_awaited_once_with(ephemeral=True)
        tts_cog.synthesize_ephemeral_jp_audio.assert_awaited_once_with("桜が満開です。", guild_id=500)
        sent_file = owner_inter.followup.send.call_args.kwargs["file"]
        self.assertEqual(sent_file.filename, "tts_preview.mp3")

        # Fallback TTS preview when enable_voice_tts=False
        settings_no_vc = _make_settings(enable_voice_tts=False)
        view_fallback = ShadowingResultView(settings=settings_no_vc, owner_id=42, session_data={"target_jp": "桜", "reading": "さくら"})
        owner_inter2 = MagicMock()
        owner_inter2.user = SimpleNamespace(id=42)
        owner_inter2.guild = None
        owner_inter2.channel = SimpleNamespace(id=1001)
        owner_inter2.response = AsyncMock()
        owner_inter2.followup = AsyncMock()
        with patch.object(tts_engine.HybridTtsSynthesizer, "synthesize_edge", new=AsyncMock(return_value=(b"MP3", "mp3"))):
            await view_fallback.on_listen_tts.callback(owner_inter2)
        self.assertEqual(owner_inter2.followup.send.call_args.kwargs["file"].filename, "tts_preview.mp3")

        # Owner clicks retry -> re-arms 90s session
        owner_inter.response.reset_mock()
        await view.on_retry.callback(owner_inter)
        rearmed = shadowing_engine.get_active_shadowing_session(1001, 42)
        self.assertIsNotNone(rearmed)
        self.assertEqual(rearmed["target_jp"], "桜が満開です。")

    async def test_flashcard_cog_shadow_command_and_audio_handler(self):
        settings = _make_settings(enable_japanese_mode=True, enable_flashcard_mode=True)
        fake_resp = SimpleNamespace(
            text=(
                '{"score": 88, "transcript": "おつかれさまです", "pitch_and_rhythm": "節奏自然流暢", '
                '"strengths": "發音清楚", "improvements": "長音可再穩定一點", "encouragement_jp": "よくできました！"}'
            )
        )
        mock_gen = AsyncMock(return_value=fake_resp)
        fake_client = SimpleNamespace(aio=SimpleNamespace(models=SimpleNamespace(generate_content=mock_gen)))
        bot = MagicMock()
        bot.get_cog.return_value = None
        cog = FlashcardCog(bot, fake_client, settings, MagicMock())

        ctx = MagicMock()
        ctx.channel = SimpleNamespace(id=1001)
        ctx.author = SimpleNamespace(id=77, display_name="小花")
        ctx.send = AsyncMock()

        # 1. !shadow with explicit Japanese text
        await cog.shadow_command.callback(cog, ctx, target_text="おやすみなさい")
        ctx.send.assert_awaited_once()
        sess = shadowing_engine.get_active_shadowing_session(1001, 77)
        self.assertEqual(sess["target_jp"], "おやすみなさい")

        # 2. Oversized duration audio keeps session alive and returns True
        too_long_att = SimpleNamespace(
            filename="voice-message.ogg",
            content_type="audio/ogg; codecs=opus",
            duration=45.0,
            size=500,
            read=AsyncMock(return_value=b"LONG"),
        )
        msg_long = MagicMock()
        msg_long.author = SimpleNamespace(id=77, display_name="小花", bot=False)
        msg_long.channel = MagicMock()
        msg_long.channel.id = 1001
        msg_long.channel.typing.return_value = _AsyncTypingCtx()
        msg_long.channel.send = AsyncMock()
        msg_long.attachments = [too_long_att]

        handled_long = await cog.handle_shadowing_audio_message(msg_long)
        self.assertTrue(handled_long)
        too_long_att.read.assert_not_awaited()
        self.assertIsNotNone(shadowing_engine.get_active_shadowing_session(1001, 77))
        self.assertIn("跟讀練習仍在等待中", msg_long.channel.send.call_args.args[0])

        # 3. Valid audio consumes session, calls Gemini, sends Feedback Embed + ShadowingResultView
        valid_att = SimpleNamespace(
            filename="voice-message.ogg",
            content_type="audio/ogg; codecs=opus",
            duration=4.2,
            size=400,
            read=AsyncMock(return_value=b"VALID_OGG"),
        )
        sent_feedback_msg = SimpleNamespace(id=9999)
        msg_ok = MagicMock()
        msg_ok.author = SimpleNamespace(id=77, display_name="小花", bot=False)
        msg_ok.channel = MagicMock()
        msg_ok.channel.id = 1001
        msg_ok.channel.typing.return_value = _AsyncTypingCtx()
        msg_ok.channel.send = AsyncMock(return_value=sent_feedback_msg)
        msg_ok.attachments = [valid_att]

        handled_ok = await cog.handle_shadowing_audio_message(msg_ok)
        self.assertTrue(handled_ok)
        self.assertIsNone(shadowing_engine.get_active_shadowing_session(1001, 77))
        mock_gen.assert_awaited_once()
        send_kwargs = msg_ok.channel.send.call_args.kwargs
        fb_embed = send_kwargs["embed"]
        fb_view = send_kwargs["view"]
        self.assertIn("88/100", fb_embed.description)
        self.assertEqual(len(fb_embed.fields), 5)
        self.assertIsInstance(fb_view, ShadowingResultView)
        self.assertEqual(fb_view.message, sent_feedback_msg)

        # 4. Subsequent audio message after session consumed returns False (falls back to normal chat)
        handled_after = await cog.handle_shadowing_audio_message(msg_ok)
        self.assertFalse(handled_after)

        # 5. !shadow without args pulls from user's active deck; empty deck falls back to everyday phrase
        deck = flashcard_engine.get_or_create_deck("個人自訂練習")
        flashcard_engine.set_user_active_deck(77, deck["id"])
        ctx.send.reset_mock()
        await cog.shadow_command.callback(cog, ctx, target_text=None)
        fallback_sess = shadowing_engine.get_active_shadowing_session(1001, 77)
        self.assertEqual(fallback_sess["target_jp"], "お疲れ様です！今日もいい天気ですね。")

        flashcard_engine.add_card(deck["id"], front="乾杯", reading="かんぱい", back="乾杯", pitch="0")
        ctx.send.reset_mock()
        await cog.shadow_command.callback(cog, ctx, target_text=None)
        card_sess = shadowing_engine.get_active_shadowing_session(1001, 77)
        self.assertEqual(card_sess["target_jp"], "乾杯")
        self.assertEqual(card_sess["reading"], "かんぱい")

        # 6. Community mode (enable_japanese_mode=False) skips !shadow and audio interception
        cog_comm = FlashcardCog(bot, fake_client, _make_settings(enable_japanese_mode=False), MagicMock())
        ctx.send.reset_mock()
        await cog_comm.shadow_command.callback(cog_comm, ctx, target_text="ありがとう")
        ctx.send.assert_not_awaited()
        self.assertFalse(await cog_comm.handle_shadowing_audio_message(msg_ok))


if __name__ == "__main__":
    unittest.main()
