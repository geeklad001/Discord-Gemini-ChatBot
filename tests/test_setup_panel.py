"""Tests for runtime config persistence, overrides, health checks, SetupPanelView, and SetupCog."""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import httpx

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from cogs.setup_cog import SetupCog, SetupPanelView, run_environment_health_check
from core.config import (
    DEFAULT_FALLBACK_MODELS,
    DEFAULT_FINAL_FALLBACK_MODEL,
    DEFAULT_PRIMARY_MODEL,
    FLASH_LITE_PRESET_MODELS,
    MAX_AUDIO_ATTACHMENT_BYTES,
    MAX_AUDIO_DURATION_SECONDS,
    STRICT_FLASH_LITE_FALLBACK_MODELS,
    BotSettings,
    apply_runtime_overrides,
)
from core.llm_client import get_active_model_cooldowns, reset_model_cooldowns
from engines import (
    assistant_db,
    boardgame_engine,
    flashcard_engine,
    lifesim_engine,
    tts_engine,
)


def _make_settings(**overrides) -> BotSettings:
    defaults = dict(
        env_file=".env.test_missing",
        bot_instance_name="SetupTestBot",
        discord_bot_token="tok",
        gemini_api_key="secret_gemini_key",
        target_channel_id=1001,
        tts_channel_id=1002,
        general_channel_id=1003,
        enable_japanese_mode=True,
        enable_flashcard_mode=True,
        enable_game_wiki=True,
        enable_board_games=True,
        enable_voice_tts=True,
        enable_safety_guardrail=True,
        tts_api_url="http://127.0.0.1:9880",
        tts_api_token="secret_tts_tok",
        bot_owner_ids=(9000,),
    )
    defaults.update(overrides)
    return BotSettings(**defaults)


def _assert_discord_view_limits(testcase: unittest.TestCase, view: discord.ui.View, tab_name: str) -> None:
    testcase.assertLessEqual(len(view.children), 25, f"{tab_name}: exceeds 25 components")
    rows: dict[int, list[discord.ui.Item]] = {}
    for child in view.children:
        testcase.assertIsNotNone(child.row, f"{tab_name}: item {child!r} missing row")
        testcase.assertIn(child.row, (0, 1, 2, 3, 4), f"{tab_name}: invalid row {child.row}")
        rows.setdefault(child.row, []).append(child)

    for row_idx, items in rows.items():
        testcase.assertLessEqual(len(items), 5, f"{tab_name} row {row_idx}: >5 items")
        total_width = sum(getattr(it, "width", 1) for it in items)
        testcase.assertLessEqual(total_width, 5, f"{tab_name} row {row_idx}: width {total_width} > 5")
        has_select = any(isinstance(it, (discord.ui.Select, discord.ui.ChannelSelect)) for it in items)
        if has_select:
            testcase.assertEqual(len(items), 1, f"{tab_name} row {row_idx}: Select must be alone on its row")

    payload = view.to_components()
    testcase.assertLessEqual(len(payload), 5, f"{tab_name}: action rows > 5")


class TestRuntimeConfigDBAndOverrides(unittest.TestCase):
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
        assistant_db._initialized_dbs.clear()
        self.env_patch = patch.dict(os.environ, {"DB_NAME": "test_setup_panel.db"})
        self.env_patch.start()
        assistant_db.init_db()

    def tearDown(self):
        self.env_patch.stop()
        assistant_db.DB_DIR = self.orig_db_dir
        assistant_db.DATA_DIR = self.orig_data_dir
        assistant_db.IMPORTS_DIR = self.orig_imports_dir
        assistant_db.MEDIA_DIR = self.orig_media_dir
        assistant_db._initialized_dbs.clear()
        self.tmpdir.cleanup()

    def test_allowed_keys_rejects_secrets_and_unknown_keys(self):
        forbidden = (
            "discord_bot_token",
            "gemini_api_key",
            "tts_api_token",
            "bot_owner_ids",
            "env_file",
            "bot_instance_name",
            "arbitrary_unknown_key",
            "",
        )
        for bad_key in forbidden:
            self.assertNotIn(bad_key, assistant_db.RUNTIME_CONFIG_ALLOWED_KEYS)
            with self.assertRaises(ValueError, msg=f"Expected ValueError for key {bad_key!r}"):
                assistant_db.set_runtime_config_override(bad_key, "evil")

    def test_runtime_config_db_roundtrip_and_clear(self):
        self.assertEqual(assistant_db.get_runtime_config_overrides(), {})

        assistant_db.set_runtime_config_override("enable_japanese_mode", "false")
        assistant_db.set_runtime_config_override("target_channel_id", "888888")
        assistant_db.set_runtime_config_override("target_channel_id", "999999")
        loaded = assistant_db.get_runtime_config_overrides()
        self.assertEqual(
            loaded,
            {
                "enable_japanese_mode": "false",
                "target_channel_id": "999999",
            },
        )

        # Delete single key with value=None
        assistant_db.set_runtime_config_override("enable_japanese_mode", None)
        self.assertEqual(assistant_db.get_runtime_config_overrides(), {"target_channel_id": "999999"})

        # Clear all overrides
        assistant_db.set_runtime_config_override("primary_model", "gemini-2.5-flash-lite")
        deleted_cnt = assistant_db.clear_runtime_config_overrides()
        self.assertEqual(deleted_cnt, 2)
        self.assertEqual(assistant_db.get_runtime_config_overrides(), {})

    def test_apply_runtime_overrides_and_secret_protection(self):
        self.assertEqual(MAX_AUDIO_ATTACHMENT_BYTES, 3 * 1024 * 1024)
        self.assertEqual(MAX_AUDIO_DURATION_SECONDS, 30)
        self.assertEqual(FLASH_LITE_PRESET_MODELS[0], DEFAULT_PRIMARY_MODEL)

        s = _make_settings()
        applied = apply_runtime_overrides(
            s,
            {
                "enable_japanese_mode": "false",
                "enable_voice_tts": "0",
                "tts_ai_auto_read_general": "true",
                "target_channel_id": "222222",
                "tts_channel_id": "",
                "general_channel_id": "333333",
                "primary_model": "gemini-3.5-flash-lite",
                "fallback_models": "gemini-3.1-flash-lite, , gemini-2.5-flash-lite ",
                "final_fallback_model": "off",
                # Secrets that MUST be ignored
                "discord_bot_token": "hijacked",
                "gemini_api_key": "hijacked",
                "tts_api_token": "hijacked",
                "bot_owner_ids": "1,2,3",
                "env_file": ".env.hacked",
                "bot_instance_name": "HackedBot",
            },
        )

        self.assertFalse(s.enable_japanese_mode)
        self.assertFalse(s.enable_voice_tts)
        self.assertTrue(s.tts_ai_auto_read_general)
        self.assertEqual(s.target_channel_id, 222222)
        self.assertIsNone(s.tts_channel_id)
        self.assertEqual(s.general_channel_id, 333333)
        self.assertEqual(s.primary_model, "gemini-3.5-flash-lite")
        self.assertEqual(s.fallback_models, ["gemini-3.1-flash-lite", "gemini-2.5-flash-lite"])
        self.assertEqual(s.final_fallback_model, "")
        self.assertIn("target_channel_id", applied)
        self.assertIn("tts_channel_id", applied)
        self.assertNotIn("discord_bot_token", applied)
        self.assertNotIn("gemini_api_key", applied)
        self.assertEqual(s.discord_bot_token, "tok")
        self.assertEqual(s.gemini_api_key, "secret_gemini_key")
        self.assertEqual(s.tts_api_token, "secret_tts_tok")
        self.assertEqual(s.bot_owner_ids, (9000,))
        self.assertEqual(s.env_file, ".env.test_missing")
        self.assertEqual(s.bot_instance_name, "SetupTestBot")

        # Invalid target_channel_id values must be rejected
        for bad_target in ("0", "-10", "not_int", ""):
            res = apply_runtime_overrides(s, {"target_channel_id": bad_target})
            self.assertEqual(res, [])
            self.assertEqual(s.target_channel_id, 222222)


class TestHealthCheckAndSetupPanel(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
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
        boardgame_engine._initialized_bg_dbs.clear()
        lifesim_engine._initialized_ls_dbs.clear()
        flashcard_engine._initialized_dbs.clear()
        tts_engine._initialized_tts_dbs.clear()
        self.env_patch = patch.dict(os.environ, {"DB_NAME": "test_setup_async.db"})
        self.env_patch.start()
        assistant_db.init_db()

    async def asyncTearDown(self):
        reset_model_cooldowns()
        self.env_patch.stop()
        assistant_db.DB_DIR = self.orig_db_dir
        assistant_db.DATA_DIR = self.orig_data_dir
        assistant_db.IMPORTS_DIR = self.orig_imports_dir
        assistant_db.MEDIA_DIR = self.orig_media_dir
        flashcard_engine.IMPORTS_DIR = self.orig_imports_dir
        flashcard_engine.MEDIA_DIR = self.orig_media_dir
        assistant_db._initialized_dbs.clear()
        boardgame_engine._initialized_bg_dbs.clear()
        lifesim_engine._initialized_ls_dbs.clear()
        flashcard_engine._initialized_dbs.clear()
        tts_engine._initialized_tts_dbs.clear()
        self.tmpdir.cleanup()

    @staticmethod
    def _make_mock_channel(cid: int, name: str, *, embed_links: bool = True, create_threads: bool = True):
        perms = SimpleNamespace(
            send_messages=True,
            embed_links=embed_links,
            attach_files=True,
            read_message_history=True,
            create_public_threads=create_threads,
        )
        return SimpleNamespace(id=cid, name=name, permissions_for=lambda _member: perms)

    async def test_health_check_detects_missing_ffmpeg_and_discord_perms(self):
        settings = _make_settings(tts_channel_id=None, general_channel_id=None, enable_voice_tts=True)
        ch_missing_perm = self._make_mock_channel(1001, "chat", embed_links=False, create_threads=False)
        vc_no_speak = SimpleNamespace(
            permissions_for=lambda _m: SimpleNamespace(connect=True, speak=False)
        )
        guild = SimpleNamespace(
            me=SimpleNamespace(guild_permissions=SimpleNamespace(connect=True, speak=False)),
            voice_channels=[vc_no_speak],
        )
        bot = MagicMock()
        bot.get_channel.side_effect = lambda cid: ch_missing_perm if cid == 1001 else None

        with (
            patch("cogs.setup_cog.shutil.which", return_value=None),
            patch("cogs.setup_cog.httpx.get", side_effect=httpx.ConnectError("refused")),
        ):
            results = await run_environment_health_check(
                bot,
                settings,
                gemini_client=None,
                guild=guild,
                channel=ch_missing_perm,
                include_api_ping=False,
            )

        by_name = {r["name"]: r for r in results}
        self.assertFalse(by_name["FFmpeg 語音轉檔工具"]["ok"])
        self.assertIn("!setup", by_name["FFmpeg 語音轉檔工具"]["fix_hint"])
        self.assertFalse(by_name["Discord 頻道文字與討論串權限"]["ok"])
        self.assertIn("嵌入連結", by_name["Discord 頻道文字與討論串權限"]["detail"])
        self.assertIn("建立公開討論串", by_name["Discord 頻道文字與討論串權限"]["detail"])
        self.assertFalse(by_name["Discord 語音頻道權限"]["ok"])
        self.assertIn("說話", by_name["Discord 語音頻道權限"]["detail"])
        # F5-TTS offline still leaves ok=True and clarifies Edge TTS works
        self.assertTrue(by_name["本地 F5-TTS 語音克隆服務"]["ok"])
        self.assertIn("Edge TTS 仍可正常運作", by_name["本地 F5-TTS 語音克隆服務"]["detail"])

        # Missing channel ID (bot.get_channel returns None)
        bot.get_channel.side_effect = None
        bot.get_channel.return_value = None
        with (
            patch("cogs.setup_cog.shutil.which", return_value="/usr/bin/ffmpeg"),
            patch("cogs.setup_cog.httpx.get", side_effect=httpx.ConnectError("refused")),
        ):
            missing_res = await run_environment_health_check(
                bot,
                settings,
                guild=guild,
                channel=None,
                include_api_ping=False,
            )
        ch_check = {r["name"]: r for r in missing_res}["Discord 頻道文字與討論串權限"]
        self.assertFalse(ch_check["ok"])
        self.assertIn("頻道綁定", ch_check["fix_hint"])

    async def test_health_check_working_bot_with_api_ping(self):
        settings = _make_settings()
        channels = {
            1001: self._make_mock_channel(1001, "jp-chat"),
            1002: self._make_mock_channel(1002, "tts-log"),
            1003: self._make_mock_channel(1003, "general"),
        }
        vc_ok = SimpleNamespace(permissions_for=lambda _m: SimpleNamespace(connect=True, speak=True))
        guild = SimpleNamespace(
            me=SimpleNamespace(guild_permissions=SimpleNamespace(connect=True, speak=True)),
            voice_channels=[vc_ok],
        )
        bot = MagicMock()
        bot.get_channel.side_effect = lambda cid: channels.get(cid)
        fake_client = MagicMock()

        with (
            patch("cogs.setup_cog.shutil.which", return_value="C:\\ffmpeg\\ffmpeg.exe"),
            patch("cogs.setup_cog.httpx.get", return_value=SimpleNamespace(status_code=200)),
            patch(
                "cogs.setup_cog.generate_with_fallback",
                new=AsyncMock(return_value=(SimpleNamespace(text="OK"), "gemini-flash-lite-latest")),
            ),
        ):
            results = await run_environment_health_check(
                bot,
                settings,
                gemini_client=fake_client,
                guild=guild,
                channel=channels[1001],
                include_api_ping=True,
            )

        self.assertTrue(all(r["ok"] for r in results), msg=f"Unexpected failures: {results}")
        by_name = {r["name"]: r for r in results}
        self.assertIn("gemini-flash-lite-latest", by_name["Gemini Flash-Lite API 連線"]["detail"])
        self.assertIn("test_setup_async.db", by_name["SQLite 動態設定狀態"]["detail"])

    async def test_setup_panel_view_all_tabs_and_callbacks(self):
        settings = _make_settings()
        bot = MagicMock()
        bot.is_owner = AsyncMock(return_value=False)
        loaded_cogs: dict[str, object] = {
            "BoardGameCog": object(),
            "LifeSimCog": object(),
            "FlashcardCog": object(),
            "WikiCog": object(),
            "TtsCog": object(),
            "RpgCog": object(),
        }
        bot.get_cog.side_effect = lambda name: loaded_cogs.get(name)
        bot.add_cog = AsyncMock(side_effect=lambda c: loaded_cogs.__setitem__(c.__class__.__name__, c))
        bot.remove_cog = AsyncMock(side_effect=lambda name: loaded_cogs.pop(name, None))

        cog = SetupCog(bot, settings, MagicMock(), gemini_client=None)

        # Verify all 4 tabs obey Discord component limits
        for tab in ("features", "channels", "models", "health"):
            view = SetupPanelView(cog, active_tab=tab)
            _assert_discord_view_limits(self, view, tab)
            embed = view.build_embed()
            self.assertLessEqual(len(embed.description or ""), 4096)

        # Verify owner-only interaction_check
        view = SetupPanelView(cog, active_tab="features")
        non_owner_inter = MagicMock()
        non_owner_inter.user = SimpleNamespace(id=12345)
        non_owner_inter.response = MagicMock()
        non_owner_inter.response.is_done.return_value = False
        non_owner_inter.response.send_message = AsyncMock()
        self.assertFalse(await view.interaction_check(non_owner_inter))
        non_owner_inter.response.send_message.assert_awaited_once()

        owner_inter = MagicMock()
        owner_inter.user = SimpleNamespace(id=9000)
        owner_inter.response = MagicMock()
        owner_inter.response.edit_message = AsyncMock()
        self.assertTrue(await view.interaction_check(owner_inter))

        # Click Wiki toggle button (row 1, 3rd toggle button)
        wiki_btn = [c for c in view.children if isinstance(c, discord.ui.Button) and "學園百科" in (c.label or "")][0]
        await wiki_btn.callback(owner_inter)
        self.assertFalse(settings.enable_game_wiki)
        self.assertEqual(assistant_db.get_runtime_config_overrides()["enable_game_wiki"], "false")
        self.assertNotIn("WikiCog", loaded_cogs)

        # Toggle back on -> WikiCog re-added
        wiki_btn_on = [c for c in view.children if isinstance(c, discord.ui.Button) and "學園百科" in (c.label or "")][0]
        await wiki_btn_on.callback(owner_inter)
        self.assertTrue(settings.enable_game_wiki)
        self.assertIn("WikiCog", loaded_cogs)

        # Channels tab: select target channel & clear tts_channel_id
        ch_view = SetupPanelView(cog, active_tab="channels")
        target_select = [c for c in ch_view.children if isinstance(c, discord.ui.ChannelSelect) and c.row == 1][0]
        with patch.object(discord.ui.ChannelSelect, "values", new=[SimpleNamespace(id=777888)]):
            await target_select.callback(owner_inter)
        self.assertEqual(settings.target_channel_id, 777888)
        self.assertEqual(assistant_db.get_runtime_config_overrides()["target_channel_id"], "777888")

        clear_tts = [c for c in ch_view.children if isinstance(c, discord.ui.Button) and "清除朗讀頻道" in (c.label or "")][0]
        await clear_tts.callback(owner_inter)
        self.assertIsNone(settings.tts_channel_id)
        self.assertEqual(assistant_db.get_runtime_config_overrides()["tts_channel_id"], "")

        # Models tab: model select, cooldown clear, reset model chain & clear all overrides
        m_view = SetupPanelView(cog, active_tab="models")
        model_select = [c for c in m_view.children if isinstance(c, discord.ui.Select)][0]
        with patch.object(discord.ui.Select, "values", new=["gemini-2.5-flash-lite"]):
            await model_select.callback(owner_inter)
        self.assertEqual(settings.primary_model, "gemini-2.5-flash-lite")
        self.assertEqual(assistant_db.get_runtime_config_overrides()["primary_model"], "gemini-2.5-flash-lite")

        clear_cd_btn = [c for c in m_view.children if isinstance(c, discord.ui.Button) and "冷卻" in (c.label or "")][0]
        await clear_cd_btn.callback(owner_inter)
        self.assertEqual(get_active_model_cooldowns(), {})

        reset_btn = [c for c in m_view.children if isinstance(c, discord.ui.Button) and "重置預設" in (c.label or "")][0]
        await reset_btn.callback(owner_inter)
        self.assertEqual(settings.primary_model, DEFAULT_PRIMARY_MODEL)
        self.assertEqual(settings.fallback_models, DEFAULT_FALLBACK_MODELS)
        self.assertEqual(settings.final_fallback_model, DEFAULT_FINAL_FALLBACK_MODEL)

        strict_btn = [c for c in m_view.children if isinstance(c, discord.ui.Button) and "純 Flash-Lite" in (c.label or "")][0]
        await strict_btn.callback(owner_inter)
        self.assertEqual(settings.primary_model, DEFAULT_PRIMARY_MODEL)
        self.assertEqual(settings.fallback_models, STRICT_FLASH_LITE_FALLBACK_MODELS)
        self.assertEqual(settings.final_fallback_model, DEFAULT_FINAL_FALLBACK_MODEL)

        clear_all_btn = [c for c in m_view.children if isinstance(c, discord.ui.Button) and "清除全部" in (c.label or "")][0]
        await clear_all_btn.callback(owner_inter)
        self.assertEqual(assistant_db.get_runtime_config_overrides(), {})

        # Health tab: full health button runs defer + edit_original_response
        h_view = SetupPanelView(cog, active_tab="health")
        health_inter = MagicMock()
        health_inter.user = SimpleNamespace(id=9000)
        health_inter.guild = SimpleNamespace(
            me=SimpleNamespace(guild_permissions=SimpleNamespace(connect=True, speak=True)),
            voice_channels=[],
        )
        health_inter.channel = self._make_mock_channel(1001, "jp-chat")
        health_inter.response = MagicMock()
        health_inter.response.defer = AsyncMock()
        health_inter.edit_original_response = AsyncMock()
        run_health_btn = [c for c in h_view.children if isinstance(c, discord.ui.Button) and "完整健檢" in (c.label or "")][0]
        with (
            patch("cogs.setup_cog.shutil.which", return_value="ffmpeg"),
            patch("cogs.setup_cog.httpx.get", side_effect=httpx.ConnectError("refused")),
        ):
            await run_health_btn.callback(health_inter)
        health_inter.response.defer.assert_awaited_once()
        health_inter.edit_original_response.assert_awaited_once()
        self.assertIsNotNone(h_view.health_results)

    async def test_setup_command_and_sync_feature_cogs(self):
        settings = _make_settings(
            enable_board_games=False,
            enable_flashcard_mode=True,
            enable_game_wiki=True,
            enable_voice_tts=False,
            final_fallback_model="gemini-flash-lite-latest",
        )
        bot = MagicMock()
        bot.is_owner = AsyncMock(return_value=False)
        loaded_cogs: dict[str, object] = {
            "BoardGameCog": object(),
            "LifeSimCog": object(),
            "TtsCog": object(),
            "RpgCog": object(),
        }
        bot.get_cog.side_effect = lambda name: loaded_cogs.get(name)
        bot.add_cog = AsyncMock(side_effect=lambda c: loaded_cogs.__setitem__(c.__class__.__name__, c))
        bot.remove_cog = AsyncMock(side_effect=lambda name: loaded_cogs.pop(name, None))

        cog = SetupCog(bot, settings, MagicMock(), gemini_client=None)
        changes = await cog.sync_feature_cogs()
        self.assertEqual(
            set(changes),
            {"-BoardGameCog", "-LifeSimCog", "+FlashcardCog", "+WikiCog", "-TtsCog"},
        )
        self.assertIn("RpgCog", loaded_cogs)
        self.assertEqual(settings.final_fallback_model, "gemini-flash-lite-latest")

        # Non-owner rejected in target channel
        non_owner_ctx = MagicMock()
        non_owner_ctx.author = SimpleNamespace(id=555)
        non_owner_ctx.channel = SimpleNamespace(id=1001)
        non_owner_ctx.send = AsyncMock()
        await cog.setup_command.callback(cog, non_owner_ctx)
        self.assertIn("🔒", non_owner_ctx.send.call_args.args[0])

        # Owner !setup check opens health tab with API ping
        owner_ctx = MagicMock()
        owner_ctx.author = SimpleNamespace(id=9000)
        owner_ctx.channel = self._make_mock_channel(1001, "jp-chat")
        owner_ctx.guild = SimpleNamespace(
            me=SimpleNamespace(guild_permissions=SimpleNamespace(connect=True, speak=True)),
            voice_channels=[],
        )
        owner_ctx.typing = MagicMock(return_value=None)
        sent_msg = MagicMock()
        owner_ctx.send = AsyncMock(return_value=sent_msg)

        bot.get_channel.side_effect = lambda cid: owner_ctx.channel if cid == 1001 else None
        with patch("cogs.setup_cog.shutil.which", return_value="ffmpeg"):
            await cog.setup_command.callback(cog, owner_ctx, action="check")
        sent_view = owner_ctx.send.call_args.kwargs["view"]
        self.assertIsInstance(sent_view, SetupPanelView)
        self.assertEqual(sent_view.active_tab, "health")
        self.assertIs(sent_view.message, sent_msg)


if __name__ == "__main__":
    unittest.main()
