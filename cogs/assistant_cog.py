"""Assistant Cog: !reset, !reload, !todo, !remind, !memory, !help, and reminder loop."""

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import discord
from discord.ext import commands, tasks

import assistant_db
import assistant_tools
import game_wiki_engine
from core.chat_pipeline import is_target_channel, record_channel_reset_timestamp
from core.config import BASE_DIR, BotSettings, truncate_embed_text


def build_help_overview_embed(settings: BotSettings) -> discord.Embed:
    """Build the main !help Embed dynamically according to enabled feature flags."""
    if settings.enable_japanese_mode:
        desc = "平時可直接聊天、傳圖片/貼圖互動或進行日語共學。常用指令如下："
    else:
        desc = "平時可直接聊天、傳圖片/貼圖/短影片互動，或隨時開啟 TRPG 跑團。常用指令如下："

    embed = discord.Embed(
        title=f"📖 {settings.bot_instance_name} 指令速查表",
        description=desc,
        color=0x5865F2,
    )

    if settings.enable_game_wiki:
        embed.add_field(
            name="🧟 《殭屍學園》知識庫 (`!wiki` / `!圖鑑`)",
            value=(
                "`!wiki <關鍵字>` — 查詢韓服黑話、角色、模式或攻略\n"
                "`!wiki add <關鍵字> | <內容>` — 單筆補充設定或黑話\n"
                "`!wiki import` — 貼上長文或**夾帶 `.txt`/`.md` 檔**批次擴充資料庫\n"
                "`!wiki list` / `!wiki del <編號>` — 查看或刪除群友補充條目"
            ),
            inline=False,
        )

    embed.add_field(
        name="📖 設定集與常駐記憶 (`!lore` / `!memory`)",
        value=(
            "`!lore` / `!lore add <詞> | <內容>` — 查看或新增通用世界觀設定\n"
            "`!lore import` — 貼文或夾帶 `.txt`/`.md` 批次匯入世界觀\n"
            "`!memory` / `!remember <內容>` / `!forget <編號>` — 管理常駐記憶"
        ),
        inline=False,
    )

    embed.add_field(
        name="🗡️ TRPG 跑團冒險 (`!rpg` / `!sheet` / `!roll`)",
        value=(
            "`!rpg start [主題]` — 開啟專屬討論串跑團（`!rpg reset` 重置）\n"
            "`!rpg join` / `!rpg leave` — 主動加入或單獨退出當前冒險隊伍\n"
            "`!rpg` / `!party` — 查看個人狀態卡 ／ 小隊全員面板\n"
            "`!sheet [文字或附檔]` — 查看或匯入詳細角色卡（匯入即入隊）\n"
            "`!save <名>` / `!load <名>` / `!saves` — 存檔與讀檔\n"
            "`!note <線索>` / `!journal` / `!roll [1d20]` — 手記與擲骰"
        ),
        inline=False,
    )

    if settings.enable_board_games:
        embed.add_field(
            name="🎲 桌遊與圖形棋盤 (`!play` ＋ 按鈕／口語控制)",
            value=(
                "`!play` — 開啟互動大廳（黑白棋、西洋棋、四子棋、五子棋；支援 PvE 簡單/普通/困難/🧠LLM ＆ PvP）\n"
                "其餘皆可點卡片按鈕（上一步悔棋、提示、座標、投降、戰績）或直接口語請 Bot 代勞（輔助：`!m <座標>`）"
            ),
            inline=False,
        )

    if settings.enable_flashcard_mode:
        embed.add_field(
            name="🌸 日語單字與搶答 (`!card` / `!quiz`)",
            value=(
                "`!card` / `!card add <單字>` — 互動單字卡 ／ 新增單字\n"
                "`!card decks` / `!quiz [牌組] [題數]` — 查看牌組 ／ 多人搶答"
            ),
            inline=False,
        )

    embed.add_field(
        name="🛠️ 待辦、提醒與系統管理 (`!todo` / `!remind` / `!reset`)",
        value=(
            "`!todo` / `!todo add <事項>` / `!todo done <編號>` — 待辦清單\n"
            "`!remind 10m <事項>` / `!reminds` — 定時提醒與清單\n"
            "`!reset` — 清除當前頻道／討論串的對話上下文\n"
            "`!reload` — 重新檢查並載入人設提示詞與知識庫設定"
        ),
        inline=False,
    )

    footer_hints = ["!help rpg", "!help lore"]
    if settings.enable_game_wiki:
        footer_hints.insert(0, "!help wiki")
    embed.set_footer(text=f"💡 輸入 {'、'.join(footer_hints)} 可查看該分類的詳細教學與範例！")
    return embed


class AssistantCog(commands.Cog):
    """生活助理、待辦、提醒排程、長期記憶與系統指令。"""

    def __init__(self, bot: commands.Bot, settings: BotSettings, logger: logging.Logger):
        self.bot = bot
        self.settings = settings
        self.logger = logger

    async def cog_load(self) -> None:
        if not self.reminder_check_loop.is_running():
            self.reminder_check_loop.start()

    async def cog_unload(self) -> None:
        if self.reminder_check_loop.is_running():
            self.reminder_check_loop.cancel()

    @tasks.loop(seconds=10)
    async def reminder_check_loop(self):
        """背景定時排程：每 10 秒檢查是否有到期的提醒事項並推播至 Discord。"""
        due_items = assistant_db.get_pending_due_reminders()
        for rem in due_items:
            try:
                channel = self.bot.get_channel(rem["channel_id"])
                origin_note = ""

                if not channel:
                    fallback_id = self.settings.general_channel_id or self.settings.target_channel_id
                    channel = self.bot.get_channel(fallback_id) if fallback_id else None
                    origin_note = "（原討論串已刪除，自動轉移至此通知）"

                if channel:
                    user_ping = f"<@{rem['user_id']}>"
                    embed = discord.Embed(
                        title="⏰ 提醒時間到了！",
                        description=f"**{rem['content']}**",
                        color=0xFFD166,
                    )
                    footer_text = f"來自 {rem['user_name']} 的定時提醒"
                    if origin_note:
                        footer_text += f" • {origin_note}"
                    embed.set_footer(text=footer_text)
                    await channel.send(content=user_ping, embed=embed)
                    self.logger.info(f"⏰ 已成功發送定時提醒給 {rem['user_name']} (ID: {rem['id']}): {rem['content']}")
                assistant_db.mark_reminder_sent(rem["id"])
            except Exception as e:
                self.logger.error(f"❌ 發送定時提醒失敗 (ID: {rem['id']}): {e}")

    @reminder_check_loop.before_loop
    async def before_reminder_loop(self):
        await self.bot.wait_until_ready()

    @commands.command(name="reset", aliases=["clear", "重設", "重置"])
    async def reset_context(self, ctx: commands.Context):
        """手動重設當前頻道或討論串的上下文記憶（並持久化至 SQLite）。"""
        if not is_target_channel(ctx.channel, self.settings):
            return

        now = datetime.now(timezone.utc)
        cid = ctx.channel.id

        if isinstance(ctx.channel, discord.Thread):
            record_channel_reset_timestamp(cid, now)
            await ctx.send("🧹 **當前討論串的對話記憶已重設。** 隨時可以開啟新話題。")
            self.logger.info(f"討論串 {cid} 已重設上下文。")
            return

        if not self.settings.enable_japanese_mode or (
            self.settings.general_channel_id and cid == self.settings.general_channel_id
        ):
            record_channel_reset_timestamp(cid, now)
            await ctx.send("🧹 **對話記憶已重設。** 過去的上下文已清除，隨時可以開啟新話題。")
            self.logger.info(f"頻道 {cid} 已重設上下文。")
            return

        record_channel_reset_timestamp(self.settings.target_channel_id, now)
        main_ch = self.bot.get_channel(self.settings.target_channel_id)
        if main_ch:
            record_channel_reset_timestamp(main_ch.id, now)
            await main_ch.send("🧹 **日語共學記憶已重設！** 過去的對話已清除，可以隨時開啟新話題。")

        if self.settings.tts_channel_id:
            record_channel_reset_timestamp(self.settings.tts_channel_id, now)
            tts_ch = self.bot.get_channel(self.settings.tts_channel_id)
            if tts_ch and tts_ch.id != ctx.channel.id:
                await tts_ch.send("🧹 **日語共學記憶已重設！**")

        if cid not in (self.settings.target_channel_id, self.settings.tts_channel_id):
            record_channel_reset_timestamp(cid, now)
            await ctx.send("🧹 **已重設上下文記憶！**")

        self.logger.info("已重設日語學習雙頻道上下文記憶。")

    @commands.command(name="reload", aliases=["重載"])
    async def reload_command(self, ctx: commands.Context):
        """熱重載人設提示詞檔案與知識庫索引（修改 .txt / wiki 後可立即檢查生效）。"""
        if not is_target_channel(ctx.channel, self.settings):
            return

        checked: list[str] = []
        prompt_files = [self.settings.general_prompt_file]
        if self.settings.enable_japanese_mode:
            prompt_files.append(self.settings.system_prompt_file)

        for fname in prompt_files:
            p_path = BASE_DIR / Path(fname.strip()).name
            if p_path.is_file():
                try:
                    content = p_path.read_text(encoding="utf-8").strip()
                    checked.append(f"`{p_path.name}`（{len(content)} 字）")
                except Exception as e:
                    await ctx.send(f"⚠️ 讀取提示詞檔案 `{p_path.name}` 失敗：{e}")
                    return
            else:
                checked.append(f"`{p_path.name}`（未找到，將使用內建預設人設）")

        wiki_msg = ""
        if self.settings.enable_game_wiki:
            entries = game_wiki_engine.load_wiki_entries(force_reload=True)
            wiki_msg = f"\n📚 遊戲知識庫已重新載入（共 {len(entries)} 筆條目）"

        await ctx.send(f"🔄 **人設與設定已重新載入！**\n• 提示詞：{', '.join(checked)}{wiki_msg}")

    @commands.command(name="todo", aliases=["td", "todos"])
    async def todo_command(self, ctx: commands.Context, action: str = None, *, arg: str = None):
        """待辦事項清單指令。"""
        if not is_target_channel(ctx.channel, self.settings):
            return

        if not action or action.lower() in ("list", "show", "ls"):
            todos = assistant_db.list_todos(only_pending=True)
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
            if not arg:
                await ctx.send("❓ 請輸入要新增的待辦事項內容，例如：`!todo add 買牛奶`")
                return
            tid = assistant_db.add_todo(arg.strip(), user_name=ctx.author.display_name)
            await ctx.send(f"✅ 已成功新增待辦事項 **#{tid}**：{arg.strip()}")

        elif act in ("done", "finish", "ok", "check", "v"):
            if not arg:
                await ctx.send("❓ 請輸入要完成的待辦編號或關鍵字，例如：`!todo done 1` 或 `!todo done 牛奶`")
                return
            target = arg.strip()
            completed = assistant_db.complete_todo_by_id(int(target)) if target.isdigit() else None
            if not completed:
                completed = assistant_db.complete_todo_by_keyword(target)

            if completed:
                await ctx.send(f"🎉 已完成待辦事項 **#{completed['id']}**：~~{completed['content']}~~")
            else:
                await ctx.send(f"❌ 找不到符合「{target}」的未完成待辦事項。")

        elif act in ("del", "delete", "rm", "remove", "-"):
            if not arg or not arg.strip().isdigit():
                await ctx.send("❓ 請輸入要刪除的待辦事項編號，例如：`!todo del 1`")
                return
            tid = int(arg.strip())
            if assistant_db.delete_todo(tid):
                await ctx.send(f"🗑️ 已刪除待辦事項 **#{tid}**。")
            else:
                await ctx.send(f"❌ 找不到待辦事項 **#{tid}**。")

        elif act in ("all", "history"):
            todos = assistant_db.list_todos(only_pending=False)
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
            tid = assistant_db.add_todo(full_content.strip(), user_name=ctx.author.display_name)
            await ctx.send(f"✅ 已成功新增待辦事項 **#{tid}**：{full_content.strip()}")

    @commands.command(name="remind", aliases=["reminder", "rem"])
    async def remind_command(self, ctx: commands.Context, time_spec: str = None, *, content: str = None):
        """定時提醒設定指令。"""
        if not is_target_channel(ctx.channel, self.settings):
            return

        if not time_spec or time_spec.lower() in ("list", "show", "ls"):
            rems = assistant_db.list_upcoming_reminders()
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
            ok, info = assistant_db.cancel_reminder(rid, user_id=ctx.author.id)
            if ok:
                await ctx.send(f"🗑️ 已取消提醒事項 **#{rid}**（{info}）。")
            else:
                await ctx.send(f"❌ 無法取消提醒 **#{rid}**：{info}")
            return

        if not content:
            await ctx.send("❓ 請提供提醒時間與內容，例如：`!remind 10m 關火` 或 `!remind 18:30 倒垃圾`")
            return

        target_dt = assistant_tools.parse_time_argument(time_spec)
        if not target_dt:
            await ctx.send(f"❌ 無法辨識時間格式「{time_spec}」，請嘗試：`10m`、`2h`、`18:30` 或 `2026-10-31 15:00`")
            return

        now = datetime.now(assistant_db.TZ_TAIPEI)
        if target_dt <= now:
            await ctx.send("❌ 設定的時間已經過去了，請設定未來的時間。")
            return

        rid = assistant_db.add_reminder(
            channel_id=ctx.channel.id,
            user_id=ctx.author.id,
            user_name=ctx.author.display_name,
            remind_at_iso=target_dt.isoformat(),
            content=content.strip(),
        )
        formatted = target_dt.strftime("%Y-%m-%d %H:%M:%S")
        await ctx.send(f"⏰ 已設定定時提醒 **#{rid}**！\n我會在 `{formatted}` 於此頻道提醒你：**{content.strip()}**")

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
            memories = assistant_db.get_all_memories()
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
            if not arg:
                example_text = "女友最愛吃抹茶" if self.settings.enable_japanese_mode else "每週六晚上八點固定打團"
                await ctx.send(f"❓ 請輸入要記住的事實，例如：`!remember {example_text}`")
                return
            mid = assistant_db.add_memory(arg.strip(), user_name=ctx.author.display_name)
            await ctx.send(f"🧠 已存入長期記憶庫 **#{mid}**：{arg.strip()}")

        elif act in ("del", "delete", "rm", "forget", "-"):
            if not arg or not arg.strip().isdigit():
                await ctx.send("❓ 請輸入要刪除的記憶編號，例如：`!forget 1`")
                return
            mid = int(arg.strip())
            if assistant_db.delete_memory(mid):
                await ctx.send(f"🗑️ 已從長期記憶庫刪除 **#{mid}**。")
            else:
                await ctx.send(f"❌ 找不到記憶編號 **#{mid}**。")
        else:
            full_content = f"{action} {arg}" if arg else action
            mid = assistant_db.add_memory(full_content.strip(), user_name=ctx.author.display_name)
            await ctx.send(f"🧠 已存入長期記憶庫 **#{mid}**：{full_content.strip()}")

    @commands.command(name="remember")
    async def remember_shortcut(self, ctx: commands.Context, *, content: str):
        """快速存入長期記憶：!remember <事實>"""
        await self.memory_command(ctx, action="add", arg=content)

    @commands.command(name="forget")
    async def forget_shortcut(self, ctx: commands.Context, memory_id: int):
        """快速刪除長期記憶：!forget <編號>"""
        await self.memory_command(ctx, action="del", arg=str(memory_id))

    @commands.command(name="help", aliases=["指令", "說明"])
    async def help_command(self, ctx: commands.Context, *, topic: Optional[str] = None):
        """查看指令手冊（依啟用模組動態顯示，並支援 !help wiki / !help rpg / !help lore）。"""
        if not is_target_channel(ctx.channel, self.settings):
            return

        sub = (topic or "").strip().lower()

        if sub in ("wiki", "圖鑑", "黑話", "좀고", "知識庫", "補充", "import"):
            if not self.settings.enable_game_wiki:
                await ctx.send("ℹ️ 此伺服器目前未啟用《殭屍學園》知識庫模組（`ENABLE_GAME_WIKI=false`）。")
                return
            embed = discord.Embed(
                title="🧟 《殭屍學園》知識庫 (`!wiki`) 詳細指南",
                description="查詢韓服黑話與設定，或直接丟文本／傳檔案擴充資料庫（免重啟立即生效）：",
                color=0x2ECC71,
            )
            embed.add_field(
                name="🔍 查詢指令（別名：`!圖鑑`、`!黑話`、`!좀고`）",
                value=(
                    "`!wiki` — 查看收錄統計\n"
                    "`!wiki <關鍵字>` — 查黑話、角色、模式（如 `!wiki ㄷㅋㅈ`、`!wiki 進化感染`）"
                ),
                inline=False,
            )
            embed.add_field(
                name="📥 補充資料庫的 4 種方式",
                value=(
                    "1️⃣ **直接貼無標示長文**：輸入 `!wiki import` 換行貼上觀念心得，自動分段抽詞建檔\n"
                    "2️⃣ **上傳檔案**：輸入 `!wiki import [主題]` 並夾帶 `.txt` / `.md` 檔案上傳\n"
                    "3️⃣ **多段標題格式**：輸入 `!wiki import` 換行貼上多段 `【標題】內容` 或 `# 標題`\n"
                    "4️⃣ **單筆快速新增**：`!wiki add 關鍵字1, 關鍵字2 | 內容說明`"
                ),
                inline=False,
            )
            embed.add_field(
                name="🗂️ 管理補充條目",
                value=(
                    "`!wiki list` — 查看群友補充的條目與編號\n"
                    "`!wiki del <編號>` — 刪除補充條目（如 `!wiki del contrib_1`）"
                ),
                inline=False,
            )
            await ctx.send(embed=embed)
            return

        if sub in ("rpg", "trpg", "adv", "sheet", "跑團", "冒險", "角色卡"):
            embed = discord.Embed(
                title="🗡️ TRPG 跑團與角色卡 (`!rpg`) 詳細指南",
                color=0xF15BB5,
            )
            embed.add_field(
                name="🎲 開局與檢定",
                value=(
                    "`!rpg start [主題]` — 建立專屬討論串開局（別名：`!adv`）\n"
                    "`!roll [骰子]` — 擲骰檢定（如 `!roll 1d20`、`!roll 2d6+3`）\n"
                    "`!rpg reset` — 重置當前頻道的冒險狀態"
                ),
                inline=False,
            )
            embed.add_field(
                name="👤 角色卡與小隊",
                value=(
                    "`!rpg` — 查看個人簡要狀態卡（HP、地點、背包）\n"
                    "`!sheet` — 查看個人極詳細角色卡（別名：`!角色卡`）\n"
                    "`!sheet <文字或附檔>` — 貼上文字或上傳 `.txt`/`.md` 匯入角色卡\n"
                    "`!rpg name <名>` / `!rpg class <職業>` — 修改名字或職業\n"
                    "`!party` — 查看小隊全員名冊與血量"
                ),
                inline=False,
            )
            embed.add_field(
                name="💾 存讀檔與手記",
                value=(
                    "`!save <名稱>` / `!load <名稱>` — 保存或載入全隊進度\n"
                    "`!saves` / `!rpg delsave <名稱>` — 查看或刪除存檔\n"
                    "`!note <線索>` / `!journal` / `!rpg delnote <編號>` — 管理冒險手記"
                ),
                inline=False,
            )
            await ctx.send(embed=embed)
            return

        if sub in ("lore", "memory", "mem", "設定", "世界觀", "記憶"):
            embed = discord.Embed(
                title="📖 通用設定集 (`!lore`) 與常駐記憶 (`!memory`) 指南",
                color=0x00BBF9,
            )
            embed.add_field(
                name="🌍 通用／跑團設定集（提及關鍵字時自動喚醒）",
                value=(
                    "`!lore` — 查看所有世界觀設定\n"
                    "`!lore add <關鍵字> | <內容>` — 新增單筆設定\n"
                    "`!lore import` — 貼上多段 `【標題】內容` 或夾帶 `.txt`/`.md` 批次匯入\n"
                    "`!lore del <編號>` — 刪除單筆設定 ｜ `!lore clear` — 清空設定集"
                ),
                inline=False,
            )
            embed.add_field(
                name="🧠 全群常駐記憶（每次對話固定載入，適合簡短共識）",
                value=(
                    "`!memory` — 查看常駐記憶清單\n"
                    "`!remember <內容>` — 新增一筆常駐記憶\n"
                    "`!forget <編號>` — 刪除指定編號的記憶"
                ),
                inline=False,
            )
            await ctx.send(embed=embed)
            return

        await ctx.send(embed=build_help_overview_embed(self.settings))
