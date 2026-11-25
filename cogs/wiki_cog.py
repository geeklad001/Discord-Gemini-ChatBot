"""Game Wiki Cog: !wiki command for Zombie High School Contextual-LightRAG knowledge base."""

import asyncio
import logging
from pathlib import Path
from typing import Optional

import discord
from discord.ext import commands
from google import genai

from cogs.wiki_views import (
    WikiBrowseView,
    WikiOverviewView,
    WikiSearchView,
    _build_wiki_entry_embed,
    _resolve_browse_categories,
    _split_long_text_pages,
    build_wiki_disambiguation_embed,
    build_wiki_empty_search_embed,
    build_wiki_import_result_embed,
    build_wiki_list_embed,
    build_wiki_overview_embed,
    build_wiki_replace_embed,
)
from core.chat_pipeline import is_target_channel
from core.config import MAX_TEXT_ATTACHMENT_BYTES, BotSettings, is_bot_owner
from engines import game_wiki_engine, safety_guardrail

_WIKI_IMPORT_GUIDE = (
    "📥 **《殭屍學園》知識庫補充教學**：\n"
    "1️⃣ **單筆快速補充**：\n"
    "`!wiki add 關鍵字1, 關鍵字2 | 這裡填寫詳細設定、機制或攻略說明`\n\n"
    "2️⃣ **多段文本直接貼上**：\n"
    "```text\n"
    "!wiki import\n"
    "【條目一標題 / 別名】\n"
    "這裡寫條目一的詳細內容...\n\n"
    "【條目二標題 / 韓文名】\n"
    "這裡寫條目二的詳細內容...\n"
    "```\n"
    "3️⃣ **直接上傳 `.txt` / `.md` 檔案**：\n"
    "在訊息框輸入 `!wiki import [主題名稱]` 並**夾帶 `.txt` 或 `.md` 檔案**發送，系統會自動分段建立索引！"
)


class WikiCog(commands.Cog):
    """《殭屍學園 (좀비고등학교)》知識庫查詢與補充管理指令。"""

    def __init__(
        self,
        bot: commands.Bot,
        gemini_client: genai.Client,
        settings: BotSettings,
        logger: logging.Logger,
    ):
        self.bot = bot
        self.gemini_client = gemini_client
        self.settings = settings
        self.logger = logger

    async def _has_community_mod_or_owner(self, ctx: commands.Context) -> bool:
        if await is_bot_owner(self.bot, self.settings, ctx.author):
            return True
        perms = getattr(ctx.author, "guild_permissions", None)
        return bool(
            perms
            and (
                getattr(perms, "administrator", False)
                or getattr(perms, "manage_guild", False)
                or getattr(perms, "manage_messages", False)
            )
        )

    async def _handle_wiki_overview(self, ctx: commands.Context) -> None:
        stats = await asyncio.to_thread(game_wiki_engine.get_wiki_stats)
        embed = build_wiki_overview_embed(stats)
        overview_view = await WikiOverviewView.create()
        sent_msg = await ctx.send(embed=embed, view=overview_view)
        overview_view.message = sent_msg

    async def _handle_wiki_browse(self, ctx: commands.Context, rest: str) -> None:
        target_cat = "evolution"
        if rest:
            rest_compact = "".join(rest.lower().split())
            alias_hit = game_wiki_engine._CATEGORY_QUERY_ALIASES.get(rest_compact)
            if alias_hit:
                target_cat = alias_hit
            else:
                for ck, clabel in game_wiki_engine.CATEGORY_LABELS.items():
                    if rest_compact in ck.lower() or rest_compact in "".join(clabel.lower().split()):
                        target_cat = ck
                        break
        browse_view = await WikiBrowseView.create(category_key=target_cat, page=0)
        sent_msg = await ctx.send(embed=browse_view.build_embed(), view=browse_view)
        browse_view.message = sent_msg

    async def _handle_wiki_list(self, ctx: commands.Context) -> None:
        contribs = await asyncio.to_thread(game_wiki_engine.list_community_wiki_entries)
        if not contribs:
            await ctx.reply(
                "📭 目前還沒有群友透過指令補充的條目！\n"
                "💡 歡迎使用 `!wiki add <關鍵字> | <內容>` 或 `!wiki import [主題]`（可夾帶 `.txt`/`.md` 檔）來擴充資料庫！",
                mention_author=False,
            )
            return
        await ctx.send(embed=build_wiki_list_embed(contribs))

    async def _handle_wiki_del(self, ctx: commands.Context, rest: str) -> None:
        if not self.settings.enable_japanese_mode and not await self._has_community_mod_or_owner(ctx):
            await ctx.reply(
                "🔒 社群模式下僅限管理員（`manage_messages` / `manage_guild` / `administrator`）或 Bot 擁有者可刪除知識庫條目！",
                mention_author=False,
            )
            return
        if not rest:
            await ctx.reply(
                "⚠️ 請指定要刪除的補充條目編號，例如：`!wiki del contrib_1`（可先用 `!wiki list` 查看編號）。",
                mention_author=False,
            )
            return
        ok = await asyncio.to_thread(game_wiki_engine.delete_community_wiki_entry, rest)
        if ok:
            await ctx.reply(
                f"🗑️ 已從《殭屍學園》知識庫刪除群友補充條目 **`{rest}`**，並即時更新檢索索引！",
                mention_author=False,
            )
        else:
            await ctx.reply(
                f"⚠️ 找不到編號為 `{rest}` 的群友補充條目（僅限刪除透過 `!wiki add/import` 新增的 `contrib_N` 條目）。",
                mention_author=False,
            )

    async def _handle_wiki_replace(self, ctx: commands.Context, rest: str) -> None:
        if not self.settings.enable_japanese_mode and not await self._has_community_mod_or_owner(ctx):
            await ctx.reply(
                "🔒 社群模式下僅限管理員（`manage_messages` / `manage_guild` / `administrator`）或 Bot 擁有者可執行知識庫熱修補覆寫！",
                mention_author=False,
            )
            return
        if not rest or ("|" not in rest and "｜" not in rest):
            await ctx.reply(
                "🔄 **《殭屍學園》知識庫條目熱修補／覆寫用法**：\n"
                "• `!wiki replace <舊條目ID或關鍵字> | <更新後的內容>`\n"
                "• 範例：`!wiki replace slang_dkj | 最新改版說明...`\n"
                "💡 若不確定舊條目 ID，可先輸入 `!wiki <關鍵字>` 查看該條目底部的 `ID: ...`。",
                mention_author=False,
            )
            return

        pipe_idx = rest.find("|")
        full_pipe_idx = rest.find("｜")
        sep = "|" if (pipe_idx != -1 and (full_pipe_idx == -1 or pipe_idx < full_pipe_idx)) else "｜"
        target_key, new_content = rest.split(sep, 1)
        target_key = target_key.strip()
        new_content = new_content.strip()
        if not target_key or not new_content:
            await ctx.reply(
                "⚠️ 請同時提供「舊條目 ID（或關鍵字）」與「更新後的內容」，例如：\n"
                "`!wiki replace slang_dkj | 最新改版說明...`",
                mention_author=False,
            )
            return

        if self.settings.enable_safety_guardrail:
            guard_res = safety_guardrail.check_prompt_injection(
                new_content,
                enable_japanese_mode=self.settings.enable_japanese_mode,
            )
            if not guard_res.allowed:
                await ctx.reply(
                    guard_res.reply or "⚠️ 內容包含不允許的系統指令語法，無法寫入知識庫。",
                    mention_author=False,
                )
                return

        author_display = getattr(ctx.author, "display_name", str(ctx.author))
        res = await asyncio.to_thread(
            game_wiki_engine.replace_wiki_entry_with_community_hotfix,
            target_key,
            new_content,
            author_name=author_display,
        )
        if not res.get("success"):
            await ctx.reply(f"⚠️ {res.get('error', '熱修補失敗。')}", mention_author=False)
            return

        embed = build_wiki_replace_embed(res["replaced_entry"], res["new_entry"])
        await ctx.reply(embed=embed, mention_author=False)

    async def _collect_wiki_submission_text(
        self,
        ctx: commands.Context,
        rest: str,
    ) -> Optional[tuple[str, str]]:
        """Read `.txt`/`.md` attachments and inline text; returns None if validation/read failed."""
        raw_text_sources: list[str] = []
        default_title = ""

        text_atts = [att for att in ctx.message.attachments if att.filename.lower().endswith((".txt", ".md"))]
        declared_total_bytes = sum((att.size or 0) for att in text_atts)
        if declared_total_bytes > MAX_TEXT_ATTACHMENT_BYTES:
            await ctx.reply(
                f"⚠️ 附件總大小超過 {MAX_TEXT_ATTACHMENT_BYTES // (1024 * 1024)}MB 上限，請分批匯入。",
                mention_author=False,
            )
            return None

        total_read_bytes = 0
        for att in text_atts:
            try:
                file_bytes = await att.read()
            except Exception as e:
                self.logger.warning(f"⚠️ 讀取知識庫附件 {att.filename} 失敗: {e}")
                await ctx.reply(f"⚠️ 讀取附件 `{att.filename}` 失敗，請確認檔案為有效的 UTF-8 純文字檔。", mention_author=False)
                return None

            total_read_bytes += len(file_bytes)
            if total_read_bytes > MAX_TEXT_ATTACHMENT_BYTES:
                await ctx.reply(
                    f"⚠️ 附件總大小超過 {MAX_TEXT_ATTACHMENT_BYTES // (1024 * 1024)}MB 上限，請分批匯入。",
                    mention_author=False,
                )
                return None

            file_str = file_bytes.decode("utf-8", errors="replace").strip()
            if file_str:
                raw_text_sources.append(file_str)
                if not default_title:
                    default_title = Path(att.filename).stem

        if rest:
            if raw_text_sources and "\n" not in rest and "|" not in rest and "｜" not in rest and "【" not in rest:
                default_title = rest.strip()
            else:
                raw_text_sources.insert(0, rest)

        return "\n\n".join(raw_text_sources).strip(), default_title

    async def _commit_wiki_entries(
        self,
        ctx: commands.Context,
        combined_text: str,
        default_title: str = "",
    ) -> None:
        if self.settings.enable_safety_guardrail:
            guard_res = safety_guardrail.check_prompt_injection(
                combined_text,
                enable_japanese_mode=self.settings.enable_japanese_mode,
            )
            if not guard_res.allowed:
                await ctx.reply(
                    guard_res.reply or "⚠️ 內容包含不允許的系統指令語法，無法寫入知識庫。",
                    mention_author=False,
                )
                return

        parsed_entries = await asyncio.to_thread(
            game_wiki_engine.parse_community_wiki_blocks,
            combined_text,
            default_title=default_title,
        )
        if not parsed_entries:
            await ctx.reply("⚠️ 無法從輸入內容解析出有效條目，請確認文本不為空。", mention_author=False)
            return

        if len(parsed_entries) > game_wiki_engine.MAX_COMMUNITY_IMPORT_BATCH:
            await ctx.reply(
                f"⚠️ 單次最多僅限新增或匯入 {game_wiki_engine.MAX_COMMUNITY_IMPORT_BATCH} 筆群友條目"
                f"（本次解析出 {len(parsed_entries)} 筆），請分段貼上或分批匯入！",
                mention_author=False,
            )
            return

        parsed_entries = await game_wiki_engine.enrich_community_entries_with_ai(
            self.gemini_client,
            self.settings.models_to_try,
            parsed_entries,
            disable_safety_settings=self.settings.disable_safety_settings,
            final_fallback_model=self.settings.final_fallback_model,
        )

        author_display = getattr(ctx.author, "display_name", str(ctx.author))
        try:
            added = await asyncio.to_thread(
                game_wiki_engine.add_community_wiki_entries,
                parsed_entries,
                author_name=author_display,
            )
        except ValueError as e:
            await ctx.reply(f"⚠️ {e}", mention_author=False)
            return
        stats = await asyncio.to_thread(game_wiki_engine.get_wiki_stats)

        embed = build_wiki_import_result_embed(added, stats["total"])
        await ctx.reply(embed=embed, mention_author=False)

    async def _handle_wiki_add(self, ctx: commands.Context, rest: str) -> None:
        if ctx.message.attachments:
            await self._handle_wiki_import(ctx, rest)
            return
        if not rest.strip():
            await ctx.reply(_WIKI_IMPORT_GUIDE, mention_author=False)
            return
        await self._commit_wiki_entries(ctx, rest.strip())

    async def _handle_wiki_import(self, ctx: commands.Context, rest: str) -> None:
        collected = await self._collect_wiki_submission_text(ctx, rest)
        if collected is None:
            return
        combined_text, default_title = collected
        if not combined_text:
            await ctx.reply(_WIKI_IMPORT_GUIDE, mention_author=False)
            return
        await self._commit_wiki_entries(ctx, combined_text, default_title=default_title)

    async def _handle_wiki_search(self, ctx: commands.Context, raw_arg: str) -> None:
        search_res = await asyncio.to_thread(game_wiki_engine.search_wiki_interactive, raw_arg)
        mode = search_res["mode"]

        if mode == "category":
            browse_view = await WikiBrowseView.create(category_key=search_res["category_key"], page=0)
            sent_msg = await ctx.send(embed=browse_view.build_embed(), view=browse_view)
            browse_view.message = sent_msg
            return

        if mode in ("results", "suggestions"):
            results = search_res["results"]
            related_entries = search_res["related_entries"]
            dynamic_notes = search_res["dynamic_notes"]

            if results:
                display_entries = (results + related_entries)[:25]
                related_ids = {r["id"] for r in related_entries}
                search_view = WikiSearchView(
                    query=raw_arg,
                    entries=display_entries,
                    focused_snippets=search_res["focused_snippets"],
                    dynamic_notes=dynamic_notes,
                    related_ids=related_ids,
                    is_suggestion_mode=(mode == "suggestions"),
                )
                sent_msg = await ctx.send(embed=search_view.build_embed(), view=search_view)
                search_view.message = sent_msg
                return

            if dynamic_notes:
                await ctx.send(embed=build_wiki_disambiguation_embed(raw_arg, dynamic_notes))
                return

        overview_view = await WikiOverviewView.create()
        sent_msg = await ctx.send(embed=build_wiki_empty_search_embed(raw_arg), view=overview_view)
        overview_view.message = sent_msg

    @commands.command(name="wiki", aliases=["圖鑑", "黑話", "좀고"])
    async def wiki_command(self, ctx: commands.Context, *, query: Optional[str] = None):
        """查詢與擴充《殭屍學園 (좀비고등학교)》知識庫。"""
        if not is_target_channel(ctx.channel, self.settings):
            return

        if not self.settings.enable_game_wiki:
            await ctx.send("ℹ️ 此伺服器目前未啟用《殭屍學園》知識庫功能。")
            return

        raw_arg = (query or "").strip()
        has_text_attachment = any(
            att.filename.lower().endswith((".txt", ".md")) for att in ctx.message.attachments
        )
        if not raw_arg and has_text_attachment:
            raw_arg = "import"

        if not raw_arg:
            await self._handle_wiki_overview(ctx)
            return

        parts = raw_arg.split(maxsplit=1)
        sub = parts[0].lower()
        rest = parts[1].strip() if len(parts) > 1 else ""

        if sub in ("browse", "dir", "cat", "category", "目錄", "分類", "瀏覽"):
            await self._handle_wiki_browse(ctx, rest)
        elif sub in ("list", "contrib", "清單", "補充清單"):
            await self._handle_wiki_list(ctx)
        elif sub in ("del", "delete", "remove", "rm", "刪除"):
            await self._handle_wiki_del(ctx, rest)
        elif sub in ("replace", "hotfix", "override", "update", "替換", "覆寫", "勘誤"):
            await self._handle_wiki_replace(ctx, rest)
        elif sub in ("add", "新增", "補充"):
            await self._handle_wiki_add(ctx, rest)
        elif sub in ("import", "匯入"):
            await self._handle_wiki_import(ctx, rest)
        else:
            await self._handle_wiki_search(ctx, raw_arg)
