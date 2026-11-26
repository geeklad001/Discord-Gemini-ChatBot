"""Assistant Cog: !reset, !reload, !todo, !remind, !memory, !help, and reminder loop."""

import asyncio
import io
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import discord
from discord.ext import commands, tasks

from cogs.feedback_views import (
    FeedbackListView,
    build_feedback_detail_embed,
    build_feedback_list_embed,
)
from engines import assistant_db, assistant_tools, game_wiki_engine, safety_guardrail
from core.chat_pipeline import is_target_channel, record_channel_reset_timestamp
from core.config import (
    BASE_DIR,
    BotSettings,
    apply_runtime_overrides,
    init_runtime_env,
    is_bot_owner,
    load_settings,
    resolve_prompt_path,
    truncate_embed_text,
)
from core.help_embeds import (
    build_help_overview_embed,
    build_help_topic_embed,
    build_lore_help_embed,
    build_rpg_help_embed,
    build_system_help_embed,
    build_tts_help_embed,
    build_wiki_help_embed,
)
from core.llm_client import get_active_model_cooldowns, reset_model_cooldowns


class AssistantCog(commands.Cog):
    """生活助理、待辦、提醒排程、長期記憶與系統指令。"""

    def __init__(
        self,
        bot: commands.Bot,
        settings: BotSettings,
        logger: logging.Logger,
        gemini_client=None,
    ):
        self.bot = bot
        self.settings = settings
        self.logger = logger
        self.gemini_client = gemini_client
        self._reminder_fail_counts: dict[int, int] = {}

    async def _has_admin_or_owner(self, ctx: commands.Context) -> bool:
        if await is_bot_owner(self.bot, self.settings, ctx.author):
            return True
        perms = getattr(ctx.author, "guild_permissions", None)
        return bool(perms and (getattr(perms, "administrator", False) or getattr(perms, "manage_guild", False)))

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

    async def cog_load(self) -> None:
        if not self.reminder_check_loop.is_running():
            self.reminder_check_loop.start()

    async def cog_unload(self) -> None:
        if self.reminder_check_loop.is_running():
            self.reminder_check_loop.cancel()

    @tasks.loop(seconds=10)
    async def reminder_check_loop(self):
        """背景定時排程：每 10 秒檢查是否有到期的提醒事項並推播至 Discord。"""
        due_items = await asyncio.to_thread(assistant_db.get_pending_due_reminders)
        for rem in due_items:
            rem_id = rem["id"]
            try:
                channel = self.bot.get_channel(rem["channel_id"])
                origin_note = ""

                if channel is None and rem.get("channel_id"):
                    try:
                        channel = await self.bot.fetch_channel(rem["channel_id"])
                    except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                        channel = None

                if not channel:
                    fallback_id = self.settings.general_channel_id or self.settings.target_channel_id
                    if fallback_id:
                        channel = self.bot.get_channel(fallback_id)
                        if channel is None:
                            try:
                                channel = await self.bot.fetch_channel(fallback_id)
                            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                                channel = None
                    origin_note = "（原討論串已刪除，自動轉移至此通知）"

                if channel:
                    user_ping = f"<@{rem['user_id']}>"
                    embed = discord.Embed(
                        title="⏰ 提醒時間到了！",
                        description=truncate_embed_text(f"**{rem['content']}**", max_len=3800),
                        color=0xFFD166,
                    )
                    footer_text = f"來自 {rem['user_name']} 的定時提醒"
                    if origin_note:
                        footer_text += f" • {origin_note}"
                    embed.set_footer(text=footer_text[:2000])
                    await channel.send(content=user_ping, embed=embed)
                    self.logger.info(f"⏰ 已成功發送定時提醒給 {rem['user_name']} (ID: {rem_id}): {rem['content']}")
                await asyncio.to_thread(assistant_db.mark_reminder_sent, rem_id)
                self._reminder_fail_counts.pop(rem_id, None)
            except discord.HTTPException as e:
                status = getattr(e, "status", None)
                self.logger.error(f"❌ 發送定時提醒失敗 (ID: {rem_id}, status={status}): {e}")
                if status in (400, 403, 404):
                    await asyncio.to_thread(assistant_db.mark_reminder_sent, rem_id)
                    self._reminder_fail_counts.pop(rem_id, None)
                else:
                    fails = self._reminder_fail_counts.get(rem_id, 0) + 1
                    self._reminder_fail_counts[rem_id] = fails
                    if fails >= 3:
                        self.logger.warning(f"⚠️ 定時提醒 (ID: {rem_id}) 連續失敗 {fails} 次，已放棄重試並標記完成。")
                        await asyncio.to_thread(assistant_db.mark_reminder_sent, rem_id)
                        self._reminder_fail_counts.pop(rem_id, None)
            except Exception as e:
                self.logger.error(f"❌ 發送定時提醒失敗 (ID: {rem_id}): {e}")
                fails = self._reminder_fail_counts.get(rem_id, 0) + 1
                self._reminder_fail_counts[rem_id] = fails
                if fails >= 3:
                    self.logger.warning(f"⚠️ 定時提醒 (ID: {rem_id}) 連續失敗 {fails} 次，已放棄重試並標記完成。")
                    await asyncio.to_thread(assistant_db.mark_reminder_sent, rem_id)
                    self._reminder_fail_counts.pop(rem_id, None)

    @reminder_check_loop.before_loop
    async def before_reminder_loop(self):
        await self.bot.wait_until_ready()

    @commands.command(name="reset", aliases=["clear", "重設", "重置"])
    async def reset_context(self, ctx: commands.Context):
        """手動重設當前頻道或討論串的上下文記憶（並持久化至 SQLite）。"""
        if not is_target_channel(ctx.channel, self.settings) and not await is_bot_owner(
            self.bot, self.settings, ctx.author
        ):
            return

        if not self.settings.enable_japanese_mode and not await self._has_community_mod_or_owner(ctx):
            await ctx.send("🔒 社群模式下僅限管理員（管理訊息權限）或機器人擁有者可重設對話記憶。")
            return

        now = datetime.now(timezone.utc)
        cid = ctx.channel.id

        if isinstance(ctx.channel, discord.Thread):
            await asyncio.to_thread(record_channel_reset_timestamp, cid, now)
            await ctx.send("🧹 **當前討論串的對話記憶已重設。** 隨時可以開啟新話題。")
            self.logger.info(f"討論串 {cid} 已重設上下文。")
            return

        if not self.settings.enable_japanese_mode or cid not in (
            self.settings.target_channel_id,
            self.settings.tts_channel_id,
        ):
            await asyncio.to_thread(record_channel_reset_timestamp, cid, now)
            await ctx.send("🧹 **對話記憶已重設。** 過去的上下文已清除，隨時可以開啟新話題。")
            self.logger.info(f"頻道 {cid} 已重設上下文。")
            return

        await asyncio.to_thread(record_channel_reset_timestamp, self.settings.target_channel_id, now)
        main_ch = self.bot.get_channel(self.settings.target_channel_id)
        if main_ch:
            await main_ch.send("🧹 **日語共學記憶已重設！** 過去的對話已清除，可以隨時開啟新話題。")
        elif cid == self.settings.target_channel_id:
            await ctx.send("🧹 **日語共學記憶已重設！** 過去的對話已清除，可以隨時開啟新話題。")

        if self.settings.tts_channel_id and self.settings.tts_channel_id != self.settings.target_channel_id:
            await asyncio.to_thread(record_channel_reset_timestamp, self.settings.tts_channel_id, now)
            tts_ch = self.bot.get_channel(self.settings.tts_channel_id)
            if tts_ch:
                await tts_ch.send("🧹 **日語共學記憶已重設！**")
            elif cid == self.settings.tts_channel_id:
                await ctx.send("🧹 **日語共學記憶已重設！**")

        self.logger.info("已重設日語學習雙頻道上下文記憶。")

    @commands.command(name="reload", aliases=["重載"])
    async def reload_command(self, ctx: commands.Context):
        """熱重載人設提示詞檔案與知識庫索引（修改 .txt / wiki 後可立即檢查生效）。"""
        if not is_target_channel(ctx.channel, self.settings) and not await is_bot_owner(
            self.bot, self.settings, ctx.author
        ):
            return

        if not await self._has_admin_or_owner(ctx):
            await ctx.send("🔒 僅限機器人擁有者或伺服器管理員可使用 `!reload`。")
            return

        checked: list[str] = []
        prompt_files = [self.settings.general_prompt_file]
        if self.settings.enable_japanese_mode:
            prompt_files.append(self.settings.system_prompt_file)

        def _read_prompt_sync(path: Path) -> Optional[str]:
            if not path.is_file():
                return None
            return path.read_text(encoding="utf-8").strip()

        for fname in prompt_files:
            p_path = resolve_prompt_path(fname)
            try:
                content = await asyncio.to_thread(_read_prompt_sync, p_path)
            except Exception as e:
                self.logger.error(f"❌ 讀取提示詞檔案 {p_path} 失敗: {e}", exc_info=True)
                await ctx.send("⚠️ 重新載入部分提示詞設定失敗，請查看後台日誌。")
                return
            if content is not None:
                checked.append(f"`{p_path.name}`（{len(content)} 字）")
            else:
                checked.append(f"`{p_path.name}`（未找到，將使用內建預設人設）")

        wiki_msg = ""
        if self.settings.enable_game_wiki:
            entries = await asyncio.to_thread(game_wiki_engine.load_wiki_entries, force_reload=True)
            wiki_msg = f"\n📚 遊戲知識庫已重新載入（共 {len(entries)} 筆條目）"

        env_msg = ""
        env_path = BASE_DIR / self.settings.env_file
        has_env_file = await asyncio.to_thread(env_path.is_file)
        if self.settings.discord_bot_token != "tok" and has_env_file:
            try:
                def _reload_env_sync():
                    init_runtime_env(argv=[f"--env={self.settings.env_file}"], override=True)
                    return load_settings(self.settings.env_file)

                refreshed = await asyncio.to_thread(_reload_env_sync)
                self.settings.primary_model = refreshed.primary_model
                self.settings.fallback_models = refreshed.fallback_models
                self.settings.final_fallback_model = refreshed.final_fallback_model
                self.settings.enable_user_voice_clone = refreshed.enable_user_voice_clone
                self.settings.tts_clone_approval_mode = refreshed.tts_clone_approval_mode
                self.settings.tts_max_chars = refreshed.tts_max_chars
                self.settings.tts_f5_max_chars = refreshed.tts_f5_max_chars
                self.settings.bot_owner_ids = refreshed.bot_owner_ids
                reset_model_cooldowns()
                clone_state = "開啟" if self.settings.enable_user_voice_clone else "關閉"
                env_msg = (
                    f"\n⚙️ `.env` 動態開關已同步（首選模型：`{self.settings.primary_model}`"
                    f"｜語音克隆：**{clone_state}**）"
                )
            except Exception as e:
                self.logger.warning(f"⚠️ 重新載入 .env 動態設定失敗: {e}")

        overrides = await asyncio.to_thread(assistant_db.get_runtime_config_overrides)
        applied_overrides = apply_runtime_overrides(self.settings, overrides)
        setup_cog = self.bot.get_cog("SetupCog")
        if setup_cog and hasattr(setup_cog, "sync_feature_cogs"):
            try:
                await setup_cog.sync_feature_cogs()
            except Exception as e:
                self.logger.warning(f"⚠️ !setup 模組狀態同步失敗: {e}")
        override_msg = (
            f"\n🧩 已保留 {len(applied_overrides)} 項 `!setup` 互動面板動態覆寫"
            if applied_overrides
            else ""
        )

        await ctx.send(f"🔄 **人設與設定已重新載入！**\n• 提示詞：{', '.join(checked)}{wiki_msg}{env_msg}{override_msg}")

    @commands.command(name="models", aliases=["模型"])
    async def models_command(self, ctx: commands.Context):
        """擁有者專用：檢視目前 Flash-Lite 輪替鏈、429 冷卻狀態與 AI Studio 即時可用模型。"""
        is_owner = await is_bot_owner(self.bot, self.settings, ctx.author)
        if not is_owner:
            if is_target_channel(ctx.channel, self.settings):
                await ctx.send("🔒 僅限機器人擁有者（Bot Owner）可使用 `!models` 查看模型診斷資訊。")
            return

        cooldowns = get_active_model_cooldowns()
        cd_text = (
            "\n".join(f"• `{m}`：剩餘 {sec} 秒" for m, sec in cooldowns.items())
            if cooldowns
            else "無（全部模型皆處於就緒狀態）"
        )
        embed = discord.Embed(
            title="🤖 Gemini 模型輪替與可用狀態",
            color=0x38BDF8,
        )
        embed.add_field(
            name="📌 目前設定的模型順序",
            value=(
                f"**首選 (`PRIMARY_MODEL`)**：`{self.settings.primary_model}`\n"
                f"**備援 (`FALLBACK_MODELS`)**：`{', '.join(self.settings.fallback_models) or '無'}`\n"
                f"**最終保底 (`FINAL_FALLBACK_MODEL`)**：`{self.settings.final_fallback_model or '已停用'}`"
            ),
            inline=False,
        )
        embed.add_field(name="⏳ 目前 429 冷卻中模型", value=truncate_embed_text(cd_text, 900), inline=False)

        if self.gemini_client and hasattr(getattr(self.gemini_client, "aio", None), "models"):
            try:
                lite_models: list[str] = []
                other_flash: list[str] = []
                pager = await asyncio.wait_for(self.gemini_client.aio.models.list(), timeout=10.0)
                async for item in pager:
                    raw_name = str(getattr(item, "name", "") or "")
                    short_name = raw_name.removeprefix("models/")
                    actions = [str(a) for a in (getattr(item, "supported_actions", None) or [])]
                    if actions and "generateContent" not in actions:
                        continue
                    low = short_name.lower()
                    if "flash-lite" in low:
                        lite_models.append(short_name)
                    elif "flash" in low or "hydrogen" in low or "gemma" in low:
                        other_flash.append(short_name)
                    if len(lite_models) + len(other_flash) >= 35:
                        break
                api_lines = []
                if lite_models:
                    api_lines.append("**Flash-Lite 系列**：\n" + "\n".join(f"• `{m}`" for m in lite_models[:12]))
                if other_flash:
                    api_lines.append("**Flash / Hydrogen / Gemma 備援系列**：\n" + "\n".join(f"• `{m}`" for m in other_flash[:12]))
                embed.add_field(
                    name="🌐 AI Studio 即時可用模型（節錄）",
                    value=truncate_embed_text("\n".join(api_lines) if api_lines else "查無支援文字生成的模型", 950),
                    inline=False,
                )
            except Exception as exc:
                self.logger.warning(f"⚠️ 查詢 AI Studio 模型清單失敗: {exc}")
                embed.add_field(
                    name="🌐 AI Studio 即時可用模型",
                    value="⚠️ 暫時無法連線取得模型清單，請稍後再試。",
                    inline=False,
                )

        await ctx.send(embed=embed)

    @commands.command(name="todo", aliases=["td", "todos"])
    async def todo_command(self, ctx: commands.Context, action: str = None, *, arg: str = None):
        """待辦事項清單指令。"""
        if not is_target_channel(ctx.channel, self.settings):
            return

        if not action or action.lower() in ("list", "show", "ls"):
            todos = await asyncio.to_thread(assistant_db.list_todos, only_pending=True)
            if not todos:
                await ctx.send("📋 目前沒有任何未完成的待辦事項。")
                return
            desc_lines = [
                f"**#{t['id']}** {t['content']}\n└ *由 {t['user_name']} 於 {t['created_at'][5:16]} 新增*"
                for t in todos
            ]
            embed = discord.Embed(
                title="📋 待辦事項清單",
                description=truncate_embed_text("\n".join(desc_lines)),
                color=0x48CAE4,
            )
            embed.set_footer(text="💡 完成請輸入 !todo done <編號>，或直接跟機器人說「我做完了」")
            await ctx.send(embed=embed)
            return

        act = action.lower()
        if act in ("add", "new", "+"):
            if not arg or not arg.strip():
                await ctx.send("❓ 請輸入要新增的待辦事項內容，例如：`!todo add 買牛奶`")
                return
            clean_todo = arg.strip()
            tid = await asyncio.to_thread(assistant_db.add_todo, clean_todo, user_name=ctx.author.display_name)
            await ctx.send(f"✅ 已成功新增待辦事項 **#{tid}**：{clean_todo[:1800]}")

        elif act in ("done", "finish", "ok", "check", "v"):
            if not arg or not arg.strip():
                await ctx.send("❓ 請輸入要完成的待辦編號或關鍵字，例如：`!todo done 1` 或 `!todo done 牛奶`")
                return
            target = arg.strip()
            completed = (
                await asyncio.to_thread(assistant_db.complete_todo_by_id, int(target))
                if target.isdigit()
                else None
            )
            if not completed:
                completed = await asyncio.to_thread(assistant_db.complete_todo_by_keyword, target)

            if completed:
                await ctx.send(f"🎉 已完成待辦事項 **#{completed['id']}**：~~{str(completed['content'])[:1800]}~~")
            else:
                await ctx.send(f"❌ 找不到符合「{target[:1800]}」的未完成待辦事項。")

        elif act in ("del", "delete", "rm", "remove", "-"):
            if not self.settings.enable_japanese_mode and not await self._has_community_mod_or_owner(ctx):
                await ctx.send("🔒 社群模式下僅限管理員（管理訊息權限）或機器人擁有者可刪除待辦事項。")
                return
            if not arg or not arg.strip().isdigit():
                await ctx.send("❓ 請輸入要刪除的待辦事項編號，例如：`!todo del 1`")
                return
            tid = int(arg.strip())
            if await asyncio.to_thread(assistant_db.delete_todo, tid):
                await ctx.send(f"🗑️ 已刪除待辦事項 **#{tid}**。")
            else:
                await ctx.send(f"❌ 找不到待辦事項 **#{tid}**。")

        elif act in ("all", "history"):
            todos = await asyncio.to_thread(assistant_db.list_todos, only_pending=False)
            if not todos:
                await ctx.send("📋 目前待辦清單完全是空的。")
                return
            desc_lines = []
            for t in todos[:25]:
                status_icon = "✅" if t["is_done"] else "⬜"
                content_display = f"~~{t['content']}~~" if t["is_done"] else f"**{t['content']}**"
                desc_lines.append(f"{status_icon} **#{t['id']}** {content_display} ({t['user_name']})")
            embed = discord.Embed(
                title="📋 待辦事項歷史紀錄（最近 25 項）",
                description=truncate_embed_text("\n".join(desc_lines)),
                color=0x48CAE4,
            )
            await ctx.send(embed=embed)
        else:
            full_content = f"{action} {arg}" if arg else action
            clean_todo = full_content.strip()
            tid = await asyncio.to_thread(assistant_db.add_todo, clean_todo, user_name=ctx.author.display_name)
            await ctx.send(f"✅ 已成功新增待辦事項 **#{tid}**：{clean_todo[:1800]}")

    @commands.command(name="remind", aliases=["reminder", "rem"])
    async def remind_command(self, ctx: commands.Context, time_spec: str = None, *, content: str = None):
        """定時提醒設定指令。"""
        if not is_target_channel(ctx.channel, self.settings):
            return

        if not time_spec or time_spec.lower() in ("list", "show", "ls"):
            rems = await asyncio.to_thread(assistant_db.list_upcoming_reminders)
            if not self.settings.enable_japanese_mode and not await is_bot_owner(
                self.bot, self.settings, ctx.author
            ):
                rems = [
                    r for r in rems
                    if r.get("channel_id") == ctx.channel.id or r.get("user_id") == ctx.author.id
                ]
            if not rems:
                await ctx.send("⏰ 目前沒有任何預約中的定時提醒。")
                return
            lines = [
                f"**#{r['id']}** 預定於 `{r['remind_at'][:16]}`\n└ 提醒內容：{r['content']} ({r['user_name']})"
                for r in rems
            ]
            embed = discord.Embed(
                title="⏰ 預約中的定時提醒清單",
                description=truncate_embed_text("\n".join(lines)),
                color=0xFFD166,
            )
            embed.set_footer(text="💡 取消提醒請輸入 !remind del <編號>")
            await ctx.send(embed=embed)
            return

        if time_spec.lower() in ("del", "delete", "rm", "cancel"):
            if not content or not content.strip().isdigit():
                await ctx.send("❓ 請輸入要取消的提醒編號，例如：`!remind del 1`")
                return
            rid = int(content.strip())
            ok, info = await asyncio.to_thread(assistant_db.cancel_reminder, rid, user_id=ctx.author.id)
            if ok:
                await ctx.send(f"🗑️ 已取消提醒事項 **#{rid}**（{str(info)[:1800]}）。")
            else:
                await ctx.send(f"❌ 無法取消提醒 **#{rid}**：{str(info)[:1800]}")
            return

        if not content or not content.strip():
            await ctx.send("❓ 請提供提醒時間與內容，例如：`!remind 10m 關火` 或 `!remind 18:30 倒垃圾`")
            return

        target_dt = assistant_tools.parse_time_argument(time_spec)
        if not target_dt:
            await ctx.send(f"❌ 無法辨識時間格式「{time_spec[:200]}」，請嘗試：`10m`、`2h`、`18:30` 或 `2026-10-31 15:00`")
            return

        now = datetime.now(assistant_db.TZ_TAIPEI)
        if target_dt <= now:
            await ctx.send("❌ 設定的時間已經過去了，請設定未來的時間。")
            return

        clean_rem = content.strip()
        rid = await asyncio.to_thread(
            assistant_db.add_reminder,
            channel_id=ctx.channel.id,
            user_id=ctx.author.id,
            user_name=ctx.author.display_name,
            remind_at_iso=target_dt.isoformat(),
            content=clean_rem,
        )
        formatted = target_dt.strftime("%Y-%m-%d %H:%M:%S")
        await ctx.send(f"⏰ 已設定定時提醒 **#{rid}**！\n我會在 `{formatted}` 於此頻道提醒你：**{clean_rem[:1800]}**")

    @commands.command(name="reminds", aliases=["reminders"])
    async def list_reminds_shortcut(self, ctx: commands.Context):
        """查看預約中的提醒清單。"""
        await self.remind_command(ctx, time_spec="list")

    @commands.command(name="memory", aliases=["mem", "memories"])
    async def memory_command(self, ctx: commands.Context, action: str = None, *, arg: str = None):
        """長期記憶庫管理指令。"""
        if not is_target_channel(ctx.channel, self.settings):
            return

        if not action or action.lower() in ("list", "show", "ls"):
            memories = await asyncio.to_thread(assistant_db.get_all_memories)
            if not memories:
                await ctx.send("🧠 目前長期記憶庫是空的。可以在聊天中隨口說「記住喔，我喜歡...」，或輸入 `!remember <事情>` 來記錄！")
                return
            lines = []
            for m in memories:
                user_part = f"[{m['user_name']}] " if m["user_name"] else ""
                lines.append(f"**#{m['id']}** {user_part}{m['content']}\n└ *存於 {m['created_at'][5:16]}*")
            mem_title = "🧠 情侶長期記憶庫" if self.settings.enable_japanese_mode else "🧠 群組長期記憶與共識庫"
            embed = discord.Embed(
                title=mem_title,
                description=truncate_embed_text("\n".join(lines)),
                color=0xBDB2FF,
            )
            embed.set_footer(text="💡 刪除記憶請輸入 !forget <編號>，或隨時在對話中更新")
            await ctx.send(embed=embed)
            return

        act = action.lower()
        if act in ("add", "new", "+"):
            if not arg or not arg.strip():
                example_text = "女友最愛吃抹茶" if self.settings.enable_japanese_mode else "每週六晚上八點固定打團"
                await ctx.send(f"❓ 請輸入要記住的事實，例如：`!remember {example_text}`")
                return
            clean_mem = arg.strip()
            if self.settings.enable_safety_guardrail:
                inj = safety_guardrail.check_prompt_injection(
                    clean_mem,
                    enable_japanese_mode=self.settings.enable_japanese_mode,
                    max_chars=500,
                )
                if not inj.allowed:
                    await ctx.send(inj.reply or "⚠️ 內容包含不允許的指令語法或過長。")
                    return
            mid = await asyncio.to_thread(assistant_db.add_memory, clean_mem, user_name=ctx.author.display_name)
            await ctx.send(f"🧠 已存入長期記憶庫 **#{mid}**：{clean_mem[:1500]}")

        elif act in ("del", "delete", "rm", "forget", "-"):
            if not self.settings.enable_japanese_mode and not await self._has_community_mod_or_owner(ctx):
                await ctx.send("🔒 社群模式下僅限管理員（管理訊息權限）或機器人擁有者可刪除長期記憶。")
                return
            if not arg or not arg.strip().isdigit():
                await ctx.send("❓ 請輸入要刪除的記憶編號，例如：`!forget 1`")
                return
            mid = int(arg.strip())
            if await asyncio.to_thread(assistant_db.delete_memory, mid):
                await ctx.send(f"🗑️ 已從長期記憶庫刪除 **#{mid}**。")
            else:
                await ctx.send(f"❌ 找不到記憶編號 **#{mid}**。")
        else:
            full_content = f"{action} {arg}" if arg else action
            clean_mem = full_content.strip()
            if self.settings.enable_safety_guardrail:
                inj = safety_guardrail.check_prompt_injection(
                    clean_mem,
                    enable_japanese_mode=self.settings.enable_japanese_mode,
                    max_chars=500,
                )
                if not inj.allowed:
                    await ctx.send(inj.reply or "⚠️ 內容包含不允許的指令語法或過長。")
                    return
            mid = await asyncio.to_thread(assistant_db.add_memory, clean_mem, user_name=ctx.author.display_name)
            await ctx.send(f"🧠 已存入長期記憶庫 **#{mid}**：{clean_mem[:1500]}")

    @commands.command(name="remember")
    async def remember_shortcut(self, ctx: commands.Context, *, content: Optional[str] = None):
        """快速存入長期記憶：!remember <事實>"""
        await self.memory_command(ctx, action="add", arg=content)

    @commands.command(name="forget")
    async def forget_shortcut(self, ctx: commands.Context, memory_id: Optional[str] = None):
        """快速刪除長期記憶：!forget <編號>"""
        await self.memory_command(ctx, action="del", arg=str(memory_id) if memory_id is not None else None)

    async def _extract_reply_context_for_feedback(self, message: discord.Message) -> str:
        """從使用者回覆的訊息截取現場脈絡摘錄（不顯示任何回報者身分）。"""
        ref = getattr(message, "reference", None)
        if ref is None:
            return ""
        ref_msg = getattr(ref, "resolved", None)
        if not isinstance(ref_msg, discord.Message) and not (
            hasattr(ref_msg, "content") or hasattr(ref_msg, "embeds")
        ):
            ref_msg = None
            ref_id = getattr(ref, "message_id", None)
            if ref_id and hasattr(message.channel, "fetch_message"):
                try:
                    ref_msg = await message.channel.fetch_message(ref_id)
                except Exception:
                    ref_msg = None
        if ref_msg is None:
            return ""
        parts: list[str] = []
        raw_clean = getattr(ref_msg, "clean_content", None)
        raw_plain = getattr(ref_msg, "content", None)
        text_body = (
            raw_clean if isinstance(raw_clean, str) and raw_clean.strip()
            else (raw_plain if isinstance(raw_plain, str) else "")
        ).strip()
        if text_body:
            parts.append(text_body[:350])
        embeds = getattr(ref_msg, "embeds", None)
        if isinstance(embeds, (list, tuple)) and embeds:
            emb = embeds[0]
            emb_title = getattr(emb, "title", None)
            emb_desc = getattr(emb, "description", None)
            emb_joined = "｜".join(
                x for x in (
                    emb_title.strip() if isinstance(emb_title, str) else "",
                    emb_desc.strip() if isinstance(emb_desc, str) else "",
                ) if x
            )
            if emb_joined:
                parts.append(emb_joined[:250])
        excerpt = "\n".join(parts).strip()
        if not excerpt:
            return ""
        raw_url = getattr(ref_msg, "jump_url", None)
        ref_url = raw_url.strip() if isinstance(raw_url, str) else ""
        header = f"[被回覆訊息] ({ref_url})" if ref_url else "[被回覆訊息]"
        return f"{header}\n{excerpt}"[:550]

    @commands.command(name="feedback", aliases=["fb", "回報", "建議"])
    async def feedback_command(self, ctx: commands.Context, action: str = None, *, arg: str = None):
        """非即時回饋／建議信箱與公開看板（嚴格隱藏回報者身分且永不提及回報者）。"""
        if not is_target_channel(ctx.channel, self.settings) and not await is_bot_owner(
            self.bot, self.settings, ctx.author
        ):
            return

        no_mention = discord.AllowedMentions.none()
        act_raw = (action or "").strip()
        act = act_raw.lower()

        # 1. 公開看板：!feedback / !feedback list [status/category]
        if not act_raw or act in ("list", "ls", "show", "清單", "列表"):
            status_filter = "open"
            category_filter: Optional[str] = None
            status_token_map = {
                "open": "open",
                "active": "open",
                "pending": "open",
                "待處理": "open",
                "triaged": "triaged",
                "排程": "triaged",
                "已確認": "triaged",
                "resolved": "resolved",
                "done": "resolved",
                "fixed": "resolved",
                "已解決": "resolved",
                "已完成": "resolved",
                "wontfix": "wontfix",
                "closed": "wontfix",
                "暫不處理": "wontfix",
                "已關閉": "wontfix",
                "all": "all",
                "全部": "all",
            }
            for tok in (arg or "").split():
                low = tok.strip().lower()
                if low in status_token_map:
                    status_filter = status_token_map[low]
                elif low in assistant_db.FEEDBACK_CATEGORY_ALIASES:
                    category_filter = assistant_db.FEEDBACK_CATEGORY_ALIASES[low]

            view = FeedbackListView(status_filter=status_filter, category_filter=category_filter)
            embed = await view.reload_data()
            sent = await ctx.send(embed=embed, view=view, allowed_mentions=no_mention)
            view.message = sent
            return

        # 2. 公開詳情：!feedback view <編號> 或 !feedback #<編號>
        if act in ("view", "detail", "查看", "詳情") or (act_raw.lstrip("#").isdigit() and not (arg or "").strip()):
            id_token = (arg or "").strip().lstrip("#") if act in ("view", "detail", "查看", "詳情") else act_raw.lstrip("#")
            if not id_token.isdigit():
                await ctx.send("❓ 請輸入要查看的回饋編號，例如：`!feedback view 3`", allowed_mentions=no_mention)
                return
            fid = int(id_token)
            report = await asyncio.to_thread(assistant_db.get_feedback_report, fid)
            if not report:
                await ctx.send(f"❌ 找不到回饋編號 **#{fid}**。", allowed_mentions=no_mention)
                return
            await ctx.send(embed=build_feedback_detail_embed(report), allowed_mentions=no_mention)
            return

        # 3. 狀態管理（管理員或機器人擁有者）：done / triage / close / reopen
        status_action_map = {
            "done": ("resolved", "✅ 已解決"),
            "resolve": ("resolved", "✅ 已解決"),
            "resolved": ("resolved", "✅ 已解決"),
            "fixed": ("resolved", "✅ 已解決"),
            "完成": ("resolved", "✅ 已解決"),
            "已修": ("resolved", "✅ 已解決"),
            "triage": ("triaged", "🔵 已確認／排程中"),
            "confirm": ("triaged", "🔵 已確認／排程中"),
            "accept": ("triaged", "🔵 已確認／排程中"),
            "確認": ("triaged", "🔵 已確認／排程中"),
            "排程": ("triaged", "🔵 已確認／排程中"),
            "close": ("wontfix", "⚪ 暫不處理／關閉"),
            "wontfix": ("wontfix", "⚪ 暫不處理／關閉"),
            "reject": ("wontfix", "⚪ 暫不處理／關閉"),
            "關閉": ("wontfix", "⚪ 暫不處理／關閉"),
            "reopen": ("open", "🟡 重新開啟"),
            "重開": ("open", "🟡 重新開啟"),
        }
        if act in status_action_map:
            if not await self._has_community_mod_or_owner(ctx):
                await ctx.send("🔒 僅限伺服器管理員或機器人擁有者可更新回饋處理狀態。", allowed_mentions=no_mention)
                return
            parts = (arg or "").strip().split(None, 1)
            if not parts or not parts[0].lstrip("#").isdigit():
                await ctx.send(f"❓ 請輸入回饋編號，例如：`!feedback {act} 3 已修正`", allowed_mentions=no_mention)
                return
            fid = int(parts[0].lstrip("#"))
            note = parts[1].strip() if len(parts) > 1 else ""
            target_status, label = status_action_map[act]
            updated = await asyncio.to_thread(assistant_db.update_feedback_status, fid, target_status, note)
            if not updated:
                await ctx.send(f"❌ 找不到回饋編號 **#{fid}**。", allowed_mentions=no_mention)
                return
            note_part = f"\n└ 備註：{str(updated['resolution_note'])[:400]}" if updated.get("resolution_note") else ""
            await ctx.send(
                f"{label} 回饋 **#{fid}**（{str(updated['summary'])[:120]}）{note_part}",
                allowed_mentions=no_mention,
            )
            return

        # 4. 刪除回饋（管理員或擁有者）
        if act in ("del", "delete", "rm", "刪除"):
            if not await self._has_admin_or_owner(ctx):
                await ctx.send("🔒 僅限伺服器管理員（管理訊息/管理伺服器權限）或機器人擁有者可刪除回饋紀錄。", allowed_mentions=no_mention)
                return
            id_token = (arg or "").strip().lstrip("#")
            if not id_token.isdigit():
                await ctx.send("❓ 請輸入要刪除的回饋編號，例如：`!feedback del 3`", allowed_mentions=no_mention)
                return
            fid = int(id_token)
            if await asyncio.to_thread(assistant_db.delete_feedback_report, fid):
                await ctx.send(f"🗑️ 已刪除回饋紀錄 **#{fid}**。", allowed_mentions=no_mention)
            else:
                await ctx.send(f"❌ 找不到回饋編號 **#{fid}**。", allowed_mentions=no_mention)
            return

        # 5. 匯出 Markdown（管理員或擁有者，隱藏回報者身分）
        if act in ("export", "匯出"):
            if not await self._has_admin_or_owner(ctx):
                await ctx.send("🔒 僅限伺服器管理員或機器人擁有者可匯出回饋清單。", allowed_mentions=no_mention)
                return
            exp_status = "open"
            exp_cat: Optional[str] = None
            for tok in (arg or "").split():
                low = tok.strip().lower()
                if low in ("all", "open", "triaged", "resolved", "wontfix"):
                    exp_status = low
                elif low in assistant_db.FEEDBACK_CATEGORY_ALIASES:
                    exp_cat = assistant_db.FEEDBACK_CATEGORY_ALIASES[low]
            reports = await asyncio.to_thread(
                assistant_db.list_feedback_reports, status=exp_status, category=exp_cat, limit=300
            )
            md = assistant_db.format_feedback_markdown(reports, include_reporter=False)
            fp = io.BytesIO(md.encode("utf-8"))
            await ctx.send(
                content=f"📤 已匯出 {len(reports)} 筆回饋（狀態：`{exp_status}`）。",
                file=discord.File(fp, filename="feedback_export.md"),
                allowed_mentions=no_mention,
            )
            return

        # 6. 提交新回饋 / 建議（全體成員可用）
        category_hint = "feature" if (getattr(ctx, "invoked_with", "") or "").lower() == "建議" else ""
        if act in ("add", "new", "+"):
            raw_body = (arg or "").strip()
            if not raw_body:
                await ctx.send("❓ 請輸入想回報的問題或建議內容，例如：`!feedback wiki 聖誕工坊資料有誤` 或 `!建議 新增自動計算功能`", allowed_mentions=no_mention)
                return
            first_tok, *rest_toks = raw_body.split(None, 1)
            if rest_toks and first_tok.lower() in assistant_db.FEEDBACK_CATEGORY_ALIASES:
                category_hint = assistant_db.FEEDBACK_CATEGORY_ALIASES[first_tok.lower()]
                body = rest_toks[0].strip()
            else:
                body = raw_body
        else:
            if (arg or "").strip() and act in assistant_db.FEEDBACK_CATEGORY_ALIASES:
                category_hint = assistant_db.FEEDBACK_CATEGORY_ALIASES[act]
                body = arg.strip()
            else:
                body = f"{act_raw} {arg}".strip() if arg else act_raw

        if not body:
            await ctx.send("❓ 請輸入要回饋或建議的內容。", allowed_mentions=no_mention)
            return

        if len(body) > 1200:
            await ctx.send("⚠️ 回饋內容請控制在 1200 字元以內。", allowed_mentions=no_mention)
            return

        excerpt = await self._extract_reply_context_for_feedback(ctx.message)
        summary = " ".join(body.split())[:80]
        norm_cat = assistant_db.normalize_feedback_category(category_hint, body)
        fid, status = await asyncio.to_thread(
            assistant_db.add_feedback_report,
            summary=summary,
            detail=body,
            category=norm_cat,
            source="command",
            reporter_id=getattr(ctx.author, "id", 0),
            reporter_name=getattr(ctx.author, "display_name", ""),
            channel_id=getattr(ctx.channel, "id", 0),
            message_jump_url=str(getattr(ctx.message, "jump_url", "") or ""),
            context_excerpt=excerpt,
            cooldown_seconds=15,
        )
        cat_label = assistant_db.FEEDBACK_CATEGORY_LABELS.get(norm_cat, norm_cat)
        if status == "created" and fid:
            await ctx.send(
                f"📮 已收錄非即時回饋 **#{fid}**（{cat_label}），後續將由開發者離線審核處理。可隨時用 `!feedback list` 查看處理進度。",
                allowed_mentions=no_mention,
            )
        elif status == "duplicate" and fid:
            await ctx.send(
                f"📮 已整合至既有尚待處理回饋 **#{fid}**，無需重複提交。可隨時用 `!feedback list` 查看進度。",
                allowed_mentions=no_mention,
            )
        elif status == "rate_limited":
            await ctx.send("⏳ 剛剛才剛提交過一筆回饋，請稍候片刻再試。", allowed_mentions=no_mention)
        else:
            await ctx.send("❓ 請輸入有效的回饋內容。", allowed_mentions=no_mention)

    async def cog_command_error(self, ctx: commands.Context, error: commands.CommandError) -> None:
        if isinstance(error, (commands.MissingRequiredArgument, commands.BadArgument)):
            if is_target_channel(ctx.channel, self.settings):
                await ctx.send(f"❓ 指令參數格式不正確，請輸入 `!help` 查看用法。")
            return
        self.logger.error(f"❌ AssistantCog 指令執行錯誤 ({ctx.command}): {error}", exc_info=error)

    @commands.command(name="help", aliases=["指令", "說明"])
    async def help_command(self, ctx: commands.Context, *, topic: Optional[str] = None):
        """查看指令手冊（依啟用模組動態顯示，並支援 !help wiki / !help rpg / !help lore / !help tts）。"""
        if not is_target_channel(ctx.channel, self.settings) and not await is_bot_owner(
            self.bot, self.settings, ctx.author
        ):
            return

        embed, notice = build_help_topic_embed(self.settings, topic)
        if notice:
            await ctx.send(notice)
            return
        await ctx.send(embed=embed)
