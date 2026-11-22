"""Regression tests for the promoted Evolution Mode Wiki and Korean translation RAG."""

import re
import sys
import unittest
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engines import game_wiki_engine


class TestEvolutionWikiRegression(unittest.TestCase):
    def setUp(self):
        game_wiki_engine.load_wiki_entries(force_reload=True)

    def test_promoted_evolution_mode_integrity(self):
        entries = game_wiki_engine.load_wiki_entries(force_reload=True)
        entries_by_id = game_wiki_engine._WIKI_CACHE["entries_by_id"]

        evo_entries = [e for e in entries if e.get("source_file") == "evolution_mode.md"]
        self.assertEqual(len(evo_entries), 64)

        evo_ids = [e["id"] for e in evo_entries]
        self.assertTrue(
            all(eid.startswith("evo_") for eid in evo_ids),
            f"Found non-evo_ IDs in evolution_mode.md: {[eid for eid in evo_ids if not eid.startswith('evo_')]}",
        )
        self.assertEqual(len(set(evo_ids)), 64)

        # Global 0 duplicate IDs across ALL data/game_wiki/*.md files (and loaded entries)
        all_md_ids: list[tuple[str, str]] = []
        for md_path in sorted(game_wiki_engine.WIKI_DIR.glob("*.md")):
            for item in game_wiki_engine._parse_markdown_wiki_file(md_path):
                all_md_ids.append((item["id"], md_path.name))
        id_counts = Counter(eid for eid, _ in all_md_ids)
        duplicates = {
            eid: [fname for cur_id, fname in all_md_ids if cur_id == eid]
            for eid, cnt in id_counts.items()
            if cnt > 1
        }
        self.assertEqual(duplicates, {}, f"Duplicate IDs found across markdown files: {duplicates}")
        self.assertEqual(len(entries), len(entries_by_id), "Global ID collision across wiki + CSV entries")

        # Zero review/leak markers in promoted evolution_mode.md
        evo_md_path = game_wiki_engine.WIKI_DIR / "evolution_mode.md"
        evo_raw = evo_md_path.read_text(encoding="utf-8")
        self.assertNotIn("> 🔍", evo_raw)
        self.assertNotIn("> ⚠️", evo_raw)
        self.assertNotIn("status: pending_review", evo_raw)

        # Every evo_* entry's related_ids resolves in entries_by_id
        unresolved: list[tuple[str, str]] = []
        for entry in evo_entries:
            for rel_id in entry.get("related_ids", []):
                if rel_id not in entries_by_id:
                    unresolved.append((entry["id"], rel_id))
        self.assertEqual(unresolved, [], f"Unresolved related_ids in evolution_mode.md: {unresolved}")

    def test_evolution_golden_queries_regression(self):
        golden_cases: list[tuple[str, tuple[str, ...]]] = [
            ("進化感染怎麼玩", ("evo_rule_overview_matching",)),
            ("革命感染勝利條件", ("evo_rule_switch_hacking_win",)),
            ("針跟馬怎麼修", ("evo_rule_hacking_minigames_security",)),
            ("黃針藍針", ("evo_rule_hacking_minigames_security",)),
            ("三掛幾秒淘汰", ("evo_rule_zombie_evolution_and_infection", "evo_tactic_forced_rescue_and_3hook")),
            ("吃人幾倍經驗", ("evo_rule_zombie_evolution_and_infection", "evo_zombie_common_mechanics")),
            ("千勝徽章", ("evo_rule_mastery_and_emblems",)),
            ("警衛藍光手電筒幾秒", ("evo_zombie_madness_stats", "evo_zombie_madness_skill_flashlight")),
            ("3階手電筒CD", ("evo_zombie_madness_skill_flashlight",)),
            ("燒瓶小燒瓶範圍", ("evo_zombie_liquid_skills",)),
            ("燒瓶毒液幾秒", ("evo_zombie_liquid_boiling",)),
            ("阿修羅2技踩", ("evo_zombie_asura_skills_dash",)),
            ("阿修羅1技蓄力", ("evo_zombie_asura_skills_rage",)),
            ("阿修羅吃人加血", ("evo_zombie_asura_intake",)),
            ("幸運草職業", ("evo_human_roles_and_weapon_rules",)),
            ("武器庫的刀", ("evo_weapon_tech_room_and_raid",)),
            ("手榴彈暈幾秒", ("evo_weapon_tech_room_and_raid", "evo_weapon_tech_room_raid_tactics")),
            ("雷射槍暈幾秒", ("evo_weapon_crossbow_and_pulse_beam",)),
            ("教室雨傘牆洞", ("evo_map_school_callouts",)),
            ("教室紅房", ("evo_map_school_callouts",)),
            ("監獄木牆打幾下", ("evo_map_underground_prison",)),
            ("餐廳隱藏飲料機", ("evo_map_cafeteria_spawns_vending",)),
            ("廚房武器庫下面", ("evo_map_cafeteria_callouts",)),
            ("時鐘報位 7 2", ("evo_slang_tw_clock_callouts", "evo_tactic_clock_callouts_and_npc")),
            ("掃把被緩速", ("evo_tactic_broom_speed_and_drink_timing",)),
            ("速度表", ("evo_tactic_speed_table_and_turning",)),
            ("고오오 겹스", ("evo_cafe_kr_callouts_macros",)),
            ("1해 3기", ("evo_slang_tactics_part1", "evo_human_roles_and_weapon_rules")),
            ("教室機台機率", ("evo_cafe_switch_prob_school",)),
            ("監獄機台機率", ("evo_cafe_switch_spawn_prob",)),
        ]
        self.assertGreaterEqual(len(golden_cases), 28)

        for query, expected_ids in golden_cases:
            with self.subTest(query=query):
                res = game_wiki_engine.search_wiki_entries(
                    query,
                    max_slang=6,
                    max_lore=4,
                    expand_lightrag=True,
                )
                slang_ids = [e["id"] for e in res["slang_hits"]]
                lore_ids = [e["id"] for e in res["lore_hits"]]
                related_ids = [e["id"] for e in res["related_hits"]]
                combined_ids = slang_ids + lore_ids + related_ids
                self.assertTrue(
                    any(exp_id in combined_ids for exp_id in expected_ids),
                    (
                        f"Query {query!r} missed expected {expected_ids}; "
                        f"max_specific_score={res['max_specific_score']:.1f}, "
                        f"slang_hits={slang_ids}, lore_hits={lore_ids}, related_hits={related_ids}"
                    ),
                )

    def test_korean_short_sentence_translation_mode_and_anti_hallucination(self):
        # 1. Particle-inflected Korean translation query
        q_particle = "翻譯一下：고오오 겹스인데 빨리 기술실을 열자"
        rag_particle = game_wiki_engine.build_auto_rag_context(q_particle)
        self.assertIn("韓文攻略短句翻譯輔助模式", rag_particle)

        res_particle = game_wiki_engine.search_wiki_entries(
            q_particle,
            max_slang=6,
            max_lore=4,
            expand_lightrag=True,
        )
        hit_ids_particle = {
            e["id"]
            for e in (res_particle["slang_hits"] + res_particle["lore_hits"] + res_particle["related_hits"])
        }
        self.assertTrue(
            bool({"evo_cafe_kr_callouts_macros", "evo_rule_human_growth_tech_room"} & hit_ids_particle),
            f"Expected callout or tech room card in hits, got: {sorted(hit_ids_particle)}",
        )

        unmatched_tokens = game_wiki_engine._detect_unmatched_korean_tokens(
            q_particle,
            res_particle["slang_hits"],
            res_particle["lore_hits"],
            res_particle["related_hits"],
        )
        self.assertNotIn("겹스인데", unmatched_tokens)
        self.assertNotIn("기술실을", unmatched_tokens)

        partial_block_match = re.search(
            r"CRAG 部分未知攔截.*?(?=\n🌐|\n⚡|\n📖|\n🗂️|\n🔗|\n🛡️|$)",
            rag_particle,
            flags=re.DOTALL,
        )
        if partial_block_match:
            partial_block = partial_block_match.group(0)
            for tok in ("겹스인데", "기술실을", "빨리", "열자"):
                self.assertNotIn(tok, partial_block)
        self.assertNotIn("CRAG 部分未知攔截", rag_particle)

        # 2. Translation request with unknown pure-choseong acronym
        q_choseong = "翻譯：고오오 겹스 ㅃㅃㅃ"
        rag_choseong = game_wiki_engine.build_auto_rag_context(q_choseong)
        self.assertIn("韓文攻略短句翻譯輔助模式", rag_choseong)
        self.assertIn("CRAG 部分未知攔截", rag_choseong)
        self.assertIn("ㅃㅃㅃ", rag_choseong)

        # 3. Translation request with no hits (in_domain fallback)
        q_no_hits_trans = "翻譯一下：殭學 케이크 먹고싶어"
        rag_no_hits_trans = game_wiki_engine.build_auto_rag_context(q_no_hits_trans)
        self.assertIn("韓文短句翻譯模式", rag_no_hits_trans)
        self.assertNotIn("🛑【CRAG 知識邊界提醒", rag_no_hits_trans)

        # 4. Non-translation factual query with unknown Korean word still triggers CRAG abstention
        q_unknown_fact = "請問 뷁뷁뷁 是哪隻殭屍"
        rag_unknown_fact = game_wiki_engine.build_auto_rag_context(q_unknown_fact)
        self.assertTrue(
            ("CRAG 知識邊界提醒" in rag_unknown_fact)
            or ("CRAG 部分未知攔截" in rag_unknown_fact and "뷁뷁뷁" in rag_unknown_fact),
            f"Expected CRAG abstention for unknown Korean token, got:\n{rag_unknown_fact}",
        )
        self.assertNotIn("韓文短句翻譯模式", rag_unknown_fact)
        self.assertNotIn("韓文攻略短句翻譯輔助模式", rag_unknown_fact)


if __name__ == "__main__":
    unittest.main()
