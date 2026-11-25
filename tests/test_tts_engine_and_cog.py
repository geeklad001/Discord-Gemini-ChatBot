"""Tests for engines.tts_engine, cogs.tts_cog (3-tier permissions & routing), flashcards TTS, and F5 catalog privacy."""

import ast
import asyncio
import io
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
import wave
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import discord
from discord.ext import commands

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from cogs.assistant_cog import AssistantCog
from cogs.flashcard_cog import FlashcardStudyView
from cogs.rpg_cog import RpgCog
from cogs.tts_cog import CloneAuditView, CloneReviewView, GuildVoicePipeline, TtsCog, TtsJob, VoiceSelectView


def _make_wav_bytes(duration_sec: float, samplerate: int = 24000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(samplerate)
        nframes = max(1, int(samplerate * duration_sec))
        wf.writeframes(b"\x00\x00" * nframes)
    return buf.getvalue()
from core.chat_pipeline import is_target_channel
from core.config import BotSettings
from engines import assistant_db, flashcard_engine, rpg_engine, tts_engine


def _make_settings(**overrides) -> BotSettings:
    defaults = dict(
        env_file=".env",
        bot_instance_name="TtsTestBot",
        discord_bot_token="tok",
        gemini_api_key="key",
        target_channel_id=1001,
        tts_channel_id=1002,
        general_channel_id=1003,
        enable_voice_tts=True,
        tts_api_url="http://127.0.0.1:9880",
        bot_owner_ids=(9000,),
    )
    defaults.update(overrides)
    return BotSettings(**defaults)


def _make_ctx(
    user_id: int,
    guild_id: int = 500,
    channel_id: int = 1001,
    *,
    administrator: bool = False,
    manage_guild: bool = False,
    manage_channels: bool = False,
    manage_messages: bool = False,
    channels: list | None = None,
):
    perms = SimpleNamespace(
        administrator=administrator,
        manage_guild=manage_guild,
        manage_channels=manage_channels,
        manage_messages=manage_messages,
    )
    author = SimpleNamespace(
        id=user_id,
        display_name=f"User{user_id}",
        guild_permissions=perms,
        voice=None,
    )
    ch_map = {c.id: c for c in (channels or [])}
    guild = MagicMock()
    guild.id = guild_id
    guild.channels = list(channels or [])
    guild.get_channel.side_effect = lambda cid: ch_map.get(cid)
    guild.get_channel_or_thread.side_effect = lambda cid: ch_map.get(cid)

    channel = ch_map.get(channel_id) or SimpleNamespace(
        id=channel_id,
        name=f"ch-{channel_id}",
        mention=f"<#{channel_id}>",
        guild=guild,
    )
    ctx = MagicMock()
    ctx.author = author
    ctx.guild = guild
    ctx.channel = channel
    ctx.send = AsyncMock()
    return ctx


class TestTtsEngineAndCog(unittest.IsolatedAsyncioTestCase):
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
        self.env_patch = patch.dict(os.environ, {"DB_NAME": "test_tts.db"})
        self.env_patch.start()
        assistant_db.init_db()
        tts_engine.init_tts_tables()
        rpg_engine.init_rpg_tables()

    def tearDown(self):
        self.env_patch.stop()
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

    def test_sqlite_user_prefs_and_guild_settings(self):
        pref_default = tts_engine.get_user_voice_pref(42)
        self.assertEqual(pref_default["voice_id"], tts_engine.DEFAULT_EDGE_VOICE)
        self.assertEqual(pref_default["rate_pct"], 0)
        self.assertEqual(pref_default["pitch_hz"], 0)
        self.assertFalse(pref_default["is_custom"])

        saved = tts_engine.set_user_voice_pref(
            42,
            voice_id="edge:zh-TW-YunJheNeural",
            rate_pct=120,
            pitch_hz=-99,
        )
        self.assertEqual(saved["voice_id"], "edge:zh-TW-YunJheNeural")
        self.assertEqual(saved["rate_pct"], 50)
        self.assertEqual(saved["pitch_hz"], -30)
        self.assertTrue(saved["is_custom"])

        loaded = tts_engine.get_user_voice_pref(42)
        self.assertEqual(loaded, saved)

        tts_engine.reset_user_voice_pref(42)
        self.assertFalse(tts_engine.get_user_voice_pref(42)["is_custom"])

        g_default = tts_engine.get_guild_tts_settings(999, default_ai_jp=True, default_ai_general=False)
        self.assertTrue(g_default["proxy_enabled"])
        self.assertTrue(g_default["ai_read_jp"])
        self.assertFalse(g_default["ai_read_general"])
        self.assertEqual(g_default["allowed_channels"], [])

        g_updated = tts_engine.update_guild_tts_settings(
            999,
            proxy_enabled=False,
            ai_read_general=True,
            allowed_channels=[10, 20, 10],
            ignored_channels=[30],
        )
        self.assertFalse(g_updated["proxy_enabled"])
        self.assertTrue(g_updated["ai_read_general"])
        self.assertEqual(g_updated["allowed_channels"], [10, 20])
        self.assertEqual(g_updated["ignored_channels"], [30])
        self.assertFalse(g_updated["ai_voice_is_custom"])
        self.assertFalse(g_updated["ai_jp_voice_is_custom"])

        # Updating proxy_enabled must not hardcode default voice into DB
        g_rechecked = tts_engine.get_guild_tts_settings(
            999,
            default_ai_voice="edge:zh-TW-YunJheNeural",
            default_ai_jp_voice="edge:ja-JP-KeitaNeural",
        )
        self.assertEqual(g_rechecked["ai_voice_id"], "edge:zh-TW-YunJheNeural")
        self.assertEqual(g_rechecked["ai_jp_voice_id"], "edge:ja-JP-KeitaNeural")

        g_ai = tts_engine.update_guild_tts_settings(
            999,
            ai_voice_id="edge:zh-CN-YunxiNeural",
            ai_rate_pct=15,
            ai_pitch_hz=-5,
            ai_jp_voice_id="edge:ja-JP-KeitaNeural",
            ai_jp_rate_pct=-20,
            ai_jp_pitch_hz=4,
        )
        self.assertTrue(g_ai["ai_voice_is_custom"])
        self.assertTrue(g_ai["ai_jp_voice_is_custom"])
        self.assertEqual(g_ai["ai_voice_id"], "edge:zh-CN-YunxiNeural")
        self.assertEqual(g_ai["ai_rate_pct"], 15)
        self.assertEqual(g_ai["ai_pitch_hz"], -5)
        self.assertEqual(g_ai["ai_jp_voice_id"], "edge:ja-JP-KeitaNeural")
        self.assertEqual(g_ai["ai_jp_rate_pct"], -20)
        self.assertEqual(g_ai["ai_jp_pitch_hz"], 4)

        g_reset_gen = tts_engine.reset_guild_ai_voice(999, target="general")
        self.assertFalse(g_reset_gen["ai_voice_is_custom"])
        self.assertTrue(g_reset_gen["ai_jp_voice_is_custom"])
        g_reset_jp = tts_engine.reset_guild_ai_voice(999, target="jp")
        self.assertFalse(g_reset_jp["ai_jp_voice_is_custom"])

    def test_legacy_guild_table_migration(self):
        with patch.dict(os.environ, {"DB_NAME": "legacy_tts.db"}):
            tts_engine._initialized_tts_dbs.clear()
            db_path = assistant_db.get_db_path()
            conn = sqlite3.connect(db_path)
            conn.execute(
                """
                CREATE TABLE tts_guild_settings (
                    guild_id INTEGER PRIMARY KEY,
                    proxy_enabled INTEGER NOT NULL DEFAULT 1,
                    ai_read_jp INTEGER NOT NULL DEFAULT 1,
                    ai_read_general INTEGER NOT NULL DEFAULT 0,
                    allowed_channels TEXT NOT NULL DEFAULT '[]',
                    ignored_channels TEXT NOT NULL DEFAULT '[]',
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                "INSERT INTO tts_guild_settings VALUES (77, 1, 1, 1, '[123]', '[]', '2026-01-01 00:00:00')"
            )
            conn.commit()
            conn.close()

            tts_engine.init_tts_tables()
            cfg = tts_engine.get_guild_tts_settings(77)
            self.assertEqual(cfg["allowed_channels"], [123])
            self.assertEqual(cfg["ai_voice_id"], tts_engine.DEFAULT_EDGE_VOICE)
            self.assertEqual(cfg["ai_jp_voice_id"], tts_engine.DEFAULT_JP_EDGE_VOICE)
            self.assertFalse(cfg["ai_voice_is_custom"])
            self.assertFalse(cfg["require_in_vc"])

    def test_clean_text_and_chunking(self):
        raw = (
            "Hello <@12345> <#999> <:wave:111> 😀🌸！\n"
            "```python\nprint('secret code')\n```\n"
            "請看這個網址 https://example.com/a/b ，「図書館」へ行きます。。。"
        )
        cleaned = tts_engine.clean_text_for_tts(raw, max_chars=200)
        self.assertNotIn("http", cleaned)
        self.assertNotIn("secret code", cleaned)
        self.assertNotIn("12345", cleaned)
        self.assertNotIn("😀", cleaned)
        self.assertIn("図書館", cleaned)
        self.assertIn("へ行きます。", cleaned)

        long_text = "第一句話測試一下長度。第二句話繼續補充更多細節內容！第三句話也超過二十字看看會不會切開呢？"
        chunks = tts_engine.split_into_speech_chunks(long_text, max_chunk_chars=25)
        self.assertGreaterEqual(len(chunks), 3)
        self.assertTrue(all(len(c) <= 25 for c in chunks))

        truncated = tts_engine.clean_text_for_tts("字" * 300, max_chars=50)
        self.assertEqual(len(truncated), 50)
        self.assertEqual(tts_engine.clean_text_for_tts("ㄏㄏ"), "ㄏㄏ")
        self.assertEqual(tts_engine.clean_text_for_tts("ㄅㄆㄇ！"), "ㄅㄆㄇ！")

    def test_audio_lru_cache_hit_and_eviction(self):
        cache = tts_engine.AudioLRUCache(max_entries=2, max_total_bytes=1024)
        cache.put("k1", b"a" * 10, "mp3")
        cache.put("k2", b"b" * 10, "mp3")
        self.assertIsNotNone(cache.get("k1"))  # moves k1 to MRU; k2 becomes LRU
        cache.put("k3", b"c" * 10, "mp3")
        self.assertIsNone(cache.get("k2"))
        self.assertIsNotNone(cache.get("k1"))
        self.assertIsNotNone(cache.get("k3"))

    async def test_hybrid_synthesizer_edge_cache_and_f5_fallback(self):
        synth = tts_engine.HybridTtsSynthesizer(
            tts_api_url="http://127.0.0.1:9880",
            offline_cooldown_seconds=30.0,
        )
        with patch.object(synth, "synthesize_edge", new=AsyncMock(return_value=(b"EDGE_MP3", "mp3"))) as mock_edge:
            b1, ext1, fb1 = await synth.synthesize("測試語音", "edge:zh-TW-HsiaoChenNeural")
            b2, ext2, fb2 = await synth.synthesize("測試語音", "edge:zh-TW-HsiaoChenNeural")
            self.assertEqual((b1, ext1, fb1), (b"EDGE_MP3", "mp3", False))
            self.assertEqual((b2, ext2, fb2), (b"EDGE_MP3", "mp3", False))
            mock_edge.assert_awaited_once()  # second call served from LRU cache

        with (
            patch.object(synth, "synthesize_f5", new=AsyncMock(return_value=(b"F5_WAV", "wav"))) as mock_f5,
            patch.object(synth, "synthesize_edge", new=AsyncMock(return_value=(b"FB_MP3", "mp3"))) as mock_edge,
        ):
            bf, extf, fbf = await synth.synthesize("本地聲音", "f5:preset_warm", user_id=777, rate_pct=10, pitch_hz=-12)
            self.assertEqual((bf, extf, fbf), (b"F5_WAV", "wav", False))
            mock_f5.assert_awaited_once_with("本地聲音", "preset_warm", user_id=777, rate_pct=10, pitch_hz=-12)
            mock_edge.assert_not_awaited()

        # Verify apply_wav_pitch_shift shifts pitch on real WAV bytes while preserving sample rate & duration
        raw_wav = _make_wav_bytes(2.0, samplerate=24000)
        self.assertEqual(await tts_engine.apply_wav_pitch_shift(raw_wav, 0), raw_wav)
        shifted_wav = await tts_engine.apply_wav_pitch_shift(raw_wav, -15)
        self.assertNotEqual(shifted_wav, raw_wav)
        with wave.open(io.BytesIO(shifted_wav), "rb") as wf_shifted:
            self.assertEqual(wf_shifted.getframerate(), 24000)
            self.assertEqual(wf_shifted.getnchannels(), 1)
            dur_shifted = wf_shifted.getnframes() / float(wf_shifted.getframerate())
            self.assertAlmostEqual(dur_shifted, 2.0, delta=0.15)

        # Simulate F5 failure -> graceful instant degradation to Edge-TTS + cooldown
        async def fail_f5(*args, **kwargs):
            synth.mark_f5_offline()
            raise ConnectionError("offline")

        with (
            patch.object(synth, "synthesize_f5", new=AsyncMock(side_effect=fail_f5)),
            patch.object(synth, "synthesize_edge", new=AsyncMock(return_value=(b"FB_MP3", "mp3"))) as mock_edge,
        ):
            b_fb, ext_fb, used_fb = await synth.synthesize("降級測試", "f5:preset_warm", user_id=777)
            self.assertEqual((b_fb, ext_fb, used_fb), (b"FB_MP3", "mp3", True))
            self.assertTrue(synth.is_f5_in_cooldown())
            mock_edge.assert_awaited_once()

    async def test_cog_registration_no_status_collision(self):
        intents = discord.Intents.none()
        bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)
        settings = _make_settings()
        logger = MagicMock()
        await bot.add_cog(AssistantCog(bot, settings, logger))
        await bot.add_cog(RpgCog(bot, settings, logger))
        await bot.add_cog(TtsCog(bot, settings, logger))

        self.assertIsNotNone(bot.get_command("join"))
        self.assertIsNotNone(bot.get_command("leave"))
        self.assertIsNotNone(bot.get_command("say"))
        self.assertIsNotNone(bot.get_command("skip"))
        self.assertIsNotNone(bot.get_command("voice"))
        self.assertIsNotNone(bot.get_command("botvoice"))
        self.assertIsNotNone(bot.get_command("voiceclone"))
        self.assertEqual(bot.get_command("clone").name, "voiceclone")
        self.assertIsNotNone(bot.get_command("tts"))
        # !status remains RPG's alias with zero collision
        self.assertEqual(bot.get_command("status").name, "rpg")
        await bot.close()

    async def test_three_tier_permissions_and_no_self_clone(self):
        bot = MagicMock()
        bot.is_owner = AsyncMock(return_value=False)
        settings = _make_settings(bot_owner_ids=(9000,))
        cog = TtsCog(bot, settings, MagicMock())
        cog.synthesizer.fetch_f5_voices = AsyncMock(return_value=[])
        cog.synthesizer.unload_f5_model = AsyncMock(return_value={"was_loaded": True, "vram_allocated_gb": 0.0})
        cog.synthesizer.reload_f5_voices = AsyncMock(return_value={"public_voice_count": 1, "total_voice_count": 3})
        cog.synthesizer.check_f5_health = AsyncMock(return_value=None)

        ch_voice_proxy = SimpleNamespace(id=7777, name="voice-proxy", mention="<#7777>")
        regular_ctx = _make_ctx(user_id=111, channel_id=7777, channels=[ch_voice_proxy])
        admin_ctx = _make_ctx(user_id=222, channel_id=7777, manage_guild=True, channels=[ch_voice_proxy])
        owner_ctx = _make_ctx(user_id=9000, channel_id=7777, channels=[ch_voice_proxy])

        # 1. General user can customize own !voice, read !tts status/channels, CANNOT clone or change channels
        await cog.voice_command.callback(cog, regular_ctx, sub="pitch", arg="+12")
        await cog.voice_command.callback(cog, regular_ctx, sub="rate", arg="-10")
        pref = tts_engine.get_user_voice_pref(111)
        self.assertEqual(pref["pitch_hz"], 12)
        self.assertEqual(pref["rate_pct"], -10)

        await cog.voice_command.callback(cog, regular_ctx, sub="clone", arg="my_wav")
        self.assertIn("僅限機器人擁有者", regular_ctx.send.call_args.args[0])

        await cog.voice_command.callback(cog, regular_ctx, sub="reset")
        self.assertFalse(tts_engine.get_user_voice_pref(111)["is_custom"])

        regular_ctx.send.reset_mock()
        await cog.tts_command.callback(cog, regular_ctx, action="status")
        self.assertIn("embed", regular_ctx.send.call_args.kwargs)

        regular_ctx.send.reset_mock()
        await cog.tts_command.callback(cog, regular_ctx, action="channels")
        self.assertIn("對象頻道", regular_ctx.send.call_args.args[0])

        for admin_action, admin_arg in [
            ("channel", "add <#7777>"),
            ("bind", "<#7777>"),
            ("ai", "on"),
            ("proxy", "off"),
            ("vconly", "on"),
            ("unload", None),
            ("reloadvoices", None),
        ]:
            regular_ctx.send.reset_mock()
            await cog.tts_command.callback(cog, regular_ctx, action=admin_action, arg=admin_arg)
            self.assertIn("🔒", regular_ctx.send.call_args.args[0])

        # 2. Guild Admin CAN configure target channels and toggles, CANNOT run owner GPU commands
        await cog.tts_command.callback(cog, admin_ctx, action="channel", arg="add <#7777>")
        self.assertEqual(tts_engine.get_guild_tts_settings(500)["allowed_channels"], [7777])

        await cog.tts_command.callback(cog, admin_ctx, action="ai", arg="general on")
        self.assertTrue(tts_engine.get_guild_tts_settings(500)["ai_read_general"])

        await cog.tts_command.callback(cog, admin_ctx, action="vconly", arg="on")
        self.assertTrue(tts_engine.get_guild_tts_settings(500)["require_in_vc"])
        await cog.tts_command.callback(cog, admin_ctx, action="vconly", arg="off")
        self.assertFalse(tts_engine.get_guild_tts_settings(500)["require_in_vc"])

        await cog.tts_command.callback(cog, admin_ctx, action="channel", arg="clear")
        self.assertEqual(tts_engine.get_guild_tts_settings(500)["allowed_channels"], [])

        for owner_action in ("unload", "reloadvoices"):
            admin_ctx.send.reset_mock()
            await cog.tts_command.callback(cog, admin_ctx, action=owner_action)
            self.assertIn("機器人擁有者", admin_ctx.send.call_args.args[0])

        # 3. Bot Owner CAN execute unload & reloadvoices
        await cog.tts_command.callback(cog, owner_ctx, action="unload")
        cog.synthesizer.unload_f5_model.assert_awaited_once()
        await cog.tts_command.callback(cog, owner_ctx, action="reloadvoices")
        cog.synthesizer.reload_f5_voices.assert_awaited_once()

    async def test_proxy_routing_vc_chat_vs_bound_channel(self):
        vc_channel = SimpleNamespace(id=3001, name="語音房")
        ai_channel = SimpleNamespace(id=1001, name="日文共學")
        bound_channel = SimpleNamespace(id=4001, name="代說頻道")

        vc = MagicMock()
        vc.channel = vc_channel
        vc.is_connected.return_value = True

        guild = MagicMock()
        guild.id = 500
        guild.voice_client = vc

        bot = MagicMock()
        bot.get_guild.return_value = guild
        settings = _make_settings(target_channel_id=1001, enable_japanese_mode=False)
        cog = TtsCog(bot, settings, MagicMock())
        fake_pipeline = MagicMock()
        cog._get_or_create_pipeline = MagicMock(return_value=fake_pipeline)

        def make_msg(uid: int, ch, text: str, author_vc):
            return SimpleNamespace(
                author=SimpleNamespace(
                    id=uid,
                    bot=False,
                    display_name="Tester",
                    voice=SimpleNamespace(channel=author_vc) if author_vc else None,
                ),
                guild=guild,
                channel=ch,
                clean_content=text,
            )

        # By default (require_in_vc=False), user outside VC in VC chat or bound channel IS accepted
        self.assertTrue(await cog.handle_proxy_message(make_msg(11, vc_channel, "未進房在語音文字區發言", None)))
        self.assertEqual(fake_pipeline.enqueue.call_count, 1)
        # In Community Mode (enable_japanese_mode=False), AI channel when allowed_channels=[] -> ignored until bound
        self.assertFalse(await cog.handle_proxy_message(make_msg(11, ai_channel, "こんにちは", vc_channel)))
        # VC built-in text chat when allowed_channels=[] -> accepted & queued
        self.assertTrue(await cog.handle_proxy_message(make_msg(11, vc_channel, "嗨大家", vc_channel)))
        self.assertEqual(fake_pipeline.enqueue.call_count, 2)

        # Admin binds explicit channel -> VC chat no longer accepted unless bound; non-VC user in bound channel accepted
        tts_engine.update_guild_tts_settings(500, allowed_channels=[bound_channel.id])
        fake_pipeline.enqueue.reset_mock()
        self.assertFalse(await cog.handle_proxy_message(make_msg(11, vc_channel, "嗨", vc_channel)))
        self.assertTrue(await cog.handle_proxy_message(make_msg(11, bound_channel, "沒進語音也代說這句", None)))
        fake_pipeline.enqueue.assert_called_once()

        # In Couple Mode (enable_japanese_mode=True, ai_read_jp=True), BOTH Japanese channels (1001 & 1002)
        # automatically accept user proxy messages even when allowed_channels only has bound_channel (4001)
        jp_channel_2 = SimpleNamespace(id=1002, name="日文朗讀頻道")
        couple_cog = TtsCog(bot, _make_settings(target_channel_id=1001, tts_channel_id=1002, enable_japanese_mode=True), MagicMock())
        couple_cog._get_or_create_pipeline = MagicMock(return_value=fake_pipeline)
        fake_pipeline.enqueue.reset_mock()
        self.assertTrue(await couple_cog.handle_proxy_message(make_msg(11, ai_channel, "今日はいい天気ですね", None)))
        self.assertTrue(await couple_cog.handle_proxy_message(make_msg(11, jp_channel_2, "おやすみなさい", None)))
        self.assertEqual(fake_pipeline.enqueue.call_count, 2)

        # When admin turns on require_in_vc -> outside-VC user is rejected, in-VC user still accepted
        tts_engine.update_guild_tts_settings(500, require_in_vc=True)
        fake_pipeline.enqueue.reset_mock()
        self.assertFalse(await cog.handle_proxy_message(make_msg(11, bound_channel, "我在房外", None)))
        self.assertTrue(await cog.handle_proxy_message(make_msg(11, bound_channel, "我在房內", vc_channel)))
        fake_pipeline.enqueue.assert_called_once()

    async def test_flashcard_on_tts_vc_and_ephemeral(self):
        flashcard_engine.init_flashcard_tables()
        deck = flashcard_engine.get_or_create_deck("測試牌組")
        media_file = assistant_db.MEDIA_DIR / "sakura.mp3"
        media_file.parent.mkdir(parents=True, exist_ok=True)
        media_file.write_bytes(b"FAKE_ANKI_MP3")

        settings = _make_settings()
        bot = MagicMock()
        tts_cog = MagicMock(spec=TtsCog)
        tts_cog.is_connected_in_guild = MagicMock(return_value=True)
        tts_cog.enqueue_local_audio_file = AsyncMock(return_value=True)
        tts_cog.enqueue_ai_speech = AsyncMock(return_value=True)
        tts_cog.synthesize_ephemeral_jp_audio = AsyncMock(return_value=(b"JP_MP3", "mp3"))
        bot.get_cog.return_value = tts_cog

        # 1. Card with native Anki audio while in VC -> plays live in VC
        card_native = {"id": 1, "front": "桜", "reading": "さくら", "back": "櫻花", "audio_filename": "sakura.mp3"}
        view = FlashcardStudyView(bot, MagicMock(), settings, MagicMock(), 1, deck["id"], card_native)
        inter = MagicMock()
        inter.guild = SimpleNamespace(id=500)
        inter.response = AsyncMock()
        inter.followup = AsyncMock()
        await view.on_tts(inter)
        tts_cog.enqueue_local_audio_file.assert_awaited_once()
        self.assertIn("真人原音", inter.response.send_message.call_args.args[0])

        # 2. Card without native audio while in VC -> plays Edge-TTS JP in VC
        card_no_audio = {"id": 2, "front": "猫", "reading": "ねこ", "back": "貓", "audio_filename": ""}
        view2 = FlashcardStudyView(bot, MagicMock(), settings, MagicMock(), 1, deck["id"], card_no_audio)
        inter2 = MagicMock()
        inter2.guild = SimpleNamespace(id=500)
        inter2.response = AsyncMock()
        await view2.on_tts(inter2)
        tts_cog.enqueue_ai_speech.assert_awaited_once_with(inter2.guild, "ねこ", is_japanese=True, force=True)

        # 3. Card without native audio while NOT in VC -> synthesizes ephemeral MP3 attachment
        tts_cog.is_connected_in_guild.return_value = False
        inter3 = MagicMock()
        inter3.guild = SimpleNamespace(id=500)
        inter3.response = AsyncMock()
        inter3.followup = AsyncMock()
        await view2.on_tts(inter3)
        inter3.response.defer.assert_awaited_once_with(ephemeral=True)
        tts_cog.synthesize_ephemeral_jp_audio.assert_awaited_once_with("ねこ", guild_id=500)
        self.assertIsNotNone(inter3.followup.send.call_args.kwargs.get("file"))

    async def test_bot_voice_config_public_only_and_ai_speech(self):
        vc = MagicMock()
        vc.channel = SimpleNamespace(id=3001, name="語音房")
        vc.is_connected.return_value = True

        guild = MagicMock()
        guild.id = 500
        guild.voice_client = vc

        bot = MagicMock()
        bot.get_guild.return_value = guild
        settings = _make_settings()
        cog = TtsCog(bot, settings, MagicMock())
        fake_pipeline = MagicMock()
        cog._get_or_create_pipeline = MagicMock(return_value=fake_pipeline)
        cog.synthesizer.fetch_f5_voices = AsyncMock(
            return_value=[
                {"id": "f5:pub_narrator", "raw_id": "pub_narrator", "name": "公開旁白", "description": "", "public": True},
                {"id": "f5:secret_clone", "raw_id": "secret_clone", "name": "私人秘密", "description": "", "public": False},
            ]
        )

        regular_ctx = _make_ctx(user_id=111, guild_id=500, channel_id=1001)
        regular_ctx.guild = guild

        # 1. Regular user can configure bot general voice via !voice bot and !botvoice
        await cog.voice_command.callback(cog, regular_ctx, sub="bot", arg="Yunxi")
        cog.synthesizer.fetch_f5_voices.assert_awaited_with(user_id=None)
        await cog.botvoice_command.callback(cog, regular_ctx, arg="rate +10")
        await cog.botvoice_command.callback(cog, regular_ctx, arg="pitch -6")

        # 2. Regular user can configure bot Japanese voice
        await cog.voice_command.callback(cog, regular_ctx, sub="bot", arg="jp Keita")
        await cog.botvoice_command.callback(cog, regular_ctx, arg="jp rate -15")
        await cog.botvoice_command.callback(cog, regular_ctx, arg="jp pitch +5")

        # 3. Private clone voices & clone subcommands are strictly rejected for bot voice
        regular_ctx.send.reset_mock()
        await cog.botvoice_command.callback(cog, regular_ctx, arg="secret_clone")
        self.assertIn("找不到符合", regular_ctx.send.call_args.args[0])

        regular_ctx.send.reset_mock()
        await cog.botvoice_command.callback(cog, regular_ctx, arg="clone my_wav")
        self.assertIn("🔒", regular_ctx.send.call_args.args[0])

        # 4. Verify enqueue_ai_speech uses guild-configured AI voices & user_id=None
        tts_engine.update_guild_tts_settings(500, ai_read_general=True, ai_read_jp=True)
        ok_gen = await cog.enqueue_ai_speech(guild, "大家好，今天來聊聊吧！", is_japanese=False)
        self.assertTrue(ok_gen)
        job_gen = fake_pipeline.enqueue.call_args.args[0]
        self.assertEqual(job_gen.voice_id, "edge:zh-CN-YunxiNeural")
        self.assertEqual(job_gen.rate_pct, 10)
        self.assertEqual(job_gen.pitch_hz, -6)
        self.assertIsNone(job_gen.user_id)

        fake_pipeline.enqueue.reset_mock()
        ok_jp = await cog.enqueue_ai_speech(guild, "こんにちは、よろしくお願いします。", is_japanese=True)
        self.assertTrue(ok_jp)
        job_jp = fake_pipeline.enqueue.call_args.args[0]
        self.assertEqual(job_jp.voice_id, "edge:ja-JP-KeitaNeural")
        self.assertEqual(job_jp.rate_pct, -15)
        self.assertEqual(job_jp.pitch_hz, 5)
        self.assertIsNone(job_jp.user_id)

        # 5. Resetting restores default AI voices
        await cog.botvoice_command.callback(cog, regular_ctx, arg="reset")
        await cog.botvoice_command.callback(cog, regular_ctx, arg="jp reset")
        cfg_after = tts_engine.get_guild_tts_settings(500)
        self.assertFalse(cfg_after["ai_voice_is_custom"])
        self.assertFalse(cfg_after["ai_jp_voice_is_custom"])

    async def test_simultaneous_proxy_tts_and_ai_target_channel(self):
        vc_channel = SimpleNamespace(id=3001, name="語音房")
        ai_channel = SimpleNamespace(id=1001, name="一般聊天")

        vc = MagicMock()
        vc.channel = vc_channel
        vc.is_connected.return_value = True

        guild = MagicMock()
        guild.id = 500
        guild.voice_client = vc

        bot = MagicMock()
        bot.get_guild.return_value = guild
        settings = _make_settings(target_channel_id=1001, general_channel_id=1003)
        cog = TtsCog(bot, settings, MagicMock())
        fake_pipeline = MagicMock()
        cog._get_or_create_pipeline = MagicMock(return_value=fake_pipeline)

        # Admin binds the AI target channel for TTS as well
        tts_engine.update_guild_tts_settings(500, allowed_channels=[ai_channel.id])
        msg = SimpleNamespace(
            author=SimpleNamespace(
                id=11,
                bot=False,
                display_name="Tester",
                voice=SimpleNamespace(channel=vc_channel),
            ),
            guild=guild,
            channel=ai_channel,
            clean_content="這題怎麼解？",
        )
        self.assertTrue(await cog.handle_proxy_message(msg))
        fake_pipeline.enqueue.assert_called_once()
        self.assertTrue(is_target_channel(ai_channel, settings))

        # Verify bot.py on_message does not return early when handle_proxy_message succeeds
        bot_tree = ast.parse((ROOT / "bot.py").read_text(encoding="utf-8"))
        on_msg_fn = next(
            n for n in bot_tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == "on_message"
        )
        proxy_if = next(
            n
            for n in on_msg_fn.body
            if isinstance(n, ast.If)
            and any(isinstance(sub, ast.Attribute) and sub.attr == "handle_proxy_message" for sub in ast.walk(n))
        )
        self.assertFalse(any(isinstance(node, ast.Return) for node in ast.walk(proxy_if)))

    async def test_ai_replies_spoken_automatically_in_bound_tts_channel(self):
        vc_channel = SimpleNamespace(id=3001, name="語音房")
        ch_bound = SimpleNamespace(id=1001, name="綁定聊天", parent_id=None)
        ch_other = SimpleNamespace(id=9999, name="其他頻道", parent_id=None)

        vc = MagicMock()
        vc.channel = vc_channel
        vc.is_connected.return_value = True

        guild = MagicMock()
        guild.id = 500
        guild.voice_client = vc

        bot = MagicMock()
        bot.get_guild.return_value = guild
        settings = _make_settings(tts_ai_auto_read_general=False)
        cog = TtsCog(bot, settings, MagicMock())
        fake_pipeline = MagicMock()
        fake_pipeline.enqueue.return_value = True
        cog._get_or_create_pipeline = MagicMock(return_value=fake_pipeline)

        # Bind channel 1001 while ai_read_general stays default False
        tts_engine.update_guild_tts_settings(
            500,
            allowed_channels=[1001],
            default_ai_jp=True,
            default_ai_general=False,
        )

        # 1. Bound channel -> AI reply IS automatically spoken using bot AI voice (user_id=None)
        ok_bound = await cog.enqueue_ai_speech(guild, "你好呀！", is_japanese=False, channel=ch_bound)
        self.assertTrue(ok_bound)
        job = fake_pipeline.enqueue.call_args.args[0]
        self.assertIsNone(job.user_id)
        self.assertEqual(job.text, "你好呀！")

        # 2. Unbound channel -> AI reply is NOT spoken when ai_read_general is False
        fake_pipeline.enqueue.reset_mock()
        ok_other = await cog.enqueue_ai_speech(guild, "別的地方的話", is_japanese=False, channel=ch_other)
        self.assertFalse(ok_other)
        fake_pipeline.enqueue.assert_not_called()

        # 3. Ignored channel -> never spoken even if also in allowed_channels
        tts_engine.update_guild_tts_settings(500, ignored_channels=[1001])
        ok_ignored = await cog.enqueue_ai_speech(guild, "這句被忽略", is_japanese=False, channel=ch_bound)
        self.assertFalse(ok_ignored)
        fake_pipeline.enqueue.assert_not_called()

    async def test_cross_server_bot_owner_authority(self):
        bot = MagicMock()
        bot.is_owner = AsyncMock(return_value=False)
        bot.get_channel.return_value = None
        settings = _make_settings(
            target_channel_id=1001,
            enable_japanese_mode=False,
            bot_owner_ids=(124225918267817984,),
        )
        logger = MagicMock()
        tts_cog = TtsCog(bot, settings, logger)
        tts_cog.synthesizer.unload_f5_model = AsyncMock(
            return_value={"was_loaded": False, "vram_allocated_gb": 0.0}
        )
        tts_cog.synthesizer.reload_f5_voices = AsyncMock(
            return_value={"public_voice_count": 2, "total_voice_count": 3}
        )
        asst_cog = AssistantCog(bot, settings, logger)
        rpg_cog = RpgCog(bot, settings, logger)

        foreign_ch = SimpleNamespace(id=88888, name="foreign-debug", mention="<#88888>")
        foreign_owner_ctx = _make_ctx(
            user_id=124225918267817984,
            guild_id=99999,
            channel_id=88888,
            channels=[foreign_ch],
        )
        foreign_regular_ctx = _make_ctx(
            user_id=55555,
            guild_id=99999,
            channel_id=88888,
            channels=[foreign_ch],
        )

        # Owner with ZERO guild roles in a foreign server & non-target channel can configure & debug
        await tts_cog.tts_command.callback(tts_cog, foreign_owner_ctx, action="bind", arg="<#88888>")
        self.assertEqual(tts_engine.get_guild_tts_settings(99999)["allowed_channels"], [88888])
        await tts_cog.tts_command.callback(tts_cog, foreign_owner_ctx, action="channel", arg="add <#88888>")
        await tts_cog.tts_command.callback(tts_cog, foreign_owner_ctx, action="ai", arg="on")
        self.assertTrue(tts_engine.get_guild_tts_settings(99999)["ai_read_general"])
        await tts_cog.tts_command.callback(tts_cog, foreign_owner_ctx, action="unload")
        tts_cog.synthesizer.unload_f5_model.assert_awaited_once()
        await tts_cog.tts_command.callback(tts_cog, foreign_owner_ctx, action="reloadvoices")
        tts_cog.synthesizer.reload_f5_voices.assert_awaited_once()

        foreign_owner_ctx.send.reset_mock()
        await asst_cog.help_command.callback(asst_cog, foreign_owner_ctx)
        self.assertIn("embed", foreign_owner_ctx.send.call_args.kwargs)

        foreign_owner_ctx.send.reset_mock()
        await asst_cog.reload_command.callback(asst_cog, foreign_owner_ctx)
        self.assertIn("已重新載入", foreign_owner_ctx.send.call_args.args[0])

        foreign_owner_ctx.send.reset_mock()
        await asst_cog.reset_context.callback(asst_cog, foreign_owner_ctx)
        self.assertIn("已重設", foreign_owner_ctx.send.call_args.args[0])

        rpg_engine.add_lorebook_entry("測試", "測試內容")
        foreign_owner_ctx.send.reset_mock()
        await rpg_cog.lore_command.callback(rpg_cog, foreign_owner_ctx, action="clear")
        self.assertIn("已清空世界觀", foreign_owner_ctx.send.call_args.args[0])

        # Non-owner without permissions remains restricted
        await tts_cog.tts_command.callback(tts_cog, foreign_regular_ctx, action="bind", arg="<#88888>")
        self.assertIn("🔒", foreign_regular_ctx.send.call_args.args[0])
        foreign_regular_ctx.send.reset_mock()
        await asst_cog.help_command.callback(asst_cog, foreign_regular_ctx)
        foreign_regular_ctx.send.assert_not_called()
        await asst_cog.reload_command.callback(asst_cog, foreign_regular_ctx)
        foreign_regular_ctx.send.assert_not_called()
        await rpg_cog.lore_command.callback(rpg_cog, foreign_regular_ctx, action="clear")
        foreign_regular_ctx.send.assert_not_called()

        target_regular_ctx = _make_ctx(
            user_id=55555,
            guild_id=99999,
            channel_id=1001,
            channels=[foreign_ch],
        )
        await rpg_cog.lore_command.callback(rpg_cog, target_regular_ctx, action="clear")
        self.assertIn("⚠️", target_regular_ctx.send.call_args.args[0])

    def test_f5_server_voice_catalog_privacy_isolation(self):
        tts_server_py = ROOT / "services" / "tts_server.py"
        tts_venv_py = ROOT / ".venv_tts" / "Scripts" / "python.exe"
        self.assertTrue(tts_server_py.is_file(), "services/tts_server.py must exist")
        if not tts_venv_py.is_file():
            self.skipTest(".venv_tts environment not present")

        with tempfile.TemporaryDirectory() as srv_dir:
            srv_path = Path(srv_dir)
            (srv_path / "pub.wav").write_bytes(b"RIFF0000")
            (srv_path / "priv.wav").write_bytes(b"RIFF0000")
            voices_cfg = {
                "voices": {
                    "pub_story": {
                        "name": "故事旁白",
                        "description": "公開",
                        "prompt_audio": "pub.wav",
                        "prompt_text": "你好",
                        "public": True,
                        "allowed_users": [],
                    },
                    "owner_secret": {
                        "name": "私人克隆",
                        "description": "不可外流",
                        "prompt_audio": "priv.wav",
                        "prompt_text": "早安",
                        "public": False,
                        "allowed_users": ["12345"],
                    },
                    "traversal_bad": {
                        "name": "惡意路徑",
                        "prompt_audio": "../outside.wav",
                        "prompt_text": "bad",
                        "public": True,
                    },
                }
            }
            (srv_path / "voices.json").write_text(json.dumps(voices_cfg), encoding="utf-8")

            script = f"""
import sys
from pathlib import Path
sys.path.insert(0, r"{str(ROOT)}")
from fastapi.testclient import TestClient
from services import tts_server

cat = tts_server.VoiceCatalog(data_dir=Path(r"{srv_dir}"))
tts_server.catalog = cat
client = TestClient(tts_server.app)

r_health = client.get("/").json()
assert r_health["model_loaded"] is False
assert r_health["public_voice_count"] == 1
assert r_health["total_voice_count"] == 2

r_anon = client.get("/voices").json()["voices"]
assert [v["id"] for v in r_anon] == ["pub_story"]
assert "prompt_audio_path" not in r_anon[0]

r_other = client.get("/voices", params={{"user_id": "99999"}}).json()["voices"]
assert [v["id"] for v in r_other] == ["pub_story"]

r_owner = client.get("/voices", params={{"user_id": "12345"}}).json()["voices"]
assert [v["id"] for v in r_owner] == ["pub_story", "owner_secret"]

r_forbid = client.post("/tts_by_voice", data={{"tts_text": "hi", "voice_id": "owner_secret", "user_id": "99999"}})
assert r_forbid.status_code == 403
print("CATALOG_PRIVACY_OK")
"""
            res = subprocess.run(
                [str(tts_venv_py), "-c", script],
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=30,
            )
            self.assertEqual(res.returncode, 0, msg=f"STDOUT: {res.stdout}\nSTDERR: {res.stderr}")
            self.assertIn("CATALOG_PRIVACY_OK", res.stdout)

    async def test_proxy_unconfigured_user_and_voice_states_fallback(self):
        vc_ch_a = SimpleNamespace(id=3001, name="語音房", voice_states={}, members=[])
        vc_ch_copy = SimpleNamespace(id=3001, name="語音房")  # Distinct instance with same ID
        bound_ch = SimpleNamespace(id=4001, name="綁定聊天", parent_id=None)

        vc = MagicMock()
        vc.channel = vc_ch_a
        vc.is_connected.return_value = True

        guild = MagicMock()
        guild.id = 500
        guild.voice_client = vc
        guild._voice_states = {}

        bot = MagicMock()
        bot.get_guild.return_value = guild
        settings = _make_settings(tts_default_voice="edge:zh-TW-HsiaoChenNeural")
        logger = MagicMock()
        cog = TtsCog(bot, settings, logger)
        fake_pipeline = MagicMock()
        fake_pipeline.enqueue.return_value = True
        cog._get_or_create_pipeline = MagicMock(return_value=fake_pipeline)

        tts_engine.update_guild_tts_settings(500, allowed_channels=[bound_ch.id])

        # 1. Unconfigured non-VC user (no row in tts_user_prefs) works by default (require_in_vc=False)
        msg_unconfigured = SimpleNamespace(
            author=SimpleNamespace(
                id=777001,
                bot=False,
                display_name="新手路人",
                voice=None,
            ),
            guild=guild,
            channel=bound_ch,
            clean_content="測試沒設聲音也沒進語音的人",
        )
        self.assertTrue(await cog.handle_proxy_message(msg_unconfigured))
        job = fake_pipeline.enqueue.call_args.args[0]
        self.assertEqual(job.voice_id, "edge:zh-TW-HsiaoChenNeural")
        self.assertEqual(job.rate_pct, 0)
        self.assertEqual(job.pitch_hz, 0)
        self.assertTrue(any("[TTS 代說]" in str(c.args[0]) for c in logger.info.call_args_list))

        # 1b. !say also works for non-VC user when bot is already in VC & require_in_vc=False
        fake_pipeline.enqueue.reset_mock()
        non_vc_say_ctx = _make_ctx(user_id=777001, guild_id=500, channel_id=bound_ch.id)
        non_vc_say_ctx.guild = guild
        await cog.say_command.callback(cog, non_vc_say_ctx, text="房外直接說這句")
        fake_pipeline.enqueue.assert_called_once()

        # Enable require_in_vc=True for strict voice_states fallback & rejection checks
        tts_engine.update_guild_tts_settings(500, require_in_vc=True)

        # 2. discord.User-like author without .voice attr -> matched via vc.channel.voice_states
        fake_pipeline.enqueue.reset_mock()
        vc_ch_a.voice_states = {777002: SimpleNamespace(channel=vc_ch_a)}
        msg_via_vc_vs = SimpleNamespace(
            author=SimpleNamespace(id=777002, bot=False, display_name="未快取User"),
            guild=guild,
            channel=bound_ch,
            clean_content="用 voice_states 找得到我嗎",
        )
        self.assertTrue(await cog.handle_proxy_message(msg_via_vc_vs))
        fake_pipeline.enqueue.assert_called_once()

        # 3. discord.User-like author without .voice attr -> matched via guild._voice_states fallback
        fake_pipeline.enqueue.reset_mock()
        vc_ch_a.voice_states = {}
        guild._voice_states = {777003: SimpleNamespace(channel=vc_ch_copy)}
        msg_via_guild_vs = SimpleNamespace(
            author=SimpleNamespace(id=777003, bot=False, display_name="GuildCacheUser"),
            guild=guild,
            channel=bound_ch,
            clean_content="guild voice state fallback",
        )
        self.assertTrue(await cog.handle_proxy_message(msg_via_guild_vs))
        fake_pipeline.enqueue.assert_called_once()

        # 4. User outside VC when require_in_vc=True -> proxy returns False + logs skip; !say warns
        logger.info.reset_mock()
        guild._voice_states = {}
        msg_outside = SimpleNamespace(
            author=SimpleNamespace(id=777004, bot=False, display_name="沒進房的人", voice=None),
            guild=guild,
            channel=bound_ch,
            clean_content="我不在語音房",
        )
        self.assertFalse(await cog.handle_proxy_message(msg_outside))
        self.assertTrue(any("[TTS 略過]" in str(c.args[0]) for c in logger.info.call_args_list))

        non_vc_say_ctx.send.reset_mock()
        await cog.say_command.callback(cog, non_vc_say_ctx, text="同房限制後不能說")
        self.assertIn("已開啟同房限制", non_vc_say_ctx.send.call_args.args[0])

        # 5. Full queue returns False and logs warning
        fake_pipeline.enqueue.return_value = False
        vc_ch_a.voice_states = {777002: SimpleNamespace(channel=vc_ch_a)}
        self.assertFalse(await cog.handle_proxy_message(msg_via_vc_vs))
        self.assertTrue(any("語音佇列已滿" in str(c.args[0]) for c in logger.warning.call_args_list))

    async def test_auto_disconnect_uses_voice_states_and_status_shows_caller_detection(self):
        bot_member = SimpleNamespace(id=9999, bot=True)
        vc_channel = SimpleNamespace(
            id=3001,
            name="語音房",
            members=[bot_member],  # Human is uncached in .members
            voice_states={
                9999: SimpleNamespace(channel=None),
                111: SimpleNamespace(channel=None),
            },
        )
        vc_channel.voice_states[9999].channel = vc_channel
        vc_channel.voice_states[111].channel = vc_channel

        vc = MagicMock()
        vc.channel = vc_channel
        vc.is_connected.return_value = True
        vc.disconnect = AsyncMock()

        guild = MagicMock()
        guild.id = 500
        guild.voice_client = vc
        guild._voice_states = dict(vc_channel.voice_states)
        guild.get_member.side_effect = lambda uid: bot_member if uid == 9999 else None

        bot = MagicMock()
        bot.user = SimpleNamespace(id=9999)
        bot.get_guild.return_value = guild
        cog = TtsCog(bot, _make_settings(), MagicMock())
        cog.synthesizer.fetch_f5_voices = AsyncMock(return_value=[])
        cog.synthesizer.check_f5_health = AsyncMock(return_value=None)

        # 1. Human still in voice_states -> must NOT disconnect even though .members only shows bot
        other_member = SimpleNamespace(id=555, guild=guild)
        await cog.on_voice_state_update(
            other_member,
            SimpleNamespace(channel=vc_channel),
            SimpleNamespace(channel=None),
        )
        vc.disconnect.assert_not_awaited()

        # 2. !tts status while caller (111) is detected via voice_states vs outside VC
        in_vc_ctx = _make_ctx(user_id=111, guild_id=500, channel_id=1001)
        in_vc_ctx.guild = guild
        await cog.tts_command.callback(cog, in_vc_ctx, action="status")
        in_embed = in_vc_ctx.send.call_args.kwargs["embed"]
        fields = {f.name: f.value for f in in_embed.fields}
        self.assertIn("偵測在線人數：1 人", fields["語音連線"])
        self.assertIn("已在此語音房內", fields["語音連線"])
        self.assertIn("你的個人聲線", fields)

        outside_ctx = _make_ctx(user_id=222, guild_id=500, channel_id=1001)
        outside_ctx.guild = guild
        await cog.tts_command.callback(cog, outside_ctx, action="status")
        out_embed = outside_ctx.send.call_args.kwargs["embed"]
        out_fields = {f.name: f.value for f in out_embed.fields}
        self.assertIn("開放未進房成員於對象頻道代說", out_fields["語音連線"])

        tts_engine.update_guild_tts_settings(500, require_in_vc=True)
        outside_ctx.send.reset_mock()
        await cog.tts_command.callback(cog, outside_ctx, action="status")
        out_strict_fields = {f.name: f.value for f in outside_ctx.send.call_args.kwargs["embed"].fields}
        self.assertIn("未偵測到此語音房內", out_strict_fields["語音連線"])
        self.assertIn("已開啟同房限制", out_strict_fields["語音連線"])

        # 3. Human leaves voice_states too (only bot remains) -> disconnects cleanly
        del vc_channel.voice_states[111]
        await cog.on_voice_state_update(
            SimpleNamespace(id=111, guild=guild),
            SimpleNamespace(channel=vc_channel),
            SimpleNamespace(channel=None),
        )
        vc.disconnect.assert_awaited_once()

    async def test_voiceclone_stub_command_and_tts_clone_notice(self):
        bot = MagicMock()
        settings_with_owner = _make_settings(bot_owner_ids=(9000,))
        cog = TtsCog(bot, settings_with_owner, MagicMock())
        regular_ctx = _make_ctx(user_id=111, guild_id=500, channel_id=1001)

        # 1. !voiceclone displays owner contact mention without pinging + !voice hint
        await cog.voiceclone_command.callback(cog, regular_ctx, _arg="幫我做聲音")
        kwargs = regular_ctx.send.call_args.kwargs
        embed = kwargs["embed"]
        allowed_mentions = kwargs["allowed_mentions"]
        self.assertFalse(allowed_mentions.users)
        self.assertIn("<@9000>", embed.description)
        self.assertIn("聯絡", embed.description)
        self.assertTrue(any("!voice" in f.value for f in embed.fields))

        # 2. !tts clone routes to the same notice embed
        regular_ctx.send.reset_mock()
        await cog.tts_command.callback(cog, regular_ctx, action="clone")
        tts_embed = regular_ctx.send.call_args.kwargs["embed"]
        self.assertIn("語音克隆", tts_embed.title)
        self.assertIn("<@9000>", tts_embed.description)

        # 3. Fallback neutral label when bot_owner_ids is empty
        cog_no_owner = TtsCog(bot, _make_settings(bot_owner_ids=()), MagicMock())
        regular_ctx.send.reset_mock()
        await cog_no_owner.voiceclone_command.callback(cog_no_owner, regular_ctx)
        no_owner_embed = regular_ctx.send.call_args.kwargs["embed"]
        self.assertIn("機器人擁有者", no_owner_embed.description)
        self.assertNotIn("<@>", no_owner_embed.description)

        # 4. COMMANDS.md stays under 2000 chars and includes !voiceclone
        commands_doc = (ROOT / "docs" / "COMMANDS.md").read_text(encoding="utf-8")
        self.assertLess(len(commands_doc), 2000)
        self.assertIn("!voiceclone", commands_doc)

    def test_f5_server_register_delete_and_token_security(self):
        tts_server_py = ROOT / "services" / "tts_server.py"
        tts_venv_py = ROOT / ".venv_tts" / "Scripts" / "python.exe"
        self.assertTrue(tts_server_py.is_file(), "services/tts_server.py must exist")
        if not tts_venv_py.is_file():
            self.skipTest(".venv_tts environment not present")

        with tempfile.TemporaryDirectory() as srv_dir:
            srv_path = Path(srv_dir)
            (srv_path / "preset.wav").write_bytes(_make_wav_bytes(4.0))
            voices_cfg = {
                "voices": {
                    "manual_preset": {
                        "name": "手動預設",
                        "prompt_audio": "preset.wav",
                        "prompt_text": "手動預設參考音訊",
                        "public": True,
                        "allowed_users": [],
                    }
                }
            }
            (srv_path / "voices.json").write_text(json.dumps(voices_cfg), encoding="utf-8")

            script = f"""
import io, json, os, wave
from pathlib import Path
import sys
sys.path.insert(0, r"{str(ROOT)}")
from fastapi.testclient import TestClient
from services import tts_server

os.environ["TTS_API_TOKEN"] = "unit_secret_token"
try:
    cat = tts_server.VoiceCatalog(data_dir=Path(r"{srv_dir}"))
    tts_server.catalog = cat
    client = TestClient(tts_server.app)

    def make_wav(dur, sr=24000):
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sr)
            wf.writeframes(b"\\x00\\x00" * int(sr * dur))
        return buf.getvalue()

    wav_ok = make_wav(5.0)
    wav_short = make_wav(1.5)
    wav_long = make_wav(18.0)
    headers = {{"X-TTS-Admin-Token": "unit_secret_token"}}

    # 1. Missing/wrong admin token rejected (401)
    r_no_tok = client.post(
        "/register_voice",
        files={{"audio": ("a.wav", wav_ok, "audio/wav")}},
        data={{"owner_user_id": "111", "slot": "1", "name": "測試", "prompt_text": "你好這是一段測試文字"}},
    )
    assert r_no_tok.status_code == 401
    assert client.post("/reload_voices").status_code == 401

    # 2. Invalid WAV / <3s / >15s / short prompt rejected (400)
    r_bad = client.post(
        "/register_voice",
        headers=headers,
        files={{"audio": ("bad.wav", b"NOT_A_WAV" * 200, "audio/wav")}},
        data={{"owner_user_id": "111", "slot": "1", "name": "壞檔", "prompt_text": "這是一段有效逐字稿"}},
    )
    assert r_bad.status_code == 400
    assert client.post(
        "/register_voice",
        headers=headers,
        files={{"audio": ("s.wav", wav_short, "audio/wav")}},
        data={{"owner_user_id": "111", "slot": "1", "name": "太短", "prompt_text": "這是一段有效逐字稿"}},
    ).status_code == 400
    assert client.post(
        "/register_voice",
        headers=headers,
        files={{"audio": ("l.wav", wav_long, "audio/wav")}},
        data={{"owner_user_id": "111", "slot": "1", "name": "太長", "prompt_text": "這是一段有效逐字稿"}},
    ).status_code == 400

    # 3. Register slot 1 (private) & slot 2 (public) with privacy isolation
    r1 = client.post(
        "/register_voice",
        headers=headers,
        files={{"audio": ("u.wav", wav_ok, "audio/wav")}},
        data={{"owner_user_id": "111", "slot": "1", "name": "私人一", "prompt_text": "你好這是我的第一組聲音", "public": "false", "max_per_user": "2"}},
    )
    assert r1.status_code == 200, r1.text
    assert r1.json()["voice_id"] == "u_111_s1"

    r2 = client.post(
        "/register_voice",
        headers=headers,
        files={{"audio": ("u2.wav", wav_ok, "audio/wav")}},
        data={{"owner_user_id": "111", "slot": "2", "name": "公開二", "prompt_text": "你好這是我的第二組公開聲音", "public": "true", "max_per_user": "2"}},
    )
    assert r2.status_code == 200, r2.text

    # Reject slot > max_per_user (2) & reject exceeding global cap
    assert client.post(
        "/register_voice",
        headers=headers,
        files={{"audio": ("u3.wav", wav_ok, "audio/wav")}},
        data={{"owner_user_id": "111", "slot": "3", "name": "超額", "prompt_text": "你好這組不應該通過", "max_per_user": "2"}},
    ).status_code == 400
    assert client.post(
        "/register_voice",
        headers=headers,
        files={{"audio": ("u9.wav", wav_ok, "audio/wav")}},
        data={{"owner_user_id": "999", "slot": "1", "name": "總量爆", "prompt_text": "你好這組測試總量上限", "max_per_user": "2", "max_total": "2"}},
    ).status_code == 409

    # Privacy isolation & token enforcement on /voices and /tts_by_voice
    assert client.get("/voices", params={{"user_id": "222"}}).status_code == 401
    assert client.post("/tts_by_voice", data={{"tts_text": "測試", "voice_id": "u_111_s1", "user_id": "111"}}).status_code == 401
    v_other = [v["id"] for v in client.get("/voices", headers=headers, params={{"user_id": "222"}}).json()["voices"]]
    assert "u_111_s1" not in v_other and "u_111_s2" in v_other
    v_owner = [v["id"] for v in client.get("/voices", headers=headers, params={{"user_id": "111"}}).json()["voices"]]
    assert "u_111_s1" in v_owner and "u_111_s2" in v_owner
    assert client.post("/tts_by_voice", headers=headers, data={{"tts_text": "測試", "voice_id": "u_111_s1", "user_id": "222"}}).status_code == 403

    # Visibility promotion / demotion endpoint with token + manual_preset protection
    assert client.post("/voices/u_111_s1/visibility", data={{"public": "true"}}).status_code == 401
    assert client.post("/voices/manual_preset/visibility", headers=headers, data={{"public": "false"}}).status_code == 403
    assert client.post("/voices/u_111_s1/visibility", headers=headers, data={{"public": "true"}}).status_code == 200
    assert "u_111_s1" in [v["id"] for v in client.get("/voices", headers=headers, params={{"user_id": "222"}}).json()["voices"]]
    assert client.post("/voices/u_111_s1/visibility", headers=headers, data={{"public": "false"}}).status_code == 200
    assert "u_111_s1" not in [v["id"] for v in client.get("/voices", headers=headers, params={{"user_id": "222"}}).json()["voices"]]

    # 4. Delete protection: cannot delete manual preset or someone else's clone
    assert client.delete("/voices/manual_preset", headers=headers).status_code == 403
    assert client.delete("/voices/u_111_s1", headers=headers, params={{"requester_user_id": "222"}}).status_code == 403
    r_del = client.delete("/voices/u_111_s1", headers=headers, params={{"requester_user_id": "111"}})
    assert r_del.status_code == 200
    assert not (Path(r"{srv_dir}") / "user_clones" / "u_111_s1.wav").exists()
    print("SERVER_CLONE_SECURITY_OK")
finally:
    os.environ.pop("TTS_API_TOKEN", None)
"""
            res = subprocess.run(
                [str(tts_venv_py), "-c", script],
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=30,
            )
            self.assertEqual(res.returncode, 0, msg=f"STDOUT: {res.stdout}\nSTDERR: {res.stderr}")
            self.assertIn("SERVER_CLONE_SECURITY_OK", res.stdout)

    async def test_clone_audio_ffmpeg_sanitizer_and_prompt_validation(self):
        # Prompt validator checks
        ok_valid, clean_txt = tts_engine.validate_clone_prompt_text("大家好，今天是晴天 <@12345>\n`測試`！")
        self.assertTrue(ok_valid)
        self.assertNotIn("12345", clean_txt)
        self.assertFalse(tts_engine.validate_clone_prompt_text("太短")[0])
        self.assertFalse(tts_engine.validate_clone_prompt_text("字" * 125)[0])
        self.assertFalse(tts_engine.validate_clone_prompt_text("！！！。。。")[0])

        # FFmpeg sanitizer tests (real ffmpeg re-encoding + duration enforcement)
        valid_wav = _make_wav_bytes(5.0, samplerate=44100)
        sanitized_wav, dur = await tts_engine.sanitize_clone_audio_bytes(valid_wav, "voice.wav")
        self.assertGreaterEqual(dur, 4.9)
        self.assertLessEqual(dur, 5.1)
        with wave.open(io.BytesIO(sanitized_wav), "rb") as wf:
            self.assertEqual(wf.getnchannels(), 1)
            self.assertEqual(wf.getframerate(), 24000)

        with self.assertRaises(ValueError):
            await tts_engine.sanitize_clone_audio_bytes(_make_wav_bytes(1.0), "short.wav")
        with self.assertRaises(ValueError):
            await tts_engine.sanitize_clone_audio_bytes(_make_wav_bytes(18.0), "long.wav")
        with self.assertRaises(ValueError):
            await tts_engine.sanitize_clone_audio_bytes(valid_wav, "malware.exe")
        with self.assertRaises(ValueError):
            await tts_engine.sanitize_clone_audio_bytes(b"x" * (tts_engine.CLONE_MAX_UPLOAD_BYTES + 10), "big.wav")

    async def test_user_voice_clone_approval_workflow_and_security(self):
        bot = MagicMock()
        bot.is_owner = AsyncMock(return_value=False)
        owner_dm = AsyncMock()
        owner_user = MagicMock()
        owner_user.id = 9000
        owner_user.send = owner_dm
        user_111 = MagicMock()
        user_111.id = 111
        user_111.send = AsyncMock()
        bot.get_user.side_effect = lambda uid: owner_user if uid == 9000 else (user_111 if uid == 111 else None)
        bot.get_channel.return_value = None

        settings = _make_settings(
            enable_user_voice_clone=True,
            tts_clone_approval_mode="manual",
            bot_owner_ids=(9000,),
            tts_api_url="http://127.0.0.1:9880",
            tts_api_token="sec_tok",
            tts_clone_max_per_user=2,
            tts_clone_max_total=40,
            tts_clone_cooldown_seconds=600,
            tts_f5_max_chars=30,
        )
        cog = TtsCog(bot, settings, MagicMock())
        cog.synthesizer.register_f5_voice = AsyncMock(
            side_effect=lambda **kw: {
                "status": "registered",
                "voice_id": f"u_{kw['owner_user_id']}_s{kw['slot']}",
                "name": kw["name"],
                "public": kw["public"],
            }
        )
        cog.synthesizer.delete_f5_voice = AsyncMock(return_value={"status": "deleted"})

        valid_wav = _make_wav_bytes(4.5, samplerate=24000)

        def attach(filename="sample.wav", data=valid_wav):
            att = MagicMock()
            att.filename = filename
            att.size = len(data)
            att.read = AsyncMock(return_value=data)
            return att

        # 1. Submit voice clone -> quarantined WAV saved, DM sent to owner with CloneReviewView
        ctx_u1 = _make_ctx(user_id=111, guild_id=500, channel_id=1001)
        ctx_u1.message = SimpleNamespace(attachments=[attach()])
        await cog.voiceclone_command.callback(cog, ctx_u1, _arg="阿明專屬 | 大家好這是我的測試錄音檔")
        self.assertIn("已送出語音克隆申請", ctx_u1.send.call_args.args[0])
        owner_dm.assert_awaited_once()
        dm_kwargs = owner_dm.call_args.kwargs
        self.assertIsInstance(dm_kwargs["view"], CloneReviewView)
        pending = tts_engine.list_pending_clone_requests(user_id=111)
        self.assertEqual(len(pending), 1)
        req1_id = pending[0]["id"]
        q_wav_1 = Path(pending[0]["sanitized_audio_path"])
        self.assertTrue(q_wav_1.is_file())

        # 2. Second concurrent submission while pending is blocked
        ctx_u1.send.reset_mock()
        await cog.voiceclone_command.callback(cog, ctx_u1, _arg="再送一次看看")
        self.assertIn("已有待審核的申請", ctx_u1.send.call_args.args[0])

        # 3. Non-owner clicking approval button is denied cleanly
        fake_non_owner_inter = MagicMock()
        fake_non_owner_inter.type = discord.InteractionType.component
        fake_non_owner_inter.user = SimpleNamespace(id=222)
        fake_non_owner_inter.data = {"custom_id": f"vc_approve_priv:{req1_id}"}
        fake_non_owner_inter.response = AsyncMock()
        await cog._on_clone_review_interaction(fake_non_owner_inter)
        self.assertIn("🔒", fake_non_owner_inter.response.send_message.call_args.args[0])
        self.assertEqual(len(tts_engine.list_pending_clone_requests(user_id=111)), 1)

        # 4. Owner clicks [✅ 核准為私人聲線] via persistent on_interaction listener
        fake_owner_inter = MagicMock()
        fake_owner_inter.type = discord.InteractionType.component
        fake_owner_inter.user = SimpleNamespace(id=9000)
        fake_owner_inter.data = {"custom_id": f"vc_approve_priv:{req1_id}"}
        fake_owner_inter.message = AsyncMock()
        fake_owner_inter.message.embeds = [discord.Embed(title="review")]
        fake_owner_inter.response = AsyncMock()
        fake_owner_inter.followup = AsyncMock()
        await cog._on_clone_review_interaction(fake_owner_inter)
        cog.synthesizer.register_f5_voice.assert_awaited_once()
        self.assertFalse(cog.synthesizer.register_f5_voice.call_args.kwargs["public"])
        self.assertFalse(q_wav_1.exists())
        self.assertEqual(tts_engine.get_clone_request(req1_id)["status"], "approved_private")
        clones_111 = tts_engine.list_user_clones(user_id=111)
        self.assertEqual([c["voice_id"] for c in clones_111], ["u_111_s1"])

        # 5. Cooldown blocks immediate re-upload for non-owner; after resetting timestamp -> slot 2 public approval works
        ctx_u1.send.reset_mock()
        await cog.voiceclone_command.callback(cog, ctx_u1, _arg="2 公開二號 | 我馬上再傳一次會觸發冷卻")
        self.assertIn("提交過於頻繁", ctx_u1.send.call_args.args[0])

        with tts_engine.get_conn() as conn:
            conn.execute("UPDATE tts_clone_requests SET created_at = '2020-01-01 00:00:00'")
        ctx_u1.send.reset_mock()
        await cog.voiceclone_command.callback(cog, ctx_u1, _arg="2 公開二號 | 第二段錄音檔測試通過囉")
        req2_id = tts_engine.list_pending_clone_requests(user_id=111)[0]["id"]
        owner_ctx = _make_ctx(user_id=9000, guild_id=500, channel_id=1001)
        await cog.voiceclone_command.callback(cog, owner_ctx, _arg=f"approve {req2_id} public")
        self.assertEqual(tts_engine.get_clone_request(req2_id)["status"], "approved_public")
        self.assertEqual(len(tts_engine.list_user_clones(user_id=111)), 2)

        # 6. User sets slot 1 as personal voice then deletes slot 1 -> user pref automatically cleared
        tts_engine.set_user_voice_pref(111, voice_id="f5:u_111_s1")
        ctx_u1_cmd = _make_ctx(user_id=111, guild_id=500, channel_id=1001)
        await cog.voiceclone_command.callback(cog, ctx_u1_cmd, _arg="delete 1")
        cog.synthesizer.delete_f5_voice.assert_awaited_with("u_111_s1", requester_user_id=111)
        self.assertFalse(tts_engine.get_user_voice_pref(111)["is_custom"])

        # 7. Owner ban + revoke + unban
        await cog.voiceclone_command.callback(cog, owner_ctx, _arg="ban 111 亂傳雜訊")
        self.assertIsNotNone(tts_engine.get_clone_ban(111))
        ctx_u1.send.reset_mock()
        await cog.voiceclone_command.callback(cog, ctx_u1, _arg="黑名單後傳音檔")
        self.assertIn("🚫", ctx_u1.send.call_args.args[0])
        self.assertIn("限制申請", ctx_u1.send.call_args.args[0])
        await cog.voiceclone_command.callback(cog, owner_ctx, _arg="revoke 111 all")
        self.assertEqual(tts_engine.list_user_clones(user_id=111), [])
        await cog.voiceclone_command.callback(cog, owner_ctx, _arg="unban 111")
        self.assertIsNone(tts_engine.get_clone_ban(111))

        # 8. Owner reject deletes quarantine WAV cleanly
        ctx_u2 = _make_ctx(user_id=222, guild_id=500, channel_id=1001)
        ctx_u2.message = SimpleNamespace(attachments=[attach()])
        await cog.voiceclone_command.callback(cog, ctx_u2, _arg="測試拒絕 | 這一段會被擁有者拒絕")
        req3 = tts_engine.list_pending_clone_requests(user_id=222)[0]
        q_wav_3 = Path(req3["sanitized_audio_path"])
        self.assertTrue(q_wav_3.is_file())
        await cog.voiceclone_command.callback(cog, owner_ctx, _arg=f"reject {req3['id']} 雜音太大")
        self.assertEqual(tts_engine.get_clone_request(req3["id"])["status"], "rejected")
        self.assertFalse(q_wav_3.exists())

        # 9. Verify GuildVoicePipeline truncates F5 jobs to f5_max_chars (25) without truncating Edge jobs
        mock_synth = MagicMock()
        seen_texts: list[tuple[str, str]] = []

        async def fake_synth(chunk, voice_id, **kwargs):
            seen_texts.append((voice_id, chunk))
            return valid_wav, "wav", False

        mock_synth.synthesize = AsyncMock(side_effect=fake_synth)
        pipeline = GuildVoicePipeline(bot, 500, mock_synth, 1.0, MagicMock(), f5_max_chars=25)
        vc_dummy = MagicMock()
        vc_dummy.is_connected.return_value = True
        vc_dummy.is_playing.return_value = False
        vc_dummy.play.side_effect = lambda src, after=None: after(None) if after else None
        pipeline._get_connected_vc = MagicMock(return_value=vc_dummy)
        with patch("cogs.tts_cog.discord.FFmpegPCMAudio", return_value=MagicMock()):
            pipeline.enqueue(
                TtsJob(
                    text="一二三四五六七八九十" * 5,
                    voice_id="f5:u_111_s2",
                    user_id=111,
                )
            )
            pipeline.enqueue(
                TtsJob(
                    text="一二三四五六七八九十" * 5,
                    voice_id="edge:zh-TW-HsiaoChenNeural",
                )
            )
            await asyncio.wait_for(pipeline.text_queue.join(), timeout=5.0)
        await pipeline.stop()
        f5_total = sum(len(t) for vid, t in seen_texts if vid.startswith("f5:"))
        edge_total = sum(len(t) for vid, t in seen_texts if vid.startswith("edge:"))
        self.assertLessEqual(f5_total, 25)
        self.assertGreaterEqual(edge_total, 45)

    async def test_user_voice_clone_auto_mode_and_post_hoc_audit(self):
        bot = MagicMock()
        bot.is_owner = AsyncMock(return_value=False)
        owner_dm = AsyncMock()
        owner_user = MagicMock()
        owner_user.id = 9000
        owner_user.send = owner_dm
        bot.get_user.side_effect = lambda uid: owner_user if uid == 9000 else None
        bot.get_channel.return_value = None

        # Default tts_clone_approval_mode is "auto"
        settings = _make_settings(
            enable_user_voice_clone=True,
            bot_owner_ids=(9000,),
            tts_api_url="http://127.0.0.1:9880",
            tts_clone_max_per_user=2,
            tts_clone_max_total=40,
            tts_clone_cooldown_seconds=600,
        )
        self.assertEqual(settings.tts_clone_approval_mode, "auto")
        cog = TtsCog(bot, settings, MagicMock())
        cog.synthesizer.register_f5_voice = AsyncMock(
            side_effect=lambda **kw: {
                "status": "registered",
                "voice_id": f"u_{kw['owner_user_id']}_s{kw['slot']}",
                "name": kw["name"],
                "public": kw["public"],
            }
        )
        cog.synthesizer.set_f5_voice_visibility = AsyncMock(return_value={"status": "updated", "public": True})
        cog.synthesizer.delete_f5_voice = AsyncMock(return_value={"status": "deleted"})

        valid_wav = _make_wav_bytes(4.5, samplerate=24000)
        att = MagicMock()
        att.filename = "auto_voice.wav"
        att.size = len(valid_wav)
        att.read = AsyncMock(return_value=valid_wav)

        # 1. Submission without 'public' succeeds immediately (private-only), owner gets notification WITHOUT promote button
        ctx_u1 = _make_ctx(user_id=111, guild_id=500, channel_id=1001)
        ctx_u1.message = SimpleNamespace(attachments=[att])
        await cog.voiceclone_command.callback(cog, ctx_u1, _arg="我的男聲 | 大家好今天天氣真的很好喔")
        reply_text = ctx_u1.send.call_args.args[0]
        self.assertIn("立即啟用", reply_text)
        self.assertIn("僅限本人使用", reply_text)
        cog.synthesizer.register_f5_voice.assert_awaited_once()
        self.assertFalse(cog.synthesizer.register_f5_voice.call_args.kwargs["public"])
        self.assertEqual(tts_engine.list_pending_clone_requests(user_id=111), [])
        self.assertEqual(list(tts_engine.get_pending_clones_dir().glob("*.wav")), [])
        clone_rec = tts_engine.get_user_clone("u_111_s1")
        self.assertIsNotNone(clone_rec)
        self.assertFalse(clone_rec["is_public"])

        owner_dm.assert_awaited_once()
        priv_audit_view = owner_dm.call_args.kwargs["view"]
        self.assertIsInstance(priv_audit_view, CloneAuditView)
        priv_btn_ids = [getattr(item, "custom_id", "") for item in priv_audit_view.children]
        self.assertEqual(priv_btn_ids, ["vc_audit_revoke:u_111_s1"])
        self.assertIn("私人語音克隆通知", owner_dm.call_args.kwargs["embed"].title)

        # 1b. User can apply to make existing slot 1 public via `!voiceclone public 1` (or inline `!voiceclone public ...`)
        owner_dm.reset_mock()
        ctx_u1_pub = _make_ctx(user_id=111, guild_id=500, channel_id=1001)
        ctx_u1_pub.message = SimpleNamespace(attachments=[])
        await cog.voiceclone_command.callback(cog, ctx_u1_pub, _arg="public 1")
        self.assertIn("公開聲線", ctx_u1_pub.send.call_args.args[0])
        owner_dm.assert_awaited_once()
        pub_audit_view = owner_dm.call_args.kwargs["view"]
        pub_btn_ids = [getattr(item, "custom_id", "") for item in pub_audit_view.children]
        self.assertEqual(
            pub_btn_ids,
            ["vc_promote_pub:u_111_s1", "vc_keep_priv:u_111_s1", "vc_audit_revoke:u_111_s1"],
        )
        self.assertIn("申請公開語音克隆審核", owner_dm.call_args.kwargs["embed"].title)

        # Also verify inline parsing of `!voiceclone public 2 名稱 | 逐字稿` and `!voiceclone 2 公開 名稱 | 逐字稿`
        s_a, n_a, p_a, pub_a = cog._parse_clone_submission_args("public 2 角色A | 測試逐字稿內容", [], "U")
        self.assertEqual((s_a, n_a, p_a, pub_a), (2, "角色A", "測試逐字稿內容", True))
        s_b, n_b, p_b, pub_b = cog._parse_clone_submission_args("2 公開 角色B | 測試逐字稿內容", [], "U")
        self.assertEqual((s_b, n_b, p_b, pub_b), (2, "角色B", "測試逐字稿內容", True))
        s_c, n_c, p_c, pub_c = cog._parse_clone_submission_args("角色C | 測試逐字稿內容", [], "U")
        self.assertEqual((s_c, n_c, p_c, pub_c), (1, "角色C", "測試逐字稿內容", False))

        # 2. Cooldown still applies in auto mode
        ctx_u1.send.reset_mock()
        await cog.voiceclone_command.callback(cog, ctx_u1, _arg="2 第二組 | 立刻重傳會觸發冷卻")
        self.assertIn("提交過於頻繁", ctx_u1.send.call_args.args[0])

        # 3. Non-owner clicking audit buttons is denied
        bad_inter = MagicMock()
        bad_inter.type = discord.InteractionType.component
        bad_inter.user = SimpleNamespace(id=222)
        bad_inter.data = {"custom_id": "vc_promote_pub:u_111_s1"}
        bad_inter.response = AsyncMock()
        await cog._on_clone_review_interaction(bad_inter)
        self.assertIn("🔒", bad_inter.response.send_message.call_args.args[0])

        # 4. Owner clicks [🌐 升級為公開聲線] -> promotes to public
        prom_inter = MagicMock()
        prom_inter.type = discord.InteractionType.component
        prom_inter.user = SimpleNamespace(id=9000)
        prom_inter.data = {"custom_id": "vc_promote_pub:u_111_s1"}
        prom_inter.message = AsyncMock()
        prom_inter.message.embeds = [discord.Embed(title="audit")]
        prom_inter.response = AsyncMock()
        prom_inter.followup = AsyncMock()
        await cog._on_clone_review_interaction(prom_inter)
        cog.synthesizer.set_f5_voice_visibility.assert_awaited_once_with("u_111_s1", public=True)
        self.assertTrue(tts_engine.get_user_clone("u_111_s1")["is_public"])

        # 5. User selects u_111_s1, then owner clicks [🗑️ 撤銷刪除此聲線] -> deleted + pref cleared
        tts_engine.set_user_voice_pref(111, voice_id="f5:u_111_s1")
        rev_inter = MagicMock()
        rev_inter.type = discord.InteractionType.component
        rev_inter.user = SimpleNamespace(id=9000)
        rev_inter.data = {"custom_id": "vc_audit_revoke:u_111_s1"}
        rev_inter.message = AsyncMock()
        rev_inter.message.embeds = [discord.Embed(title="audit")]
        rev_inter.response = AsyncMock()
        rev_inter.followup = AsyncMock()
        await cog._on_clone_review_interaction(rev_inter)
        cog.synthesizer.delete_f5_voice.assert_awaited_once_with("u_111_s1", requester_user_id=None)
        self.assertIsNone(tts_engine.get_user_clone("u_111_s1"))
        self.assertFalse(tts_engine.get_user_voice_pref(111)["is_custom"])

    async def test_discord_error_messages_never_leak_backend_details(self):
        forbidden = (
            "127.0.0.1",
            "9880",
            "http://",
            "/register_voice",
            "HTTP 500",
            "TTS_API_URL",
            "run_tts_server",
            "DiscordVoiceCloneTTSBot",
            "voices.json",
            "dgbot_clone_",
            "Traceback",
            "C:\\Users\\secret",
            "pipe:0",
        )

        def assert_clean_and_logged(text: str, mock_logger_method, expected_log_substr: str):
            for tok in forbidden:
                self.assertNotIn(tok, text, msg=f"Discord message leaked forbidden token {tok!r}: {text}")
            self.assertTrue(
                any(expected_log_substr in " ".join(str(a) for a in call.args) for call in mock_logger_method.call_args_list),
                msg=f"Expected backend detail {expected_log_substr!r} in logger calls: {mock_logger_method.call_args_list}",
            )

        bot = MagicMock()
        bot.is_owner = AsyncMock(return_value=False)
        bot.get_user.return_value = None
        bot.get_channel.return_value = None
        logger = MagicMock()
        valid_wav = _make_wav_bytes(4.5, samplerate=24000)

        def make_att(filename="clip.wav", data=valid_wav):
            att = MagicMock()
            att.filename = filename
            att.size = len(data)
            att.read = AsyncMock(return_value=data)
            return att

        # 1. Auto-mode !voiceclone ConnectionError and RuntimeError must log details & hide from Discord
        settings = _make_settings(
            enable_user_voice_clone=True,
            tts_clone_approval_mode="auto",
            bot_owner_ids=(9000,),
            tts_api_url="http://127.0.0.1:9880",
            tts_clone_cooldown_seconds=0,
        )
        cog = TtsCog(bot, settings, logger)
        cog.synthesizer.register_f5_voice = AsyncMock(
            side_effect=ConnectionError("無法連線至本機 F5-TTS 服務 (http://127.0.0.1:9880/register_voice): Connection refused")
        )
        ctx = _make_ctx(user_id=111, guild_id=500, channel_id=1001)
        ctx.message = SimpleNamespace(attachments=[make_att()])
        await cog.voiceclone_command.callback(cog, ctx, _arg="我的聲線 | 大家好今天天氣很好")
        assert_clean_and_logged(ctx.send.call_args.args[0], logger.error, "127.0.0.1:9880/register_voice")

        logger.error.reset_mock()
        ctx.send.reset_mock()
        cog.synthesizer.register_f5_voice = AsyncMock(
            side_effect=RuntimeError("F5-TTS HTTP 500: Traceback C:\\Users\\secret/voices.json")
        )
        await cog.voiceclone_command.callback(cog, ctx, _arg="我的聲線 | 大家好今天天氣很好")
        assert_clean_and_logged(ctx.send.call_args.args[0], logger.error, "C:\\Users\\secret/voices.json")

        # 2. Manual-mode approve button ConnectionError
        logger.error.reset_mock()
        cog.settings = _make_settings(
            enable_user_voice_clone=True,
            tts_clone_approval_mode="manual",
            bot_owner_ids=(9000,),
            tts_api_url="http://127.0.0.1:9880",
            tts_clone_cooldown_seconds=0,
        )
        await cog.voiceclone_command.callback(cog, ctx, _arg="手動申請 | 大家好這是手動審核測試")
        req_id = tts_engine.list_pending_clone_requests(user_id=111)[0]["id"]
        cog.synthesizer.register_f5_voice = AsyncMock(
            side_effect=ConnectionError("http://127.0.0.1:9880/register_voice timeout")
        )
        owner_inter = MagicMock()
        owner_inter.type = discord.InteractionType.component
        owner_inter.user = SimpleNamespace(id=9000)
        owner_inter.data = {"custom_id": f"vc_approve_priv:{req_id}"}
        owner_inter.message = AsyncMock()
        owner_inter.message.embeds = [discord.Embed(title="review")]
        owner_inter.response = AsyncMock()
        owner_inter.followup = AsyncMock()
        await cog._on_clone_review_interaction(owner_inter)
        assert_clean_and_logged(owner_inter.followup.send.call_args.args[0], logger.error, "127.0.0.1:9880/register_voice")

        # 3. Unconfigured tts_api_url must not leak TTS_API_URL
        logger.warning.reset_mock()
        cog_unconf = TtsCog(
            bot,
            _make_settings(enable_user_voice_clone=True, tts_api_url=""),
            logger,
        )
        ctx.send.reset_mock()
        await cog_unconf.voiceclone_command.callback(cog_unconf, ctx, _arg="無後端測試音檔文字")
        assert_clean_and_logged(ctx.send.call_args.args[0], logger.warning, "TTS_API_URL")

        # 4. delete / revoke / CloneAuditView / VoiceSelectView.on_preview failures
        tts_engine.upsert_user_clone(
            voice_id="u_111_s1",
            user_id=111,
            slot=1,
            voice_name="測試聲音",
            prompt_text="大家好今天天氣不錯",
            is_public=False,
            approved_by=9000,
        )
        cog.synthesizer.delete_f5_voice = AsyncMock(
            side_effect=RuntimeError("F5-TTS HTTP 500: http://127.0.0.1:9880/voices/u_111_s1")
        )
        cog.synthesizer.set_f5_voice_visibility = AsyncMock(
            side_effect=RuntimeError("F5-TTS HTTP 500: http://127.0.0.1:9880/voices/u_111_s1/visibility")
        )

        logger.error.reset_mock()
        ctx_del = _make_ctx(user_id=111, guild_id=500, channel_id=1001)
        await cog.voiceclone_command.callback(cog, ctx_del, _arg="delete 1")
        assert_clean_and_logged(ctx_del.send.call_args.args[0], logger.error, "127.0.0.1:9880")

        logger.error.reset_mock()
        ctx_rev = _make_ctx(user_id=9000, guild_id=500, channel_id=1001)
        await cog.voiceclone_command.callback(cog, ctx_rev, _arg="revoke 111 1")
        assert_clean_and_logged(ctx_rev.send.call_args.args[0], logger.error, "127.0.0.1:9880")

        logger.error.reset_mock()
        audit_prom_inter = MagicMock()
        audit_prom_inter.type = discord.InteractionType.component
        audit_prom_inter.user = SimpleNamespace(id=9000)
        audit_prom_inter.data = {"custom_id": "vc_promote_pub:u_111_s1"}
        audit_prom_inter.response = AsyncMock()
        audit_prom_inter.followup = AsyncMock()
        await cog._on_clone_review_interaction(audit_prom_inter)
        assert_clean_and_logged(audit_prom_inter.followup.send.call_args.args[0], logger.error, "127.0.0.1:9880")

        logger.error.reset_mock()
        audit_rev_inter = MagicMock()
        audit_rev_inter.type = discord.InteractionType.component
        audit_rev_inter.user = SimpleNamespace(id=9000)
        audit_rev_inter.data = {"custom_id": "vc_audit_revoke:u_111_s1"}
        audit_rev_inter.response = AsyncMock()
        audit_rev_inter.followup = AsyncMock()
        await cog._on_clone_review_interaction(audit_rev_inter)
        assert_clean_and_logged(audit_rev_inter.followup.send.call_args.args[0], logger.error, "127.0.0.1:9880")

        logger.error.reset_mock()
        cog.synthesizer.synthesize = AsyncMock(side_effect=RuntimeError("http://127.0.0.1:9880 Traceback C:\\Users\\secret"))
        view = VoiceSelectView(cog, 111, tts_engine.get_user_voice_pref(111), [])
        prev_inter = MagicMock()
        prev_inter.guild = None
        prev_inter.user = SimpleNamespace(id=111, display_name="User111")
        prev_inter.response = AsyncMock()
        prev_inter.followup = AsyncMock()
        await view.on_preview(prev_inter)
        assert_clean_and_logged(prev_inter.followup.send.call_args.args[0], logger.error, "C:\\Users\\secret")

        # 5. Corrupt audio uploaded to sanitize_clone_audio_bytes via !voiceclone must not leak temp path/stderr
        ctx_corrupt = _make_ctx(user_id=222, guild_id=500, channel_id=1001)
        ctx_corrupt.message = SimpleNamespace(attachments=[make_att(filename="bad.wav", data=b"NOT_AUDIO_DATA" * 100)])
        await cog.voiceclone_command.callback(cog, ctx_corrupt, _arg="壞檔測試 | 這是一段壞掉的音檔文字")
        corrupt_reply = ctx_corrupt.send.call_args.args[0]
        self.assertIn("音檔檢查未通過", corrupt_reply)
        for tok in forbidden:
            self.assertNotIn(tok, corrupt_reply)

        # 6. Non-owner !tts status hides GPU hardware name, VRAM & private totals; owner sees them
        cog.synthesizer.fetch_f5_voices = AsyncMock(return_value=[])
        cog.synthesizer.check_f5_health = AsyncMock(
            return_value={
                "status": "online",
                "model_loaded": True,
                "device": "cuda",
                "gpu_name": "NVIDIA RTX 4090",
                "vram_allocated_gb": 2.5,
                "idle_remaining_seconds": 540,
                "public_voice_count": 3,
                "total_voice_count": 8,
            }
        )
        non_owner_ctx = _make_ctx(user_id=555, guild_id=500, channel_id=1001)
        await cog.tts_command.callback(cog, non_owner_ctx, action="status")
        non_owner_embed = non_owner_ctx.send.call_args.kwargs["embed"]
        non_owner_text = " ".join(f"{f.name} {f.value}" for f in non_owner_embed.fields)
        self.assertNotIn("RTX 4090", non_owner_text)
        self.assertNotIn("2.5 GB", non_owner_text)
        self.assertIn("3", non_owner_text)

        owner_ctx = _make_ctx(user_id=9000, guild_id=500, channel_id=1001)
        await cog.tts_command.callback(cog, owner_ctx, action="status")
        owner_embed = owner_ctx.send.call_args.kwargs["embed"]
        owner_text = " ".join(f"{f.name} {f.value}" for f in owner_embed.fields)
        self.assertIn("NVIDIA RTX 4090", owner_text)
        self.assertIn("2.5 GB", owner_text)

    async def test_edge_tts_prosody_fallback_prevents_silence(self):
        from edge_tts.exceptions import NoAudioReceived

        synth = tts_engine.HybridTtsSynthesizer(tts_api_url="", default_voice="edge:zh-TW-HsiaoChenNeural")
        calls: list[tuple[str, str, int, int]] = []
        yun_fail_mode = False

        async def fake_stream_once(text: str, short_name: str, rate_pct: int, pitch_hz: int) -> bytes:
            calls.append((text, short_name, rate_pct, pitch_hz))
            if short_name == "zh-CN-TestVoice" and pitch_hz != 0:
                raise NoAudioReceived("No audio received for non-zero pitch")
            if short_name == "zh-TW-YunJheNeural":
                if pitch_hz != 0 or rate_pct != 0:
                    raise AssertionError("zh-TW-YunJheNeural must bypass non-zero pitch/rate upfront")
                if yun_fail_mode:
                    raise NoAudioReceived("YunJhe unstable on this phrase")
            if short_name == "zh-HK-WanLungNeural":
                raise NoAudioReceived("Broken voice everywhere")
            if short_name == "zh-TW-HsiaoChenNeural" and text == "こんにちは":
                raise NoAudioReceived("Taiwanese voice cannot speak pure Kana")
            return b"OK_MP3"

        with patch.object(synth, "_stream_edge_once", side_effect=fake_stream_once):
            # 1. Dynamic prosody learning on unknown voice: fails -10Hz, falls back to (15, 0) & caches
            audio1, ext1 = await synth.synthesize_edge("測試一", "zh-CN-TestVoice", rate_pct=15, pitch_hz=-10)
            self.assertEqual((audio1, ext1), (b"OK_MP3", "mp3"))
            self.assertEqual(calls, [("測試一", "zh-CN-TestVoice", 15, -10), ("測試一", "zh-CN-TestVoice", 15, 0)])
            self.assertIn("zh-CN-TestVoice", synth._edge_no_pitch_voices)

            # Next call skips pitch adjustment upfront in a single attempt
            calls.clear()
            await synth.synthesize_edge("測試二", "zh-CN-TestVoice", rate_pct=15, pitch_hz=-12)
            self.assertEqual(calls, [("測試二", "zh-CN-TestVoice", 15, 0)])

            # 2. zh-TW-YunJheNeural with -16Hz immediately synthesizes cleanly at (0, 0) when upstream succeeds
            calls.clear()
            audio_yj, ext_yj, fb_yj = await synth.synthesize(
                "我是雲哲測試", "edge:zh-TW-YunJheNeural", rate_pct=10, pitch_hz=-16
            )
            self.assertEqual((audio_yj, ext_yj, fb_yj), (b"OK_MP3", "mp3", False))
            self.assertEqual(calls, [("我是雲哲測試", "zh-TW-YunJheNeural", 0, 0)])

            # 2b. When zh-TW-YunJheNeural fails at (0, 0), falls back to male zh-CN-YunxiNeural preserving (10, -16)
            yun_fail_mode = True
            calls.clear()
            audio_male_fb, ext_male_fb, used_male_fb = await synth.synthesize(
                "不穩測試", "edge:zh-TW-YunJheNeural", rate_pct=10, pitch_hz=-16
            )
            self.assertEqual((audio_male_fb, ext_male_fb, used_male_fb), (b"OK_MP3", "mp3", True))
            self.assertIn(("不穩測試", "zh-CN-YunxiNeural", 10, -16), calls)
            yun_fail_mode = False

            # 3. Complete failure of a non-default Edge voice (in EDGE_VOICES) falls back to default_voice at (0, 0)
            calls.clear()
            with patch("engines.tts_engine.asyncio.sleep", new=AsyncMock()):
                audio_fb, ext_fb, used_fb = await synth.synthesize(
                    "備援朗讀測試", "edge:zh-HK-WanLungNeural", rate_pct=10, pitch_hz=-10
                )
            self.assertEqual((audio_fb, ext_fb, used_fb), (b"OK_MP3", "mp3", True))
            self.assertIn(("備援朗讀測試", "zh-TW-HsiaoChenNeural", 0, 0), calls)

            # 3b. Pure Kana chunk on zh-TW-HsiaoChenNeural automatically falls back to ja-JP-NanamiNeural
            calls.clear()
            with patch("engines.tts_engine.asyncio.sleep", new=AsyncMock()):
                audio_jp, ext_jp, used_jp = await synth.synthesize("こんにちは", "edge:zh-TW-HsiaoChenNeural")
            self.assertEqual((audio_jp, ext_jp, used_jp), (b"OK_MP3", "mp3", True))
            self.assertIn(("こんにちは", "ja-JP-NanamiNeural", 0, 0), calls)

        # 4. !voice bot pitch -16 on zh-TW-YunJheNeural displays friendly non-backend reminder
        bot = MagicMock()
        bot.is_owner = AsyncMock(return_value=False)
        cog = TtsCog(bot, _make_settings(), MagicMock())
        cog.synthesizer.fetch_f5_voices = AsyncMock(return_value=[])
        tts_engine.update_guild_tts_settings(500, ai_voice_id="edge:zh-TW-YunJheNeural")
        ctx = _make_ctx(user_id=111, guild_id=500, channel_id=1001)
        await cog.botvoice_command.callback(cog, ctx, arg="pitch -16")
        reply = ctx.send.call_args.args[0]
        self.assertIn("不支援微調音高／語速", reply)
        self.assertIn("穩定男聲備援", reply)
        self.assertIn("雲健", reply)

    async def test_english_paragraph_preservation_chunking_and_fifo_order(self):
        maya = (
            "Every morning before work, Maya stops by a small corner café to pick up an iced latte. "
            "The shop smells like roasted beans and warm butter croissants. "
            "Today, she decides to sit near the window instead of taking her coffee to go. "
            "Outside, people hurry toward the train station with umbrellas open under a light drizzle. "
            "Watching the steady rhythm of the street helps her slow down and clear her mind before a busy day at the office."
        )
        # 1.clean_text_for_tts preserves ASCII punctuation and full English paragraph within max_chars=200 & f5=100
        cleaned = tts_engine.clean_text_for_tts(maya, max_chars=200)
        self.assertNotIn("，", cleaned)
        self.assertIn("work, Maya", cleaned)
        self.assertIn("busy day at the office.", cleaned)
        f5_truncated = tts_engine.truncate_for_tts(cleaned, 100)
        self.assertIn("busy day at the office.", f5_truncated)

        # 2. Language-aware chunking cleanly groups complete English sentences (<= 150) without mid-word cuts
        self.assertTrue(tts_engine.is_latin_dominant(cleaned))
        en_chunks = tts_engine.split_into_speech_chunks(cleaned, max_chunk_chars=150)
        self.assertGreaterEqual(len(en_chunks), 3)
        self.assertTrue(all(len(c) <= 150 for c in en_chunks))
        self.assertTrue(all(c.endswith(".") for c in en_chunks))
        self.assertEqual(" ".join(en_chunks), cleaned)

        # 2b. Progressive first-chunk sizing splits Chunk 0 into a short leading clause (<= 70 chars) for instant speech start
        fast_chunks = tts_engine.split_into_speech_chunks(
            cleaned, max_chunk_chars=135, first_chunk_max_chars=70
        )
        self.assertLessEqual(len(fast_chunks[0]), 70)
        self.assertEqual(fast_chunks[0], "Every morning before work,")
        self.assertEqual(" ".join(fast_chunks), cleaned)

        # 2c. Prosody-limited Edge voice (zh-TW-YunJheNeural) routes English paragraphs directly to paired male voice
        # without repeated upstream stalls or mid-paragraph voice switching
        synth = tts_engine.HybridTtsSynthesizer(
            default_voice="edge:zh-TW-HsiaoChenNeural",
            cache=tts_engine.AudioLRUCache(max_entries=1),
        )
        routed_voices: list[tuple[str, int, int]] = []

        async def track_stream(text: str, short_name: str, rate_pct: int, pitch_hz: int) -> bytes:
            routed_voices.append((short_name, rate_pct, pitch_hz))
            return b"OK_MP3"

        with patch.object(synth, "_stream_edge_once", side_effect=track_stream):
            for fc in fast_chunks:
                _, _, used_fb = await synth.synthesize(
                    fc, "edge:zh-TW-YunJheNeural", rate_pct=0, pitch_hz=-16
                )
                self.assertTrue(used_fb)
        # Every single chunk was spoken consistently by zh-CN-YunxiNeural at -16Hz in 1 attempt per chunk
        self.assertEqual(len(routed_voices), len(fast_chunks))
        self.assertTrue(all(v == ("zh-CN-YunxiNeural", 0, -16) for v in routed_voices))

        # 3. Overflow regression test: oversized English clause must still strictly respect max_chunk_chars
        overflow_txt = "Every morning before work, " + "alpha " * 25
        small_chunks = tts_engine.split_into_speech_chunks(overflow_txt.strip(), max_chunk_chars=45)
        self.assertTrue(all(len(c) <= 45 for c in small_chunks), msg=f"Chunk exceeded 45: {small_chunks}")
        # Rejoining words confirms no word was sliced mid-word
        rejoined = " ".join(small_chunks)
        for token in rejoined.replace(",", "").split():
            self.assertIn(token, ("Every", "morning", "before", "work", "alpha"))

        # 4. Concurrent handle_proxy_message calls must preserve strict FIFO enqueue order despite uneven latency
        vc_ch = SimpleNamespace(id=3001, name="語音房")
        vc = MagicMock()
        vc.channel = vc_ch
        vc.is_connected.return_value = True
        guild = MagicMock()
        guild.id = 500
        guild.voice_client = vc
        bot = MagicMock()
        bot.get_guild.return_value = guild
        cog = TtsCog(bot, _make_settings(), MagicMock())
        tts_engine.update_guild_tts_settings(500, allowed_channels=[vc_ch.id])

        enqueued_texts: list[str] = []
        fake_pipeline = MagicMock()

        def capture_enqueue(job: TtsJob) -> bool:
            enqueued_texts.append(job.text)
            return True

        fake_pipeline.enqueue.side_effect = capture_enqueue
        cog._get_or_create_pipeline = MagicMock(return_value=fake_pipeline)

        async def uneven_to_thread(fn, *args, **kwargs):
            if fn is tts_engine.get_user_voice_pref and args and args[0] == 101:
                await asyncio.sleep(0.06)  # Message 1 slower in DB lookup than Message 2
            elif fn is tts_engine.get_user_voice_pref and args and args[0] == 102:
                await asyncio.sleep(0.01)
            return fn(*args, **kwargs)

        msg1 = SimpleNamespace(
            author=SimpleNamespace(id=101, bot=False, display_name="First", voice=None),
            guild=guild,
            channel=vc_ch,
            clean_content="First message arrives first.",
        )
        msg2 = SimpleNamespace(
            author=SimpleNamespace(id=102, bot=False, display_name="Second", voice=None),
            guild=guild,
            channel=vc_ch,
            clean_content="Second message arrives second.",
        )

        with patch("cogs.tts_cog.asyncio.to_thread", side_effect=uneven_to_thread):
            await asyncio.gather(
                cog.handle_proxy_message(msg1),
                cog.handle_proxy_message(msg2),
            )
        self.assertEqual(
            enqueued_texts,
            ["First message arrives first.", "Second message arrives second."],
        )

    async def test_voiceclone_off_by_owner_persists_without_env_reload_bypass(self):
        import core.config as config_mod

        bot = MagicMock()
        bot.is_owner = AsyncMock(return_value=False)

        with tempfile.TemporaryDirectory() as env_tmp:
            env_dir = Path(env_tmp)
            env_file_name = ".env.vc_persist_test"
            (env_dir / env_file_name).write_text("ENABLE_USER_VOICE_CLONE=true\n", encoding="utf-8")

            settings = _make_settings(
                env_file=env_file_name,
                enable_user_voice_clone=True,
                bot_owner_ids=(9000,),
            )
            cog = TtsCog(bot, settings, MagicMock())
            cog.synthesizer.register_f5_voice = AsyncMock()

            owner_ctx = _make_ctx(user_id=9000, guild_id=500, channel_id=1001)
            owner_ctx.message = SimpleNamespace(attachments=[])
            user_ctx = _make_ctx(user_id=111, guild_id=500, channel_id=1001)
            user_ctx.message = SimpleNamespace(attachments=[])

            with (
                patch.object(config_mod, "BASE_DIR", env_dir),
                patch.dict(os.environ, {"ENABLE_USER_VOICE_CLONE": "true"}),
            ):
                # 1. Owner turns off !voiceclone
                await cog.voiceclone_command.callback(cog, owner_ctx, _arg="off")
                self.assertFalse(settings.enable_user_voice_clone)
                self.assertIn("已關閉", owner_ctx.send.call_args.args[0])

                # 2. Another user invokes !voiceclone (plain, list, and with attachment) -> MUST stay off
                await cog.voiceclone_command.callback(cog, user_ctx, _arg=None)
                self.assertFalse(settings.enable_user_voice_clone)
                notice_embed = user_ctx.send.call_args.kwargs["embed"]
                self.assertIn("不開放線上自助上傳或註冊", notice_embed.description)

                user_ctx.send.reset_mock()
                await cog.voiceclone_command.callback(cog, user_ctx, _arg="list")
                self.assertFalse(settings.enable_user_voice_clone)
                self.assertIn("不開放線上自助上傳或註冊", user_ctx.send.call_args.kwargs["embed"].description)

                wav_data = _make_wav_bytes(4.5, samplerate=24000)
                att = MagicMock()
                att.filename = "try_bypass.wav"
                att.size = len(wav_data)
                att.read = AsyncMock(return_value=wav_data)
                user_ctx.message = SimpleNamespace(attachments=[att])
                user_ctx.send.reset_mock()
                await cog.voiceclone_command.callback(cog, user_ctx, _arg="試圖繞過 | 大家好這是一段繞過測試")
                self.assertFalse(settings.enable_user_voice_clone)
                cog.synthesizer.register_f5_voice.assert_not_awaited()
                self.assertIn("不開放線上自助上傳或註冊", user_ctx.send.call_args.kwargs["embed"].description)

                # 3. Non-owner cannot turn it back on
                user_ctx.message = SimpleNamespace(attachments=[])
                user_ctx.send.reset_mock()
                await cog.voiceclone_command.callback(cog, user_ctx, _arg="on")
                self.assertFalse(settings.enable_user_voice_clone)
                self.assertIn("🔒", user_ctx.send.call_args.args[0])

                # 4. Owner turns it back on -> regular user sees guide embed again
                owner_ctx.send.reset_mock()
                await cog.voiceclone_command.callback(cog, owner_ctx, _arg="on")
                self.assertTrue(settings.enable_user_voice_clone)
                self.assertIn("已開放", owner_ctx.send.call_args.args[0])

                user_ctx.send.reset_mock()
                await cog.voiceclone_command.callback(cog, user_ctx, _arg=None)
                guide_embed = user_ctx.send.call_args.kwargs["embed"]
                self.assertIn("專屬槽位", guide_embed.description)


if __name__ == "__main__":
    unittest.main()
