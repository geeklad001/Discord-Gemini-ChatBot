"""LifeSim Cog: 1 in 8 Billion (80億分之1) interactive Views, PIL card lifecycle & LLM narrative engine."""

import asyncio
import io
import logging
from typing import Optional

import discord
from discord.ext import commands
from google import genai
from google.genai import types

from core.config import BotSettings, truncate_embed_text
from core.llm_client import generate_with_fallback, get_safety_settings
from engines import lifesim_engine as ls


def build_life_embed(
    run: dict,
    note: str = "",
    unlocked_count: int = 1,
    include_birth_cert: bool = False,
) -> discord.Embed:
    """建立《80億分之1》人生狀態卡片或終局謝幕紀念卡片 Embed。"""
    era_lbl = ls.ERA_CONFIG.get(run["era"], {}).get("label", run["era"])
    if run["status"] == "finished":
        tl = run.get("timeline") or []
        sample_tl = tl[:1] + tl[-5:] if len(tl) > 6 else tl
        tl_lines = "\n".join(f"• **{t['age']}歲** {t['title']}：{t['detail']}" for t in sample_tl) or "（無詳細紀錄）"
        embed = discord.Embed(
            title=f"📜 人生謝幕紀念卡 — {run['char_name']}（享壽 {run['age']} 歲）",
            description=(
                f"🌍 **出生地**：`{run['country_name']}・{run['city']}`（{era_lbl}｜全球幸運度 PR `{run['luck_pr']}`）\n"
                f"💼 **最終職業**：`{run['occupation']}` ｜ 🏠 **出身家境**：`{run['family_class']}`\n"
                f"🪦 **生命終章**：{run.get('death_cause') or '走完了一生。'}\n\n"
                f"📊 **終局五維**：❤️ 健康 `{run['health']}` ｜ 😊 快樂 `{run['happiness']}` ｜ 📚 知識 `{run['knowledge']}` ｜ 💰 財富 `{run['wealth']}` ｜ 🤝 人際 `{run['social']}`\n"
                f"🗺️ **世界國家圖鑑**：已解鎖 `{unlocked_count} / 197` 個國家與地區"
            ),
            color=0xD4AF37,
        )
        embed.add_field(name="⏳ 生平重要里程碑", value=truncate_embed_text(tl_lines, max_len=950), inline=False)
        embed.set_image(url="attachment://life_summary.png")
        embed.set_footer(text=f"人生 #{run['id']} | 80億分之1 (1in8B) | 玩家：{run['user_name']}")
        return embed

    pe = run.get("pending_event") or {}
    choices = pe.get("choices") or []
    choice_lines = [f"**[{chr(64 + idx)}]** {ch.get('label', f'選項{idx}')}" for idx, ch in enumerate(choices[:3], start=1)]

    mode_lbl = "🧠 Gemini 沉浸劇情版" if run["story_mode"] == "llm" else "⚡ 極速統計版"
    focus_lbl = ls.FOCUS_LABELS.get(run.get("focus", "balance"), "⚖️ 均衡發展")
    desc_parts = [
        f"🌍 **{run['country_name']}・{run['city']}**（出生確率 `{run['birth_prob_pct']:.3f}%`｜全球幸運度 `PR {run['luck_pr']}`）",
        f"👤 **{run['char_name']}**（{run['gender']}｜{era_lbl} {run['birth_year']}年生｜{run['family_class']}）",
        f"🎂 **{run['age']} 歲** ｜ 💼 **職業**：`{run['occupation']}` ｜ 🎯 **重心**：`{focus_lbl}`",
        f"📊 ❤️健康 `{run['health']}` ｜ 😊快樂 `{run['happiness']}` ｜ 📚知識 `{run['knowledge']}` ｜ 💰財富 `{run['wealth']}` ｜ 🤝人際 `{run['social']}`",
    ]
    if note:
        desc_parts.append(f"\n📌 **上一步結果**：{note}")

    embed = discord.Embed(
        title=f"🌍 80億分之1 (1in8B) — {run['char_name']}（{run['age']}歲）",
        description="\n".join(desc_parts),
        color=0x2ECC71 if run["health"] >= 60 else (0xF1C40F if run["health"] >= 30 else 0xE74C3C),
    )
    ev_title = f"⏳ {pe.get('stage', '人生轉折')}｜{pe.get('title', '命運的十字路口')}"
    ev_body = f"{pe.get('story', '')}\n\n" + "\n".join(choice_lines)
    if run["story_mode"] == "llm":
        ev_body += "\n\n💬 *可點下方 `[A]/[B]/[C]` 按鈕，或直接對 Bot 打字說出你的自訂決定！*"
    embed.add_field(name=ev_title[:250], value=truncate_embed_text(ev_body, max_len=1000), inline=False)
    if include_birth_cert:
        embed.set_image(url="attachment://birth_cert.png")
    embed.set_footer(text=f"人生 #{run['id']} | {mode_lbl} | 圖鑑 {unlocked_count}/197 | 玩家：{run['user_name']}")
    return embed


def build_atlas_embed(target_user: discord.abc.User, entries: list[dict]) -> discord.Embed:
    """建立玩家《80億分之1》世界國家圖鑑 Embed。"""
    unlocked = len(entries)
    pct = (unlocked / 197.0) * 100.0
    if not entries:
        return discord.Embed(
            title=f"🗺️ {target_user.display_name} 的世界國家圖鑑（0 / 197）",
            description="還沒有在《80億分之1》出生過任何國家！點擊「🌍 80億分之1」開始你的第一場投胎體驗吧。",
            color=0x3498DB,
        )
    top_lines = [
        f"• **{e['country_name']}** (`{e['country_code']}`) — 投胎 `{e['runs_count']}` 次｜最高 `{e['best_age']}` 歲｜幸運 `PR {e['best_luck_pr']}`"
        for e in entries[:15]
    ]
    extra = f"\n…以及另外 `{unlocked - 15}` 個已探索國家！" if unlocked > 15 else ""
    return discord.Embed(
        title=f"🗺️ {target_user.display_name} 的世界國家圖鑑（{unlocked} / 197｜{pct:.1f}%）",
        description="\n".join(top_lines) + extra,
        color=0x2ECC71,
    )


def build_lifesim_setup_embed(era: str, draw_method: str, story_mode: str) -> discord.Embed:
    """建立《80億分之1》開局參數預覽 Embed（大廳與 !play 1in8b 共用）。"""
    return discord.Embed(
        title="🌍 80億分之1 (1 in 8 Billion) — 投胎設定",
        description=(
            "你即將根據 **聯合國/世界銀行 197 國真實統計權重**，隨機降生在地球上的某個家庭。\n\n"
            f"• **時代**：`{ls.ERA_CONFIG[era]['label']}`\n"
            f"• **機率基準**：`{ls.DRAW_METHOD_LABELS[draw_method]}`\n"
            f"• **故事引擎**：`{'🧠 Gemini 沉浸劇情版（支援自由打字自訂決定）' if story_mode == 'llm' else '⚡ 極速統計版（0 Token，瞬間結算）'}`\n\n"
            "可直接點擊下方 **「🌍 立即投胎出生」**，或點 **「🧵 開專屬串投胎」** 在獨立討論串體驗完整一生！"
        ),
        color=0x2ECC71,
    )


def create_lifesim_option_selects(era: str, draw_method: str, story_mode: str, start_row: int = 0):
    """建構時代、機率分配基準與故事引擎三個 Discord 下拉選單。"""
    era_opts = [
        discord.SelectOption(label="🌍 現代（2024–2026）", value="present", default=(era == "present")),
        discord.SelectOption(label="📼 過去（1980–1995）", value="past_1980", default=(era == "past_1980")),
        discord.SelectOption(label="🕰️ 戰後（1950–1965）", value="past_1950", default=(era == "past_1950")),
        discord.SelectOption(label="🚀 未來預測（2048–2055）", value="future_2050", default=(era == "future_2050")),
    ]
    era_select = discord.ui.Select(placeholder="1️⃣ 選擇降生時代...", options=era_opts, row=start_row)

    method_opts = [
        discord.SelectOption(label="👶 依真實新生兒出生率抽取（推薦・寫實）", value="births", default=(draw_method == "births")),
        discord.SelectOption(label="👥 依全球現存人口比例抽取", value="population", default=(draw_method == "population")),
    ]
    method_select = discord.ui.Select(placeholder="2️⃣ 選擇機率分配方式...", options=method_opts, row=start_row + 1)

    story_opts = [
        discord.SelectOption(label="⚡ 極速統計版（0 Token，瞬間結算）", value="fast", default=(story_mode == "fast")),
        discord.SelectOption(label="🧠 Gemini 沉浸劇情版（在地文化＋自由打字決定）", value="llm", default=(story_mode == "llm")),
    ]
    story_select = discord.ui.Select(placeholder="3️⃣ 選擇遊玩引擎...", options=story_opts, row=start_row + 2)
    return era_select, method_select, story_select


class LifeSimSetupView(discord.ui.View):
    """《80億分之1》獨立設定面板（供 !play 1in8b 直接呼叫）。"""

    def __init__(self, cog: "LifeSimCog", owner_id: int):
        super().__init__(timeout=600)
        self.cog = cog
        self.owner_id = owner_id
        self.era = "present"
        self.draw_method = "births"
        self.story_mode = "fast"

        self.era_select, self.method_select, self.story_select = create_lifesim_option_selects(
            self.era, self.draw_method, self.story_mode, start_row=0
        )
        self.era_select.callback = self._on_era
        self.method_select.callback = self._on_method
        self.story_select.callback = self._on_story
        self.add_item(self.era_select)
        self.add_item(self.method_select)
        self.add_item(self.story_select)

    def _build_setup_embed(self) -> discord.Embed:
        return build_lifesim_setup_embed(self.era, self.draw_method, self.story_mode)

    async def _check_owner(self, interaction: discord.Interaction) -> bool:
        if self.owner_id and interaction.user.id != self.owner_id:
            await interaction.response.send_message("ℹ️ 請輸入 `!play` 開啟你自己的設定面板，才不會點到別人的喔！", ephemeral=True)
            return False
        return True

    async def _on_era(self, interaction: discord.Interaction):
        if not await self._check_owner(interaction):
            return
        self.era = self.era_select.values[0]
        for opt in self.era_select.options:
            opt.default = (opt.value == self.era)
        await interaction.response.edit_message(embed=self._build_setup_embed(), view=self)

    async def _on_method(self, interaction: discord.Interaction):
        if not await self._check_owner(interaction):
            return
        self.draw_method = self.method_select.values[0]
        for opt in self.method_select.options:
            opt.default = (opt.value == self.draw_method)
        await interaction.response.edit_message(embed=self._build_setup_embed(), view=self)

    async def _on_story(self, interaction: discord.Interaction):
        if not await self._check_owner(interaction):
            return
        self.story_mode = self.story_select.values[0]
        for opt in self.story_select.options:
            opt.default = (opt.value == self.story_mode)
        await interaction.response.edit_message(embed=self._build_setup_embed(), view=self)

    @discord.ui.button(label="立即投胎出生", emoji="🌍", style=discord.ButtonStyle.success, row=3)
    async def start_here_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_owner(interaction):
            return
        await interaction.response.defer()
        await self.cog.start_life_and_send(
            interaction.channel,
            interaction.user,
            story_mode=self.story_mode,
            era=self.era,
            draw_method=self.draw_method,
            retire_interaction=interaction,
        )

    @discord.ui.button(label="開專屬串投胎", emoji="🧵", style=discord.ButtonStyle.primary, row=3)
    async def start_thread_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_owner(interaction):
            return
        await self.cog.start_life_in_new_thread(
            interaction,
            story_mode=self.story_mode,
            era=self.era,
            draw_method=self.draw_method,
            retire_interaction=interaction,
        )

    @discord.ui.button(label="我的國家圖鑑", emoji="🗺️", style=discord.ButtonStyle.secondary, row=3)
    async def atlas_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        entries = await asyncio.to_thread(ls.get_user_country_dex, interaction.user.id)
        await interaction.response.send_message(embed=build_atlas_embed(interaction.user, entries), ephemeral=True)

    @discord.ui.button(label="回遊戲大廳", emoji="⬅️", style=discord.ButtonStyle.secondary, row=3)
    async def back_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_owner(interaction):
            return
        bg_cog = self.cog.bot.get_cog("BoardGameCog")
        if bg_cog:
            await bg_cog.show_lobby_from_interaction(interaction, edit=True)


class LifeSimActiveView(discord.ui.View):
    """《80億分之1》進行中互動卡片：A/B/C 選擇、年度重心、自動過完與圖鑑。"""

    def __init__(self, cog: "LifeSimCog", run: dict):
        super().__init__(timeout=1800)
        self.cog = cog
        self.run_id = run["id"]
        self.owner_id = run["user_id"]

        choices = (run.get("pending_event") or {}).get("choices") or []
        for idx in range(3):
            letter = chr(65 + idx)
            if idx < len(choices):
                lbl_raw = choices[idx].get("label", f"選項 {letter}")
                btn_lbl = f"{letter}. {lbl_raw[:22]}{'…' if len(lbl_raw) > 22 else ''}"
                disabled = False
            else:
                btn_lbl = f"{letter}. （無）"
                disabled = True
            btn = discord.ui.Button(label=btn_lbl, style=discord.ButtonStyle.primary, row=0, disabled=disabled)
            btn.callback = self._make_choice_cb(idx)
            self.add_item(btn)

        focus_opts = [
            discord.SelectOption(
                label=lbl,
                value=key,
                default=(key == (run.get("focus") or "balance")),
            )
            for key, lbl in ls.FOCUS_LABELS.items()
        ]
        self.focus_select = discord.ui.Select(placeholder="🎯 調整下一階段人生重心...", options=focus_opts, row=1)
        self.focus_select.callback = self._on_focus
        self.add_item(self.focus_select)

    def _make_choice_cb(self, idx: int):
        async def _cb(interaction: discord.Interaction):
            if interaction.user.id != self.owner_id:
                await interaction.response.send_message("ℹ️ 這段人生屬於原玩家，輸入 `!play 1in8b` 可開啟你自己的投胎！", ephemeral=True)
                return
            await interaction.response.defer()
            try:
                updated, note = await asyncio.to_thread(ls.resolve_life_step, self.run_id, interaction.user.id, choice_idx=idx)
            except ValueError as e:
                await interaction.followup.send(f"⚠️ {e}", ephemeral=True)
                return
            if updated["status"] == "active" and updated["story_mode"] == "llm":
                updated = await self.cog.populate_llm_next_event(updated)
            await self.cog.retire_and_send_life_card(interaction, updated, note=note)
        return _cb

    async def _on_focus(self, interaction: discord.Interaction):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("ℹ️ 只能調整你自己的人生重心喔！", ephemeral=True)
            return
        focus_val = self.focus_select.values[0]
        try:
            updated = await asyncio.to_thread(ls.set_life_focus, self.run_id, interaction.user.id, focus_val)
        except ValueError as e:
            await interaction.response.send_message(f"⚠️ {e}", ephemeral=True)
            return
        unlocked = len(await asyncio.to_thread(ls.get_user_country_dex, updated["user_id"]))
        embed = build_life_embed(updated, note=f"已將人生重心調整為：{ls.FOCUS_LABELS[focus_val]}", unlocked_count=unlocked)
        await interaction.response.edit_message(embed=embed, view=LifeSimActiveView(self.cog, updated))

    @discord.ui.button(label="自動過完此生", emoji="🤖", style=discord.ButtonStyle.secondary, row=2)
    async def auto_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("ℹ️ 只能快轉你自己的人生！", ephemeral=True)
            return
        await interaction.response.defer()
        try:
            finished = await asyncio.to_thread(ls.auto_finish_life, self.run_id, interaction.user.id)
        except ValueError as e:
            await interaction.followup.send(f"⚠️ {e}", ephemeral=True)
            return
        await self.cog.retire_and_send_life_card(interaction, finished, note="⚡ 已快速模擬完畢剩餘人生。")

    @discord.ui.button(label="國家圖鑑", emoji="🗺️", style=discord.ButtonStyle.secondary, row=2)
    async def dex_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        entries = await asyncio.to_thread(ls.get_user_country_dex, interaction.user.id)
        await interaction.response.send_message(embed=build_atlas_embed(interaction.user, entries), ephemeral=True)

    @discord.ui.button(label="投胎重開", emoji="🔁", style=discord.ButtonStyle.danger, row=2)
    async def restart_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("ℹ️ 只能重開你自己的人生！", ephemeral=True)
            return
        await interaction.response.defer()
        try:
            finished = await asyncio.to_thread(ls.abandon_life_run, self.run_id, interaction.user.id)
        except ValueError as e:
            await interaction.followup.send(f"⚠️ {e}", ephemeral=True)
            return
        await self.cog.retire_and_send_life_card(interaction, finished, note="已結束本輪人生，準備重新投胎！")


class LifeSimFinishedView(discord.ui.View):
    """《80億分之1》終局謝幕卡片按鈕。"""

    def __init__(self, cog: "LifeSimCog", run: dict):
        super().__init__(timeout=1800)
        self.cog = cog
        self.story_mode = run.get("story_mode", "fast")
        self.era = run.get("era", "present")
        self.draw_method = run.get("draw_method", "births")

    @discord.ui.button(label="再投胎一次", emoji="🔁", style=discord.ButtonStyle.success, row=0)
    async def replay_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        await self.cog.start_life_and_send(
            interaction.channel,
            interaction.user,
            story_mode=self.story_mode,
            era=self.era,
            draw_method=self.draw_method,
        )

    @discord.ui.button(label="我的世界圖鑑", emoji="🗺️", style=discord.ButtonStyle.primary, row=0)
    async def dex_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        entries = await asyncio.to_thread(ls.get_user_country_dex, interaction.user.id)
        await interaction.response.send_message(embed=build_atlas_embed(interaction.user, entries), ephemeral=True)

    @discord.ui.button(label="回遊戲大廳", emoji="🎮", style=discord.ButtonStyle.secondary, row=0)
    async def lobby_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        bg_cog = self.cog.bot.get_cog("BoardGameCog")
        if bg_cog:
            await bg_cog.show_lobby_from_interaction(interaction, edit=False)


class LifeSimCog(commands.Cog):
    """《80億分之1 (1in8B)》全球人生模擬系統，支援統計與 Gemini 沉浸劇情雙引擎。"""

    def __init__(self, bot: commands.Bot, gemini_client: genai.Client, settings: BotSettings, logger: logging.Logger):
        self.bot = bot
        self.gemini_client = gemini_client
        self.settings = settings
        self.logger = logger

    async def populate_llm_next_event(self, run: dict) -> dict:
        """在 Gemini 沉浸劇情模式下，為當前年齡生成在地文化轉折事件（失敗自動保留本地事件）。"""
        if not run or run["status"] != "active":
            return run
        prompt = ls.format_lifesim_next_event_prompt(run)
        try:
            resp, _ = await generate_with_fallback(
                client=self.gemini_client,
                models_to_try=self.settings.models_to_try,
                contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=0.75,
                    safety_settings=get_safety_settings(self.settings.disable_safety_settings),
                ),
                timeout=22.0,
                context_label="80億分之1 LLM 劇情生成",
                logger=self.logger,
            )
            ev = ls.parse_lifesim_event_json(getattr(resp, "text", "") or "", run["age"])
            if ev:
                return await asyncio.to_thread(ls.set_pending_event, run["id"], ev)
        except Exception as e:
            self.logger.warning(f"⚠️ [1in8B LLM] 事件生成失敗，改用本地統計事件: {e}")
        return run

    async def evaluate_llm_custom_choice(self, run: dict, user_decision: str) -> tuple[str, dict]:
        """在 Gemini 沉浸劇情模式下評估玩家自然語言自訂決定的合理後果。"""
        prompt = ls.format_lifesim_custom_choice_prompt(run, user_decision)
        try:
            resp, _ = await generate_with_fallback(
                client=self.gemini_client,
                models_to_try=self.settings.models_to_try,
                contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=0.55,
                    safety_settings=get_safety_settings(self.settings.disable_safety_settings),
                ),
                timeout=22.0,
                context_label="80億分之1 自訂抉擇判定",
                logger=self.logger,
            )
            return ls.parse_lifesim_custom_json(getattr(resp, "text", "") or "")
        except Exception as e:
            self.logger.warning(f"⚠️ [1in8B LLM] 自訂決定判定失敗，改用中性結果: {e}")
            return f"你選擇了「{user_decision[:40]}」，並為這段人生留下特別的足跡。", {"happiness": 3}

    async def send_life_card(
        self,
        channel: discord.abc.Messageable,
        run: dict,
        note: str = "",
        include_birth_cert: bool = False,
        content_text: Optional[str] = None,
    ) -> discord.Message:
        unlocked = len(await asyncio.to_thread(ls.get_user_country_dex, run["user_id"]))
        embed = build_life_embed(run, note=note, unlocked_count=unlocked, include_birth_cert=include_birth_cert)
        view = LifeSimActiveView(self, run) if run["status"] == "active" else LifeSimFinishedView(self, run)
        clean_content = (content_text or "").strip() or None
        if clean_content and len(clean_content) > 1900:
            clean_content = clean_content[:1900] + "..."

        files: list[discord.File] = []
        if run["status"] == "finished":
            png_bytes = await asyncio.to_thread(ls.render_life_epitaph_png, run, unlocked, 197)
            files.append(discord.File(io.BytesIO(png_bytes), filename="life_summary.png"))
        elif include_birth_cert:
            png_bytes = await asyncio.to_thread(ls.render_birth_certificate_png, run)
            files.append(discord.File(io.BytesIO(png_bytes), filename="birth_cert.png"))

        if files:
            return await channel.send(content=clean_content, files=files, embed=embed, view=view)
        return await channel.send(content=clean_content, embed=embed, view=view)

    async def retire_and_send_life_card(
        self,
        interaction: discord.Interaction,
        run: dict,
        note: str = "",
        include_birth_cert: bool = False,
        content_text: Optional[str] = None,
    ) -> discord.Message:
        try:
            await interaction.edit_original_response(view=None)
        except discord.HTTPException:
            pass
        return await self.send_life_card(
            interaction.channel,
            run,
            note=note,
            include_birth_cert=include_birth_cert,
            content_text=content_text,
        )

    async def start_life_and_send(
        self,
        channel: discord.abc.Messageable,
        user: discord.abc.User,
        story_mode: str = "fast",
        era: str = "present",
        draw_method: str = "births",
        retire_interaction: Optional[discord.Interaction] = None,
    ) -> discord.Message:
        run = await asyncio.to_thread(
            ls.create_life_run,
            channel.id,
            user.id,
            user.display_name,
            story_mode=story_mode,
            era=era,
            draw_method=draw_method,
        )
        if story_mode == "llm" and run["status"] == "active":
            run = await self.populate_llm_next_event(run)
        if retire_interaction is not None:
            try:
                await retire_interaction.edit_original_response(view=None)
            except discord.HTTPException:
                pass
        return await self.send_life_card(
            channel,
            run,
            note="你呱呱墜地了！以下是你的全球出生證明書與首次命運轉折。",
            include_birth_cert=True,
        )

    async def start_life_in_new_thread(
        self,
        interaction: discord.Interaction,
        story_mode: str = "fast",
        era: str = "present",
        draw_method: str = "births",
        retire_interaction: Optional[discord.Interaction] = None,
    ) -> discord.Message:
        """建立專屬公開討論串（不支援時自動退回原頻道）並開始《80億分之1》。"""
        await interaction.response.defer()
        target_ch = interaction.channel
        created_thread = False
        if isinstance(interaction.channel, discord.TextChannel):
            try:
                target_ch = await interaction.channel.create_thread(
                    name=f"🌍 {interaction.user.display_name} 的80億分之1人生",
                    type=discord.ChannelType.public_thread,
                    auto_archive_duration=1440,
                )
                created_thread = True
            except discord.HTTPException as e:
                self.logger.warning(f"⚠️ 無法建立討論串，改回當前頻道遊玩: {e}")
        msg = await self.start_life_and_send(
            target_ch,
            interaction.user,
            story_mode=story_mode,
            era=era,
            draw_method=draw_method,
            retire_interaction=retire_interaction,
        )
        if created_thread:
            await interaction.followup.send(f"🧵 已為你開啟專屬投胎討論串：{target_ch.mention}", ephemeral=True)
        return msg
