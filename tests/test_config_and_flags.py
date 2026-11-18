"""Tests for core.config, feature flags, tool scoping, and dynamic !help embed."""

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engines import assistant_tools
from cogs.assistant_cog import build_help_overview_embed
from core.config import BotSettings, load_settings, resolve_env_file, resolve_prompt_path, truncate_embed_text
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
            "ENABLE_SAFETY_GUARDRAIL": "false",
            "DISABLE_SAFETY_SETTINGS": "false",
            "PRIMARY_MODEL": "gemini-flash-lite-latest",
            "FALLBACK_MODELS": "m1, m2",
        }
        with patch.dict(os.environ, env, clear=True):
            s = load_settings(env_file=".env.community")
            self.assertFalse(s.enable_japanese_mode)
            self.assertFalse(s.enable_flashcard_mode)
            self.assertFalse(s.enable_game_wiki)
            self.assertFalse(s.enable_safety_guardrail)
            self.assertFalse(s.disable_safety_settings)
            self.assertEqual(s.models_to_try, ["gemini-flash-lite-latest", "m1", "m2"])
            self.assertIsNone(get_safety_settings(s.disable_safety_settings))
            self.assertEqual(len(get_safety_settings(True)), 4)

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
        )
        emb = build_help_overview_embed(starter)
        field_names = [f.name for f in emb.fields]
        self.assertFalse(any("殭屍學園" in n for n in field_names))
        self.assertFalse(any("單字" in n for n in field_names))
        self.assertFalse(any("桌遊" in n for n in field_names))
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
        )
        emb_full = build_help_overview_embed(full)
        field_names_full = [f.name for f in emb_full.fields]
        self.assertTrue(any("殭屍學園" in n for n in field_names_full))
        self.assertTrue(any("單字" in n for n in field_names_full))
        self.assertTrue(any("桌遊" in n for n in field_names_full))

    def test_truncate_embed_text(self):
        long_str = "a" * 4000
        res = truncate_embed_text(long_str, max_len=100)
        self.assertTrue(len(res) < 150)
        self.assertIn("截斷", res)


if __name__ == "__main__":
    unittest.main()
