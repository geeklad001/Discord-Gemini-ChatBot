"""Interactive UI Views and Embed builders for the Board Game system (!play)."""

import asyncio
import io
from typing import TYPE_CHECKING, Optional

import discord

from cogs.lifesim_cog import (
    build_atlas_embed,
    build_lifesim_setup_embed,
    create_lifesim_option_selects,
)
from core.config import resolve_bot_display_name, truncate_embed_text
from engines import boardgame_engine as bg
from engines import lifesim_engine as ls

if TYPE_CHECKING:
    from cogs.boardgame_cog import BoardGameCog


async def _disable_view_on_timeout(view: discord.ui.View) -> None:
    """停用逾時棋局/大廳卡片上的所有互動元件，避免舊訊息按鈕殘留可點狀態。"""
    for child in view.children:
        if hasattr(child, "disabled"):
            child.disabled = True
    msg = getattr(view, "message", None)
    if msg is not None:
        try:
            await msg.edit(view=view)
        except Exception:
            pass


async def _try_acquire_game_lock(
    cog: "BoardGameCog",
    game_id: int,
    interaction: discord.Interaction,
) -> Optional[asyncio.Lock]:
    """Check if the per-game lock is free; notify ephemerally and return None if busy."""
    lock = cog._get_game_lock(game_id)
    if lock.locked():
        await interaction.response.send_message(
            "⏳ 目前棋局仍在處理上一步（或 AI 思考中），請稍候再試！",
            ephemeral=True,
        )
        return None
    return lock


def _build_board_embed(
    game: dict,
    last_note: str = "",
    selected_sq: Optional[str] = None,
    bot_name: str = "機器人",
) -> discord.Embed:
    """Build the Discord Embed representing a waiting, active, or finished board game."""
    clean_bot = (bot_name or "機器人").strip()[:24] or "機器人"
    gtype = game["game_type"]
    meta = bg.GAME_CATALOG[gtype]
    status = game["status"]
    p1_label = meta["p1_label"]
    p2_label = meta["p2_label"]
    if game["mode"] == "pve":
        mode_tag = (
            f"與 {clean_bot} 對戰"
            if game["difficulty"] == "llm"
            else f"PvE ({bg.DIFFICULTY_LABELS.get(game['difficulty'], '普通')})"
        )
    else:
        mode_tag = "PvP 雙人對戰"

    if status == "waiting":
        invite_txt = f"指定挑戰 <@{game['invited_id']}>" if game.get("invited_id") else "公開挑戰（任何人皆可加入）"
        embed = discord.Embed(
            title=f"{meta['emoji']} {meta['name']} — 等待對手加入",
            description=truncate_embed_text(
                f"發起人 **{game['p1_name']}** ({p1_label}) 開了一局新對戰！\n"
                f"📣 **對戰形式**：{invite_txt}\n\n"
                f"點擊下方 **「⚔️ 加入對戰」** 即可立刻開打，或由發起人改與 {clean_bot} 對戰。",
                max_len=3800,
            ),
            color=0xFEE75C,
        )
        embed.set_footer(text=f"棋局 #{game['id']} | {mode_tag}")
        return embed

    if status == "finished":
        winner = game["winner"]
        reason = game.get("end_reason", "")
        if reason == "aborted":
            result_line = "🚫 **此對局已取消**"
            color = 0x95A5A6
        elif winner == -1:
            result_line = "🤝 **雙方和局 (Draw)**"
            color = 0x3498DB
        elif winner == 1:
            result_line = f"🏆 **勝者：{p1_label} {game['p1_name']}**"
            color = 0x57F287
        else:
            result_line = f"🏆 **勝者：{p2_label} {game['p2_name']}**"
            color = 0x57F287
        if reason == "resign":
            result_line += "（對手投降）"
        elif reason == "checkmate":
            result_line += "（將死 Checkmate）"
    else:
        turn_label = p1_label if game["turn"] == 1 else p2_label
        turn_name = game["p1_name"] if game["turn"] == 1 else game["p2_name"]
        result_line = f"👉 **目前輪到：{turn_label} `{turn_name}`**"
        color = 0x5865F2 if game["turn"] == 1 else 0xEB459E

    desc_parts = [
        f"**{p1_label}**：`{game['p1_name']}`  🆚  **{p2_label}**：`{game['p2_name']}`",
        result_line,
    ]
    if last_note:
        desc_parts.append(f"📌 **最新動態**：{last_note}")
    if gtype == "othello":
        b_cnt, w_cnt = bg._othello_counts(game["state"]["board"])
        desc_parts.append(f"📊 **目前子數**：⚫ `{b_cnt}`  :  `{w_cnt}` ⚪")
    elif gtype == "chess" and status == "active":
        if game["state"].get("in_check"):
            desc_parts.append("⚠️ **將軍 (CHECK)！請優先解除將軍威脅**")
        if selected_sq:
            desc_parts.append(f"🎯 **已選棋子**：`{selected_sq}`（請在第二個選單選擇綠色高亮目標格）")
    if status == "active" and game.get("draw_offer_by"):
        offerer = game["p1_name"] if game["draw_offer_by"] == game["p1_id"] else game["p2_name"]
        desc_parts.append(f"🤝 **{offerer}** 提出了和局請求（對手可點擊「🤝 接受和局」）")
    if status == "active" and game.get("undo_approvals"):
        approvals = game["undo_approvals"]
        req_cnt = len(bg.required_human_players(game))
        names = []
        for uid in approvals:
            if uid == game["p1_id"]:
                names.append(game["p1_name"])
            elif uid == game.get("p2_id"):
                names.append(game["p2_name"])
        desc_parts.append(
            f"↩️ **{'、'.join(names) or '玩家'}** 請求退回上一步（同意進度 `{len(approvals)}/{req_cnt}` — 需全體參賽玩家點擊「✅ 同意上一步」）"
        )

    embed = discord.Embed(
        title=f"{meta['emoji']} {meta['name']} — {mode_tag}",
        description=truncate_embed_text("\n".join(desc_parts), max_len=3800),
        color=color,
    )

    recent_moves = (game["state"].get("moves") or [])[-6:]
    if recent_moves:
        embed.add_field(
            name=f"📜 最近著法（第 {game['move_count']} 手）",
            value=truncate_embed_text(" → ".join(recent_moves), max_len=900),
            inline=False,
        )

    embed.set_image(url="attachment://board.png")

    if status == "active":
        coord_hint = f"可點擊下方按鈕/選單，或直接用自然語言請 {clean_bot} 幫忙下棋（輔助：!m <座標>）"
        if gtype == "othello":
            coord_hint = f"用下方選單點選綠點，或直接對 {clean_bot} 說想下哪裡（亦支援 !m d3）"
        elif gtype == "chess":
            coord_hint = f"用下方選單選子→選格，或口語指揮 {clean_bot}（亦支援 !m e2e4 / Nf3）"
        elif gtype == "connect4":
            coord_hint = f"點擊下方 1️⃣~7️⃣ 按鈕投子，或對 {clean_bot} 說投第幾欄"
        elif gtype == "gomoku":
            coord_hint = f"用推薦熱點選單、對 {clean_bot} 口述座標，或輸入 !m g7"
        embed.set_footer(text=f"棋局 #{game['id']} | {coord_hint}")
    else:
        embed.set_footer(text=f"棋局 #{game['id']} 已結束 | 可點「🏆 我的戰績」或對 {clean_bot} 說「查看桌遊戰績」")
    return embed


async def _render_board_file(game: dict, highlight_from: Optional[str] = None) -> discord.File:
    """Render a board state to a PNG Discord attachment off the event loop."""
    png_bytes = await asyncio.to_thread(bg.render_board_image, game, highlight_from)
    return discord.File(io.BytesIO(png_bytes), filename="board.png")


def _build_game_lobby_embed(
    channel_id: int,
    user_id: Optional[int] = None,
    bot_name: str = "機器人",
) -> discord.Embed:
    """Build the unified Discord game lobby Embed for board games and 1in8B."""
    clean_bot = (bot_name or "機器人").strip()[:24] or "機器人"
    active = bg.get_active_game(channel_id)
    active_life = ls.get_active_life_run(channel_id, user_id=user_id) if user_id else None
    lines = []
    for key, meta in bg.GAME_CATALOG.items():
        lines.append(f"{meta['emoji']} **{meta['name']}**（`{key}` | {meta['size']}）\n└ {meta['desc']}")
    lines.append(
        "🌍 **80億分之1 (1 in 8 Billion)**（`1in8b` | 197 國真實人口統計）\n"
        "└ 依聯合國/世界銀行 197 國真實出生機率隨機投胎，含城鄉・特質・檢定擲骰・跨國移居！（⚡經典深度版 / 🎭精華劇情版 / 🧠全程故事版）"
    )

    banners = []
    if active:
        ameta = bg.GAME_CATALOG[active["game_type"]]
        banners.append(f"⚠️ 此頻道有一場進行中的 `{ameta['name']}`（輸入 `!play board` 或說「重貼棋盤」可叫回面板）。")
    if active_life:
        banners.append(
            f"🌍 你在此頻道有一段進行中的人生：**{active_life['char_name']}**（{active_life['country_name']}｜{active_life['age']}歲）"
        )
    status_banner = ("\n\n" + "\n".join(banners)) if banners else ""

    embed = discord.Embed(
        title="🎮 Discord 遊戲大廳 (`!play`)",
        description=truncate_embed_text(
            "所有棋局規則與人生模擬皆由內建裁判系統嚴格判定、公平公正，並即時繪製專屬圖片卡片！\n\n"
            + "\n\n".join(lines)
            + status_banner,
            max_len=3800,
        ),
        color=0x5865F2,
    )
    embed.add_field(
        name="🚀 極簡玩法（按鈕選單 ＋ 自然語言都能玩）",
        value=(
            f"• **用選的**：下拉選遊戲與模式（棋盤支援 **對戰電腦 / 🧠 {clean_bot} / 雙人對戰**；`80億分之1` 可選 **⚡經典深度版 / 🎭精華劇情版 / 🧠全程故事版 / 🧵開專屬串**）\n"
            f"• **用說的**：直接打「我想跟 {clean_bot} 玩黑白棋」、「幫我下 C4」、「我想玩80億分之1」、「這題選A / 我決定去考公職」\n"
            "• **用指令**：`!play 黑白棋` / `!play 西洋棋 ai` / `!play 1in8b` / `!play 圖鑑`"
        ),
        inline=False,
    )
    embed.set_footer(text="💡 對局內所有功能（投降、悔棋、提示、人生抉擇、世界圖鑑、戰績）皆可直接點卡片按鈕或口語操作！")
    return embed


def build_stats_embed(target_user: Optional[discord.abc.User] = None) -> discord.Embed:
    """Build a user or server-wide board game stats Embed."""
    rows = bg.get_user_stats(target_user.id if target_user else None, limit=15)
    title = f"🏆 {target_user.display_name} 的桌遊戰績" if target_user else "🏆 伺服器桌遊排行榜"
    if not rows:
        return discord.Embed(title=title, description="尚無已完成的棋局紀錄，快輸入 `!play` 開一局吧！", color=0x5865F2)
    lines = []
    for r in rows[:15]:
        meta = bg.GAME_CATALOG.get(r["game_type"], {"short": r["game_type"], "emoji": "🎲"})
        mode_tag = "PvE" if r["mode"] == "pve" else "PvP"
        lines.append(
            f"{meta['emoji']} **{meta['short']}** [{mode_tag}] — {r['user_name']}："
            f"`{r['wins']} 勝` / `{r['losses']} 敗` / `{r['draws']} 和`"
        )
    return discord.Embed(title=title, description=truncate_embed_text("\n".join(lines), max_len=3800), color=0xF1C40F)


class GameLobbyView(discord.ui.View):
    """互動式遊戲大廳：依所選遊戲動態切換「棋盤對戰設定」或「80億分之1投胎設定」。"""

    def __init__(
        self,
        cog: Optional["BoardGameCog"],
        initial_game: str = "othello",
        owner_id: Optional[int] = None,
        bot_name: str = "機器人",
    ):
        super().__init__(timeout=600)
        self.cog = cog
        self.owner_id = owner_id
        self.bot_name = (bot_name or "機器人").strip()[:24] or "機器人"
        self.message: Optional[discord.Message] = None
        self.selected_game = initial_game
        self.selected_mode = "pve:normal"
        self.era = "present"
        self.draw_method = "births"
        self.story_mode = "fast"

        self.game_select = self._make_game_select()
        self.mode_select = self._make_mode_select()
        self.era_select, self.method_select, self.story_select = create_lifesim_option_selects(
            self.era, self.draw_method, self.story_mode, start_row=1
        )
        self.era_select.callback = self._on_select_era
        self.method_select.callback = self._on_select_method
        self.story_select.callback = self._on_select_story

        self.board_start_btn = discord.ui.Button(label="立即開局", emoji="▶️", style=discord.ButtonStyle.success, row=2)
        self.board_start_btn.callback = self._on_board_start
        self.stats_btn = discord.ui.Button(label="我的戰績 / 圖鑑", emoji="🏆", style=discord.ButtonStyle.secondary, row=2)
        self.stats_btn.callback = self._on_stats

        self.life_start_btn = discord.ui.Button(label="立即投胎出生", emoji="🌍", style=discord.ButtonStyle.success, row=4)
        self.life_start_btn.callback = self._on_life_start_here
        self.life_thread_btn = discord.ui.Button(label="開專屬串投胎", emoji="🧵", style=discord.ButtonStyle.primary, row=4)
        self.life_thread_btn.callback = self._on_life_start_thread
        self.life_atlas_btn = discord.ui.Button(label="我的國家圖鑑", emoji="🗺️", style=discord.ButtonStyle.secondary, row=4)
        self.life_atlas_btn.callback = self._on_life_atlas

        self._rebuild_items()

    def _make_game_select(self) -> discord.ui.Select:
        game_opts = [
            discord.SelectOption(
                label=meta["name"],
                value=gtype,
                emoji=meta["emoji"],
                description=meta["size"],
                default=(gtype == self.selected_game),
            )
            for gtype, meta in bg.GAME_CATALOG.items()
        ]
        game_opts.append(
            discord.SelectOption(
                label="80億分之1 (1in8B 人生模擬)",
                value="1in8b",
                emoji="🌍",
                description="197國真實機率投胎｜⚡經典深度版 / 🎭精華劇情版 / 🧠全程故事版",
                default=(self.selected_game == "1in8b"),
            )
        )
        sel = discord.ui.Select(placeholder="1️⃣ 選擇要玩的遊戲...", options=game_opts, row=0)
        sel.callback = self._on_select_game
        return sel

    def _make_mode_select(self) -> discord.ui.Select:
        llm_label = f"與 {self.bot_name} 對戰（會陪聊互動 🧠）"[:100]
        mode_opts = [
            discord.SelectOption(label="對戰電腦（簡單 🌱）", value="pve:easy", default=(self.selected_mode == "pve:easy")),
            discord.SelectOption(label="對戰電腦（普通 ⚔️）", value="pve:normal", default=(self.selected_mode == "pve:normal")),
            discord.SelectOption(label="對戰電腦（困難 🔥）", value="pve:hard", default=(self.selected_mode == "pve:hard")),
            discord.SelectOption(label=llm_label, value="pve:llm", default=(self.selected_mode == "pve:llm")),
            discord.SelectOption(label="PvP 雙人公開對戰（等待群友加入 👥）", value="pvp:open", default=(self.selected_mode == "pvp:open")),
        ]
        sel = discord.ui.Select(placeholder="2️⃣ 選擇 PvE 難度或 PvP 對戰...", options=mode_opts, row=1)
        sel.callback = self._on_select_mode
        return sel

    def _rebuild_items(self) -> None:
        self.clear_items()
        for opt in self.game_select.options:
            opt.default = (opt.value == self.selected_game)
        self.add_item(self.game_select)

        if self.selected_game == "1in8b":
            for opt in self.era_select.options:
                opt.default = (opt.value == self.era)
            for opt in self.method_select.options:
                opt.default = (opt.value == self.draw_method)
            for opt in self.story_select.options:
                opt.default = (opt.value == self.story_mode)
            self.add_item(self.era_select)
            self.add_item(self.method_select)
            self.add_item(self.story_select)
            self.add_item(self.life_start_btn)
            self.add_item(self.life_thread_btn)
            self.add_item(self.life_atlas_btn)
        else:
            for opt in self.mode_select.options:
                opt.default = (opt.value == self.selected_mode)
            self.add_item(self.mode_select)
            self.add_item(self.board_start_btn)
            self.add_item(self.stats_btn)

    def _current_embed(self, channel_id: int, user_id: Optional[int] = None) -> discord.Embed:
        if self.selected_game == "1in8b":
            return build_lifesim_setup_embed(self.era, self.draw_method, self.story_mode)
        return _build_game_lobby_embed(channel_id, user_id=user_id, bot_name=self.bot_name)

    async def _current_embed_async(self, channel_id: int, user_id: Optional[int] = None) -> discord.Embed:
        if self.selected_game == "1in8b":
            return build_lifesim_setup_embed(self.era, self.draw_method, self.story_mode)
        return await asyncio.to_thread(_build_game_lobby_embed, channel_id, user_id, self.bot_name)

    async def _check_owner(self, interaction: discord.Interaction) -> bool:
        if self.owner_id is not None and interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "ℹ️ 請輸入 `!play` 開啟你自己的遊戲大廳，才不會改到別人的設定喔！",
                ephemeral=True,
            )
            return False
        return True

    async def _retire_lobby(self, interaction: discord.Interaction) -> None:
        self.message = None
        self.stop()
        try:
            await interaction.edit_original_response(view=None)
        except Exception:
            pass

    async def on_timeout(self) -> None:
        await _disable_view_on_timeout(self)

    async def _on_select_game(self, interaction: discord.Interaction):
        if not await self._check_owner(interaction):
            return
        self.selected_game = self.game_select.values[0]
        self._rebuild_items()
        embed = await self._current_embed_async(interaction.channel.id, user_id=interaction.user.id)
        await interaction.response.edit_message(embed=embed, view=self)

    async def _on_select_mode(self, interaction: discord.Interaction):
        if not await self._check_owner(interaction):
            return
        self.selected_mode = self.mode_select.values[0]
        for opt in self.mode_select.options:
            opt.default = (opt.value == self.selected_mode)
        await interaction.response.edit_message(view=self)

    async def _on_select_era(self, interaction: discord.Interaction):
        if not await self._check_owner(interaction):
            return
        self.era = self.era_select.values[0]
        self._rebuild_items()
        embed = await self._current_embed_async(interaction.channel.id, user_id=interaction.user.id)
        await interaction.response.edit_message(embed=embed, view=self)

    async def _on_select_method(self, interaction: discord.Interaction):
        if not await self._check_owner(interaction):
            return
        self.draw_method = self.method_select.values[0]
        self._rebuild_items()
        embed = await self._current_embed_async(interaction.channel.id, user_id=interaction.user.id)
        await interaction.response.edit_message(embed=embed, view=self)

    async def _on_select_story(self, interaction: discord.Interaction):
        if not await self._check_owner(interaction):
            return
        self.story_mode = self.story_select.values[0]
        self._rebuild_items()
        embed = await self._current_embed_async(interaction.channel.id, user_id=interaction.user.id)
        await interaction.response.edit_message(embed=embed, view=self)

    async def _on_board_start(self, interaction: discord.Interaction):
        if not await self._check_owner(interaction):
            return
        mode_kind, sub = self.selected_mode.split(":", 1)
        mode = "pve" if mode_kind == "pve" else "pvp"
        diff = sub if mode == "pve" else "normal"
        bname = (
            resolve_bot_display_name(
                self.cog.bot,
                guild=interaction.guild,
                channel=interaction.channel,
                settings=self.cog.settings,
            )
            if self.cog
            else self.bot_name
        )
        try:
            game = await asyncio.to_thread(
                bg.create_game,
                interaction.channel.id,
                self.selected_game,
                mode,
                interaction.user.id,
                interaction.user.display_name,
                diff,
                bot_name=bname,
            )
        except ValueError as e:
            await interaction.response.send_message(f"⚠️ {e}", ephemeral=True)
            return

        await interaction.response.defer()
        await self._retire_lobby(interaction)
        await self.cog.send_game_card(interaction.channel, game, last_note="對局已建立！")

    async def _on_life_start_here(self, interaction: discord.Interaction):
        if not await self._check_owner(interaction):
            return
        life_cog = self.cog.bot.get_cog("LifeSimCog")
        if not life_cog:
            await interaction.response.send_message("⚠️ 80億分之1 模組尚未載入。", ephemeral=True)
            return
        await interaction.response.defer()
        try:
            await life_cog.start_life_and_send(
                interaction.channel,
                interaction.user,
                story_mode=self.story_mode,
                era=self.era,
                draw_method=self.draw_method,
                retire_interaction=interaction,
            )
        except ValueError as e:
            await interaction.followup.send(f"⚠️ {e}", ephemeral=True)
            return
        self.message = None
        self.stop()

    async def _on_life_start_thread(self, interaction: discord.Interaction):
        if not await self._check_owner(interaction):
            return
        life_cog = self.cog.bot.get_cog("LifeSimCog")
        if not life_cog:
            await interaction.response.send_message("⚠️ 80億分之1 模組尚未載入。", ephemeral=True)
            return
        try:
            msg = await life_cog.start_life_in_new_thread(
                interaction,
                story_mode=self.story_mode,
                era=self.era,
                draw_method=self.draw_method,
                retire_interaction=interaction,
            )
        except ValueError as e:
            if interaction.response.is_done():
                await interaction.followup.send(f"⚠️ {e}", ephemeral=True)
            else:
                await interaction.response.send_message(f"⚠️ {e}", ephemeral=True)
            return
        if msg is not None:
            self.message = None
            self.stop()

    async def _on_life_atlas(self, interaction: discord.Interaction):
        entries = await asyncio.to_thread(ls.get_user_country_dex, interaction.user.id)
        await interaction.response.send_message(embed=build_atlas_embed(interaction.user, entries), ephemeral=True)

    async def _on_stats(self, interaction: discord.Interaction):
        embed = await asyncio.to_thread(build_stats_embed, interaction.user)
        entries = await asyncio.to_thread(ls.get_user_country_dex, interaction.user.id)
        embed.add_field(
            name="🗺️ 80億分之1 世界圖鑑",
            value=f"已解鎖 **{len(entries)} / 197** 個國家與地區（輸入 `!play 圖鑑` 查看詳情）",
            inline=False,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)


class LobbyChallengeView(discord.ui.View):
    """PvP 等待對手加入卡片按鈕。"""

    def __init__(self, cog: "BoardGameCog", game_id: int, bot_name: str = "機器人"):
        super().__init__(timeout=1800)
        self.cog = cog
        self.game_id = game_id
        self.bot_name = (bot_name or "機器人").strip()[:24] or "機器人"
        self.message: Optional[discord.Message] = None
        self.switch_pve_btn.label = f"改與 {self.bot_name} 對戰"[:80]

    async def on_timeout(self) -> None:
        await _disable_view_on_timeout(self)

    @discord.ui.button(label="加入對戰", emoji="⚔️", style=discord.ButtonStyle.success)
    async def join_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        lock = await _try_acquire_game_lock(self.cog, self.game_id, interaction)
        if lock is None:
            return
        async with lock:
            try:
                game = await asyncio.to_thread(bg.join_pvp_game, self.game_id, interaction.user.id, interaction.user.display_name)
            except ValueError as e:
                await interaction.response.send_message(f"⚠️ {e}", ephemeral=True)
                return
            await interaction.response.defer()
            await self.cog.edit_interaction_game_card(
                interaction,
                game,
                last_note=f"{interaction.user.display_name} 加入對戰，開局！",
                old_view=self,
            )

    @discord.ui.button(label="改為單人對戰", emoji="🤖", style=discord.ButtonStyle.primary)
    async def switch_pve_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        lock = await _try_acquire_game_lock(self.cog, self.game_id, interaction)
        if lock is None:
            return
        async with lock:
            bname = (
                resolve_bot_display_name(
                    self.cog.bot,
                    guild=interaction.guild,
                    channel=interaction.channel,
                    settings=self.cog.settings,
                )
                if self.cog
                else self.bot_name
            )
            try:
                game = await asyncio.to_thread(
                    bg.switch_waiting_to_pve,
                    self.game_id,
                    interaction.user.id,
                    bot_name=bname,
                )
            except ValueError as e:
                await interaction.response.send_message(f"⚠️ {e}", ephemeral=True)
                return
            await interaction.response.defer()
            await self.cog.edit_interaction_game_card(
                interaction,
                game,
                last_note="已切換為單人對戰，開局！",
                old_view=self,
            )

    @discord.ui.button(label="取消開局", emoji="❌", style=discord.ButtonStyle.danger)
    async def cancel_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        lock = await _try_acquire_game_lock(self.cog, self.game_id, interaction)
        if lock is None:
            return
        async with lock:
            try:
                game = await asyncio.to_thread(bg.cancel_waiting_game, self.game_id, interaction.user.id)
            except ValueError as e:
                await interaction.response.send_message(f"⚠️ {e}", ephemeral=True)
                return
            embed = _build_board_embed(game, last_note="發起人已取消開局。", bot_name=self.bot_name)
            await interaction.response.edit_message(embed=embed, view=None, attachments=[])
            self.message = None
            self.stop()


class FinishedBoardView(discord.ui.View):
    """對局結束後的一鍵再戰／戰績／回大廳按鈕。"""

    def __init__(self, cog: "BoardGameCog", game: dict):
        super().__init__(timeout=1800)
        self.cog = cog
        self.game = game
        self.message: Optional[discord.Message] = None

    async def on_timeout(self) -> None:
        await _disable_view_on_timeout(self)

    @discord.ui.button(label="再來一局 (互換先後手)", emoji="🔁", style=discord.ButtonStyle.success)
    async def rematch_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        g = self.game
        if g["mode"] == "pvp" and g.get("p2_id"):
            if interaction.user.id not in (g["p1_id"], g["p2_id"]):
                await interaction.response.send_message("⚠️ 僅限本局兩位玩家或點擊「回遊戲大廳」開新局。", ephemeral=True)
                return
            try:
                # 互換先後手並直接進入 active
                new_game = await asyncio.to_thread(
                    bg.create_game,
                    interaction.channel.id,
                    g["game_type"],
                    "pvp",
                    g["p2_id"],
                    g["p2_name"],
                    g["difficulty"],
                    g["p1_id"],
                    g["p1_name"],
                )
                new_game = await asyncio.to_thread(bg.join_pvp_game, new_game["id"], g["p1_id"], g["p1_name"])
            except ValueError as e:
                await interaction.response.send_message(f"⚠️ {e}", ephemeral=True)
                return
        else:
            bname = (
                resolve_bot_display_name(
                    self.cog.bot,
                    guild=interaction.guild,
                    channel=interaction.channel,
                    settings=self.cog.settings,
                )
                if self.cog
                else "機器人"
            )
            try:
                new_game = await asyncio.to_thread(
                    bg.create_game,
                    interaction.channel.id,
                    g["game_type"],
                    "pve",
                    interaction.user.id,
                    interaction.user.display_name,
                    g["difficulty"],
                    bot_name=bname,
                )
            except ValueError as e:
                await interaction.response.send_message(f"⚠️ {e}", ephemeral=True)
                return

        await interaction.response.defer()
        self.message = None
        self.stop()
        try:
            await interaction.edit_original_response(view=None)
        except Exception:
            pass
        await self.cog.send_game_card(interaction.channel, new_game, last_note="新局開始（Rematch）！")

    @discord.ui.button(label="我的戰績", emoji="🏆", style=discord.ButtonStyle.secondary)
    async def stats_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = await asyncio.to_thread(build_stats_embed, interaction.user)
        entries = await asyncio.to_thread(ls.get_user_country_dex, interaction.user.id)
        embed.add_field(
            name="🗺️ 80億分之1 世界圖鑑",
            value=f"已解鎖 **{len(entries)} / 197** 個國家與地區（輸入 `!play 圖鑑` 查看詳情）",
            inline=False,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(label="回遊戲大廳", emoji="🎮", style=discord.ButtonStyle.secondary)
    async def lobby_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        bname = (
            resolve_bot_display_name(
                self.cog.bot,
                guild=interaction.guild,
                channel=interaction.channel,
                settings=self.cog.settings,
            )
            if self.cog
            else "機器人"
        )
        embed = await asyncio.to_thread(
            _build_game_lobby_embed,
            interaction.channel.id,
            interaction.user.id,
            bot_name=bname,
        )
        view = GameLobbyView(self.cog, owner_id=interaction.user.id, bot_name=bname)
        await interaction.response.send_message(embed=embed, view=view)
        try:
            view.message = await interaction.original_response()
        except Exception:
            pass


class ActiveBoardView(discord.ui.View):
    """進行中棋局的互動控制面板（自動依遊戲類型長出按鈕/選單）。"""

    def __init__(self, cog: "BoardGameCog", game: dict, selected_sq: Optional[str] = None):
        super().__init__(timeout=1800)
        self.cog = cog
        self.game_id = game["id"]
        self.game = game
        self.selected_sq = selected_sq
        self.message: Optional[discord.Message] = None

        gtype = game["game_type"]
        if gtype == "connect4":
            self._add_connect4_buttons()
            util_row = 2
        elif gtype == "othello":
            self._add_othello_select()
            util_row = 1
        elif gtype == "chess":
            self._add_chess_selects()
            util_row = 2
        else:  # gomoku
            self._add_gomoku_select()
            util_row = 1

        self._add_utility_buttons(util_row)

    def _add_connect4_buttons(self) -> None:
        legal_cols = set(bg.get_connect4_legal_cols(self.game["state"]["board"]))
        num_emojis = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣"]
        for c in range(bg.C4_COLS):
            row_idx = 0 if c < 4 else 1
            btn = discord.ui.Button(
                label=f"第{c + 1}欄",
                emoji=num_emojis[c],
                style=discord.ButtonStyle.primary,
                disabled=(c not in legal_cols),
                row=row_idx,
            )
            btn.callback = self._make_direct_move_cb(str(c + 1))
            self.add_item(btn)

    def _add_othello_select(self) -> None:
        moves = bg.get_othello_legal_moves(self.game["state"]["board"], self.game["turn"])
        if not moves:
            return
        options = [
            discord.SelectOption(
                label=f"落子 {bg._coord_label(r, c)}",
                value=bg._coord_label(r, c),
                description=f"第 {r + 1} 列・第 {chr(ord('A') + c)} 行",
            )
            for r, c in moves[:25]
        ]
        sel = discord.ui.Select(placeholder="🎯 選擇合法落子座標...", options=options, row=0)
        sel.callback = self._on_select_move
        self.add_item(sel)

    def _add_gomoku_select(self) -> None:
        cands = bg.get_gomoku_candidates(self.game["state"]["board"], limit=20, player_hint=self.game["turn"])
        if not cands:
            return
        options = [
            discord.SelectOption(label=f"落子 {coord}", value=coord, description="建議熱點／鄰近交點（亦可 !m 座標）")
            for coord in cands
        ]
        sel = discord.ui.Select(placeholder="⭕ 選擇推薦熱點座標（或直接 !m g7）...", options=options, row=0)
        sel.callback = self._on_select_move
        self.add_item(sel)

    def _add_chess_selects(self) -> None:
        white_turn = (self.game["turn"] == 1)
        legal = bg.get_chess_legal_moves(self.game["state"], white_turn)
        if not legal:
            return
        board = self.game["state"]["board"]

        # Group moves by source square
        by_src: dict[str, list[tuple[int, int, int, int, str]]] = {}
        for mv in legal:
            src = bg._chess_sq_name(mv[0], mv[1])
            by_src.setdefault(src, []).append(mv)

        piece_options = []
        for src, mvs in sorted(by_src.items()):
            r, c = bg._chess_parse_sq(src)
            p_name = bg.CHESS_PIECE_NAMES.get(board[r][c], board[r][c])
            piece_options.append(
                discord.SelectOption(
                    label=f"{p_name} {src}（{len(mvs)} 個可走位置）",
                    value=src,
                    default=(src == self.selected_sq),
                )
            )
        piece_sel = discord.ui.Select(placeholder="1️⃣ 選擇要移動的己方棋子...", options=piece_options[:25], row=0)
        piece_sel.callback = self._on_chess_pick_piece
        self.add_item(piece_sel)

        if self.selected_sq and self.selected_sq in by_src:
            dest_options = []
            for mv in by_src[self.selected_sq][:25]:
                lan = bg._chess_format_lan(mv)
                r1, c1, r2, c2, pr = mv
                dst = bg._chess_sq_name(r2, c2)
                cap = board[r2][c2]
                desc = f"吃 {bg.CHESS_PIECE_NAMES.get(cap, cap)}" if cap else "移動"
                if board[r1][c1].upper() == "K" and abs(c2 - c1) == 2:
                    desc = "短易位 (O-O)" if c2 == 6 else "長易位 (O-O-O)"
                elif pr:
                    desc = f"升變為 {pr.upper()}"
                dest_options.append(discord.SelectOption(label=f"{self.selected_sq} → {dst} ({desc})", value=lan))
            dest_sel = discord.ui.Select(placeholder=f"2️⃣ 選擇 {self.selected_sq} 的目標格...", options=dest_options, row=1)
            dest_sel.callback = self._on_select_move
            self.add_item(dest_sel)

    def _add_utility_buttons(self, row_idx: int) -> None:
        # Row A: 檢視與輔助工具
        hint_btn = discord.ui.Button(label="提示", emoji="💡", style=discord.ButtonStyle.secondary, row=row_idx)
        hint_btn.callback = self._on_hint
        self.add_item(hint_btn)

        coords_label = "座標:開" if self.game.get("show_coords", True) else "座標:關"
        coord_btn = discord.ui.Button(label=coords_label, emoji="🧭", style=discord.ButtonStyle.secondary, row=row_idx)
        coord_btn.callback = self._on_toggle_coords
        self.add_item(coord_btn)

        refresh_btn = discord.ui.Button(label="置底", emoji="🔄", style=discord.ButtonStyle.secondary, row=row_idx)
        refresh_btn.callback = self._on_refresh
        self.add_item(refresh_btn)

        # Row B (row_idx + 1 <= 4): 悔棋（全體玩家同意）、提和（PvP）、投降
        row_b = min(4, row_idx + 1)
        has_stack = bool(self.game["state"].get("undo_stack"))
        approvals = self.game.get("undo_approvals") or []
        req_cnt = len(bg.required_human_players(self.game))

        if approvals:
            undo_lbl = f"同意上一步 ({len(approvals)}/{req_cnt})"
            undo_emoji = "✅"
            undo_style = discord.ButtonStyle.success
        else:
            undo_lbl = "上一步"
            undo_emoji = "↩️"
            undo_style = discord.ButtonStyle.secondary

        undo_btn = discord.ui.Button(
            label=undo_lbl,
            emoji=undo_emoji,
            style=undo_style,
            disabled=(not has_stack),
            row=row_b,
        )
        undo_btn.callback = self._on_undo
        self.add_item(undo_btn)

        if approvals:
            reject_btn = discord.ui.Button(label="拒絕悔棋", emoji="❌", style=discord.ButtonStyle.secondary, row=row_b)
            reject_btn.callback = self._on_reject_undo
            self.add_item(reject_btn)

        if self.game["mode"] == "pvp":
            draw_lbl = "接受和局" if (self.game.get("draw_offer_by") and self.game["draw_offer_by"] != 0) else "提和"
            draw_btn = discord.ui.Button(label=draw_lbl, emoji="🤝", style=discord.ButtonStyle.secondary, row=row_b)
            draw_btn.callback = self._on_draw
            self.add_item(draw_btn)

        resign_btn = discord.ui.Button(label="投降", emoji="🏳️", style=discord.ButtonStyle.danger, row=row_b)
        resign_btn.callback = self._on_resign
        self.add_item(resign_btn)

    async def on_timeout(self) -> None:
        await _disable_view_on_timeout(self)

    def _make_direct_move_cb(self, move_val: str):
        async def cb(interaction: discord.Interaction):
            await self.cog.handle_interaction_move(interaction, self.game_id, move_val, old_view=self)
        return cb

    async def _on_select_move(self, interaction: discord.Interaction):
        val = interaction.data["values"][0]
        await self.cog.handle_interaction_move(interaction, self.game_id, val, old_view=self)

    async def _on_chess_pick_piece(self, interaction: discord.Interaction):
        lock = await _try_acquire_game_lock(self.cog, self.game_id, interaction)
        if lock is None:
            return
        async with lock:
            latest = await asyncio.to_thread(bg.get_game_by_id, self.game_id)
            if not latest or latest["status"] != "active":
                await interaction.response.send_message("⚠️ 此棋局已結束。", ephemeral=True)
                return
            if not bg._is_participant(latest, interaction.user.id) or not bg._is_player_turn(latest, interaction.user.id):
                await interaction.response.send_message("⚠️ 目前不是你的回合喔！", ephemeral=True)
                return
            picked_sq = interaction.data["values"][0]
            await interaction.response.defer()
            await self.cog.edit_interaction_game_card(interaction, latest, selected_sq=picked_sq, old_view=self)

    async def _on_hint(self, interaction: discord.Interaction):
        latest = await asyncio.to_thread(bg.get_game_by_id, self.game_id)
        if not latest or latest["status"] != "active":
            await interaction.response.send_message("⚠️ 此棋局已結束。", ephemeral=True)
            return
        if not bg._is_participant(latest, interaction.user.id):
            await interaction.response.send_message("⚠️ 僅限參賽玩家使用提示功能。", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        suggestion = await asyncio.to_thread(bg.compute_ai_move_for_game, latest, "hard")
        if suggestion:
            await interaction.followup.send(f"💡 推薦手數：`{suggestion}`（可直接在選單點選或輸入 `!m {suggestion}`）", ephemeral=True)
        else:
            await interaction.followup.send("ℹ️ 目前沒有合法步數。", ephemeral=True)

    async def _on_toggle_coords(self, interaction: discord.Interaction):
        lock = await _try_acquire_game_lock(self.cog, self.game_id, interaction)
        if lock is None:
            return
        async with lock:
            latest = await asyncio.to_thread(bg.get_game_by_id, self.game_id)
            if not latest or latest["status"] != "active":
                await interaction.response.send_message("⚠️ 此棋局已結束。", ephemeral=True)
                return
            if not bg._is_participant(latest, interaction.user.id):
                await interaction.response.send_message("⚠️ 僅限參賽玩家調整棋盤顯示。", ephemeral=True)
                return
            updated = await asyncio.to_thread(bg.toggle_show_coords, self.game_id)
            await interaction.response.defer()
            state_lbl = "開啟" if updated["show_coords"] else "關閉"
            await self.cog.edit_interaction_game_card(
                interaction,
                updated,
                last_note=f"格內座標提示已{state_lbl}",
                selected_sq=self.selected_sq,
                old_view=self,
            )

    async def _on_draw(self, interaction: discord.Interaction):
        lock = await _try_acquire_game_lock(self.cog, self.game_id, interaction)
        if lock is None:
            return
        async with lock:
            try:
                updated, status_msg = await asyncio.to_thread(bg.offer_or_accept_draw, self.game_id, interaction.user.id)
            except ValueError as e:
                await interaction.response.send_message(f"⚠️ {e}", ephemeral=True)
                return
            if status_msg == "offered":
                await interaction.response.defer()
                await self.cog.edit_interaction_game_card(
                    interaction,
                    updated,
                    last_note=f"{interaction.user.display_name} 提出和局請求",
                    old_view=self,
                )
            elif status_msg == "accepted":
                await interaction.response.defer()
                await self.cog.retire_and_send_game_card(
                    interaction,
                    updated,
                    last_note="雙方協議和局！",
                    old_view=self,
                )
            else:
                await interaction.response.send_message(f"ℹ️ {status_msg}", ephemeral=True)

    async def _on_refresh(self, interaction: discord.Interaction):
        lock = await _try_acquire_game_lock(self.cog, self.game_id, interaction)
        if lock is None:
            return
        async with lock:
            latest = await asyncio.to_thread(bg.get_game_by_id, self.game_id)
            if not latest or latest["status"] != "active":
                await interaction.response.send_message("⚠️ 找不到進行中的此棋局。", ephemeral=True)
                return
            if not bg._is_participant(latest, interaction.user.id):
                await interaction.response.send_message(
                    "⚠️ 僅限本局參賽玩家使用「置底」功能（其他人可輸入 `!play board` 查看）。",
                    ephemeral=True,
                )
                return
            await interaction.response.defer()
            await self.cog.retire_and_send_game_card(
                interaction,
                latest,
                last_note="已重新發送最新棋盤面板",
                old_view=self,
            )

    async def _on_undo(self, interaction: discord.Interaction):
        lock = await _try_acquire_game_lock(self.cog, self.game_id, interaction)
        if lock is None:
            return
        async with lock:
            try:
                updated, undo_status = await asyncio.to_thread(bg.request_or_approve_undo, self.game_id, interaction.user.id)
            except ValueError as e:
                await interaction.response.send_message(f"⚠️ {e}", ephemeral=True)
                return

            if undo_status == "already_approved":
                await interaction.response.send_message("ℹ️ 你已經同意退回上一步，正在等待對手玩家點擊「✅ 同意上一步」。", ephemeral=True)
                return
            await interaction.response.defer()
            if undo_status == "pending":
                await self.cog.edit_interaction_game_card(
                    interaction,
                    updated,
                    last_note=f"{interaction.user.display_name} 申請退回上一步（等待全體玩家同意）",
                    old_view=self,
                )
                return
            await self.cog.retire_and_send_game_card(
                interaction,
                updated,
                last_note=f"↩️ 全體玩家已同意，棋盤已退回上一步（第 {updated['move_count']} 手）！",
                old_view=self,
            )

    async def _on_reject_undo(self, interaction: discord.Interaction):
        lock = await _try_acquire_game_lock(self.cog, self.game_id, interaction)
        if lock is None:
            return
        async with lock:
            try:
                updated = await asyncio.to_thread(bg.reject_undo, self.game_id, interaction.user.id)
            except ValueError as e:
                await interaction.response.send_message(f"⚠️ {e}", ephemeral=True)
                return
            await interaction.response.defer()
            await self.cog.edit_interaction_game_card(
                interaction,
                updated,
                last_note=f"{interaction.user.display_name} 取消了悔棋請求。",
                old_view=self,
            )

    async def _on_resign(self, interaction: discord.Interaction):
        lock = await _try_acquire_game_lock(self.cog, self.game_id, interaction)
        if lock is None:
            return
        async with lock:
            try:
                updated = await asyncio.to_thread(bg.resign_game, self.game_id, interaction.user.id)
            except ValueError as e:
                await interaction.response.send_message(f"⚠️ {e}", ephemeral=True)
                return
            await interaction.response.defer()
            await self.cog.retire_and_send_game_card(
                interaction,
                updated,
                last_note=f"{interaction.user.display_name} 選擇投降。",
                old_view=self,
            )
