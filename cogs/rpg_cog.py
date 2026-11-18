"""RPG Cog: !roll, !rpg, !sheet, !party, !adventure, !save, !load, !saves, !note, !journal, !lore."""

import asyncio
import logging
import re
from pathlib import Path
from typing import Optional

import discord
from discord.ext import commands

from engines import assistant_db, rpg_engine
from core.chat_pipeline import is_target_channel
from core.config import MAX_TEXT_ATTACHMENT_BYTES, BotSettings, truncate_embed_text


class RpgCog(commands.Cog):
    """TRPG 跑團、角色卡、小隊管理、存讀檔與動態世界觀設定集指令。"""

    def __init__(self, bot: commands.Bot, settings: BotSettings, logger: logging.Logger):
        self.bot = bot
        self.settings = settings
        self.logger = logger

    @commands.command(name="roll", aliases=["r", "dice", "擲骰"])
    async def roll_command(self, ctx: commands.Context, dice_expr: str = "1d20"):
        """擲骰子指令：!roll [表達式]（例如 !roll、!roll 2d6、!roll 1d100、!roll 1d20+3）"""
        if not is_target_channel(ctx.channel, self.settings):
            return

        try:
            total, _rolls, desc = rpg_engine.roll_dice(dice_expr)
            user_name = ctx.author.display_name
            embed = discord.Embed(
                title="🎲 擲骰結果",
                description=f"**{user_name}** 擲出 `{dice_expr}`\n\n🎯 點數：**{desc}**",
                color=0x9B5DE5,
            )
            if "1d20" in dice_expr.lower():
                if total == 20:
                    embed.set_footer(text="🌟 滿分 20！大成功 (Critical Success)！")
                elif total == 1:
                    embed.set_footer(text="💀 擲出 1 點！大失敗 (Critical Fumble)！")
            await ctx.send(embed=embed)
        except Exception as e:
            await ctx.send(f"❌ 擲骰失敗：{e}（請嘗試 `!roll`、`!roll 2d6` 或 `!roll 1d20+3`）")

    async def handle_start_adventure(self, ctx: commands.Context, topic: Optional[str] = None):
        """建立專屬獨立討論串開啟全新文字冒險。"""
        if not is_target_channel(ctx.channel, self.settings):
            return

        if isinstance(ctx.channel, discord.Thread):
            await ctx.send("⚔️ 你目前已經在獨立冒險討論串中囉！可以隨時輸入 `!rpg` 查看狀態卡，或直接說「我想探索四周」推進冒險！")
            return

        adventure_topic = topic.strip() if topic and topic.strip() else "未知的迷宮遺跡"
        clean_topic = re.sub(r'[\/\\:\*\?"<>\|]', "", adventure_topic)

        thread_name = f"🗡️-冒險-{clean_topic[:25]}"
        try:
            thread = await ctx.message.create_thread(name=thread_name, auto_archive_duration=1440)
        except Exception as e:
            self.logger.warning(f"建立討論串失敗: {e}，改在當前頻道開始冒險。")
            thread = ctx.channel

        uname = ctx.author.display_name
        await asyncio.to_thread(rpg_engine.set_rpg_session_active, thread.id, True)
        char, _ = await asyncio.to_thread(rpg_engine.join_rpg_party, thread.id, ctx.author.id, uname)

        opening_embed = discord.Embed(
            title=f"🗡️ 【{adventure_topic}】冒險篇章正式展開！",
            description=(
                f"歡迎來到全新的冒險世界，**{uname}**！\n\n"
                f"🏰 **冒險起點**：`{char['location']}`\n"
                f"🎯 **當前使命**：`{char['current_quest']}`\n\n"
                f"✨ **獨立隔離與組隊機制**：\n"
                f"• 在此討論串內的所有對話、背包物品與故事線索均為**專屬獨立**，絕不干擾日常群聊！\n"
                f"• 其他群友想參戰請輸入 **`!rpg join`**（或 `!party join`）入隊，想暫離或退隊請輸入 **`!rpg leave`**。\n"
                f"• 隨時輸入 `!save <存檔名>` 保存進度，或在未來開新討論串輸入 `!load <存檔名>` 繼承進度。\n"
                f"• 輸入 `!note <線索>` 記錄旅程筆記，或輸入 `!roll` 擲骰判定！"
            ),
            color=0xF15BB5,
        )
        opening_embed.set_footer(text="💡 輸入 !sheet 檢視或匯入角色卡，直接在下方說話即可讓 AI 引導冒險！")

        if thread != ctx.channel:
            await ctx.send(f"🎮 專屬冒險討論串已建立！點擊前往：{thread.mention}")
        await thread.send(content=f"<@{ctx.author.id}> 冒險者已就位！", embed=opening_embed)

    @commands.command(name="save", aliases=["存檔", "儲存"])
    async def save_command(self, ctx: commands.Context, *, save_name: str = None):
        """保存當前冒險進度：!save <存檔名稱>"""
        if not is_target_channel(ctx.channel, self.settings):
            return
        if not save_name:
            await ctx.send("❓ 請提供存檔名稱，例如：`!save 森林遺跡` 或 `!save 第一章通關`")
            return
        if not await asyncio.to_thread(rpg_engine.get_all_characters_in_channel, ctx.channel.id):
            await ctx.send("🏕️ 目前隊伍中尚無任何已入隊的冒險者，無法存檔！請先輸入 `!rpg join` 或 `!rpg start [主題]`。")
            return
        res = await asyncio.to_thread(
            rpg_engine.save_game, ctx.channel.id, ctx.author.id, ctx.author.display_name, save_name
        )
        embed = discord.Embed(
            title="💾 冒險進度已保存！",
            description=f"存檔名稱：**【{res['save_name']}】**",
            color=0x06D6A0,
        )
        embed.add_field(name="👤 角色", value=f"{res['char_name']}（{res['char_class']}）", inline=True)
        embed.add_field(name="❤️ 血量", value=f"{res['hp']}/{res['max_hp']}", inline=True)
        embed.add_field(name="🎒 背包物品", value=f"{len(res['inventory'])} 件隨身物品", inline=True)
        embed.add_field(name="📍 當前地點", value=res["location"], inline=True)
        embed.add_field(name="🎯 當前目標", value=res["current_quest"], inline=True)
        embed.add_field(name="📓 旅程手記", value=f"{res['notes_count']} 條獨立線索快照", inline=True)
        embed.set_footer(text=f"💡 隨時在任何頻道或討論串輸入 !load {res['save_name']} 即可接續進度！")
        await ctx.send(embed=embed)

    @commands.command(name="load", aliases=["讀檔", "載入"])
    async def load_command(self, ctx: commands.Context, *, save_name: str = None):
        """載入冒險進度：!load <存檔名稱>"""
        if not is_target_channel(ctx.channel, self.settings):
            return
        if not save_name:
            await ctx.send("❓ 請提供要載入的存檔名稱，例如：`!load 森林遺跡`（可先輸入 `!saves` 查看所有存檔）")
            return
        res = await asyncio.to_thread(
            rpg_engine.load_game, ctx.channel.id, ctx.author.id, ctx.author.display_name, save_name
        )
        if not res:
            await ctx.send(f"❌ 找不到存檔「{save_name}」。輸入 `!saves` 可查看目前所有保存的存檔清單！")
            return
        await asyncio.to_thread(rpg_engine.set_rpg_session_active, ctx.channel.id, True)
        inv_str = "、".join(res["inventory"]) if res["inventory"] else "（背包空）"
        embed = discord.Embed(
            title="📂 冒險進度載入成功！",
            description=f"已將存檔 **【{res['save_name']}】** 還原至當前頻道/討論串！",
            color=0x118AB2,
        )
        embed.add_field(name="👤 角色", value=f"**{res['char_name']}**（{res['char_class']}）", inline=True)
        embed.add_field(name="❤️ 血量", value=f"`{res['hp']}/{res['max_hp']}`", inline=True)
        embed.add_field(name="📍 當前地點", value=f"`{res['location']}`", inline=False)
        embed.add_field(name="🎯 當前目標", value=f"`{res['current_quest']}`", inline=False)
        embed.add_field(name="🎒 背包物品", value=inv_str, inline=False)
        embed.add_field(name="📓 旅程手記", value=f"已同步還原 {res['notes_count']} 條冒險線索（輸入 `!journal` 查看）", inline=False)
        embed.set_footer(text=f"存檔時間：{res['saved_at']} | 冒險者已整裝待發！")
        await ctx.send(embed=embed)

    @commands.command(name="saves", aliases=["存檔清單", "存檔列表"])
    async def saves_command(self, ctx: commands.Context):
        """查看所有冒險存檔清單：!saves"""
        if not is_target_channel(ctx.channel, self.settings):
            return
        saves = await asyncio.to_thread(rpg_engine.list_saves, ctx.author.id)
        if not saves:
            await ctx.send("💾 目前沒有任何冒險存檔。在冒險中輸入 `!save <名稱>` 即可隨時保存進度！")
            return
        lines = []
        for s in saves:
            creator = f" (由 {s['user_name']})" if s.get("user_name") else ""
            lines.append(
                f"📁 **【{s['save_name']}】**{creator}\n"
                f"└ 角色：{s['char_name']} ({s['char_class']}) | HP: {s['hp']}/{s['max_hp']} | 🎒 物品: {len(s['inventory'])} 件\n"
                f"└ 地點：{s['location']} | 手記: {len(s.get('journey_notes', []))} 條\n"
                f"└ 時間：`{s['saved_at'][:16]}`"
            )
        embed = discord.Embed(
            title="💾 冒險存檔清單",
            description=truncate_embed_text("\n\n".join(lines)),
            color=0x06D6A0,
        )
        embed.set_footer(text="💡 載入請輸入 !load <存檔名>，刪除請輸入 !rpg delsave <存檔名>")
        await ctx.send(embed=embed)

    @commands.command(name="note", aliases=["線索", "手記", "筆記"])
    async def note_command(self, ctx: commands.Context, *, content: str = None):
        """記錄當前旅程的重要線索或手記：!note <內容>"""
        if not is_target_channel(ctx.channel, self.settings):
            return
        if not content:
            await ctx.send("❓ 請輸入要記錄的冒險線索或手記，例如：`!note 守衛老伯最怕老鼠` 或 `!note 發現了發光的紫水晶手鐲`")
            return
        nid = await asyncio.to_thread(
            rpg_engine.add_journey_note,
            channel_id=ctx.channel.id,
            user_id=ctx.author.id,
            user_name=ctx.author.display_name,
            content=content.strip(),
            note_type="clue",
        )
        await ctx.send(f"📝 已記錄當前旅程手記 **#{nid}**：{content.strip()}\n💡 此線索已永久鎖定在本次冒險記憶中，AI 推進劇情時會自動融入！")

    @commands.command(name="journal", aliases=["notes", "手記本", "冒險筆記"])
    async def journal_command(self, ctx: commands.Context):
        """查看當前冒險的所有手記與線索：!journal"""
        if not is_target_channel(ctx.channel, self.settings):
            return
        notes = await asyncio.to_thread(rpg_engine.get_journey_notes, ctx.channel.id)
        if not notes:
            await ctx.send("📓 目前這趟旅程還沒有任何手記或線索。\nAI 在冒險中發現線索會自動記下，你也可以隨時輸入 `!note <筆記>` 手動記錄！")
            return
        lines = []
        for n in notes:
            tag = "重要線索" if n["note_type"] == "clue" else n["note_type"]
            lines.append(f"**#{n['id']} [{tag}]** {n['content']}\n└ *由 {n['user_name']} 記於 {n['created_at'][5:16]}*")
        embed = discord.Embed(
            title="📓 當前冒險旅程手記與關鍵線索 (Journey Journal)",
            description=truncate_embed_text("\n\n".join(lines)),
            color=0xFFB703,
        )
        embed.set_footer(text="💡 新增請輸入 !note <內容>，刪除請輸入 !rpg delnote <編號>")
        await ctx.send(embed=embed)

    async def _handle_party_join(self, ctx: commands.Context):
        """讓玩家主動加入當前頻道/討論串的冒險隊伍。"""
        cid = ctx.channel.id
        uid = ctx.author.id
        uname = ctx.author.display_name
        char, already_in = await asyncio.to_thread(rpg_engine.join_rpg_party, cid, uid, uname)
        all_chars = await asyncio.to_thread(rpg_engine.get_all_characters_in_channel, cid)
        if already_in:
            await ctx.send(
                f"⚔️ **{uname}**（角色：**{char['char_name']}**）已經在冒險隊伍中囉！目前隊伍共有 **{len(all_chars)}** 位成員（輸入 `!party` 查看）。"
            )
            return
        embed = discord.Embed(
            title="🛡️ 冒險者已正式加入隊伍！",
            description=(
                f"**{uname}** 以「**{char['char_name']}**（{char['char_class']}）」之姿加入本頻道／討論串的冒險小隊！\n\n"
                f"📍 **隊伍地點**：`{char['location']}`\n"
                f"🎯 **當前使命**：`{char['current_quest']}`\n"
                f"👥 **目前小隊人數**：`{len(all_chars)}` 人"
            ),
            color=0x06D6A0,
        )
        embed.set_footer(text="💡 輸入 !sheet <人設> 可匯入自訂角色卡 | 隨時輸入 !rpg leave 可單獨退隊")
        await ctx.send(embed=embed)

    async def _handle_party_leave(self, ctx: commands.Context):
        """讓玩家單獨退出當前冒險隊伍（保留角色設定與其他隊友的進度）。"""
        cid = ctx.channel.id
        uid = ctx.author.id
        uname = ctx.author.display_name
        left_char = await asyncio.to_thread(rpg_engine.leave_rpg_party, cid, uid)
        if not left_char:
            await ctx.send(f"ℹ️ **{uname}** 目前原本就不在這個頻道／討論串的冒險隊伍中喔。")
            return
        remaining = await asyncio.to_thread(rpg_engine.get_all_characters_in_channel, cid)
        await ctx.send(
            f"👋 **{uname}**（**{left_char['char_name']}**）已退出本頻道／討論串的冒險隊伍（已保留角色卡設定與其餘隊友的旅程手記）。\n"
            f"👥 目前隊伍剩餘 **{len(remaining)}** 位成員；隨時輸入 `!rpg join` 即可再次歸隊！"
        )

    async def _handle_party_kick(self, ctx: commands.Context, target_query: Optional[str]):
        """將指定成員（可 @mention 或輸入名稱）移出當前冒險隊伍。"""
        if not target_query or not target_query.strip():
            await ctx.send("❓ 請指定要移出隊伍的對象，例如：`!party kick @隊友` 或 `!party kick 亞瑟`")
            return
        kicked = await asyncio.to_thread(rpg_engine.kick_rpg_member, ctx.channel.id, target_query)
        if not kicked:
            await ctx.send(f"❌ 在目前入隊名單中找不到符合 `{target_query.strip()}` 的成員。")
            return
        remaining = await asyncio.to_thread(rpg_engine.get_all_characters_in_channel, ctx.channel.id)
        await ctx.send(
            f"👢 已將 **{kicked['user_name']}**（**{kicked['char_name']}**）移出當前冒險隊伍。目前剩餘 **{len(remaining)}** 位成員（對方隨時可輸入 `!rpg join` 歸隊）。"
        )

    @commands.command(name="party", aliases=["team", "隊伍", "小隊"])
    async def party_command(self, ctx: commands.Context, action: str = None, *, arg: str = None):
        """冒險小隊管理與狀態看板：!party / !party join / !party leave / !party kick <對象>"""
        if not is_target_channel(ctx.channel, self.settings):
            return
        act = (action or "").lower()
        if act in ("join", "加入", "入隊", "參戰"):
            await self._handle_party_join(ctx)
            return
        if act in ("leave", "quit", "exit", "退出", "離隊", "離開"):
            await self._handle_party_leave(ctx)
            return
        if act in ("kick", "remove", "踢出", "請離"):
            await self._handle_party_kick(ctx, arg)
            return

        chars = await asyncio.to_thread(rpg_engine.get_all_characters_in_channel, ctx.channel.id)
        if not chars:
            await ctx.send("🏕️ 當前頻道或討論串中尚未有任何冒險者入隊。輸入 `!rpg join` 加入隊伍，或 `!rpg start [主題]` 開局！")
            return
        embed = discord.Embed(
            title=f"👥 冒險小隊隊伍名冊（共 {len(chars)} 位冒險者）",
            description=f"📍 **小隊當前地點**：`{chars[0]['location']}`\n🎯 **當前使命**：`{chars[0]['current_quest']}`",
            color=0x06D6A0,
        )
        for c in chars[:20]:
            hp_ratio = max(0.0, min(1.0, c["hp"] / max(1, c["max_hp"])))
            filled_blocks = int(hp_ratio * 10)
            bar = "█" * filled_blocks + "░" * (10 - filled_blocks)
            inv_str = "、".join(c["inventory"]) if c["inventory"] else "（無隨身物品）"
            embed.add_field(
                name=f"👤 {c['char_name']}（{c['char_class']}）- {c['user_name']}",
                value=truncate_embed_text(f"❤️ 血量：`{c['hp']}/{c['max_hp']}` [{bar}]\n🎒 背包：{inv_str}", max_len=950),
                inline=False,
            )
        notes = await asyncio.to_thread(rpg_engine.get_journey_notes, ctx.channel.id)
        embed.set_footer(text=f"📓 本趟旅程共 {len(notes)} 條線索 | 入隊 !party join / 退隊 !party leave / 存檔 !save <名>")
        await ctx.send(embed=embed)

    @commands.command(name="adventure", aliases=["adv", "冒險"])
    async def adventure_command(self, ctx: commands.Context, *, topic: str = None):
        """快速開啟專屬文字冒險討論串：!adventure [主題]"""
        await self.handle_start_adventure(ctx, topic)

    async def send_sheet_embed(self, ctx: commands.Context, char: dict, title_prefix: str = "👑 冒險者角色卡"):
        """發送 Discord 角色卡 Embed，自適應各種世界觀。"""
        prof = char.get("profile") or {}
        race = prof.get("race", "")
        char_class = char.get("char_class", "冒險者")
        alignment = prof.get("alignment", "")
        background = prof.get("background", "")
        backstory = prof.get("backstory", "")
        ac = prof.get("ac")
        stats = prof.get("stats") or {}
        skills = prof.get("skills") or []
        inv = char.get("inventory") or []
        custom_traits = prof.get("custom_traits") or {}

        embed = discord.Embed(
            title=f"{title_prefix} - 【{char['char_name']}】",
            description=backstory if backstory else "一位踏上未知道路的神秘旅者...",
            color=0xF15BB5 if char.get("hp", 100) > 25 else 0xE63946,
        )

        id_lines = [f"• **身分/定位**：{char_class}"]
        if race:
            id_lines.append(f"• **種族/靈根**：{race}")
        if alignment:
            id_lines.append(f"• **陣營/派系**：{alignment}")
        if background:
            id_lines.append(f"• **出身背景**：{background}")
        embed.add_field(name="👤 基本身分", value="\n".join(id_lines), inline=True)

        hp_ratio = max(0.0, min(1.0, char.get("hp", 100) / max(1, char.get("max_hp", 100))))
        filled = int(hp_ratio * 10)
        bar = "█" * filled + "░" * (10 - filled)
        combat_lines = [f"• **體力/狀態值**：`{char.get('hp', 100)}/{char.get('max_hp', 100)}`\n  [{bar}]"]
        if ac is not None:
            combat_lines.append(f"• **防禦 (AC)**：`{ac}`")
        embed.add_field(name="⚔️ 狀態與數值", value="\n".join(combat_lines), inline=True)

        if custom_traits:
            trait_lines = [
                f"• **{k}**：{v}"
                for k, v in custom_traits.items()
                if k not in ("武器", "防具", "道具", "裝備", "隨身裝備", "隨身物品")
            ]
            if trait_lines:
                embed.add_field(
                    name="🔮 專屬世界觀特質 / 設定",
                    value=truncate_embed_text("\n".join(trait_lines[:8]), max_len=950),
                    inline=False,
                )

        if stats:
            stat_lines = (
                f"`STR 力量`：{stats.get('STR', 10):>2}   |   `INT 智力`：{stats.get('INT', 10):>2}\n"
                f"`DEX 敏捷`：{stats.get('DEX', 10):>2}   |   `WIS 感知`：{stats.get('WIS', 10):>2}\n"
                f"`CON 體質`：{stats.get('CON', 10):>2}   |   `CHA 魅力`：{stats.get('CHA', 10):>2}"
            )
            embed.add_field(name="📊 核心能力屬性 (Ability Scores)", value=stat_lines, inline=False)

        if skills:
            embed.add_field(
                name="🎭 專長技能 / 功法法術",
                value=truncate_embed_text("、".join(skills), max_len=950),
                inline=False,
            )

        if inv:
            inv_display = "\n".join([f"• {it}" for it in inv])
            if len(inv_display) > 800:
                inv_display = "、".join(inv)
            embed.add_field(name="🎒 隨身持有物 / 法寶裝備", value=truncate_embed_text(inv_display, max_len=950), inline=False)

        embed.set_footer(text="💡 支援修仙、賽博龐克、克蘇魯、奇幻冒險等任意世界觀！輸入 !sheet 查看或更新卡片")
        await ctx.send(embed=embed)

    @commands.command(name="sheet", aliases=["角色卡", "人物卡", "charsheet"])
    async def sheet_command(self, ctx: commands.Context, *, sheet_text: str = None):
        """極詳細冒險者角色卡 (Character Sheet) 檢視與匯入。"""
        if not is_target_channel(ctx.channel, self.settings):
            return

        cid = ctx.channel.id
        uid = ctx.author.id
        uname = ctx.author.display_name
        content = (sheet_text or "").strip()

        if content and "\n" not in content and content.lower().endswith((".txt", ".md", ".json")):
            local_path = assistant_db.IMPORTS_DIR / Path(content).name
            if local_path.is_file():
                try:
                    content = local_path.read_text(encoding="utf-8", errors="ignore").strip()
                except Exception as e:
                    await ctx.send(f"⚠️ 讀取本機檔案 `{local_path.name}` 失敗：{e}")
                    return

        if ctx.message.attachments:
            for att in ctx.message.attachments:
                if att.filename.lower().endswith((".txt", ".md", ".json")):
                    if att.size and att.size > MAX_TEXT_ATTACHMENT_BYTES:
                        await ctx.send(f"⚠️ 附件 `{att.filename}` 超過 2MB 上限，已略過。")
                        continue
                    try:
                        file_bytes = await att.read()
                        file_str = file_bytes.decode("utf-8", errors="ignore")
                        if file_str.strip():
                            content = file_str.strip() if not content else content + "\n\n" + file_str.strip()
                    except Exception as e:
                        await ctx.send(f"⚠️ 讀取附件 `{att.filename}` 失敗：{e}")

        if content:
            parsed = rpg_engine.parse_character_sheet(content, default_name=uname)
            applied = await asyncio.to_thread(rpg_engine.apply_character_sheet, cid, uid, uname, parsed)
            await self.send_sheet_embed(ctx, applied, title_prefix="✨ 角色卡匯入並入隊成功！")
            return

        char = await asyncio.to_thread(rpg_engine.get_rpg_character, cid, uid)
        if not char:
            await ctx.send("🏕️ 你尚未在這個頻道／討論串建立或加入角色。請輸入 `!rpg join` 加入隊伍，或輸入 `!sheet <角色設定>` 直接帶卡參戰！")
            return
        prefix = "👑 極詳細冒險者角色卡" if char["is_in_party"] else "👑 冒險者角色卡（目前暫離隊伍，輸入 !rpg join 可歸隊）"
        await self.send_sheet_embed(ctx, char, title_prefix=prefix)

    @commands.command(name="rpg", aliases=["status", "角色", "狀態"])
    async def rpg_command(self, ctx: commands.Context, action: str = None, *, arg: str = None):
        """RPG 角色狀態卡與冒險管理。"""
        if not is_target_channel(ctx.channel, self.settings):
            return

        cid = ctx.channel.id
        uid = ctx.author.id
        uname = ctx.author.display_name
        act = (action or "").lower()

        if act in ("sheet", "card", "import", "角色卡", "人物卡", "匯入角色卡"):
            await self.sheet_command(ctx, sheet_text=arg)
            return
        if act in ("start", "new", "adv", "adventure", "開局", "開始"):
            await self.handle_start_adventure(ctx, arg)
            return
        if act in ("join", "加入", "入隊", "參戰"):
            await self._handle_party_join(ctx)
            return
        if act in ("leave", "quit", "exit", "退出", "離隊", "離開"):
            await self._handle_party_leave(ctx)
            return
        if act in ("kick", "remove", "踢出", "請離"):
            await self._handle_party_kick(ctx, arg)
            return
        if act in ("party", "team", "隊伍", "小隊", "全員"):
            p_parts = arg.split(None, 1) if arg else []
            p_action = p_parts[0] if p_parts else None
            p_arg = p_parts[1] if len(p_parts) > 1 else None
            await self.party_command(ctx, action=p_action, arg=p_arg)
            return
        if act in ("save", "存檔", "儲存"):
            await self.save_command(ctx, save_name=arg)
            return
        if act in ("load", "讀檔", "載入"):
            await self.load_command(ctx, save_name=arg)
            return
        if act in ("saves", "list", "存檔清單", "存檔列表"):
            await self.saves_command(ctx)
            return
        if act in ("delsave", "deletesave", "rmsave", "刪除存檔"):
            if not arg:
                await ctx.send("❓ 請輸入要刪除的存檔名稱，例如：`!rpg delsave 森林遺跡`")
                return
            if await asyncio.to_thread(rpg_engine.delete_save, arg.strip(), uid):
                await ctx.send(f"🗑️ 已刪除存檔 **【{arg.strip()}】**。")
            else:
                await ctx.send(f"❌ 找不到存檔 **【{arg.strip()}】**。")
            return
        if act in ("note", "clue", "線索", "手記", "筆記"):
            await self.note_command(ctx, content=arg)
            return
        if act in ("journal", "notes", "手記本", "筆記本"):
            await self.journal_command(ctx)
            return
        if act in ("delnote", "rmnote", "刪除手記"):
            if not arg or not arg.strip().isdigit():
                await ctx.send("❓ 請輸入要刪除的手記編號，例如：`!rpg delnote 1`")
                return
            nid = int(arg.strip())
            if await asyncio.to_thread(rpg_engine.delete_journey_note, cid, nid):
                await ctx.send(f"🗑️ 已刪除當前旅程手記 **#{nid}**。")
            else:
                await ctx.send(f"❌ 找不到當前旅程手記 **#{nid}**。")
            return
        if act in ("reset", "restart", "重置", "重開"):
            await asyncio.to_thread(rpg_engine.reset_rpg_character, cid, uid)
            await asyncio.to_thread(rpg_engine.set_rpg_session_active, cid, False)
            await ctx.send(f"🔄 **{uname}** 的冒險紀錄與旅程手記已重置，可以隨時開始新的冒險。")
            return
        if act in ("name", "rename"):
            if not arg:
                await ctx.send("❓ 請輸入新角色名，例如：`!rpg name 艾莉絲`")
                return
            existing = await asyncio.to_thread(rpg_engine.get_rpg_character, cid, uid)
            if not existing or not existing["is_in_party"]:
                await ctx.send("🏕️ 你尚未加入目前隊伍！請先輸入 `!rpg join`（或 `!sheet <角色設定>`）入隊後再修改角色名稱。")
                return
            char = await asyncio.to_thread(rpg_engine.update_rpg_character, cid, uid, uname, char_name=arg.strip())
            await ctx.send(f"✅ 角色名稱已更改為：**{char['char_name']}**")
            return
        if act in ("class", "job"):
            if not arg:
                await ctx.send("❓ 請輸入新職業名稱，例如：`!rpg class 精靈弓箭手`")
                return
            existing = await asyncio.to_thread(rpg_engine.get_rpg_character, cid, uid)
            if not existing or not existing["is_in_party"]:
                await ctx.send("🏕️ 你尚未加入目前隊伍！請先輸入 `!rpg join`（或 `!sheet <角色設定>`）入隊後再修改職業稱號。")
                return
            char = await asyncio.to_thread(rpg_engine.update_rpg_character, cid, uid, uname, char_class=arg.strip())
            await ctx.send(f"✅ 角色職業已更改為：**{char['char_class']}**")
            return

        char = await asyncio.to_thread(rpg_engine.get_rpg_character, cid, uid)
        if not char:
            await ctx.send(
                "🏕️ 你尚未加入本頻道／討論串的冒險隊伍！\n"
                "👉 輸入 `!rpg join`（或 `!party join`）加入當前冒險\n"
                "👉 或輸入 `!rpg start [主題]` 開啟專屬討論串，亦可直接 `!sheet <角色設定>` 帶卡參戰！"
            )
            return
        if not char["is_in_party"]:
            await ctx.send(
                f"👋 **{uname}** 目前已暫離本頻道／討論串的冒險隊伍（保留角色：**{char['char_name']}**）。\n"
                f"👉 隨時輸入 `!rpg join` 即可重新歸隊繼續冒險！"
            )
            return
        notes = await asyncio.to_thread(rpg_engine.get_journey_notes, cid)
        inv_str = "、".join(char["inventory"]) if char["inventory"] else "（背包空空如也）"

        hp_ratio = max(0.0, min(1.0, char["hp"] / max(1, char["max_hp"])))
        filled_blocks = int(hp_ratio * 10)
        bar = "█" * filled_blocks + "░" * (10 - filled_blocks)

        embed = discord.Embed(
            title=f"🗡️ 【{char['char_name']}】的冒險者狀態卡",
            color=0xF15BB5 if char["hp"] > 25 else 0xE63946,
        )
        prof = char.get("profile") or {}
        race_align = (
            f"（{prof.get('race', '')} {char['char_class']} | {prof.get('alignment', '')}）"
            if (prof.get("race") or prof.get("alignment"))
            else f"（{char['char_class']}）"
        )
        embed.add_field(name="👤 角色身分", value=f"**{char['char_name']}** {race_align}", inline=True)
        embed.add_field(name="❤️ 生命值 (HP)", value=f"`{char['hp']}/{char['max_hp']}` [{bar}]", inline=True)
        embed.add_field(name="📍 當前地點", value=f"`{char['location']}`", inline=False)
        embed.add_field(name="🎯 當前目標", value=f"`{char['current_quest']}`", inline=False)
        embed.add_field(name="🎒 背包隨身裝備", value=truncate_embed_text(inv_str, max_len=950), inline=False)
        embed.add_field(name="📓 本趟旅程手記", value=f"{len(notes)} 條線索與情報（輸入 `!journal` 查看）", inline=False)
        all_chars = await asyncio.to_thread(rpg_engine.get_all_characters_in_channel, cid)
        footer_text = "💡 完整角色卡 !sheet | 保存 !save <名> | 載入 !load <名> | 擲骰 !roll"
        if len(all_chars) > 1:
            footer_text = f"👥 目前小隊共有 {len(all_chars)} 位成員！輸入 !party 查看全隊名冊 | !sheet 查看角色卡 | !save <名> 存檔"
        embed.set_footer(text=footer_text)
        await ctx.send(embed=embed)

    @commands.command(name="lore", aliases=["world", "設定", "世界觀"])
    async def lore_command(self, ctx: commands.Context, action: str = None, *, arg: str = None):
        """動態世界設定集（Lorebook）管理。"""
        if not is_target_channel(ctx.channel, self.settings):
            return

        act = (action or "").lower()

        if not action or act in ("list", "show", "ls"):
            entries = await asyncio.to_thread(rpg_engine.get_all_lorebook_entries)
            if not entries:
                await ctx.send("📖 目前世界設定集（Lorebook）是空的。可以輸入 `!lore add 關鍵字 | 設定內容` 或 `!lore import` 批次匯入！")
                return
            lines = [f"**#{e['id']} 關鍵字：`{e['keywords']}`**\n└ {e['content']}" for e in entries]
            embed = discord.Embed(
                title=f"📖 世界觀設定集 (Lorebook) - 共 {len(entries)} 筆設定",
                description=truncate_embed_text("\n\n".join(lines[:20])),
                color=0x00BBF9,
            )
            if len(entries) > 20:
                embed.set_footer(text=f"💡 僅顯示前 20 筆（共 {len(entries)} 筆）| 聊天提到關鍵字時 AI 會自動調用！")
            else:
                embed.set_footer(text="💡 聊天提到關鍵字時，AI 會自動調用該設定，完全不浪費多餘 Token！")
            await ctx.send(embed=embed)
            return

        if act in ("import", "batch", "匯入", "批次"):
            raw_text = (arg or "").strip()

            if raw_text and "\n" not in raw_text and raw_text.lower().endswith((".txt", ".md", ".json", ".text")):
                local_path = assistant_db.IMPORTS_DIR / Path(raw_text).name
                if local_path.is_file():
                    try:
                        raw_text = local_path.read_text(encoding="utf-8", errors="replace").strip()
                        self.logger.info(f"📖 成功從本機 data/imports/{local_path.name} 讀取世界觀設定")
                    except Exception as e:
                        await ctx.send(f"❌ 讀取本機檔案 `{local_path.name}` 失敗：{e}")
                        return

            if ctx.message.attachments:
                att = ctx.message.attachments[0]
                if att.filename.lower().endswith((".txt", ".md", ".json", ".text")):
                    if att.size and att.size > MAX_TEXT_ATTACHMENT_BYTES:
                        await ctx.send(f"❌ 附件 `{att.filename}` 超過 2MB 大小上限！")
                        return
                    try:
                        file_bytes = await att.read()
                        file_text = file_bytes.decode("utf-8", errors="replace")
                        raw_text = file_text + ("\n" + raw_text if raw_text else "")
                        self.logger.info(f"📖 成功從附件 {att.filename} 讀取世界觀設定 ({len(file_bytes)} bytes)")
                    except Exception as e:
                        await ctx.send(f"❌ 讀取附件檔案失敗：{e}")
                        return

            if not raw_text.strip():
                await ctx.send(
                    "📖 **世界觀設定集 (`!lore import`) 支援 3 種匯入方式：**\n"
                    "1️⃣ **直接在對話框貼上文字**（最推薦）：\n"
                    "```text\n!lore import\n【艾爾達王國】南方大陸的古老帝國...\n【蒼銀龍騎士團】帝國最精銳的騎兵...\n```\n"
                    "2️⃣ **從本機資料夾匯入**：將 `.txt`/`.md` 放進專案的 `data/imports/` 資料夾，輸入 `!lore import 檔名.txt`\n"
                    "3️⃣ **Discord 拖曳上傳檔案**：將 `.txt`/`.md` 拖進 Discord 對話框，並在留言欄輸入 `!lore import`"
                )
                return

            parsed = rpg_engine.parse_lore_text_blocks(raw_text)
            if not parsed:
                await ctx.send("❌ 未能識別出有效的設定條目。請確認使用 `【名稱】內容` 或 `名稱：內容` 格式！")
                return

            count = await asyncio.to_thread(rpg_engine.batch_add_lorebook_entries, parsed)
            preview_lines = [f"• **{kw}**：{cnt[:50]}..." if len(cnt) > 50 else f"• **{kw}**：{cnt}" for kw, cnt in parsed[:5]]
            desc = f"🎉 成功批次解析並存入 **{count}** 筆世界觀設定！\n\n**前幾筆設定預覽：**\n" + "\n".join(preview_lines)
            if count > 5:
                desc += f"\n\n*...以及其他 {count - 5} 筆設定已全部寫入資料庫！*"
            embed = discord.Embed(
                title="📖 世界觀設定集批次匯入成功！",
                description=truncate_embed_text(desc),
                color=0x06D6A0,
            )
            embed.set_footer(text="輸入 !lore 可查看所有設定清單 | 冒險中提到名稱時 AI 自動調用！")
            await ctx.send(embed=embed)
            return

        if act in ("clear", "reset", "清空", "重置"):
            if not self.settings.enable_japanese_mode and ctx.guild and not ctx.author.guild_permissions.manage_messages:
                await ctx.send("⚠️ 在社群伺服器中，僅限擁有「管理訊息」權限的管理員可執行 `!lore clear` 清空全服世界觀設定喔！（一般成員可使用 `!lore del <編號>` 刪除單筆設定）")
                return
            count = await asyncio.to_thread(rpg_engine.clear_lorebook)
            await ctx.send(f"🧹 已清空世界觀設定集（共刪除 {count} 筆設定）。隨時可以重新匯入新世界觀！")
            return

        if act in ("add", "new", "+"):
            if not arg or "|" not in arg:
                await ctx.send("❓ 請依照格式新增：`!lore add 關鍵字1, 關鍵字2 | 設定內容`\n例如：`!lore add 星露草 | 深山中的發光罕見藥草，能解百毒。`\n若要一次匯入多筆，請使用 `!lore import`！")
                return
            parts = arg.split("|", 1)
            kws = parts[0].strip()
            cnt = parts[1].strip()
            lid = await asyncio.to_thread(rpg_engine.add_lorebook_entry, kws, cnt)
            await ctx.send(f"✅ 已成功將世界設定 **#{lid}** 存入設定集！關鍵字：`{kws}`")
            return

        if act in ("del", "delete", "rm", "-"):
            if not arg or not arg.strip().isdigit():
                await ctx.send("❓ 請輸入要刪除的設定編號，例如：`!lore del 1`")
                return
            lid = int(arg.strip())
            if await asyncio.to_thread(rpg_engine.delete_lorebook_entry, lid):
                await ctx.send(f"🗑️ 已刪除設定 **#{lid}**。")
            else:
                await ctx.send(f"❌ 找不到設定編號 **#{lid}**。")
            return
