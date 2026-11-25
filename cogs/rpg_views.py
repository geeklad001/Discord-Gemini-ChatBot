"""Interactive Discord UI Views for TRPG Dice Checks and Quick Party/Bag Inspection."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Optional

import discord

from core.config import truncate_embed_text
from engines import rpg_engine

if TYPE_CHECKING:
    from cogs.rpg_cog import RpgCog


def _build_roll_result_embed(res: dict) -> discord.Embed:
    """建構權威擲骰檢定結果 Embed。"""
    if res["is_crit_success"]:
        color = 0xF1C40F
        outcome_line = "🌟 **大成功 (Critical Success)**"
    elif res["is_crit_fail"]:
        color = 0xE74C3C
        outcome_line = "💀 **大失敗 (Critical Fumble)**"
    elif res["passed"]:
        color = 0x2ECC71
        outcome_line = "✅ **檢定成功 (Success)**"
    else:
        color = 0xE74C3C
        outcome_line = "❌ **檢定失敗 (Failure)**"

    rolls = res.get("rolls") or [res.get("raw_total", 0)]
    rolls_str = "+".join(str(r) for r in rolls) if len(rolls) > 1 else str(rolls[0])

    if res.get("is_roll_under"):
        roll_line = f"🎲 骰出：`{res['dice_expr']}` = **`{res['total']}`**（目標 `<= {res['dc']}`）"
    else:
        bonus = int(res.get("bonus") or 0)
        bonus_label = str(res.get("bonus_label") or "").strip()
        if bonus != 0:
            lbl_part = f"（{bonus_label}）" if bonus_label else ""
            bonus_str = f" `{bonus:+d}`{lbl_part}"
        else:
            bonus_str = f"（{bonus_label}）" if bonus_label else ""
        roll_line = f"🎲 骰出：`{res['dice_expr']}` → `{rolls_str}`{bonus_str} = **`{res['total']}`** vs **DC {res['dc']}**"

    lines = [
        f"👤 **執行冒險者**：{res['char_name']}（{res['user_name']}）",
        roll_line,
        f"⚖️ **判定結果**：{outcome_line}",
    ]
    if res.get("reason"):
        lines.append(f"📌 **情境依據**：{res['reason']}")

    embed = discord.Embed(
        title=f"🎲 檢定結果 — {res['check_name']}"[:256],
        description=truncate_embed_text("\n".join(lines), max_len=3600),
        color=color,
    )
    embed.set_footer(text="⚔️ 結果已由本地真隨機骰子鎖定，DM 正在推演後續劇情…")
    return embed


class RpgRollCheckView(discord.ui.View):
    """互動式 TRPG 檢定按鈕列（Row 0 命運檢定按鈕 + Row 1 個人背包/手記/小隊捷徑）。"""

    def __init__(self, cog: Optional["RpgCog"], check_requests: list[dict], *, timeout: int = 900):
        super().__init__(timeout=timeout)
        self.cog = cog
        self.check_requests = list(check_requests or [])[:3]
        self.message: Optional[discord.Message] = None
        self._roll_lock = asyncio.Lock()
        self.completed_indices: set[int] = set()

        for idx, req in enumerate(self.check_requests):
            self.add_item(self._make_roll_button(idx, req))

        bag_btn = discord.ui.Button(label="🎒 我的背包", style=discord.ButtonStyle.secondary, row=1)
        bag_btn.callback = self._on_bag_info
        self.add_item(bag_btn)

        journal_btn = discord.ui.Button(label="📜 旅程手記", style=discord.ButtonStyle.secondary, row=1)
        journal_btn.callback = self._on_journal_info
        self.add_item(journal_btn)

        party_btn = discord.ui.Button(label="👥 小隊狀態", style=discord.ButtonStyle.secondary, row=1)
        party_btn.callback = self._on_party_info
        self.add_item(party_btn)

    def _make_roll_button(self, idx: int, req: dict) -> discord.ui.Button:
        check_name = str(req.get("check_name") or f"檢定 {idx + 1}").strip()
        dc = req.get("dc", 12)
        label = f"🎲 {check_name[:28]} (DC {dc})"[:80]
        btn = discord.ui.Button(label=label, style=discord.ButtonStyle.primary, row=0)

        async def _on_roll_click(interaction: discord.Interaction) -> None:
            if idx in self.completed_indices:
                await interaction.response.send_message("ℹ️ 這項檢定已經擲過骰結算囉！", ephemeral=True)
                return
            if self._roll_lock.locked():
                await interaction.response.send_message("⏳ 正在處理另一次檢定，請稍候再試！", ephemeral=True)
                return

            async with self._roll_lock:
                if idx in self.completed_indices:
                    await interaction.response.send_message("ℹ️ 這項檢定已經擲過骰結算囉！", ephemeral=True)
                    return

                await interaction.response.defer()
                channel_id = getattr(interaction.channel, "id", 0)
                user_id = getattr(interaction.user, "id", 0)
                user_name = (
                    getattr(interaction.user, "display_name", None)
                    or getattr(interaction.user, "name", None)
                    or "冒險者"
                )

                res = await asyncio.to_thread(
                    rpg_engine.execute_interactive_check,
                    channel_id,
                    user_id,
                    user_name,
                    req,
                )

                self.completed_indices.add(idx)
                btn.disabled = True
                icon = "✅" if res["passed"] else "❌"
                btn.label = f"{icon} {check_name[:24]} ({res['total']})"[:80]
                btn.style = discord.ButtonStyle.success if res["passed"] else discord.ButtonStyle.danger

                try:
                    await interaction.edit_original_response(view=self)
                except discord.HTTPException:
                    pass

                result_embed = _build_roll_result_embed(res)
                try:
                    await interaction.followup.send(embed=result_embed)
                except discord.HTTPException:
                    if interaction.channel is not None:
                        await interaction.channel.send(embed=result_embed)

                if self.cog is not None:
                    await self.cog.trigger_dm_after_roll(interaction.channel, interaction.user, res)

        btn.callback = _on_roll_click
        return btn

    async def _on_bag_info(self, interaction: discord.Interaction) -> None:
        channel_id = getattr(interaction.channel, "id", 0)
        user_id = getattr(interaction.user, "id", 0)
        char = await asyncio.to_thread(rpg_engine.get_rpg_character, channel_id, user_id)
        if not char:
            await interaction.response.send_message(
                "🏕️ 你尚未在此頻道／討論串建立角色！輸入 `!rpg join` 即可快速入隊，或 `!sheet <角色設定>` 帶卡參戰。",
                ephemeral=True,
            )
            return

        inv = char.get("inventory") or []
        inv_str = "\n".join(f"• {it}" for it in inv[:25]) if inv else "（背包空空如也）"
        if len(inv) > 25:
            inv_str += f"\n*...另有 {len(inv) - 25} 件物品*"
        embed = discord.Embed(
            title=f"🎒 {char['char_name']} 的隨身狀態"[:256],
            description=truncate_embed_text(
                f"👤 **職業／定位**：{char['char_class']}\n"
                f"❤️ **生命值**：`{char['hp']}/{char['max_hp']}`\n"
                f"📍 **地點**：`{char['location']}`\n"
                f"🎯 **目標**：`{char['current_quest']}`\n\n"
                f"**隨身物品**：\n{inv_str}",
                max_len=3600,
            ),
            color=0x3498DB,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    async def _on_journal_info(self, interaction: discord.Interaction) -> None:
        channel_id = getattr(interaction.channel, "id", 0)
        notes = await asyncio.to_thread(rpg_engine.get_journey_notes, channel_id)
        if not notes:
            await interaction.response.send_message(
                "📓 目前這趟旅程還沒有任何手記或線索。隨時輸入 `!note <線索>` 記錄重要情報！",
                ephemeral=True,
            )
            return

        lines = []
        for n in notes[-10:]:
            tag = "重要線索" if n.get("note_type") == "clue" else n.get("note_type", "clue")
            lines.append(f"**#{n['id']} [{tag}]** {n['content']}\n└ *由 {n['user_name']} 記於 {str(n['created_at'])[5:16]}*")
        embed = discord.Embed(
            title=f"📜 旅程手記（最近 {min(10, len(notes))} / 共 {len(notes)} 條）"[:256],
            description=truncate_embed_text("\n\n".join(lines), max_len=3600),
            color=0xFFB703,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    async def _on_party_info(self, interaction: discord.Interaction) -> None:
        channel_id = getattr(interaction.channel, "id", 0)
        chars = await asyncio.to_thread(rpg_engine.get_all_characters_in_channel, channel_id)
        if not chars:
            await interaction.response.send_message(
                "🏕️ 目前此頻道／討論串尚未有冒險者入隊！請輸入 `!rpg join` 加入隊伍。",
                ephemeral=True,
            )
            return

        lines = [
            f"📍 **地點**：`{chars[0]['location']}`｜🎯 **目標**：`{chars[0]['current_quest']}`",
            "",
        ]
        for c in chars[:15]:
            prof = c.get("profile") or {}
            ac_part = f" | AC `{prof['ac']}`" if prof.get("ac") is not None else ""
            lines.append(
                f"• **{c['char_name']}**（{c['char_class']}｜{c['user_name']}）"
                f"— HP `{c['hp']}/{c['max_hp']}`{ac_part}"
            )
        embed = discord.Embed(
            title=f"👥 冒險小隊狀態（共 {len(chars)} 人）"[:256],
            description=truncate_embed_text("\n".join(lines), max_len=3600),
            color=0x06D6A0,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    async def on_timeout(self) -> None:
        for child in self.children:
            if hasattr(child, "disabled"):
                child.disabled = True
        msg = getattr(self, "message", None)
        if msg is not None:
            try:
                await msg.edit(view=self)
            except Exception:
                pass
