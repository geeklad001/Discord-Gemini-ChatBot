"""Tests for core.config, feature flags, tool scoping, and dynamic !help embed."""

import asyncio
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engines import assistant_tools
from cogs.assistant_cog import build_help_overview_embed
import core.config as config_mod
from core.config import (
    DEFAULT_FALLBACK_MODELS,
    DEFAULT_FINAL_FALLBACK_MODEL,
    DEFAULT_PRIMARY_MODEL,
    STRICT_FLASH_LITE_FALLBACK_MODELS,
    BotSettings,
    init_runtime_env,
    is_bot_owner,
    is_configured_owner,
    load_settings,
    resolve_env_file,
    resolve_prompt_path,
    truncate_embed_text,
)
from core.llm_client import get_safety_settings


class TestConfigAndFlags(unittest.TestCase):
    def test_resolve_env_file(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(resolve_env_file([]), ".env")
            self.assertEqual(resolve_env_file(["--profile=community"]), ".env.community")
            self.assertEqual(resolve_env_file(["--env=.env.custom"]), ".env.custom")
            self.assertEqual(resolve_prompt_path("general_prompt.txt").parent.name, "prompts")

    def test_load_settings_missing_required(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(ValueError):
                load_settings()

    def test_load_settings_invalid_channel_id(self):
        env = {
            "DISCORD_BOT_TOKEN": "tok",
            "GEMINI_API_KEY": "key",
            "TARGET_CHANNEL_ID": "not_a_number",
        }
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaises(ValueError):
                load_settings()

    def test_load_settings_flags_and_models(self):
        env = {
            "DISCORD_BOT_TOKEN": "tok",
            "GEMINI_API_KEY": "key",
            "TARGET_CHANNEL_ID": "123456",
            "ENABLE_JAPANESE_MODE": "false",
            "ENABLE_FLASHCARD_MODE": "false",
            "ENABLE_GAME_WIKI": "false",
            "ENABLE_VOICE_TTS": "false",
            "ENABLE_USER_VOICE_CLONE": "true",
            "ENABLE_SAFETY_GUARDRAIL": "false",
            "DISABLE_SAFETY_SETTINGS": "false",
            "PRIMARY_MODEL": "gemini-flash-lite-latest",
            "FALLBACK_MODELS": "m1, m2",
            "TTS_API_URL": "",
            "TTS_API_TOKEN": "top_secret_tok",
            "TTS_DEFAULT_VOICE": "edge:zh-TW-YunJheNeural",
            "TTS_JP_VOICE": "edge:ja-JP-KeitaNeural",
            "TTS_VOLUME_BOOST": "1.6",
            "TTS_F5_MAX_CHARS": "80",
            "TTS_CLONE_MAX_PER_USER": "99",
            "TTS_CLONE_MAX_TOTAL": "40",
            "TTS_CLONE_COOLDOWN_SECONDS": "300",
            "TTS_CLONE_APPROVAL_MODE": "manual",
            "BOT_OWNER_IDS": "111, 222",
        }
        with patch.dict(os.environ, env, clear=True):
            s = load_settings(env_file=".env.community")
            self.assertFalse(s.enable_japanese_mode)
            self.assertFalse(s.enable_flashcard_mode)
            self.assertFalse(s.enable_game_wiki)
            self.assertFalse(s.enable_voice_tts)
            self.assertTrue(s.enable_user_voice_clone)
            self.assertFalse(s.enable_safety_guardrail)
            self.assertFalse(s.disable_safety_settings)
            self.assertEqual(s.tts_api_url, "")
            self.assertEqual(s.tts_api_token, "top_secret_tok")
            self.assertEqual(s.tts_default_voice, "edge:zh-TW-YunJheNeural")
            self.assertEqual(s.tts_jp_voice, "edge:ja-JP-KeitaNeural")
            self.assertAlmostEqual(s.tts_volume_boost, 1.6)
            self.assertEqual(s.tts_f5_max_chars, 80)
            self.assertEqual(s.tts_clone_max_per_user, 5)  # clamped max 5
            self.assertEqual(s.tts_clone_max_total, 40)
            self.assertEqual(s.tts_clone_cooldown_seconds, 300)
            self.assertEqual(s.tts_clone_approval_mode, "manual")
            self.assertEqual(s.bot_owner_ids, (111, 222))
            self.assertEqual(s.models_to_try, ["gemini-flash-lite-latest", "m1", "m2"])
            self.assertIsNone(get_safety_settings(s.disable_safety_settings))
            self.assertEqual(len(get_safety_settings(True)), 4)

        self.assertEqual(
            BotSettings(
                env_file=".env",
                bot_instance_name="B",
                discord_bot_token="t",
                gemini_api_key="k",
                target_channel_id=1,
            ).tts_clone_approval_mode,
            "auto",
        )

    def test_tool_scoping_respects_flags(self):
        tools_all = assistant_tools.get_scoped_tools(
            enable_flashcards=True, include_rpg=True, enable_game_wiki=True, include_boardgames=True, include_lifesim=True
        )
        names_all = {fn.__name__ for fn in tools_all}
        self.assertIn("lookup_game_wiki", names_all)
        self.assertIn("add_flashcard_card", names_all)
        self.assertIn("rpg_roll_check", names_all)
        self.assertIn("boardgame_play_move", names_all)
        self.assertIn("boardgame_undo", names_all)
        self.assertIn("boardgame_stats", names_all)
        self.assertIn("lifesim_start", names_all)
        self.assertIn("lifesim_decide", names_all)
        self.assertIn("lifesim_atlas", names_all)

        tools_starter = assistant_tools.get_scoped_tools(
            enable_flashcards=False, include_rpg=False, enable_game_wiki=False, include_boardgames=False, include_lifesim=False
        )
        names_starter = {fn.__name__ for fn in tools_starter}
        self.assertNotIn("lookup_game_wiki", names_starter)
        self.assertNotIn("add_flashcard_card", names_starter)
        self.assertNotIn("rpg_roll_check", names_starter)
        self.assertNotIn("boardgame_play_move", names_starter)
        self.assertNotIn("lifesim_start", names_starter)
        self.assertEqual(names_starter, {"record_memory", "create_todo", "finish_todo", "query_todos", "set_reminder"})

    def test_help_embed_dynamic_sections(self):
        starter = BotSettings(
            env_file=".env",
            bot_instance_name="StarterBot",
            discord_bot_token="t",
            gemini_api_key="k",
            target_channel_id=1,
            enable_japanese_mode=False,
            enable_flashcard_mode=False,
            enable_game_wiki=False,
            enable_board_games=False,
            enable_voice_tts=False,
        )
        emb = build_help_overview_embed(starter)
        field_names = [f.name for f in emb.fields]
        self.assertFalse(any("殭屍學園" in n for n in field_names))
        self.assertFalse(any("單字" in n for n in field_names))
        self.assertFalse(any("桌遊" in n for n in field_names))
        self.assertFalse(any("語音朗讀" in n for n in field_names))
        self.assertTrue(any("TRPG" in n for n in field_names))

        full = BotSettings(
            env_file=".env",
            bot_instance_name="FullBot",
            discord_bot_token="t",
            gemini_api_key="k",
            target_channel_id=1,
            enable_japanese_mode=True,
            enable_flashcard_mode=True,
            enable_game_wiki=True,
            enable_board_games=True,
            enable_voice_tts=True,
        )
        emb_full = build_help_overview_embed(full)
        field_names_full = [f.name for f in emb_full.fields]
        self.assertTrue(any("殭屍學園" in n for n in field_names_full))
        self.assertTrue(any("單字" in n for n in field_names_full))
        self.assertTrue(any("桌遊" in n for n in field_names_full))
        self.assertTrue(any("語音朗讀" in n for n in field_names_full))

    def test_truncate_embed_text(self):
        long_str = "a" * 4000
        res = truncate_embed_text(long_str, max_len=100)
        self.assertTrue(len(res) < 150)
        self.assertIn("截斷", res)

    def test_bot_owner_helpers(self):
        settings = BotSettings(
            env_file=".env",
            bot_instance_name="OwnerBot",
            discord_bot_token="t",
            gemini_api_key="k",
            target_channel_id=1,
            bot_owner_ids=(124225918267817984,),
        )
        self.assertTrue(is_configured_owner(settings, 124225918267817984))
        self.assertFalse(is_configured_owner(settings, 999))

        bot = MagicMock()
        bot.is_owner = AsyncMock(side_effect=lambda u: u.id == 888)
        self.assertTrue(asyncio.run(is_bot_owner(bot, settings, SimpleNamespace(id=124225918267817984))))
        self.assertTrue(asyncio.run(is_bot_owner(bot, settings, SimpleNamespace(id=888))))
        self.assertFalse(asyncio.run(is_bot_owner(bot, settings, SimpleNamespace(id=999))))

    def test_flash_lite_only_defaults_and_final_fallback_parsing(self):
        self.assertEqual(DEFAULT_PRIMARY_MODEL, "gemini-flash-lite-latest")
        self.assertEqual(
            STRICT_FLASH_LITE_FALLBACK_MODELS,
            ["gemini-3.5-flash-lite", "gemini-3.1-flash-lite", "gemini-2.5-flash-lite"],
        )
        self.assertEqual(
            DEFAULT_FALLBACK_MODELS,
            [
                "gemini-3.5-flash-lite",
                "gemini-3.1-flash-lite",
                "gemini-2.5-flash-lite",
                "gemini-flash-latest",
                "gemini-3.8-flash",
                "gemini-4-hydrogen",
                "gemini-2.5-flash",
            ],
        )
        self.assertEqual(DEFAULT_FINAL_FALLBACK_MODEL, "gemini-flash-lite-latest")

        base_env = {
            "DISCORD_BOT_TOKEN": "tok",
            "GEMINI_API_KEY": "key",
            "TARGET_CHANNEL_ID": "123456",
        }
        # 1. Defaults when PRIMARY_MODEL / FALLBACK_MODELS / FINAL_FALLBACK_MODEL are unset
        with patch.dict(os.environ, base_env, clear=True):
            s_def = load_settings()
            self.assertEqual(s_def.primary_model, "gemini-flash-lite-latest")
            self.assertEqual(
                s_def.fallback_models,
                [
                    "gemini-3.5-flash-lite",
                    "gemini-3.1-flash-lite",
                    "gemini-2.5-flash-lite",
                    "gemini-flash-latest",
                    "gemini-3.8-flash",
                    "gemini-4-hydrogen",
                    "gemini-2.5-flash",
                ],
            )
            self.assertEqual(s_def.final_fallback_model, "gemini-flash-lite-latest")
            self.assertEqual(
                s_def.models_to_try,
                [
                    "gemini-flash-lite-latest",
                    "gemini-3.5-flash-lite",
                    "gemini-3.1-flash-lite",
                    "gemini-2.5-flash-lite",
                    "gemini-flash-latest",
                    "gemini-3.8-flash",
                    "gemini-4-hydrogen",
                    "gemini-2.5-flash",
                ],
            )

        # 2. Non-flash-lite primary still defaults FINAL_FALLBACK_MODEL to Flash-Lite
        with patch.dict(os.environ, {**base_env, "PRIMARY_MODEL": "gemini-2.5-flash"}, clear=True):
            s_non_lite = load_settings()
            self.assertEqual(s_non_lite.final_fallback_model, "gemini-flash-lite-latest")

        # 3. Custom FINAL_FALLBACK_MODEL override (stripping inline comments)
        with patch.dict(
            os.environ,
            {**base_env, "FINAL_FALLBACK_MODEL": "gemini-2.5-flash-lite # custom safety net"},
            clear=True,
        ):
            s_custom = load_settings()
            self.assertEqual(s_custom.final_fallback_model, "gemini-2.5-flash-lite")

        # 4. "none" / "off" disable FINAL_FALLBACK_MODEL
        for disable_val in ("none", "NONE", "off", "off # disabled", "false", "disable"):
            with patch.dict(os.environ, {**base_env, "FINAL_FALLBACK_MODEL": disable_val}, clear=True):
                s_off = load_settings()
                self.assertEqual(s_off.final_fallback_model, "", msg=f"Failed for {disable_val!r}")

    def test_init_runtime_env_ignores_commented_out_voice_clone_flag(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_base = Path(tmpdir)
            env_file_name = ".env.test_comment_guard"
            (tmp_base / env_file_name).write_text(
                "DISCORD_BOT_TOKEN=tok\n"
                "GEMINI_API_KEY=key\n"
                "TARGET_CHANNEL_ID=123456\n"
                "# ENABLE_USER_VOICE_CLONE=true\n"
                "   # ALLOW_USER_VOICE_CLONE=true\n",
                encoding="utf-8",
            )
            with (
                patch.object(config_mod, "BASE_DIR", tmp_base),
                patch.dict(os.environ, {}, clear=True),
            ):
                resolved = init_runtime_env(argv=[f"--env={env_file_name}"], override=True)
                self.assertEqual(resolved, env_file_name)
                self.assertNotIn("ENABLE_USER_VOICE_CLONE", os.environ)
                s = load_settings(env_file=env_file_name)
                self.assertFalse(s.enable_user_voice_clone)


if __name__ == "__main__":
    unittest.main()
