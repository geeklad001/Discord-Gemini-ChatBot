"""Tests for boardgame_engine (Othello, Chess, Connect4, Gomoku, PIL renderer, AI, and NL tools)."""

import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import assistant_db
import assistant_tools
import boardgame_engine as bg


class TestBoardGames(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.orig_db_dir = assistant_db.DB_DIR
        self.orig_data_dir = assistant_db.DATA_DIR
        assistant_db.DB_DIR = Path(self.tmpdir.name)
        assistant_db.DATA_DIR = Path(self.tmpdir.name)
        assistant_db._initialized_dbs.clear()
        bg._initialized_bg_dbs.clear()
        self.env_patch = patch.dict(os.environ, {"DB_NAME": "test_bg.db"})
        self.env_patch.start()
        assistant_db.init_db()
        bg.init_boardgame_tables()

    def tearDown(self):
        self.env_patch.stop()
        assistant_db.DB_DIR = self.orig_db_dir
        assistant_db.DATA_DIR = self.orig_data_dir
        self.tmpdir.cleanup()

    def _verify_png(self, png_bytes: bytes) -> None:
        self.assertTrue(len(png_bytes) > 500)
        with Image.open(io.BytesIO(png_bytes)) as img:
            self.assertEqual(img.format, "PNG")
            self.assertGreater(img.width, 300)
            self.assertGreater(img.height, 300)

    def test_othello_pve_rules_ai_pass_and_render(self):
        g = bg.create_game(101, "othello", "pve", 1, "Alice", "normal")
        self.assertEqual(g["status"], "active")
        # Illegal move rejected
        with self.assertRaises(ValueError):
            bg.apply_move(g["id"], 1, "A1")

        # Valid move D3 flips D4
        g, note = bg.apply_move(g["id"], 1, "D3")
        self.assertIn("D3", note)
        self.assertEqual(g["turn"], 2)
        g, full_note = bg.play_pve_ai_turn_sync(g, note)
        self.assertIn("🤖", full_note)
        self.assertEqual(g["move_count"], 2)
        self._verify_png(bg.render_board_image(g))

        # Toggle inner coordinate watermarks
        g = bg.toggle_show_coords(g["id"])
        self.assertFalse(g["show_coords"])
        self._verify_png(bg.render_board_image(g))

    def test_chess_rules_checkmate_castling_ep_san_and_ai(self):
        # Fool's Mate verification (PvP)
        g = bg.create_game(102, "chess", "pvp", 1, "WhitePlayer")
        g = bg.join_pvp_game(g["id"], 2, "BlackPlayer")
        g, _ = bg.apply_move(g["id"], 1, "f3")
        g, _ = bg.apply_move(g["id"], 2, "e5")
        g, _ = bg.apply_move(g["id"], 1, "g2g4")
        g, mate_note = bg.apply_move(g["id"], 2, "Qh4")
        self.assertEqual(g["status"], "finished")
        self.assertEqual(g["winner"], 2)
        self.assertEqual(g["end_reason"], "checkmate")
        self.assertIn("將死", mate_note)
        self._verify_png(bg.render_board_image(g))

        # Stats recorded for PvP
        w_stats = bg.get_user_stats(1)
        b_stats = bg.get_user_stats(2)
        self.assertEqual(w_stats[0]["losses"], 1)
        self.assertEqual(b_stats[0]["wins"], 1)

        # Chess PvE AI + SAN castling test
        g2 = bg.create_game(103, "chess", "pve", 1, "Alice", "normal")
        g2, _ = bg.apply_move(g2["id"], 1, "e4")
        g2, _ = bg.play_pve_ai_turn_sync(g2)
        self.assertEqual(g2["move_count"], 2)
        self._verify_png(bg.render_board_image(g2, highlight_from="g1"))

        # King-side castling test
        st = bg._init_chess_state()
        st["board"][7][5] = ""
        st["board"][7][6] = ""
        mv = bg._parse_chess_move("O-O", st, True)
        self.assertEqual(mv, (7, 4, 7, 6, ""))

    def test_connect4_and_gomoku_win_and_pvp_draw(self):
        # Connect4 vertical win
        g = bg.create_game(104, "connect4", "pvp", 10, "Red")
        with self.assertRaises(ValueError):
            bg.join_pvp_game(g["id"], 10, "Red")  # Cannot join own game
        g = bg.join_pvp_game(g["id"], 20, "Yellow")
        for _ in range(3):
            g, _ = bg.apply_move(g["id"], 10, "4")
            g, _ = bg.apply_move(g["id"], 20, "5")
        g, win_note = bg.apply_move(g["id"], 10, "4")
        self.assertEqual(g["status"], "finished")
        self.assertEqual(g["winner"], 1)
        self.assertIn("獲勝", win_note)
        self.assertIsNotNone(g["state"].get("win_line"))
        self._verify_png(bg.render_board_image(g))

        # Gomoku PvE & offer/accept draw
        g2 = bg.create_game(105, "gomoku", "pvp", 10, "Black")
        g2 = bg.join_pvp_game(g2["id"], 20, "White")
        g2, _ = bg.apply_move(g2["id"], 10, "G7")
        g2, _ = bg.play_pve_ai_turn_sync(g2)
        g2, st1 = bg.offer_or_accept_draw(g2["id"], 10)
        self.assertEqual(st1, "offered")
        g2, st2 = bg.offer_or_accept_draw(g2["id"], 20)
        self.assertEqual(st2, "accepted")
        self.assertEqual(g2["winner"], -1)
        self._verify_png(bg.render_board_image(g2))

    def test_natural_language_boardgame_tools_anti_cheat_and_queue(self):
        fake_msg = SimpleNamespace(
            channel=SimpleNamespace(id=777),
            author=SimpleNamespace(id=55, display_name="小明"),
        )
        pending: list[tuple[int, str]] = []
        tok_msg = assistant_tools.current_discord_msg.set(fake_msg)
        tok_bg = bg.pending_board_card_updates.set(pending)
        try:
            start_out = assistant_tools.boardgame_start("黑白棋", "pve", "easy")
            self.assertIn("黑白棋", start_out)
            self.assertEqual(len(pending), 1)

            state_out = assistant_tools.boardgame_get_state()
            self.assertIn("C4", state_out)

            # Attempt illegal move -> rejected without cheating, card not queued
            bad_out = assistant_tools.boardgame_play_move("A1")
            self.assertIn("不合法", bad_out)
            self.assertEqual(len(pending), 1)

            # Valid move -> succeeds + AI plays + queues card
            ok_out = assistant_tools.boardgame_play_move("D3")
            self.assertIn("已完成" if "已完成" in ok_out else "已落子", ok_out)
            self.assertEqual(len(pending), 2)

            resign_out = assistant_tools.boardgame_resign()
            self.assertIn("認輸", resign_out)
            self.assertEqual(len(pending), 3)
            self.assertIsNone(bg.get_active_game(777))
        finally:
            bg.pending_board_card_updates.reset(tok_bg)
            assistant_tools.current_discord_msg.reset(tok_msg)

    def test_undo_requires_human_and_unanimous_consent(self):
        # 1. PvE undo rolls back both AI reply and human's last move
        g_pve = bg.create_game(201, "othello", "pve", 1, "Alice", "normal")
        g_pve, _ = bg.apply_move(g_pve["id"], 1, "D3")
        g_pve, _ = bg.play_pve_ai_turn_sync(g_pve)
        self.assertEqual(g_pve["move_count"], 2)

        # AI (user_id=0) and non-participant (user_id=999) CANNOT trigger undo
        with self.assertRaises(ValueError):
            bg.request_or_approve_undo(g_pve["id"], 0)
        with self.assertRaises(ValueError):
            bg.request_or_approve_undo(g_pve["id"], 999)

        g_pve, st_pve = bg.request_or_approve_undo(g_pve["id"], 1)
        self.assertEqual(st_pve, "applied")
        self.assertEqual(g_pve["move_count"], 0)
        self.assertEqual(g_pve["turn"], 1)

        # 2. PvP undo strictly requires ALL participants to approve
        g_pvp = bg.create_game(202, "connect4", "pvp", 10, "Red")
        g_pvp = bg.join_pvp_game(g_pvp["id"], 20, "Yellow")
        g_pvp, _ = bg.apply_move(g_pvp["id"], 10, "4")
        g_pvp, _ = bg.apply_move(g_pvp["id"], 20, "5")
        self.assertEqual(g_pvp["move_count"], 2)

        # P1 requests undo -> pending (1/2)
        g_pvp, st1 = bg.request_or_approve_undo(g_pvp["id"], 10)
        self.assertEqual(st1, "pending")
        self.assertEqual(g_pvp["move_count"], 2)

        # P1 clicking again -> still waiting for P2
        g_pvp, st_dup = bg.request_or_approve_undo(g_pvp["id"], 10)
        self.assertEqual(st_dup, "already_approved")

        # P2 rejects -> clears pending request
        g_pvp = bg.reject_undo(g_pvp["id"], 20)
        self.assertEqual(g_pvp["undo_approvals"], [])

        # P1 requests again, P2 approves -> rolled back!
        g_pvp, st_p = bg.request_or_approve_undo(g_pvp["id"], 10)
        self.assertEqual(st_p, "pending")
        g_pvp, st_ok = bg.request_or_approve_undo(g_pvp["id"], 20)
        self.assertEqual(st_ok, "applied")
        self.assertEqual(g_pvp["move_count"], 1)
        self.assertEqual(g_pvp["turn"], 2)

    def test_llm_opponent_prompt_and_anti_cheat_retry_and_fallback(self):
        import asyncio
        from unittest.mock import AsyncMock, MagicMock
        from cogs.boardgame_cog import BoardGameCog
        from core.config import BotSettings

        g = bg.create_game(203, "othello", "pve", 1, "Alice", "llm")
        self.assertEqual(g["difficulty"], "llm")
        g, _ = bg.apply_move(g["id"], 1, "D3")
        prompt = bg.format_llm_opponent_prompt(g)
        self.assertIn("C3", prompt)
        self.assertIn("合法步座標清單", prompt)

        settings = BotSettings(
            env_file=".env",
            bot_instance_name="TestBot",
            discord_bot_token="t",
            gemini_api_key="k",
            target_channel_id=203,
        )
        cog = BoardGameCog(MagicMock(), MagicMock(), settings, MagicMock())

        # Case 1: LLM tries illegal move A1 first, then succeeds with C3 on retry; taunt stays separate from card note
        bad_resp = SimpleNamespace(text='{"move": "A1", "taunt": "我要作弊下角落"}')
        good_resp = SimpleNamespace(text='{"move": "C3", "taunt": "這步不錯吧！"}')
        with patch("cogs.boardgame_cog.generate_with_fallback", new=AsyncMock(side_effect=[(bad_resp, "m1"), (good_resp, "m1")])):
            updated, note, taunt = asyncio.run(cog._pick_and_apply_llm_move(g))
        self.assertEqual(updated["move_count"], 2)
        self.assertIn("C3", note)
        self.assertNotIn("這步不錯吧", note)
        self.assertIn("這步不錯吧", taunt)

        # Case 2: LLM tries illegal move twice -> local anti-cheat engine cleanly takes over
        g2 = bg.create_game(204, "othello", "pve", 1, "Alice", "llm")
        g2, _ = bg.apply_move(g2["id"], 1, "D3")
        with patch("cogs.boardgame_cog.generate_with_fallback", new=AsyncMock(return_value=(bad_resp, "m1"))):
            updated2, note2, taunt2 = asyncio.run(cog._pick_and_apply_llm_move(g2))
        self.assertEqual(updated2["move_count"], 2)
        self.assertIn("本地防作弊裁判", note2)
        self.assertEqual(taunt2, "")

    def test_board_context_includes_ascii_grid_and_finished_game_and_first_move(self):
        fake_msg = SimpleNamespace(
            channel=SimpleNamespace(id=305),
            author=SimpleNamespace(id=1, display_name="小明"),
        )
        pending = []
        tok_msg = assistant_tools.current_discord_msg.set(fake_msg)
        tok_bg = bg.pending_board_card_updates.set(pending)
        try:
            # Start Othello against LLM AND play opening move D3 in one natural-language tool call
            start_out = assistant_tools.boardgame_start("黑白棋", mode="pve", difficulty="llm", first_move="D3")
            self.assertIn("黑白棋", start_out)
            self.assertIn("A B C D E F G H", start_out)
            self.assertIn("D3", start_out)
            self.assertEqual(len(pending), 1)
            self.assertTrue(pending[0]["run_ai"])

            # Finish game via resign -> get_board_status_summary_for_llm still keeps context for chat review!
            assistant_tools.boardgame_resign()
            summary_after = bg.get_board_status_summary_for_llm(305)
            self.assertIn("近期剛結束的棋局", summary_after)
            self.assertIn("A B C D E F G H", summary_after)
            self.assertIn("D3", summary_after)
        finally:
            bg.pending_board_card_updates.reset(tok_bg)
            assistant_tools.current_discord_msg.reset(tok_msg)


if __name__ == "__main__":
    unittest.main()
