"""Tests for asynchronous user feedback & suggestion system (DB, LLM tool, public Views, and !feedback Cog)."""

import logging
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from cogs.assistant_cog import AssistantCog
from cogs.feedback_views import (
    FeedbackListView,
    build_feedback_detail_embed,
    build_feedback_list_embed,
)
from core.chat_pipeline import (
    _extract_feedback_context_excerpt,
    build_dynamic_system_instruction,
)
from core.config import BotSettings
from engines import assistant_db, assistant_tools


def _make_settings(**overrides) -> BotSettings:
    defaults = dict(
        env_file=".env.community",
        bot_instance_name="TestBot",
        discord_bot_token="tok",
        gemini_api_key="key",
        target_channel_id=1001,
        tts_channel_id=None,
        general_channel_id=1003,
        enable_japanese_mode=False,
        enable_flashcard_mode=False,
        enable_game_wiki=True,
        enable_board_games=True,
        enable_voice_tts=False,
        enable_safety_guardrail=False,
        bot_owner_ids=(9999,),
    )
    defaults.update(overrides)
    return BotSettings(**defaults)


class FeedbackSystemTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._old_db_dir = assistant_db.DB_DIR
        self._old_db_env = os.environ.get("DB_NAME")
        assistant_db.DB_DIR = Path(self._tmp.name) / "data"
        assistant_db.DB_DIR.mkdir(parents=True, exist_ok=True)
        os.environ["DB_NAME"] = f"test_fb_{id(self)}.db"
        assistant_db._initialized_dbs.clear()
        assistant_db.init_db()

    def tearDown(self):
        assistant_db.DB_DIR = self._old_db_dir
        if self._old_db_env is None:
            os.environ.pop("DB_NAME", None)
        else:
            os.environ["DB_NAME"] = self._old_db_env
        try:
            self._tmp.cleanup()
        except OSError:
            pass

    def test_db_crud_category_inference_dedup_cooldown_and_prompt_isolation(self):
        # 1. Category inference
        self.assertEqual(assistant_db.normalize_feedback_category("", "建議新增語音指令捷徑"), "feature")
        self.assertEqual(assistant_db.normalize_feedback_category("", "Wiki 聖誕工坊條目寫錯了"), "wiki")
        self.assertEqual(assistant_db.normalize_feedback_category("", "桌遊悔棋按鈕壞掉沒反應"), "bug")
        self.assertEqual(assistant_db.normalize_feedback_category("ux", "Embed 排版太長"), "ux")

        # 2. Add & deduplicate
        fid1, st1 = assistant_db.add_feedback_report(
            summary="建議新增每日語音摘要",
            detail="希望能有一個指令整理今天語音房講過的重點",
            category="feature",
            source="ai_tool",
            reporter_id=4242,
            reporter_name="SecretReporterAlpha",
            channel_id=1001,
            message_jump_url="https://discord.com/channels/1/2/3",
            context_excerpt="[被回覆訊息]\n今天聊了好多",
            cooldown_seconds=60,
        )
        self.assertEqual(st1, "created")
        self.assertEqual(fid1, 1)

        fid_dup, st_dup = assistant_db.add_feedback_report(
            summary="建議新增每日語音摘要",
            detail="重複提交",
            reporter_id=4242,
            reporter_name="SecretReporterAlpha",
            cooldown_seconds=60,
        )
        self.assertEqual(st_dup, "duplicate")
        self.assertEqual(fid_dup, fid1)

        # 3. Rate limit for same user within cooldown
        fid_rl, st_rl = assistant_db.add_feedback_report(
            summary="另一個回饋內容",
            reporter_id=4242,
            reporter_name="SecretReporterAlpha",
            cooldown_seconds=60,
        )
        self.assertIsNone(fid_rl)
        self.assertEqual(st_rl, "rate_limited")

        # 4. Zero prompt contamination: memories & build_dynamic_system_instruction never contain feedback!
        mem_prompt = assistant_db.format_memories_for_prompt()
        self.assertNotIn("建議新增每日語音摘要", mem_prompt)
        self.assertNotIn("SecretReporterAlpha", mem_prompt)
        sys_inst = build_dynamic_system_instruction(
            _make_settings(),
            "general_prompt_community.txt",
            default_fallback="fallback",
            message=None,
        )
        self.assertNotIn("建議新增每日語音摘要", sys_inst)
        self.assertNotIn("SecretReporterAlpha", sys_inst)

        # 5. Status lifecycle & markdown privacy
        updated = assistant_db.update_feedback_status(fid1, "triaged", "列入 v2.2 規劃")
        self.assertEqual(updated["status"], "triaged")
        self.assertEqual(len(assistant_db.list_feedback_reports(status="open")), 1)
        updated = assistant_db.update_feedback_status(fid1, "resolved", "已實作完成")
        self.assertEqual(updated["status"], "resolved")
        self.assertEqual(len(assistant_db.list_feedback_reports(status="open")), 0)
        self.assertEqual(len(assistant_db.list_feedback_reports(status="resolved")), 1)

        counts = assistant_db.get_feedback_counts()
        self.assertEqual(counts["resolved"], 1)
        self.assertEqual(counts["total"], 1)

        md_public = assistant_db.format_feedback_markdown(
            assistant_db.list_feedback_reports(status="all"), include_reporter=False
        )
        self.assertIn("建議新增每日語音摘要", md_public)
        self.assertNotIn("SecretReporterAlpha", md_public)
        self.assertNotIn("4242", md_public)

        md_admin = assistant_db.format_feedback_markdown(
            assistant_db.list_feedback_reports(status="all"), include_reporter=True
        )
        self.assertIn("SecretReporterAlpha", md_admin)

        self.assertTrue(assistant_db.delete_feedback_report(fid1))
        self.assertIsNone(assistant_db.get_feedback_report(fid1))

    async def test_submit_user_feedback_tool_and_reply_context_extraction(self):
        replied_emb = SimpleNamespace(title="聖誕工坊", description="R6 需 40 份禮物")
        replied_msg = SimpleNamespace(
            clean_content="這是機器人的回答內容",
            content="這是機器人的回答內容",
            embeds=[replied_emb],
            jump_url="https://discord.com/channels/1/1001/888",
        )
        user_msg = SimpleNamespace(
            author=SimpleNamespace(id=777, display_name="HiddenUser777"),
            channel=SimpleNamespace(id=1001),
            jump_url="https://discord.com/channels/1/1001/999",
            reference=SimpleNamespace(resolved=replied_msg, message_id=888),
        )
        excerpt = await _extract_feedback_context_excerpt(user_msg, recent_text="fallback")
        self.assertIn("[被回覆訊息]", excerpt)
        self.assertIn("聖誕工坊", excerpt)
        self.assertNotIn("HiddenUser777", excerpt)

        tok_msg = assistant_tools.current_discord_msg.set(user_msg)
        tok_ctx = assistant_db.current_feedback_context.set(excerpt)
        try:
            res = assistant_tools.submit_user_feedback(
                category="wiki",
                summary="聖誕工坊 R6 配額疑似誤植",
                detail="建議確認 3 人局配額是不是 32 而不是 40",
            )
            self.assertIn("#1", res)
            self.assertIn("不要 @提及或點名回報者", res)

            res_dup = assistant_tools.submit_user_feedback(
                category="wiki",
                summary="聖誕工坊 R6 配額疑似誤植",
                detail="建議確認 3 人局配額",
            )
            self.assertIn("已沿用原編號", res_dup)
        finally:
            assistant_db.current_feedback_context.reset(tok_ctx)
            assistant_tools.current_discord_msg.reset(tok_msg)

        saved = assistant_db.get_feedback_report(1)
        self.assertIsNotNone(saved)
        self.assertEqual(saved["source"], "ai_tool")
        self.assertEqual(saved["message_jump_url"], "https://discord.com/channels/1/1001/999")
        self.assertIn("聖誕工坊", saved["context_excerpt"])

    async def test_public_list_view_submission_and_privacy_in_cog(self):
        bot = MagicMock()
        bot.is_owner = AsyncMock(side_effect=lambda u: u.id == 9999)
        settings = _make_settings()
        cog = AssistantCog(bot, settings, logging.getLogger("test_feedback"))

        member_author = SimpleNamespace(
            id=5555,
            display_name="MemberSecretName",
            guild_permissions=SimpleNamespace(administrator=False, manage_guild=False, manage_messages=False),
        )
        owner_author = SimpleNamespace(
            id=9999,
            display_name="OwnerUser",
            guild_permissions=SimpleNamespace(administrator=True, manage_guild=True, manage_messages=True),
        )
        channel = SimpleNamespace(id=1001)

        def _make_ctx(author, invoked_with="feedback", ref_msg=None):
            ctx = MagicMock()
            ctx.author = author
            ctx.channel = channel
            ctx.invoked_with = invoked_with
            ctx.message = SimpleNamespace(
                author=author,
                channel=channel,
                jump_url="https://discord.com/channels/1/1001/777",
                reference=SimpleNamespace(resolved=ref_msg, message_id=776) if ref_msg else None,
            )
            ctx.send = AsyncMock()
            return ctx

        # 1. Ordinary member submits using `!建議` alias + reply context
        replied = SimpleNamespace(
            clean_content="舊版設定文字",
            embeds=[],
            jump_url="https://discord.com/channels/1/1001/776",
        )
        submit_ctx = _make_ctx(member_author, invoked_with="建議", ref_msg=replied)
        await cog.feedback_command.callback(cog, submit_ctx, action="希望 !wiki 可以支援搜尋英文字母", arg=None)
        submit_ctx.send.assert_awaited_once()
        call_kwargs = submit_ctx.send.call_args.kwargs
        sent_text = submit_ctx.send.call_args.args[0]
        self.assertIn("#1", sent_text)
        self.assertIn("功能建議", sent_text)
        self.assertNotIn("MemberSecretName", sent_text)
        self.assertNotIn("<@5555>", sent_text)
        am = call_kwargs["allowed_mentions"]
        self.assertFalse(am.everyone or am.users or am.roles or am.replied_user)

        # 2. Ordinary member opens PUBLIC `!feedback list` -> allowed, shows item, ZERO reporter info
        list_ctx = _make_ctx(member_author, invoked_with="feedback")
        await cog.feedback_command.callback(cog, list_ctx, action="list", arg=None)
        list_ctx.send.assert_awaited_once()
        list_emb = list_ctx.send.call_args.kwargs["embed"]
        list_view = list_ctx.send.call_args.kwargs["view"]
        self.assertIsInstance(list_view, FeedbackListView)
        self.assertIn("#1", list_emb.description)
        self.assertIn("希望 !wiki 可以支援搜尋英文字母", list_emb.description)
        self.assertNotIn("MemberSecretName", list_emb.description)
        self.assertNotIn("5555", list_emb.description)

        # 3. Ordinary member opens PUBLIC `!feedback view 1`
        view_ctx = _make_ctx(member_author)
        await cog.feedback_command.callback(cog, view_ctx, action="view", arg="1")
        view_emb = view_ctx.send.call_args.kwargs["embed"]
        self.assertIn("舊版設定文字", view_emb.description)
        self.assertNotIn("MemberSecretName", view_emb.description)
        self.assertNotIn("5555", view_emb.description)

        # 4. Ordinary member CANNOT run `!feedback done 1`
        denied_ctx = _make_ctx(member_author)
        await cog.feedback_command.callback(cog, denied_ctx, action="done", arg="1 修好了")
        self.assertIn("僅限", denied_ctx.send.call_args.args[0])
        self.assertEqual(assistant_db.get_feedback_report(1)["status"], "open")

        # 5. Owner marks `!feedback done 1 修好了` -> NO mention / NO reporter name in message!
        done_ctx = _make_ctx(owner_author)
        await cog.feedback_command.callback(cog, done_ctx, action="done", arg="1 已實作優化")
        done_text = done_ctx.send.call_args.args[0]
        self.assertIn("已解決", done_text)
        self.assertIn("#1", done_text)
        self.assertNotIn("MemberSecretName", done_text)
        self.assertNotIn("5555", done_text)
        done_am = done_ctx.send.call_args.kwargs["allowed_mentions"]
        self.assertFalse(done_am.everyone or done_am.users or done_am.roles or done_am.replied_user)
        self.assertEqual(assistant_db.get_feedback_report(1)["status"], "resolved")

        # 6. Admin exports Markdown file -> NO reporter leak
        exp_ctx = _make_ctx(owner_author)
        await cog.feedback_command.callback(cog, exp_ctx, action="export", arg="all")
        exp_file = exp_ctx.send.call_args.kwargs["file"]
        exp_content = exp_file.fp.read().decode("utf-8")
        self.assertIn("希望 !wiki 可以支援搜尋英文字母", exp_content)
        self.assertNotIn("MemberSecretName", exp_content)
        self.assertNotIn("5555", exp_content)

        # 7. Pagination embed helper bounds & detail embed helper sanity
        many = [
            {
                "id": i,
                "category": "feature",
                "summary": f"建議項目 {i}",
                "detail": f"詳細內容 {i}",
                "status": "open",
                "reporter_name": f"LeakedName{i}",
                "reporter_id": 10000 + i,
                "created_at": "2026-11-26 21:00:00",
                "updated_at": "2026-11-26 21:00:00",
            }
            for i in range(1, 15)
        ]
        p2_emb = build_feedback_list_embed(many, page=1, page_size=8, status_filter="open")
        self.assertIn("第 2 / 2 頁", p2_emb.footer.text)
        self.assertNotIn("LeakedName", p2_emb.description)
        d_emb = build_feedback_detail_embed(dict(many[0], source="command"))
        self.assertNotIn("LeakedName", d_emb.description)
        self.assertIn("指令提交", d_emb.footer.text)
        self.assertNotIn("command", d_emb.footer.text)

        d_ai_emb = build_feedback_detail_embed(dict(many[0], source="ai_tool"))
        self.assertIn("對話自動收錄", d_ai_emb.footer.text)
        self.assertNotIn("ai_tool", d_ai_emb.footer.text)
