"""Board Game Cog: !play lobby/launcher, optional !m move shortcut, LLM opponent & interactive UI cards."""

import asyncio
import logging
from typing import Optional

import discord
from discord.ext import commands
from google import genai
from google.genai import types

from cogs.boardgame_views import (
    ActiveBoardView,
    FinishedBoardView,
    GameLobbyView,
    LobbyChallengeView,
    _build_board_embed,
    _build_game_lobby_embed,
    _disable_view_on_timeout,
    _render_board_file,
    build_stats_embed,
)
from cogs.lifesim_cog import LifeSimSetupView, build_atlas_embed
from core.chat_pipeline import is_target_channel
from core.config import BotSettings, resolve_bot_display_name
from core.llm_client import generate_with_fallback, get_safety_settings
from engines import boardgame_engine as bg
from engines import lifesim_engine as ls


class BoardGameCog(commands.Cog):
    """桌遊與棋盤對戰系統（黑白棋、西洋棋、四子棋、五子棋），支援 PvP、本地 AI 與防作弊 Gemini LLM 對手。"""

    def __init__(self, bot: commands.Bot, gemini_client: genai.Client, settings: BotSettings, logger: logging.Logger):
        self.bot = bot
        self.gemini_client = gemini_client
        self.settings = settings
        self.logger = logger
        self._game_locks: dict[int, asyncio.Lock] = {}
        self._active_views: dict[int, discord.ui.View] = {}

    def _get_game_lock(self, game_id: int) -> asyncio.Lock:
        lock = self._game_locks.get(game_id)
        if lock is None:
            lock = asyncio.Lock()
            self._game_locks[game_id] = lock
        return lock

    def _track_view(self, game_id: int, view: discord.ui.View) -> None:
        prev = self._active_views.get(game_id)
        if prev is not None and prev is not view:
            prev.message = None
            prev.stop()
        self._active_views[game_id] = view

    def _resolve_bot_name(self, *, guild=None, channel=None) -> str:
        return resolve_bot_display_name(self.bot, guild=guild, channel=channel, settings=self.settings)

    def _build_view_for_game(
        self,
        game: dict,
        selected_sq: Optional[str] = None,
        bot_name: str = "機器人",
    ) -> discord.ui.View:
        if game["status"] == "waiting":
            return LobbyChallengeView(self, game["id"], bot_name=bot_name)
        if game["status"] == "active":
            return ActiveBoardView(self, game, selected_sq=selected_sq)
        return FinishedBoardView(self, game)

    async def send_game_card(
        self,
        channel: discord.abc.Messageable,
        game: dict,
        last_note: str = "",
        selected_sq: Optional[str] = None,
        content_text: Optional[str] = None,
    ) -> discord.Message:
        bot_name = self._resolve_bot_name(guild=getattr(channel, "guild", None), channel=channel)
        embed = _build_board_embed(game, last_note=last_note, selected_sq=selected_sq, bot_name=bot_name)
        view = self._build_view_for_game(game, selected_sq=selected_sq, bot_name=bot_name)
        self._track_view(game["id"], view)
        clean_content = (content_text or "").strip() or None
        if clean_content and len(clean_content) > 1900:
            clean_content = clean_content[:1900] + "..."
        if game["status"] == "waiting":
            sent = await channel.send(content=clean_content, embed=embed, view=view)
            view.message = sent
            return sent
        board_file = await _render_board_file(game, highlight_from=selected_sq)
        sent = await channel.send(content=clean_content, file=board_file, embed=embed, view=view)
        view.message = sent
        return sent

    async def retire_and_send_game_card(
        self,
        interaction: discord.Interaction,
        game: dict,
        last_note: str = "",
        selected_sq: Optional[str] = None,
        content_text: Optional[str] = None,
        old_view: Optional[discord.ui.View] = None,
    ) -> discord.Message:
        """移除舊訊息的互動控制項並於聊天室最下方發送最新棋盤訊息。"""
        if old_view is not None:
            old_view.message = None
            old_view.stop()
        try:
            await interaction.edit_original_response(view=None)
        except Exception:
            pass
        return await self.send_game_card(
            interaction.channel,
            game,
            last_note=last_note,
            selected_sq=selected_sq,
            content_text=content_text,
        )

    async def edit_interaction_game_card(
        self,
        interaction: discord.Interaction,
        game: dict,
        last_note: str = "",
        selected_sq: Optional[str] = None,
        old_view: Optional[discord.ui.View] = None,
    ) -> None:
        if old_view is not None:
            old_view.message = None
            old_view.stop()
        bot_name = self._resolve_bot_name(guild=interaction.guild, channel=interaction.channel)
        embed = _build_board_embed(game, last_note=last_note, selected_sq=selected_sq, bot_name=bot_name)
        view = self._build_view_for_game(game, selected_sq=selected_sq, bot_name=bot_name)
        self._track_view(game["id"], view)
        view.message = interaction.message
        if game["status"] == "waiting":
            await interaction.edit_original_response(embed=embed, view=view, attachments=[])
            return
        board_file = await _render_board_file(game, highlight_from=selected_sq)
        await interaction.edit_original_response(embed=embed, view=view, attachments=[board_file])

    async def _pick_and_apply_llm_move(self, game: dict) -> tuple[dict, str, str]:
        """向後端 Gemini LLM 請求一步棋，以 Python 規則嚴格校驗；回傳 (updated_game, card_note, llm_taunt)。"""
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
                    final_fallback_model=self.settings.final_fallback_model,
                )
                mv, taunt = bg.parse_llm_move_response(getattr(resp, "text", "") or "")
                if not mv:
                    retry_err = "回傳內容未包含合法座標或 JSON"
                    continue
                updated, ai_note = await asyncio.to_thread(bg.apply_move, game["id"], 0, mv)
                card_note = f"🧠 我方 ({used_model})：{ai_note}"
                taunt_msg = f"我下在 **`{mv}`**！{taunt}" if taunt else f"我下在 **`{mv}`**。"
                return updated, card_note, taunt_msg
            except ValueError as e:
                retry_err = str(e)
                self.logger.warning(f"🛡️ [BoardGame Anti-Cheat] 攔截 LLM 違例棋步 (attempt={attempt + 1}): {e}")
            except Exception as e:
                self.logger.warning(f"⚠️ [BoardGame LLM] 呼叫失敗，將由本地裁判接手: {e}")
                break

        fallback_mv = await asyncio.to_thread(bg.compute_ai_move_for_game, game, "normal")
        if not fallback_mv:
            return game, "", ""
        updated, ai_note = await asyncio.to_thread(bg.apply_move, game["id"], 0, fallback_mv)
        return updated, f"🤖 [思考超時，改由標準棋手接手] {ai_note}", ""

    async def _run_pve_turn_if_needed(self, game: dict, note_prefix: str) -> tuple[dict, str, str]:
        """若為 PvE 且輪到 Bot/LLM，執行其回合；回傳 (updated_game, clean_card_note, llm_thoughts_text)。"""
        if game.get("difficulty") != "llm":
            updated, full_note = await asyncio.to_thread(bg.play_pve_ai_turn_sync, game, note_prefix)
            return updated, full_note, ""

        note = note_prefix
        thoughts: list[str] = []
        safety_loops = 0
        while game["status"] == "active" and game["mode"] == "pve" and game["turn"] == 2 and safety_loops < 4:
            safety_loops += 1
            game, llm_note, taunt_msg = await self._pick_and_apply_llm_move(game)
            if llm_note:
                note = f"{note}\n{llm_note}" if note else llm_note
            if taunt_msg:
                thoughts.append(taunt_msg)
            if not llm_note:
                break
        return game, note, "\n".join(thoughts)

    async def handle_interaction_move(
        self,
        interaction: discord.Interaction,
        game_id: int,
        move_str: str,
        old_view: Optional[discord.ui.View] = None,
    ) -> None:
        lock = self._get_game_lock(game_id)
        if lock.locked():
            await interaction.response.send_message(
                "⏳ 目前棋局仍在處理上一步（或 AI 思考中），請稍候幾秒再試！",
                ephemeral=True,
            )
            return
        async with lock:
            try:
                game, note = await asyncio.to_thread(bg.apply_move, game_id, interaction.user.id, move_str)
            except ValueError as e:
                await interaction.response.send_message(f"⚠️ {e}", ephemeral=True)
                return

            await interaction.response.defer()
            game, card_note, llm_msg = await self._run_pve_turn_if_needed(game, note)
            await self.retire_and_send_game_card(
                interaction,
                game,
                last_note=card_note,
                content_text=llm_msg,
                old_view=old_view,
            )

    async def show_lobby_from_interaction(self, interaction: discord.Interaction, edit: bool = True) -> None:
        """從其他按鈕互動返回或開啟遊戲大廳。"""
        bot_name = self._resolve_bot_name(guild=interaction.guild, channel=interaction.channel)
        embed = await asyncio.to_thread(
            _build_game_lobby_embed,
            interaction.channel.id,
            interaction.user.id,
            bot_name=bot_name,
        )
        view = GameLobbyView(self, owner_id=interaction.user.id, bot_name=bot_name)
        if edit:
            await interaction.response.edit_message(embed=embed, view=view)
            view.message = interaction.message
        else:
            await interaction.response.send_message(embed=embed, view=view)
            try:
                view.message = await interaction.original_response()
            except Exception:
                pass

    def build_stats_embed(self, target_user: Optional[discord.abc.User] = None) -> discord.Embed:
        return build_stats_embed(target_user)

    @commands.command(name="play", aliases=["game", "games", "桌遊", "棋盤", "boardgame", "bg"])
    async def play_command(self, ctx: commands.Context, *, args: str = None):
        """遊戲大廳與開局指令：!play [黑白棋/西洋棋/四子棋/五子棋/1in8b] ..."""
        if not is_target_channel(ctx.channel, self.settings):
            return

        bot_name = self._resolve_bot_name(guild=ctx.guild, channel=ctx.channel)
        raw = (args or "").strip()
        if not raw or raw.lower() in ("list", "lobby", "menu", "清單", "大廳"):
            embed = await asyncio.to_thread(
                _build_game_lobby_embed,
                ctx.channel.id,
                ctx.author.id,
                bot_name=bot_name,
            )
            lobby_view = GameLobbyView(self, owner_id=ctx.author.id, bot_name=bot_name)
            sent_lobby = await ctx.send(embed=embed, view=lobby_view)
            lobby_view.message = sent_lobby
            return

        low = raw.lower()
        if low in ("atlas", "dex", "圖鑑", "國家圖鑑", "世界圖鑑"):
            entries = await asyncio.to_thread(ls.get_user_country_dex, ctx.author.id)
            await ctx.send(embed=build_atlas_embed(ctx.author, entries))
            return
        if low in ("stats", "stat", "戰績", "排行"):
            embed = await asyncio.to_thread(build_stats_embed, ctx.author)
            entries = await asyncio.to_thread(ls.get_user_country_dex, ctx.author.id)
            embed.add_field(
                name="🗺️ 80億分之1 世界圖鑑",
                value=f"已解鎖 **{len(entries)} / 197** 個國家與地區（輸入 `!play 圖鑑` 查看詳情）",
                inline=False,
            )
            await ctx.send(embed=embed)
            return
        if low in ("board", "show", "棋盤", "看棋盤"):
            active = await asyncio.to_thread(bg.get_active_game, ctx.channel.id)
            if not active:
                active_life = await asyncio.to_thread(ls.get_active_life_run, ctx.channel.id, ctx.author.id)
                life_cog = self.bot.get_cog("LifeSimCog")
                if active_life and life_cog:
                    await life_cog.send_life_card(ctx.channel, active_life, note="已重新顯示你目前的人生模擬進度")
                    return
                await ctx.send("🎲 目前此頻道沒有進行中的棋局或人生模擬。輸入 `!play` 開一局吧！")
                return
            await self.send_game_card(ctx.channel, active, last_note="已重新顯示目前盤面")
            return
        if low in ("resign", "surrender", "投降", "認輸"):
            active = await asyncio.to_thread(bg.get_active_game, ctx.channel.id)
            if not active:
                await ctx.send("ℹ️ 目前沒有進行中的棋局。")
                return
            async with self._get_game_lock(active["id"]):
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
            async with self._get_game_lock(active["id"]):
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
        first_low = tokens[0].lower()
        if first_low in ("1in8b", "80億分之1", "八十億分之一", "life", "lifesim", "投胎", "人生模擬"):
            life_cog = self.bot.get_cog("LifeSimCog")
            if not life_cog:
                await ctx.send("⚠️ 80億分之1 模組尚未載入。")
                return
            if len(tokens) == 1:
                setup_view = LifeSimSetupView(life_cog, owner_id=ctx.author.id)
                sent_setup = await ctx.send(embed=setup_view._build_setup_embed(), view=setup_view)
                setup_view.message = sent_setup
                return
            story_mode = "fast"
            era = "present"
            draw_method = "births"
            for tok in tokens[1:]:
                tl = tok.lower()
                if tl in ("llm", "gemini", "ai", "沉浸", "劇情"):
                    story_mode = "llm"
                elif tl in ("fast", "stat", "極速", "統計"):
                    story_mode = "fast"
                elif tl in ("population", "pop", "人口"):
                    draw_method = "population"
                elif tl in ("births", "birth", "出生", "出生率"):
                    draw_method = "births"
                elif any(k in tl for k in ("1950", "1980", "2050", "未來", "戰後", "過去", "現代")):
                    era = ls.resolve_era(tl)
            async with ctx.channel.typing():
                try:
                    await life_cog.start_life_and_send(ctx.channel, ctx.author, story_mode=story_mode, era=era, draw_method=draw_method)
                except ValueError as e:
                    await ctx.send(f"⚠️ {e}")
            return

        gtype = bg.resolve_game_type(tokens[0])
        if not gtype:
            await ctx.send(f"❓ 找不到遊戲 `{tokens[0]}`。輸入 `!play` 可開啟遊戲大廳與遊戲清單！")
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
                bot_name=bot_name,
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
            bot_name = self._resolve_bot_name(guild=ctx.guild, channel=ctx.channel)
            await ctx.send(f"❓ 請輸入座標或欄號（如 `!m d3`、`!m e2e4`），或直接點擊棋盤卡片按鈕／用口語請 {bot_name} 下棋！")
            return
        active = await asyncio.to_thread(bg.get_active_game, ctx.channel.id)
        if not active or active["status"] != "active":
            await ctx.send("🎲 目前此頻道沒有進行中的棋局（輸入 `!play` 或說「我想下棋」即可開局）。")
            return
        async with self._get_game_lock(active["id"]):
            try:
                game, note = await asyncio.to_thread(bg.apply_move, active["id"], ctx.author.id, move_text.strip())
            except ValueError as e:
                await ctx.send(f"⚠️ {e}")
                return

            async with ctx.channel.typing():
                game, card_note, llm_msg = await self._run_pve_turn_if_needed(game, note)
            await self.send_game_card(ctx.channel, game, last_note=card_note, content_text=llm_msg)
