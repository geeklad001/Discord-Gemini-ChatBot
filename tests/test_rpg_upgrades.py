"""Unit and async tests for TRPG Interactive Dice-Check Buttons and PIL Visual Cards."""

import asyncio
import io
import os
import sys
import tempfile
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

from cogs.rpg_cog import RpgCog
from cogs.rpg_views import RpgRollCheckView
from core.config import BotSettings
from engines import assistant_db, rpg_engine, rpg_renderer


def _make_settings(**overrides) -> BotSettings:
    defaults = dict(
        env_file=".env",
        bot_instance_name="TestRPG",
        discord_bot_token="tok",
        gemini_api_key="key",
        target_channel_id=1001,
        tts_channel_id=None,
        general_channel_id=1003,
        enable_japanese_mode=False,
        enable_flashcard_mode=False,
        enable_game_wiki=False,
        enable_board_games=False,
        enable_voice_tts=False,
        enable_safety_guardrail=True,
        disable_safety_settings=True,
    )
    defaults.update(overrides)
    return BotSettings(**defaults)


class RpgUpgradesEngineAndRendererTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.orig_db_dir = assistant_db.DB_DIR
        self.orig_data_dir = assistant_db.DATA_DIR
        assistant_db.DB_DIR = Path(self.tmpdir.name)
        assistant_db.DATA_DIR = Path(self.tmpdir.name)
        assistant_db._initialized_dbs.clear()
        rpg_engine._initialized_rpg_dbs.clear()
        self.env_patch = patch.dict(os.environ, {"DB_NAME": f"test_rpg_upg_{id(self)}.db", "ENABLE_JAPANESE_MODE": "false"})
        self.env_patch.start()
        assistant_db.init_db()
        rpg_engine.init_rpg_tables()

    def tearDown(self):
        self.env_patch.stop()
        assistant_db.DB_DIR = self.orig_db_dir
        assistant_db.DATA_DIR = self.orig_data_dir
        assistant_db._initialized_dbs.clear()
        rpg_engine._initialized_rpg_dbs.clear()
        try:
            self.tmpdir.cleanup()
        except OSError:
            pass

    def test_compute_rpg_stat_bonus_bilingual_and_edge_cases(self):
        self.assertEqual(rpg_engine.compute_rpg_stat_bonus(None, "DEX"), (0, ""))
        char = {
            "profile": {
                "stats": {"STR": 16, "DEX": 14, "CON": 12, "INT": 8, "WIS": 4, "CHA": 10},
                "skills": ["潛行", "聖光斬", "說服"],
                "custom_traits": {"理智值 (SAN)": "65"},
            }
        }
        self.assertEqual(rpg_engine.compute_rpg_stat_bonus(char, ""), (0, ""))
        self.assertEqual(rpg_engine.compute_rpg_stat_bonus(char, "力量"), (3, "STR +3"))
        self.assertEqual(rpg_engine.compute_rpg_stat_bonus(char, "STR"), (3, "STR +3"))
        self.assertEqual(rpg_engine.compute_rpg_stat_bonus(char, "敏捷/潛行"), (4, "DEX +2 + 熟練 +2"))
        self.assertEqual(rpg_engine.compute_rpg_stat_bonus(char, "智力"), (-1, "INT -1"))
        self.assertEqual(rpg_engine.compute_rpg_stat_bonus(char, "感知"), (4, "WIS +4"))
        self.assertEqual(rpg_engine.compute_rpg_stat_bonus(char, "魅力 (說服)"), (2, "熟練 +2"))
        self.assertEqual(rpg_engine.compute_rpg_stat_bonus(char, "聖光"), (2, "熟練 +2"))
        # 'Intimidation' must not falsely trigger 'INT' modifier
        self.assertEqual(rpg_engine.compute_rpg_stat_bonus(char, "Intimidation"), (0, ""))
        # CoC SAN check does not add D&D modifiers
        self.assertEqual(rpg_engine.compute_rpg_stat_bonus(char, "SAN 理智檢定"), (0, ""))

    def test_execute_interactive_check_d20_and_d100(self):
        rpg_engine.update_rpg_character(
            channel_id=701,
            user_id=101,
            user_name="Alice",
            char_name="艾莉絲",
            char_class="遊俠",
            profile={"stats": {"DEX": 16}, "skills": ["潛行"], "ac": 15},
        )
        req_dex = {"check_name": "潛行過哨兵", "dc": 15, "stat_or_skill": "敏捷/潛行", "dice_expr": "1d20", "reason": "避開巡邏"}

        with patch("engines.rpg_engine.roll_dice", return_value=(11, [11], "11")):
            res = rpg_engine.execute_interactive_check(701, 101, "Alice", req_dex)
        self.assertEqual(res["bonus"], 5)
        self.assertEqual(res["total"], 16)
        self.assertTrue(res["passed"])
        self.assertFalse(res["is_crit_success"])
        self.assertIn("DEX +3 + 熟練 +2", res["bonus_label"])

        # Nat 20 auto-pass even against impossible DC 30
        with patch("engines.rpg_engine.roll_dice", return_value=(20, [20], "20")):
            res_crit = rpg_engine.execute_interactive_check(701, 101, "Alice", {**req_dex, "dc": 30})
        self.assertTrue(res_crit["is_crit_success"])
        self.assertTrue(res_crit["passed"])
        self.assertIn("大成功", res_crit["summary_text"])

        # Nat 1 auto-fail even against DC 2
        with patch("engines.rpg_engine.roll_dice", return_value=(1, [1], "1")):
            res_fumble = rpg_engine.execute_interactive_check(701, 101, "Alice", {**req_dex, "dc": 2})
        self.assertTrue(res_fumble["is_crit_fail"])
        self.assertFalse(res_fumble["passed"])
        self.assertIn("大失敗", res_fumble["summary_text"])

        # CoC 1d100 roll-under ignores D&D bonus
        req_coc = {"check_name": "SAN 理智檢定", "dc": 60, "stat_or_skill": "SAN", "dice_expr": "1d100"}
        with patch("engines.rpg_engine.roll_dice", return_value=(4, [4], "4")):
            res_coc_crit = rpg_engine.execute_interactive_check(701, 101, "Alice", req_coc)
        self.assertTrue(res_coc_crit["is_roll_under"])
        self.assertEqual(res_coc_crit["bonus"], 0)
        self.assertEqual(res_coc_crit["total"], 4)
        self.assertTrue(res_coc_crit["passed"])
        self.assertTrue(res_coc_crit["is_crit_success"])

        with patch("engines.rpg_engine.roll_dice", return_value=(97, [97], "97")):
            res_coc_fumble = rpg_engine.execute_interactive_check(701, 101, "Alice", req_coc)
        self.assertFalse(res_coc_fumble["passed"])
        self.assertTrue(res_coc_fumble["is_crit_fail"])

        # Non-member fallback must not auto-create DB row
        with patch("engines.rpg_engine.roll_dice", side_effect=[ValueError("bad"), (14, [14], "14")]):
            res_guest = rpg_engine.execute_interactive_check(701, 999, "Guest", {"check_name": "觀察", "dc": 12, "dice_expr": "2d6"})
        self.assertIsNone(rpg_engine.get_rpg_character(701, 999))
        self.assertEqual(res_guest["char_name"], "Guest")
        self.assertEqual(res_guest["dice_expr"], "1d20")
        self.assertTrue(res_guest["passed"])

    def test_rpg_request_check_queue_cap_and_validation(self):
        self.assertIn(rpg_engine.rpg_request_check, rpg_engine.RPG_TOOLS)
        queue: list[dict] = []
        tok = rpg_engine.pending_rpg_check_requests.set(queue)
        try:
            r1 = rpg_engine.rpg_request_check("撬鎖", dc=999, stat_or_skill="DEX", dice_expr="bad_expr", reason="生鏽鐵門")
            r2 = rpg_engine.rpg_request_check("威嚇守衛", dc=-5, stat_or_skill="CHA", dice_expr="1d20")
            r3 = rpg_engine.rpg_request_check("理智檢定", dc=65, stat_or_skill="SAN", dice_expr="1d100")
            r4 = rpg_engine.rpg_request_check("第四個檢定", dc=12)
            self.assertIn("按鈕", r1)
            self.assertIn("上限", r4)
            self.assertEqual(len(queue), 3)
            self.assertEqual(queue[0]["dc"], 100)
            self.assertEqual(queue[0]["dice_expr"], "1d20")
            self.assertEqual(queue[1]["dc"], 1)
            self.assertEqual(queue[2]["dice_expr"], "1d100")
            self.assertEqual(r2.count("DC 1"), 1)
            self.assertIn("DC 65", r3)
        finally:
            rpg_engine.pending_rpg_check_requests.reset(tok)

    def test_rpg_renderer_themes_and_png_outputs(self):
        chars = [
            {
                "char_name": "玄冥劍尊",
                "user_name": "Tester1",
                "char_class": "金丹期劍修",
                "hp": 85,
                "max_hp": 100,
                "location": "青雲宗藏經閣",
                "current_quest": "尋找上古劍陣圖",
                "inventory": ["青冥飛劍", "聚靈丹x3", "護心玉佩", "傳音符"],
                "profile": {
                    "race": "極品雷靈根",
                    "alignment": "青雲宗",
                    "custom_traits": {"境界": "金丹中期", "靈根": "雷系單靈根", "劍意": "八階劍心"},
                    "skills": ["御劍術", "九霄神雷", "御風決", "靈識探查"],
                    "backstory": "自幼拜入青雲宗，苦修劍道百年。",
                },
            },
            {
                "char_name": "V-09",
                "user_name": "Tester2",
                "char_class": "賽博駭客 Netrunner",
                "hp": 42,
                "max_hp": 100,
                "location": "夜之城下層區",
                "current_quest": "入侵荒坂資料塔",
                "inventory": ["軍用神經接入倉", "單分子線", "EMP手雷"],
                "profile": {
                    "ac": 14,
                    "custom_traits": {"義體負載": "68%", "駭客等級": "Tier 4"},
                    "skills": ["快速破解", "霓虹潛行"],
                },
            },
            {
                "char_name": "愛德華",
                "user_name": "Tester3",
                "char_class": "民俗學調查員",
                "hp": 12,
                "max_hp": 60,
                "location": "阿卡姆圖書館",
                "current_quest": "解讀死靈之書殘頁",
                "inventory": ["左輪手槍", "筆記本", "手電筒"],
                "profile": {
                    "custom_traits": {"理智值 (SAN)": "52/80", "克蘇魯神話": "12%"},
                    "skills": ["圖書館使用", "神秘學", "說服"],
                },
            },
            {
                "char_name": "亞瑟",
                "user_name": "Tester4",
                "char_class": "聖騎士",
                "hp": 120,
                "max_hp": 120,
                "location": "白銀城門口",
                "current_quest": "護送商隊穿越黑森林",
                "inventory": ["聖劍", "塔盾", "治療藥水x2"],
                "profile": {
                    "race": "人類",
                    "alignment": "守序善良",
                    "ac": 18,
                    "stats": {"STR": 18, "DEX": 10, "CON": 16, "INT": 10, "WIS": 14, "CHA": 16},
                    "skills": ["聖光斬", "神聖感應", "重甲專精"],
                },
            },
        ]

        expected_ids = ["xianxia", "cyberpunk", "coc", "fantasy"]
        for c, exp_id in zip(chars, expected_ids):
            theme = rpg_renderer.detect_rpg_theme(c)
            self.assertEqual(theme["id"], exp_id)
            png_bytes = rpg_renderer.render_character_sheet_png(c)
            self.assertTrue(png_bytes.startswith(b"\x89PNG\r\n\x1a\n"))
            with Image.open(io.BytesIO(png_bytes)) as im:
                self.assertEqual(im.width, 900)
                self.assertGreater(im.height, 400)

        # Party dashboard PNG: empty, multi-theme, and >6 overflow
        for group in ([], chars, chars + chars[:3]):
            party_png = rpg_renderer.render_party_dashboard_png(group, notes_count=5)
            self.assertTrue(party_png.startswith(b"\x89PNG\r\n\x1a\n"))
            with Image.open(io.BytesIO(party_png)) as im:
                self.assertEqual(im.width, 900)


class RpgInteractiveViewsAndCogTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.orig_db_dir = assistant_db.DB_DIR
        self.orig_data_dir = assistant_db.DATA_DIR
        assistant_db.DB_DIR = Path(self.tmpdir.name)
        assistant_db.DATA_DIR = Path(self.tmpdir.name)
        assistant_db._initialized_dbs.clear()
        rpg_engine._initialized_rpg_dbs.clear()
        self.env_patch = patch.dict(os.environ, {"DB_NAME": f"test_rpg_cog_{id(self)}.db", "ENABLE_JAPANESE_MODE": "false"})
        self.env_patch.start()
        assistant_db.init_db()
        rpg_engine.init_rpg_tables()

    def tearDown(self):
        self.env_patch.stop()
        assistant_db.DB_DIR = self.orig_db_dir
        assistant_db.DATA_DIR = self.orig_data_dir
        assistant_db._initialized_dbs.clear()
        rpg_engine._initialized_rpg_dbs.clear()
        try:
            self.tmpdir.cleanup()
        except OSError:
            pass

    async def test_rpg_cog_backward_compatibility_and_png_attachments(self):
        intents = discord.Intents.none()
        bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)
        settings = _make_settings()
        logger = MagicMock()

        # Must support legacy 3-arg initialization without gemini_client
        cog = RpgCog(bot, settings, logger)
        self.assertIsNone(cog.gemini_client)
        await cog.trigger_dm_after_roll(SimpleNamespace(id=1001), SimpleNamespace(id=1, display_name="P1"), {})

        char = rpg_engine.update_rpg_character(
            channel_id=1001,
            user_id=55,
            user_name="Tester",
            char_name="萊恩",
            char_class="盜賊",
            profile={"stats": {"DEX": 16}, "skills": ["開鎖"]},
        )

        ctx = MagicMock()
        ctx.channel = SimpleNamespace(id=1001)
        ctx.author = SimpleNamespace(id=55, display_name="Tester")
        ctx.send = AsyncMock()

        await cog.send_sheet_embed(ctx, char)
        sheet_kwargs = ctx.send.await_args.kwargs
        self.assertIn("file", sheet_kwargs)
        self.assertEqual(sheet_kwargs["file"].filename, "char_sheet.png")
        self.assertEqual(sheet_kwargs["embed"].image.url, "attachment://char_sheet.png")

        ctx.send.reset_mock()
        await cog.party_command.callback(cog, ctx)
        party_kwargs = ctx.send.await_args.kwargs
        self.assertIn("file", party_kwargs)
        self.assertEqual(party_kwargs["file"].filename, "party_status.png")
        self.assertEqual(party_kwargs["embed"].image.url, "attachment://party_status.png")

        # Fallback to plain embed if PNG rendering fails
        ctx.send.reset_mock()
        with patch("engines.rpg_renderer.render_party_dashboard_png", side_effect=RuntimeError("font err")):
            await cog.party_command.callback(cog, ctx)
        self.assertNotIn("file", ctx.send.await_args.kwargs)
        await bot.close()

    async def test_roll_check_view_rows_buttons_and_option_a_auto_continue(self):
        rpg_engine.update_rpg_character(
            channel_id=2002,
            user_id=77,
            user_name="Hero",
            char_name="索倫",
            char_class="戰士",
            profile={"stats": {"STR": 16}, "skills": ["破門"]},
        )
        rpg_engine.add_journey_note(2002, 77, "Hero", "地下室門鎖已生鏽")

        intents = discord.Intents.none()
        bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)
        fake_gemini = MagicMock()
        cog = RpgCog(bot, _make_settings(), MagicMock(), gemini_client=fake_gemini)

        reqs = [
            {"check_name": "撞開鐵門", "dc": 14, "stat_or_skill": "STR/破門", "dice_expr": "1d20", "reason": "後方追兵逼近"},
            {"check_name": "聆聽動靜", "dc": 12, "stat_or_skill": "WIS", "dice_expr": "1d20"},
            {"check_name": "第三項測試", "dc": 10, "dice_expr": "1d20"},
            {"check_name": "應被截斷第四項", "dc": 10, "dice_expr": "1d20"},
        ]
        view = RpgRollCheckView(cog, reqs)
        buttons = [c for c in view.children if isinstance(c, discord.ui.Button)]
        row0 = [b for b in buttons if b.row == 0]
        row1 = [b for b in buttons if b.row == 1]
        self.assertEqual(len(row0), 3)
        self.assertEqual(len(row1), 3)
        self.assertEqual([b.label for b in row1], ["🎒 我的背包", "📜 旅程手記", "👥 小隊狀態"])

        # Simulate clicking first roll button -> Option A trigger_dm_after_roll -> continue_rpg_after_dice_roll
        interaction = MagicMock()
        interaction.channel = SimpleNamespace(id=2002)
        interaction.user = SimpleNamespace(id=77, display_name="Hero")
        interaction.response = AsyncMock()
        interaction.followup = AsyncMock()
        interaction.edit_original_response = AsyncMock()

        with (
            patch("engines.rpg_engine.roll_dice", return_value=(12, [12], "12")),
            patch("core.chat_pipeline.continue_rpg_after_dice_roll", new=AsyncMock(), create=True) as mock_continue,
        ):
            await row0[0].callback(interaction)

        self.assertTrue(row0[0].disabled)
        self.assertIn(0, view.completed_indices)
        interaction.response.defer.assert_awaited_once()
        interaction.edit_original_response.assert_awaited_once()
        interaction.followup.send.assert_awaited_once()
        result_embed = interaction.followup.send.await_args.kwargs["embed"]
        self.assertIn("檢定結果", result_embed.title)
        mock_continue.assert_awaited_once()
        call_args = mock_continue.await_args
        self.assertEqual(call_args.args[0], bot)
        self.assertEqual(call_args.args[1], fake_gemini)
        self.assertTrue(call_args.args[5]["passed"])
        self.assertEqual(call_args.args[5]["total"], 17)

        # Duplicate click on completed button returns ephemeral warning without rolling again
        dup_interaction = MagicMock()
        dup_interaction.response = AsyncMock()
        await row0[0].callback(dup_interaction)
        dup_interaction.response.send_message.assert_awaited_once()
        self.assertTrue(dup_interaction.response.send_message.await_args.kwargs["ephemeral"])

        # Test Row 1 ephemeral utility buttons
        for util_btn in row1:
            util_inter = MagicMock()
            util_inter.channel = SimpleNamespace(id=2002)
            util_inter.user = SimpleNamespace(id=77, display_name="Hero")
            util_inter.response = AsyncMock()
            await util_btn.callback(util_inter)
            util_inter.response.send_message.assert_awaited_once()
            self.assertTrue(util_inter.response.send_message.await_args.kwargs["ephemeral"])

        # Test send_check_prompt_view
        dest_ch = MagicMock()
        dest_ch.id = 2002
        dest_ch.send = AsyncMock(return_value=MagicMock())
        sent_msg = await cog.send_check_prompt_view(dest_ch, reqs[:2], content_text="門後傳來低沉咆哮……")
        self.assertIsNotNone(sent_msg)
        send_kw = dest_ch.send.await_args.kwargs
        self.assertIsInstance(send_kw["view"], RpgRollCheckView)
        self.assertIn("命運檢定", send_kw["embed"].title)

        # Test on_timeout disables all remaining buttons
        mock_msg = AsyncMock()
        view.message = mock_msg
        await view.on_timeout()
        self.assertTrue(all(b.disabled for b in buttons))
        mock_msg.edit.assert_awaited_once_with(view=view)
        await bot.close()


if __name__ == "__main__":
    unittest.main()
