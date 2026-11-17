"""Board Game Cog: !play lobby/launcher, optional !m move shortcut, LLM opponent & interactive UI cards."""

import asyncio
import io
import logging
from typing import Optional

import discord
from discord.ext import commands
from google import genai
from google.genai import types

import boardgame_engine as bg
from core.chat_pipeline import is_target_channel
from core.config import BotSettings, truncate_embed_text
from core.llm_client import generate_with_fallback, get_safety_settings


def _build_board_embed(
    game: dict,
    last_note: str = "",
    selected_sq: Optional[str] = None,
) -> discord.Embed:
    gtype = game["game_type"]
    meta = bg.GAME_CATALOG[gtype]
    status = game["status"]
    p1_label = meta["p1_label"]
    p2_label = meta["p2_label"]
    mode_tag = f"PvE ({bg.DIFFICULTY_LABELS.get(game['difficulty'], '普通')})" if game["mode"] == "pve" else "PvP 雙人對戰"

    if status == "waiting":
        invite_txt = f"指定挑戰 <@{game['invited_id']}>" if game.get("invited_id") else "公開挑戰（任何人皆可加入）"
        embed = discord.Embed(
            title=f"{meta['emoji']} {meta['name']} — 等待對手加入",
            description=(
                f"發起人 **{game['p1_name']}** ({p1_label}) 開了一局新對戰！\n"
                f"📣 **對戰形式**：{invite_txt}\n\n"
                f"點擊下方 **「⚔️ 加入對戰」** 即可立刻開打，或由發起人改為與 Bot AI 對戰。"
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
        description="\n".join(desc_parts),
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
        coord_hint = "可點擊下方按鈕/選單，或直接用自然語言請 Bot 幫忙下棋（輔助：!m <座標>）"
        if gtype == "othello":
            coord_hint = "用下方選單點選綠點，或直接對 Bot 說想下哪裡（亦支援 !m d3）"
        elif gtype == "chess":
            coord_hint = "用下方選單選子→選格，或口語指揮 Bot（亦支援 !m e2e4 / Nf3）"
        elif gtype == "connect4":
            coord_hint = "點擊下方 1️⃣~7️⃣ 按鈕投子，或對 Bot 說投第幾欄"
        elif gtype == "gomoku":
            coord_hint = "用推薦熱點選單、對 Bot 口述座標，或輸入 !m g7"
        embed.set_footer(text=f"棋局 #{game['id']} | {coord_hint}")
    else:
        embed.set_footer(text=f"棋局 #{game['id']} 已結束 | 可點「🏆 我的戰績」或對 Bot 說「查看桌遊戰績」")
    return embed


async def _render_board_file(game: dict, highlight_from: Optional[str] = None) -> discord.File:
    png_bytes = await asyncio.to_thread(bg.render_board_image, game, highlight_from)
    return discord.File(io.BytesIO(png_bytes), filename="board.png")


def _build_game_lobby_embed(channel_id: int) -> discord.Embed:
    active = bg.get_active_game(channel_id)
    lines = []
    for key, meta in bg.GAME_CATALOG.items():
        lines.append(f"{meta['emoji']} **{meta['name']}**（`{key}` | {meta['size']}）\n└ {meta['desc']}")

    status_banner = ""
    if active:
        ameta = bg.GAME_CATALOG[active["game_type"]]
        status_banner = f"\n\n⚠️ **此頻道目前有一場進行中的 `{ameta['name']}`**（輸入 `!play board` 或說「重貼棋盤」即可叫回面板）。"

    embed = discord.Embed(
        title="🎮 Discord 桌遊與棋盤大廳 (`!play`)",
        description=(
            "所有棋盤規則、勝負與座標判定皆由 **Python 引擎＋SQLite** 嚴格校驗，**零作弊、零幻覺**，並附上四邊高清座標圖！\n\n"
            + "\n\n".join(lines)
            + status_banner
        ),
        color=0x5865F2,
    )
    embed.add_field(
        name="🚀 極簡玩法（按鈕選單 ＋ 自然語言都能玩）",
        value=(
            "• **用選的**：直接點下方選單選遊戲與對手（含 **PvE 簡單/普通/困難 AI**、**🧠 Gemini LLM**、**PvP 雙人**）\n"
            "• **用說的**：直接打「我想跟 LLM 玩黑白棋」、「幫我下在 C4」、「我想悔棋」\n"
            "• **用指令**：`!play 黑白棋` / `!play 西洋棋 llm` / `!play 五子棋 pvp @好友`"
        ),
        inline=False,
    )
    embed.set_footer(text="💡 對局內所有功能（投降、上一步悔棋、提示、座標水印、看戰績）皆可直接點卡片按鈕操作！")
    return embed


class GameLobbyView(discord.ui.View):
    """互動式遊戲大廳：下拉選遊戲與模式後一鍵開局。"""

    def __init__(self, cog: "BoardGameCog"):
        super().__init__(timeout=600)
        self.cog = cog
        self.selected_game = "othello"
        self.selected_mode = "pve:normal"

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
        self.game_select = discord.ui.Select(placeholder="1️⃣ 選擇要玩的棋盤遊戲...", options=game_opts, row=0)
        self.game_select.callback = self._on_select_game
        self.add_item(self.game_select)

        mode_opts = [
            discord.SelectOption(label="PvE 對戰本地 AI（簡單 🌱）", value="pve:easy"),
            discord.SelectOption(label="PvE 對戰本地 AI（普通 ⚔️）", value="pve:normal", default=True),
            discord.SelectOption(label="PvE 對戰本地 AI（困難 🔥）", value="pve:hard"),
            discord.SelectOption(label="PvE 對戰後端 Gemini LLM（會回話且防作弊 🧠）", value="pve:llm"),
            discord.SelectOption(label="PvP 雙人公開對戰（等待群友加入 👥）", value="pvp:open"),
        ]
        self.mode_select = discord.ui.Select(placeholder="2️⃣ 選擇 PvE 難度或 PvP 對戰...", options=mode_opts, row=1)
        self.mode_select.callback = self._on_select_mode
        self.add_item(self.mode_select)

    async def _on_select_game(self, interaction: discord.Interaction):
        self.selected_game = self.game_select.values[0]
        for opt in self.game_select.options:
            opt.default = (opt.value == self.selected_game)
        await interaction.response.edit_message(view=self)

    async def _on_select_mode(self, interaction: discord.Interaction):
        self.selected_mode = self.mode_select.values[0]
        for opt in self.mode_select.options:
            opt.default = (opt.value == self.selected_mode)
        await interaction.response.edit_message(view=self)

    @discord.ui.button(label="立即開局", emoji="▶️", style=discord.ButtonStyle.success, row=2)
    async def start_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        mode_kind, sub = self.selected_mode.split(":", 1)
        mode = "pve" if mode_kind == "pve" else "pvp"
        diff = sub if mode == "pve" else "normal"
        try:
            game = await asyncio.to_thread(
                bg.create_game,
                interaction.channel.id,
                self.selected_game,
                mode,
                interaction.user.id,
                interaction.user.display_name,
                diff,
            )
        except ValueError as e:
            await interaction.response.send_message(f"⚠️ {e}", ephemeral=True)
            return

        await interaction.response.defer()
        await self.cog.send_game_card(interaction.channel, game, last_note="對局已建立！")

    @discord.ui.button(label="我的戰績", emoji="🏆", style=discord.ButtonStyle.secondary, row=2)
    async def stats_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = self.cog.build_stats_embed(interaction.user)
        await interaction.response.send_message(embed=embed, ephemeral=True)


class LobbyChallengeView(discord.ui.View):
    """PvP 等待對手加入卡片按鈕。"""

    def __init__(self, cog: "BoardGameCog", game_id: int):
        super().__init__(timeout=1800)
        self.cog = cog
        self.game_id = game_id

    @discord.ui.button(label="加入對戰", emoji="⚔️", style=discord.ButtonStyle.success)
    async def join_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            game = await asyncio.to_thread(bg.join_pvp_game, self.game_id, interaction.user.id, interaction.user.display_name)
        except ValueError as e:
            await interaction.response.send_message(f"⚠️ {e}", ephemeral=True)
            return
        await interaction.response.defer()
        await self.cog.edit_interaction_game_card(interaction, game, last_note=f"{interaction.user.display_name} 加入對戰，開局！")

    @discord.ui.button(label="改與 Bot AI 對戰", emoji="🤖", style=discord.ButtonStyle.primary)
    async def switch_pve_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            game = await asyncio.to_thread(bg.switch_waiting_to_pve, self.game_id, interaction.user.id)
        except ValueError as e:
            await interaction.response.send_message(f"⚠️ {e}", ephemeral=True)
            return
        await interaction.response.defer()
        await self.cog.edit_interaction_game_card(interaction, game, last_note="已切換為 PvE 人機對戰，開局！")

    @discord.ui.button(label="取消開局", emoji="❌", style=discord.ButtonStyle.danger)
    async def cancel_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            game = await asyncio.to_thread(bg.cancel_waiting_game, self.game_id, interaction.user.id)
        except ValueError as e:
            await interaction.response.send_message(f"⚠️ {e}", ephemeral=True)
            return
        embed = _build_board_embed(game, last_note="發起人已取消開局。")
        await interaction.response.edit_message(embed=embed, view=None, attachments=[])


class FinishedBoardView(discord.ui.View):
    """對局結束後的一鍵再戰／回大廳按鈕。"""

    def __init__(self, cog: "BoardGameCog", game: dict):
        super().__init__(timeout=1800)
        self.cog = cog
        self.game = game

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
            try:
                new_game = await asyncio.to_thread(
                    bg.create_game,
                    interaction.channel.id,
                    g["game_type"],
                    "pve",
                    interaction.user.id,
                    interaction.user.display_name,
                    g["difficulty"],
                )
            except ValueError as e:
                await interaction.response.send_message(f"⚠️ {e}", ephemeral=True)
                return

        await interaction.response.defer()
        await self.cog.send_game_card(interaction.channel, new_game, last_note="新局開始（Rematch）！")

    @discord.ui.button(label="回遊戲大廳", emoji="🎮", style=discord.ButtonStyle.secondary)
    async def lobby_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = _build_game_lobby_embed(interaction.channel.id)
        await interaction.response.send_message(embed=embed, view=GameLobbyView(self.cog))


class ActiveBoardView(discord.ui.View):
    """進行中棋局的互動控制面板（自動依遊戲類型長出按鈕/選單）。"""

    def __init__(self, cog: "BoardGameCog", game: dict, selected_sq: Optional[str] = None):
        super().__init__(timeout=1800)
        self.cog = cog
        self.game_id = game["id"]
        self.game = game
        self.selected_sq = selected_sq

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

    def _make_direct_move_cb(self, move_val: str):
        async def cb(interaction: discord.Interaction):
            await self.cog.handle_interaction_move(interaction, self.game_id, move_val)
        return cb

    async def _on_select_move(self, interaction: discord.Interaction):
        val = interaction.data["values"][0]
        await self.cog.handle_interaction_move(interaction, self.game_id, val)

    async def _on_chess_pick_piece(self, interaction: discord.Interaction):
        latest = await asyncio.to_thread(bg.get_game_by_id, self.game_id)
        if not latest or latest["status"] != "active":
            await interaction.response.send_message("⚠️ 此棋局已結束。", ephemeral=True)
            return
        if not bg._is_participant(latest, interaction.user.id) or not bg._is_player_turn(latest, interaction.user.id):
            await interaction.response.send_message("⚠️ 目前不是你的回合喔！", ephemeral=True)
            return
        picked_sq = interaction.data["values"][0]
        await interaction.response.defer()
        await self.cog.edit_interaction_game_card(interaction, latest, selected_sq=picked_sq)

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
        await self.cog.edit_interaction_game_card(interaction, updated, last_note=f"格內座標提示已{state_lbl}", selected_sq=self.selected_sq)

    async def _on_draw(self, interaction: discord.Interaction):
        try:
            updated, status_msg = await asyncio.to_thread(bg.offer_or_accept_draw, self.game_id, interaction.user.id)
        except ValueError as e:
            await interaction.response.send_message(f"⚠️ {e}", ephemeral=True)
            return
        if status_msg == "offered":
            await interaction.response.defer()
            await self.cog.edit_interaction_game_card(interaction, updated, last_note=f"{interaction.user.display_name} 提出和局請求")
        elif status_msg == "accepted":
            await interaction.response.defer()
            await self.cog.edit_interaction_game_card(interaction, updated, last_note="雙方協議和局！")
        else:
            await interaction.response.send_message(f"ℹ️ {status_msg}", ephemeral=True)

    async def _on_refresh(self, interaction: discord.Interaction):
        latest = await asyncio.to_thread(bg.get_game_by_id, self.game_id)
        if not latest:
            await interaction.response.send_message("⚠️ 找不到此棋局。", ephemeral=True)
            return
        await interaction.response.defer()
        await self.cog.send_game_card(interaction.channel, latest, last_note="已重新發送最新棋盤面板")

    async def _on_undo(self, interaction: discord.Interaction):
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
                interaction, updated, last_note=f"{interaction.user.display_name} 申請退回上一步（等待全體玩家同意）"
            )
            return
        await self.cog.edit_interaction_game_card(
            interaction, updated, last_note=f"↩️ 全體玩家已同意，棋盤已退回上一步（第 {updated['move_count']} 手）！"
        )

    async def _on_reject_undo(self, interaction: discord.Interaction):
        try:
            updated = await asyncio.to_thread(bg.reject_undo, self.game_id, interaction.user.id)
        except ValueError as e:
            await interaction.response.send_message(f"⚠️ {e}", ephemeral=True)
            return
        await interaction.response.defer()
        await self.cog.edit_interaction_game_card(interaction, updated, last_note=f"{interaction.user.display_name} 取消了悔棋請求。")

    async def _on_resign(self, interaction: discord.Interaction):
        try:
            updated = await asyncio.to_thread(bg.resign_game, self.game_id, interaction.user.id)
        except ValueError as e:
            await interaction.response.send_message(f"⚠️ {e}", ephemeral=True)
            return
        await interaction.response.defer()
        await self.cog.edit_interaction_game_card(interaction, updated, last_note=f"{interaction.user.display_name} 選擇投降。")


class BoardGameCog(commands.Cog):
    """桌遊與棋盤對戰系統（黑白棋、西洋棋、四子棋、五子棋），支援 PvP、本地 AI 與防作弊 Gemini LLM 對手。"""

    def __init__(self, bot: commands.Bot, gemini_client: genai.Client, settings: BotSettings, logger: logging.Logger):
        self.bot = bot
        self.gemini_client = gemini_client
        self.settings = settings
        self.logger = logger

    def _build_view_for_game(self, game: dict, selected_sq: Optional[str] = None) -> discord.ui.View:
        if game["status"] == "waiting":
            return LobbyChallengeView(self, game["id"])
        if game["status"] == "active":
            return ActiveBoardView(self, game, selected_sq=selected_sq)
        return FinishedBoardView(self, game)

    async def send_game_card(
        self,
        channel: discord.abc.Messageable,
        game: dict,
        last_note: str = "",
        selected_sq: Optional[str] = None,
    ) -> discord.Message:
        embed = _build_board_embed(game, last_note=last_note, selected_sq=selected_sq)
        view = self._build_view_for_game(game, selected_sq=selected_sq)
        if game["status"] == "waiting":
            return await channel.send(embed=embed, view=view)
        board_file = await _render_board_file(game, highlight_from=selected_sq)
        return await channel.send(file=board_file, embed=embed, view=view)

    async def edit_interaction_game_card(
        self,
        interaction: discord.Interaction,
        game: dict,
        last_note: str = "",
        selected_sq: Optional[str] = None,
    ) -> None:
        embed = _build_board_embed(game, last_note=last_note, selected_sq=selected_sq)
        view = self._build_view_for_game(game, selected_sq=selected_sq)
        if game["status"] == "waiting":
            await interaction.edit_original_response(embed=embed, view=view, attachments=[])
            return
        board_file = await _render_board_file(game, highlight_from=selected_sq)
        await interaction.edit_original_response(embed=embed, view=view, attachments=[board_file])

    async def _pick_and_apply_llm_move(self, game: dict) -> tuple[dict, str]:
        """向後端 Gemini LLM 請求一步棋，並以 Python 規則引擎嚴格校驗（違例時重試一次並保底退回本地引擎）。"""
        retry_err = ""
        for attempt in range(2):
            prompt = bg.format_llm_opponent_prompt(game, retry_error=retry_err)
            try:
                resp, used_model = await generate_with_fallback(
                    client=self.gemini_client,
                    models_to_try=self.settings.models_to_try,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        temperature=0.35 if attempt == 0 else 0.15,
                        safety_settings=get_safety_settings(self.settings.disable_safety_settings),
                    ),
                    timeout=25.0,
                    context_label="桌遊 LLM 對弈",
                    logger=self.logger,
                )
                mv, taunt = bg.parse_llm_move_response(getattr(resp, "text", "") or "")
                if not mv:
                    retry_err = "回傳內容未包含合法座標或 JSON"
                    continue
                updated, ai_note = await asyncio.to_thread(bg.apply_move, game["id"], 0, mv)
                taunt_txt = f"「{taunt}」" if taunt else ""
                return updated, f"🧠 LLM ({used_model}) {ai_note} {taunt_txt}".strip()
            except ValueError as e:
                retry_err = str(e)
                self.logger.warning(f"🛡️ [BoardGame Anti-Cheat] 攔截 LLM 違例棋步 (attempt={attempt + 1}): {e}")
            except Exception as e:
                self.logger.warning(f"⚠️ [BoardGame LLM] 呼叫失敗，將由本地裁判接手: {e}")
                break

        fallback_mv = await asyncio.to_thread(bg.compute_ai_move_for_game, game, "normal")
        if not fallback_mv:
            return game, ""
        updated, ai_note = await asyncio.to_thread(bg.apply_move, game["id"], 0, fallback_mv)
        return updated, f"🤖 [LLM 思考超時/違例，由本地防作弊裁判代走] {ai_note}"

    async def _run_pve_turn_if_needed(self, game: dict, note_prefix: str) -> tuple[dict, str]:
        """若為 PvE 且輪到 Bot/LLM，執行其回合（嚴格防作弊校驗）並更新棋局。"""
        if game.get("difficulty") != "llm":
            return await asyncio.to_thread(bg.play_pve_ai_turn_sync, game, note_prefix)

        note = note_prefix
        safety_loops = 0
        while game["status"] == "active" and game["mode"] == "pve" and game["turn"] == 2 and safety_loops < 4:
            safety_loops += 1
            game, llm_note = await self._pick_and_apply_llm_move(game)
            if not llm_note:
                break
            note = f"{note}\n{llm_note}" if note else llm_note
        return game, note

    async def handle_interaction_move(self, interaction: discord.Interaction, game_id: int, move_str: str) -> None:
        try:
            game, note = await asyncio.to_thread(bg.apply_move, game_id, interaction.user.id, move_str)
        except ValueError as e:
            await interaction.response.send_message(f"⚠️ {e}", ephemeral=True)
            return

        await interaction.response.defer()
        game, full_note = await self._run_pve_turn_if_needed(game, note)
        await self.edit_interaction_game_card(interaction, game, last_note=full_note)

    def build_stats_embed(self, target_user: Optional[discord.abc.User] = None) -> discord.Embed:
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
        return discord.Embed(title=title, description="\n".join(lines), color=0xF1C40F)

    @commands.command(name="play", aliases=["game", "games", "桌遊", "棋盤", "boardgame", "bg"])
    async def play_command(self, ctx: commands.Context, *, args: str = None):
        """桌遊大廳與開局指令：!play [遊戲名] [pve/pvp] [難度或@對手]"""
        if not is_target_channel(ctx.channel, self.settings):
            return

        raw = (args or "").strip()
        if not raw or raw.lower() in ("list", "lobby", "menu", "清單", "大廳"):
            embed = _build_game_lobby_embed(ctx.channel.id)
            await ctx.send(embed=embed, view=GameLobbyView(self))
            return

        low = raw.lower()
        if low in ("stats", "stat", "戰績", "排行"):
            await ctx.send(embed=self.build_stats_embed(ctx.author))
            return
        if low in ("board", "show", "棋盤", "看棋盤"):
            active = await asyncio.to_thread(bg.get_active_game, ctx.channel.id)
            if not active:
                await ctx.send("🎲 目前此頻道沒有進行中的棋局。輸入 `!play` 開一局吧！")
                return
            await self.send_game_card(ctx.channel, active, last_note="已重新顯示目前盤面")
            return
        if low in ("resign", "surrender", "投降", "認輸"):
            active = await asyncio.to_thread(bg.get_active_game, ctx.channel.id)
            if not active:
                await ctx.send("ℹ️ 目前沒有進行中的棋局。")
                return
            try:
                updated = await asyncio.to_thread(bg.resign_game, active["id"], ctx.author.id)
            except ValueError as e:
                await ctx.send(f"⚠️ {e}")
                return
            await self.send_game_card(ctx.channel, updated, last_note=f"{ctx.author.display_name} 選擇投降。")
            return
        if low in ("undo", "悔棋", "上一步"):
            active = await asyncio.to_thread(bg.get_active_game, ctx.channel.id)
            if not active:
                await ctx.send("ℹ️ 目前沒有進行中的棋局。")
                return
            try:
                updated, undo_status = await asyncio.to_thread(bg.request_or_approve_undo, active["id"], ctx.author.id)
            except ValueError as e:
                await ctx.send(f"⚠️ {e}")
                return
            if undo_status == "pending":
                await self.send_game_card(ctx.channel, updated, last_note=f"{ctx.author.display_name} 申請退回上一步（等待全體玩家點擊同意）")
            elif undo_status == "already_approved":
                await ctx.send("ℹ️ 你已經同意退回上一步，正在等待對手同意。")
            else:
                await self.send_game_card(ctx.channel, updated, last_note=f"↩️ 全體玩家已同意，棋盤已退回上一步（第 {updated['move_count']} 手）！")
            return

        tokens = raw.split()
        gtype = bg.resolve_game_type(tokens[0])
        if not gtype:
            await ctx.send(f"❓ 找不到遊戲 `{tokens[0]}`。輸入 `!play` 可開啟大廳與遊戲清單！")
            return

        mode = "pve"
        difficulty = "normal"
        invited_id = 0
        invited_name = ""

        if ctx.message.mentions:
            target = ctx.message.mentions[0]
            mode = "pvp"
            if target.id != ctx.author.id and not target.bot:
                invited_id = target.id
                invited_name = target.display_name

        for tok in tokens[1:]:
            tl = tok.lower()
            if tl in ("pvp", "vs", "雙人", "對戰"):
                mode = "pvp"
            elif tl in ("pve", "ai", "bot", "電腦"):
                mode = "pve"
            elif tl in ("easy", "normal", "hard", "llm", "gemini", "簡單", "普通", "困難", "新手", "高手", "模型"):
                difficulty = bg.resolve_difficulty(tl)

        try:
            game = await asyncio.to_thread(
                bg.create_game,
                ctx.channel.id,
                gtype,
                mode,
                ctx.author.id,
                ctx.author.display_name,
                difficulty,
                invited_id,
                invited_name,
            )
        except ValueError as e:
            await ctx.send(f"⚠️ {e}（可直接說「重貼棋盤」或輸入 `!play board` 叫回面板）")
            return

        await self.send_game_card(ctx.channel, game, last_note="新對局已開啟！")

    @commands.command(name="m", aliases=["move", "下", "走"])
    async def move_command(self, ctx: commands.Context, *, move_text: str = None):
        """（輔助捷徑）輸入座標落子：!m d3 / !m e2e4 / !m 4；平時可直接點按鈕或用自然語言下棋！"""
        if not is_target_channel(ctx.channel, self.settings):
            return
        if not move_text or not move_text.strip():
            await ctx.send("❓ 請輸入座標或欄號（如 `!m d3`、`!m e2e4`），或直接點擊棋盤卡片按鈕／用口語請 Bot 下棋！")
            return
        active = await asyncio.to_thread(bg.get_active_game, ctx.channel.id)
        if not active or active["status"] != "active":
            await ctx.send("🎲 目前此頻道沒有進行中的棋局（輸入 `!play` 或說「我想下棋」即可開局）。")
            return
        try:
            game, note = await asyncio.to_thread(bg.apply_move, active["id"], ctx.author.id, move_text.strip())
        except ValueError as e:
            await ctx.send(f"⚠️ {e}")
            return

        async with ctx.channel.typing():
            game, full_note = await self._run_pve_turn_if_needed(game, note)
        await self.send_game_card(ctx.channel, game, last_note=full_note)
