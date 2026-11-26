"""Comprehensive unit tests for the upgraded modular 1in8B LifeSim engine and Cog."""

import asyncio
import io
import logging
import os
import random
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

from core.config import BotSettings
from engines import assistant_db
from engines import lifesim_engine as ls
from cogs.lifesim_cog import (
    LifeSimActiveView,
    LifeSimCog,
    LifeSimSetupView,
    build_leaderboard_embed,
    build_life_embed,
    build_lifesim_setup_embed,
    create_lifesim_option_selects,
)


def _make_settings() -> BotSettings:
    return BotSettings(
        env_file=".env",
        bot_instance_name="TestLifeBot",
        discord_bot_token="fake-token",
        gemini_api_key="fake-key",
        target_channel_id=1001,
        enable_board_games=True,
    )


class TestLifeSimDeepUpgrade(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._old_db_dir = assistant_db.DB_DIR
        self._old_data_dir = assistant_db.DATA_DIR
        self._old_db_env = os.environ.get("DB_NAME")
        assistant_db.DB_DIR = Path(self._tmp.name) / "data"
        assistant_db.DB_DIR.mkdir(parents=True, exist_ok=True)
        os.environ["DB_NAME"] = f"test_ls_deep_{id(self)}.db"
        assistant_db._initialized_dbs.clear()
        ls._initialized_ls_dbs.clear()
        ls.init_lifesim_tables()

    def tearDown(self):
        assistant_db.DB_DIR = self._old_db_dir
        assistant_db.DATA_DIR = self._old_data_dir
        if self._old_db_env is None:
            os.environ.pop("DB_NAME", None)
        else:
            os.environ["DB_NAME"] = self._old_db_env
        try:
            self._tmp.cleanup()
        except OSError:
            pass

    def _create_guaranteed_active_run(
        self,
        channel_id: int = 101,
        user_id: int = 201,
        user_name: str = "Alice",
        story_mode: str = "fast",
        era: str = "present",
    ) -> dict:
        with patch("engines.lifesim_engine.random.random", return_value=0.01):
            return ls.create_life_run(
                channel_id=channel_id,
                user_id=user_id,
                user_name=user_name,
                story_mode=story_mode,
                era=era,
            )

    def test_defaults_story_mode_urban_rural_and_nonconflicting_traits(self):
        self.assertEqual(ls.resolve_story_mode(None), "fast")
        self.assertEqual(ls.resolve_story_mode("hybrid"), "hybrid")
        self.assertEqual(ls.resolve_story_mode("精華混合"), "hybrid")
        self.assertEqual(ls.resolve_story_mode("llm"), "llm")
        self.assertEqual(len(ls.INNATE_TRAITS), 15)
        self.assertEqual(len(ls.STORY_MODE_LABELS), 3)
        self.assertEqual(len(ls.SIDE_ACTIONS), 5)

        for idx in range(12):
            with patch("engines.lifesim_engine.random.random", return_value=0.01):
                run = ls.create_life_run(channel_id=500 + idx, user_id=600 + idx, user_name=f"P{idx}")
            self.assertEqual(run["story_mode"], "fast")
            self.assertIn(run["urban_rural"], ("urban", "rural"))
            self.assertGreaterEqual(len(run["traits"]), 1)
            self.assertLessEqual(len(run["traits"]), 2)
            tids = {t["id"] for t in run["traits"]}
            for tid in tids:
                conflicts = set(ls.INNATE_TRAITS[tid].get("conflicts") or ())
                self.assertFalse(tids & conflicts)
            for stat in ("health", "happiness", "knowledge", "wealth", "social"):
                self.assertGreaterEqual(run[stat], 1)
                self.assertLessEqual(run[stat], 100)
            self.assertEqual(run["current_country_code"], run["country_code"])
            self.assertGreaterEqual(len(run["stat_history"]), 1)

    def test_legacy_db_schema_migration_and_deserialization(self):
        # Simulate an older SQLite database missing the 10 new life_runs columns
        os.environ["DB_NAME"] = f"legacy_ls_{id(self)}.db"
        ls._initialized_ls_dbs.clear()
        with assistant_db.get_conn() as conn:
            conn.execute("""
                CREATE TABLE life_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    channel_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    user_name TEXT NOT NULL,
                    story_mode TEXT DEFAULT 'fast',
                    era TEXT DEFAULT 'present',
                    draw_method TEXT DEFAULT 'births',
                    status TEXT NOT NULL,
                    country_code TEXT NOT NULL,
                    country_name TEXT NOT NULL,
                    region TEXT NOT NULL,
                    city TEXT NOT NULL,
                    char_name TEXT NOT NULL,
                    gender TEXT NOT NULL,
                    birth_year INTEGER NOT NULL,
                    age INTEGER DEFAULT 0,
                    max_age INTEGER NOT NULL,
                    family_class TEXT NOT NULL,
                    focus TEXT DEFAULT 'balance',
                    health INTEGER DEFAULT 70,
                    happiness INTEGER DEFAULT 70,
                    knowledge INTEGER DEFAULT 50,
                    wealth INTEGER DEFAULT 50,
                    social INTEGER DEFAULT 60,
                    luck_pr INTEGER DEFAULT 50,
                    birth_prob_pct REAL DEFAULT 0.0,
                    occupation TEXT DEFAULT '新生兒',
                    education TEXT DEFAULT '尚未入學',
                    pending_event_json TEXT DEFAULT '{}',
                    timeline_json TEXT DEFAULT '[]',
                    death_cause TEXT DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)
            conn.execute(
                """
                INSERT INTO life_runs
                (channel_id, user_id, user_name, status, country_code, country_name, region, city,
                 char_name, gender, birth_year, age, max_age, family_class, created_at, updated_at)
                VALUES (1, 2, 'OldPlayer', 'active', 'TW', '台灣', '東亞', '台北',
                        '陳小明', '男', 1995, 25, 80, '小康中產', '2026-01-01', '2026-01-01')
                """
            )
            conn.commit()

        ls.init_lifesim_tables()
        run = ls.get_life_run(1)
        self.assertIsNotNone(run)
        self.assertEqual(run["urban_rural"], "urban")
        self.assertEqual(run["current_country_code"], "TW")
        self.assertEqual(run["current_country_name"], "台灣")
        self.assertEqual(run["relationships"], {"partner": None, "married": False, "children": 0})
        self.assertEqual(run["traits"], [])
        self.assertEqual(run["side_action_age"], -1)

    def test_rich_event_pool_no_repeat_and_backward_compat_builder(self):
        total_events = len(ls.EVENT_POOL) + len(ls.HISTORICAL_ERA_EVENTS)
        self.assertGreaterEqual(total_events, 65)

        run = self._create_guaranteed_active_run(channel_id=102, user_id=202)
        seen_ids: list[str] = []
        safety = 0
        while run["status"] == "active" and safety < 20:
            safety += 1
            ev = run["pending_event"]
            eid = ev.get("id")
            if eid and eid != "fallback_generic":
                self.assertNotIn(eid, seen_ids, f"Repeated event id: {eid}")
                seen_ids.append(eid)
            run, _ = ls.resolve_life_step(run["id"], 202, choice_idx=0, rng=random.Random(42 + safety))

        # Backward compatibility wrapper _build_local_event(country, age) and _build_local_event(run)
        tw = next(c for c in ls.load_demographics() if c["code"] == "TW")
        ev_by_country = ls._build_local_event(tw, 24, rng=random.Random(7))
        self.assertTrue(ev_by_country["choices"])

    def test_probabilistic_check_odds_and_seeded_outcomes(self):
        run = self._create_guaranteed_active_run(channel_id=103, user_id=203)
        run["knowledge"] = 90
        run["traits"] = [{"id": "math_prodigy", "name": "數理奇才", "emoji": "🧮", "desc": ""}]
        check_spec = {
            "stat": "knowledge",
            "difficulty": 50,
            "label_hint": "看知識",
            "trait_bonus_ids": ["math_prodigy"],
            "crit_success": {"text": "大捷！", "deltas": {"wealth": 10}},
            "success": {"text": "成功！", "deltas": {"wealth": 5}},
            "fail": {"text": "失利！", "deltas": {"happiness": -5}},
        }
        high_pct = ls.compute_choice_success_pct(run, check_spec)
        self.assertEqual(high_pct, 95)  # clamped max 95

        run["knowledge"] = 5
        run["traits"] = []
        low_pct = ls.compute_choice_success_pct(run, check_spec)
        self.assertEqual(low_pct, 14)  # round(55 + (5-50)*0.9) = round(14.5) = 14
        run["knowledge"] = 0
        self.assertEqual(ls.compute_choice_success_pct(run, check_spec), 12)  # clamped min 12

        # Inject a check event and test deterministic success vs fail
        ls.set_pending_event(
            run["id"],
            {
                "id": "test_check_ev",
                "stage": "測試階段",
                "title": "測試檢定",
                "story": "測試",
                "choices": [{"label": "挑戰檢定", "success_pct": 60, "check": check_spec}],
            },
        )
        # Seeded RNG where randint(1, 100) <= 12 (crit_success)
        crit_rng = MagicMock()
        crit_rng.randint.return_value = 5
        crit_rng.choice.side_effect = lambda seq: seq[0]
        crit_rng.random.return_value = 0.5
        updated, note = ls.resolve_life_step(run["id"], 203, choice_idx=0, rng=crit_rng)
        self.assertIn("🎲 大成功", note)

    def test_side_actions_once_per_age_and_migration_unlocks_dex(self):
        run = self._create_guaranteed_active_run(channel_id=104, user_id=204)
        # Under 18 cannot migrate
        with self.assertRaises(ValueError):
            ls.apply_side_action(run["id"], 204, "migrate")

        # Study works at age 5
        updated, msg = ls.apply_side_action(run["id"], 204, "study")
        self.assertIn("進修", msg)
        self.assertEqual(updated["side_action_age"], 5)

        # Second side action at age 5 is blocked
        with self.assertRaises(ValueError):
            ls.apply_side_action(run["id"], 204, "health")

        # Advance to >= 18 and boost stats for guaranteed visa success
        with assistant_db.get_conn() as conn:
            conn.execute(
                "UPDATE life_runs SET age = 25, max_age = 80, knowledge = 85, wealth = 85, side_action_age = -1 WHERE id = ?",
                (run["id"],),
            )
            conn.commit()

        mig_rng = random.Random(123)
        # Force roll <= success_pct
        with patch.object(mig_rng, "randint", return_value=10):
            migrated_run, mig_msg = ls.apply_side_action(run["id"], 204, "migrate", rng=mig_rng)

        self.assertIn("成功移居至", mig_msg)
        self.assertNotEqual(migrated_run["current_country_code"], migrated_run["country_code"])
        dex = ls.get_user_country_dex(204)
        self.assertEqual(len(dex), 2)

    def test_should_use_llm_for_stage_hybrid_gates_and_timeline_prompt(self):
        run_fast = {"status": "active", "story_mode": "fast", "age": 18, "seen_events": []}
        self.assertFalse(ls.should_use_llm_for_stage(run_fast))

        run_llm = {"status": "active", "story_mode": "llm", "age": 5, "seen_events": []}
        self.assertTrue(ls.should_use_llm_for_stage(run_llm))
        self.assertFalse(ls.should_use_llm_for_stage({"status": "finished", "story_mode": "llm", "age": 80}))

        # Hybrid: only triggers once in each of the 3 milestone windows
        run_hyb = self._create_guaranteed_active_run(channel_id=105, user_id=205, story_mode="hybrid")
        self.assertEqual(run_hyb["story_mode"], "hybrid")
        self.assertFalse(ls.should_use_llm_for_stage(run_hyb))  # age 5

        with assistant_db.get_conn() as conn:
            conn.execute("UPDATE life_runs SET age = 18 WHERE id = ?", (run_hyb["id"],))
            conn.commit()
        run_hyb = ls.get_life_run(run_hyb["id"])
        self.assertTrue(ls.should_use_llm_for_stage(run_hyb))

        updated_hyb = ls.set_pending_event(
            run_hyb["id"],
            {
                "id": "llm_18",
                "stage": "18歲轉折",
                "title": "18歲・關鍵抉擇",
                "story": "故事",
                "choices": [{"label": "A", "deltas": {"knowledge": 5}}],
            },
        )
        self.assertFalse(ls.should_use_llm_for_stage(updated_hyb))

        prompt = ls.format_lifesim_next_event_prompt(updated_hyb)
        self.assertIn("近期重要里程碑", prompt)
        self.assertIn(updated_hyb["current_country_name"], prompt)
        epitaph_prompt = ls.format_lifesim_epitaph_prompt(updated_hyb)
        self.assertIn(updated_hyb["char_name"], epitaph_prompt)

    def test_evaluate_titles_and_leaderboards(self):
        sample_run = {
            "age": 96,
            "luck_pr": 18,
            "health": 72,
            "happiness": 82,
            "knowledge": 90,
            "wealth": 91,
            "social": 78,
            "country_code": "NG",
            "current_country_code": "JP",
            "relationships": {"partner": "Yui", "married": True, "children": 2},
            "traits": [{"id": "frail_body"}],
            "stat_history": [{"age": 0, "health": 35}],
            "seen_events": ["hist_1986_dotcom", "hist_2016_ai_pandemic"],
        }
        titles = ls.evaluate_titles(sample_run)
        for expected in (
            "🔥 寒門逆襲王",
            "💯 百歲人瑞",
            "✈️ 跨國旅人",
            "💎 億萬傳奇",
            "🎓 學術泰斗",
            "🏡 溫馨世家",
            "🛡️ 不屈鬥士",
            "🛠️ 斜槓達人",
            "🍀 平淡是福",
            "🕰️ 時代見證者",
        ):
            self.assertIn(expected, titles)

        # Insert two finished runs and verify age / comeback / dex leaderboards
        r1 = self._create_guaranteed_active_run(channel_id=111, user_id=301, user_name="LongLived")
        r2 = self._create_guaranteed_active_run(channel_id=112, user_id=302, user_name="ComebackKid")
        with assistant_db.get_conn() as conn:
            conn.execute(
                "UPDATE life_runs SET status='finished', age=92, luck_pr=90, health=70, happiness=70, knowledge=70, wealth=70, social=70 WHERE id=?",
                (r1["id"],),
            )
            conn.execute(
                "UPDATE life_runs SET status='finished', age=76, luck_pr=10, health=80, happiness=80, knowledge=80, wealth=80, social=80 WHERE id=?",
                (r2["id"],),
            )
            ls.unlock_country_in_dex(conn, 302, "ComebackKid", "JP", "日本", 76, 10, increment_runs=True)
            ls.unlock_country_in_dex(conn, 302, "ComebackKid", "DE", "德國", 76, 10, increment_runs=True)
            conn.commit()

        top_age = ls.get_lifesim_leaderboard("age", limit=5)
        self.assertEqual(top_age[0]["user_id"], 301)

        top_comeback = ls.get_lifesim_leaderboard("comeback", limit=5)
        self.assertEqual(top_comeback[0]["user_id"], 302)
        self.assertEqual(top_comeback[0]["comeback_score"], round(80.0 - 10 * 0.45, 1))

        top_dex = ls.get_lifesim_leaderboard("dex", limit=5)
        self.assertEqual(top_dex[0]["user_id"], 302)
        self.assertGreaterEqual(top_dex[0]["unlocked_count"], 2)

    def test_upgraded_pil_birth_and_epitaph_png_rendering(self):
        run = self._create_guaranteed_active_run(channel_id=120, user_id=401, user_name="Painter")
        birth_png = ls.render_birth_certificate_png(run)
        with Image.open(io.BytesIO(birth_png)) as im:
            self.assertEqual(im.format, "PNG")
            self.assertGreaterEqual(im.width, 720)

        # Short run (< 2 stat_history points) renders gracefully
        short_run = dict(run, status="finished", age=3, stat_history=[{"age": 0, "health": 30, "happiness": 40, "knowledge": 20, "wealth": 20, "social": 30}], titles=["🕊️ 流星微光"])
        short_png = ls.render_life_epitaph_png(short_run, unlocked_countries=1)
        with Image.open(io.BytesIO(short_png)) as im:
            self.assertEqual(im.format, "PNG")

        # Full run with trajectory + migration + titles
        stepped, _ = ls.resolve_life_step(run["id"], 401, choice_idx=0)
        finished = ls.auto_finish_life(stepped["id"], 401)
        finished["current_country_code"] = "IS"
        finished["current_country_name"] = "冰島"
        finished["current_city"] = "雷克雅維克"
        finished["titles"] = ls.evaluate_titles(finished)
        full_png = ls.render_life_epitaph_png(finished, unlocked_count=4)
        with Image.open(io.BytesIO(full_png)) as im:
            self.assertEqual(im.format, "PNG")
            self.assertGreaterEqual(im.width, 800)
            self.assertGreaterEqual(im.height, 600)

    def test_cog_views_embeds_selects_and_life_command(self):
        run = self._create_guaranteed_active_run(channel_id=130, user_id=501, user_name="UiTester", story_mode="hybrid")
        setup_embed = build_lifesim_setup_embed("present", "births", "fast")
        self.assertIn("經典深度版", setup_embed.description)
        self.assertIn("精華劇情版", setup_embed.description)
        self.assertNotIn("0-Token", setup_embed.description)

        async def _verify_ui():
            intents = discord.Intents.none()
            bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)
            cog = LifeSimCog(bot, MagicMock(), _make_settings(), logging.getLogger("test_ls"))
            await bot.add_cog(cog)
            self.assertIsNotNone(bot.get_command("life"))

            era_s, method_s, story_s = create_lifesim_option_selects("present", "births", "fast")
            self.assertEqual(len(story_s.options), 3)
            defaults = [o.value for o in story_s.options if o.default]
            self.assertEqual(defaults, ["fast"])

            setup_view = LifeSimSetupView(cog, owner_id=501)
            self.assertEqual(setup_view.story_mode, "fast")

            active_view = LifeSimActiveView(cog, run)
            self.assertFalse(active_view.side_action_select.disabled)
            row0_items = [c for c in active_view.children if c.row == 0]
            self.assertEqual(len(row0_items), 4)  # A, B, C + custom decision button in hybrid

            # When side_action_age == age, Select is disabled
            used_run = dict(run, side_action_age=run["age"])
            used_view = LifeSimActiveView(cog, used_run)
            self.assertTrue(used_view.side_action_select.disabled)

            embed = build_life_embed(run, unlocked_count=1)
            self.assertIn("人生 #", embed.footer.text)
            lb_embed = build_leaderboard_embed("comeback", [])
            self.assertIn("寒門逆襲", lb_embed.title)

            # Verify populate_llm_next_event skips LLM in hybrid non-milestone age
            res_run = await cog.populate_llm_next_event(run)
            self.assertEqual(res_run["pending_event"]["id"], run["pending_event"]["id"])
            await bot.close()

        asyncio.run(_verify_ui())


if __name__ == "__main__":
    unittest.main()
