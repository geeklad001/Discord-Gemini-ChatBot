"""LifeSim Cog: 1 in 8 Billion (80億分之1) interactive Views, PIL card lifecycle, leaderboards & Hybrid/LLM narrative engine."""

from __future__ import annotations

import asyncio
import io
import logging
from typing import Optional

import discord
from discord.ext import commands
from google import genai
from google.genai import types

from core.chat_pipeline import is_target_channel
from core.config import BotSettings, resolve_bot_display_name, truncate_embed_text
from core.llm_client import generate_with_fallback, get_safety_settings
from engines import lifesim_engine as ls


async def _disable_view_on_timeout(view: discord.ui.View) -> None:
    """停用逾時《80億分之1》卡片上的互動元件，避免舊訊息按鈕殘留。"""
    for child in view.children:
        if hasattr(child, "disabled"):
            child.disabled = True
    msg = getattr(view, "message", None)
    if msg is not None:
        try:
            await msg.edit(view=view)
        except Exception:
            pass


def _format_relationships_line(run: dict) -> str:
    rel = run.get("relationships") or {}
    married = bool(rel.get("married", False))
    partner = rel.get("partner")
    children = int(rel.get("children", 0) or 0)
    if married:
        base = f"已婚（配偶：{partner}）" if partner else "已婚"
    elif partner:
        base = f"交往中（{partner}）"
    else:
        base = "單身"
    if children > 0:
        return f"{base}・子女 {children} 人"
    return base


def _format_traits_line(run: dict) -> str:
    traits = run.get("traits") or []
    parts = [
        f"`{t.get('emoji', '')}{t.get('name', '')}`"
        for t in traits
        if isinstance(t, dict) and t.get("name")
    ]
    return " ".join(parts) if parts else "`普通資質`"


def _resolve_leaderboard_metric(raw: Optional[str]) -> str:
    q = (raw or "").strip().lower()
    if q in ("comeback", "reverse", "逆襲", "寒門", "翻身"):
        return "comeback"
    if q in ("dex", "atlas", "country", "圖鑑", "國家", "國家圖鑑", "世界圖鑑"):
        return "dex"
    return "age"


def build_leaderboard_embed(metric: str = "age", rows: Optional[list[dict]] = None) -> discord.Embed:
    """建立《80億分之1》伺服器三大排行榜 Embed（壽命榜、寒門逆襲榜、世界國家圖鑑榜）。"""
    key = _resolve_leaderboard_metric(metric)
    data = list(rows or ())

    if key == "comeback":
        title = "🔥 80億分之1 — 寒門逆襲排行榜"
        subtitle = "以低出生幸運 PR 卻打出高終局五維平均分計算逆襲指數（`平均五維 - PR × 0.45`）："
        color = 0xE67E22
    elif key == "dex":
        title = "🗺️ 80億分之1 — 世界國家圖鑑排行榜"
        subtitle = f"依玩家累積出生／跨國移居解鎖的國家數量排序（共 {ls.TOTAL_COUNTRIES_COUNT} 國）："
        color = 0x2ECC71
    else:
        title = "🏆 80億分之1 — 壽命殿堂排行榜"
        subtitle = "依最終享壽歲數與綜合五維成就排序："
        color = 0xF1C40F

    if not data:
        return discord.Embed(
            title=title,
            description=f"{subtitle}\n\n目前尚無完整紀錄，快輸入 `!life` 展開你的全球人生吧！",
            color=color,
        )

    medals = ("🥇", "🥈", "🥉")
    lines: list[str] = [subtitle, ""]
    for idx, r in enumerate(data[:10], start=1):
        prefix = medals[idx - 1] if idx <= 3 else f"`#{idx}`"
        if key == "dex":
            lines.append(
                f"{prefix} **{r['user_name']}** — 解鎖 `{r['unlocked_count']} / {ls.TOTAL_COUNTRIES_COUNT}` 國"
                f"｜最高 `{r['best_age']}` 歲｜累計 `{r['total_runs']}` 輪"
            )
        elif key == "comeback":
            route = (
                f"{r['country_name']}→{r['current_country_name']}"
                if r.get("current_country_name") and r["current_country_name"] != r["country_name"]
                else r["country_name"]
            )
            lines.append(
                f"{prefix} **{r['char_name']}**（{r['user_name']}｜{route}）— "
                f"逆襲分 `{r['comeback_score']}`（出生 `PR {r['luck_pr']}`｜均分 `{r['avg_stat']}`｜`{r['age']}` 歲）"
            )
        else:
            title_badge = f"｜{r['titles'][0]}" if r.get("titles") else ""
            route = (
                f"{r['country_name']}→{r['current_country_name']}"
                if r.get("current_country_name") and r["current_country_name"] != r["country_name"]
                else r["country_name"]
            )
            lines.append(
                f"{prefix} **{r['char_name']}**（{r['user_name']}｜{route}）— "
                f"享壽 **`{r['age']}` 歲**｜均分 `{r['avg_stat']}`｜{r['occupation']}{title_badge}"
            )

    embed = discord.Embed(
        title=title,
        description=truncate_embed_text("\n".join(lines), max_len=3800),
        color=color,
    )
    embed.set_footer(text="輸入 !life top [age|comeback|dex] 或點擊下方按鈕切換榜單")
    return embed


class LifeSimLeaderboardView(discord.ui.View):
    """排行榜互動切換面板（壽命榜／寒門逆襲榜／世界圖鑑榜）。"""

    def __init__(self, initial_metric: str = "age"):
        super().__init__(timeout=300)
        self.metric = _resolve_leaderboard_metric(initial_metric)
        self.message: Optional[discord.Message] = None

    async def on_timeout(self) -> None:
        await _disable_view_on_timeout(self)

    async def _switch_metric(self, interaction: discord.Interaction, metric: str) -> None:
        self.metric = metric
        rows = await asyncio.to_thread(ls.get_lifesim_leaderboard, metric, 10)
        await interaction.response.edit_message(embed=build_leaderboard_embed(metric, rows), view=self)

    @discord.ui.button(label="壽命殿堂榜", emoji="🏆", style=discord.ButtonStyle.primary)
    async def age_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._switch_metric(interaction, "age")

    @discord.ui.button(label="寒門逆襲榜", emoji="🔥", style=discord.ButtonStyle.secondary)
    async def comeback_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._switch_metric(interaction, "comeback")

    @discord.ui.button(label="世界圖鑑榜", emoji="🗺️", style=discord.ButtonStyle.success)
    async def dex_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._switch_metric(interaction, "dex")


def build_life_embed(
    run: dict,
    note: str = "",
    unlocked_count: int = 1,
    include_birth_cert: bool = False,
    llm_epitaph: str = "",
    bot_name: str = "機器人",
) -> discord.Embed:
    """建立《80億分之1》人生狀態卡片或終局謝幕紀念卡片 Embed。"""
    era_lbl = ls.ERA_CONFIG.get(run["era"], {}).get("label", run["era"])
    ur_lbl = ls.URBAN_RURAL_LABELS.get(run.get("urban_rural", "urban"), "🏙️ 都市")
    cur_country = run.get("current_country_name") or run["country_name"]
    cur_city = run.get("current_city") or run["city"]
    migrated = (run.get("current_country_code") or run["country_code"]) != run["country_code"]
    traits_line = _format_traits_line(run)
    rel_line = _format_relationships_line(run)
    story_mode = run.get("story_mode", "fast")
    mode_lbl = ls.STORY_MODE_LABELS.get(story_mode, ls.STORY_MODE_LABELS["fast"])

    if run["status"] == "finished":
        tl = run.get("timeline") or []
        sample_tl = tl[:1] + tl[-5:] if len(tl) > 6 else tl
        tl_lines = "\n".join(f"• **{t['age']}歲** {t['title']}：{t['detail']}" for t in sample_tl) or "（無詳細紀錄）"
        titles_list = run.get("titles") or []
        titles_txt = " ｜ ".join(f"**{t}**" for t in titles_list) if titles_list else "🌱 塵世行者"
        loc_desc = f"`{run['country_name']}・{run['city']}`（{ur_lbl}｜{era_lbl}｜PR `{run['luck_pr']}`）"
        if migrated:
            loc_desc += f"\n✈️ **移居終老**：`{cur_country}・{cur_city}`"

        desc_lines = [
            f"🌍 **出生地**：{loc_desc}",
            f"💼 **最終職業**：`{run['occupation']}` ｜ 🏠 **家境**：`{run['family_class']}` ｜ 💞 **家庭**：{rel_line}",
            f"🧬 **先天特質**：{traits_line}",
            f"🏅 **稱號**：{titles_txt}",
            f"🪦 **生命終章**：{run.get('death_cause') or '走完了一生。'}",
        ]
        if llm_epitaph:
            desc_lines.append(f"\n✒️ *{llm_epitaph.strip()}*")
        desc_lines.extend([
            "",
            f"📊 **終局五維**：❤️ 健康 `{run['health']}` ｜ 😊 快樂 `{run['happiness']}` ｜ 📚 知識 `{run['knowledge']}` ｜ 💰 財富 `{run['wealth']}` ｜ 🤝 人際 `{run['social']}`",
            f"🗺️ **世界國家圖鑑**：已解鎖 `{unlocked_count} / {ls.TOTAL_COUNTRIES_COUNT}` 個國家與地區",
        ])

        embed = discord.Embed(
            title=f"📜 人生謝幕紀念卡 — {run['char_name']}（享壽 {run['age']} 歲）",
            description=truncate_embed_text("\n".join(desc_lines), max_len=3800),
            color=0xD4AF37,
        )
        embed.add_field(name="⏳ 生平重要里程碑", value=truncate_embed_text(tl_lines, max_len=950), inline=False)
        embed.set_image(url="attachment://life_summary.png")
        embed.set_footer(text=f"人生 #{run['id']} | {mode_lbl} | 玩家：{run['user_name']}")
        return embed

    pe = run.get("pending_event") or {}
    choices = pe.get("choices") or []
    choice_lines: list[str] = []
    for idx, ch in enumerate(choices[:3], start=1):
        lbl = ch.get("label", f"選項{idx}")
        odds = ""
        if ch.get("success_pct") is not None and isinstance(ch.get("check"), dict):
            hint = ch["check"].get("label_hint") or "檢定"
            odds = f" `🎲 {hint} {ch['success_pct']}%`"
        choice_lines.append(f"**[{chr(64 + idx)}]** {lbl}{odds}")

    focus_lbl = ls.FOCUS_LABELS.get(run.get("focus", "balance"), "⚖️ 均衡發展")
    loc_line = f"🌍 **{run['country_name']}・{run['city']}**（{ur_lbl}｜出生機率 `{run['birth_prob_pct']:.3f}%`｜全球幸運度 `PR {run['luck_pr']}`）"
    if migrated:
        loc_line += f"\n✈️ **現居**：`{cur_country}・{cur_city}`（已跨國移居）"

    desc_parts = [
        loc_line,
        f"👤 **{run['char_name']}**（{run['gender']}｜{era_lbl} {run['birth_year']}年生｜{run['family_class']}）",
        f"🧬 **特質**：{traits_line} ｜ 💞 **家庭**：{rel_line}",
        f"🎂 **{run['age']} 歲** ｜ 💼 **職業**：`{run['occupation']}` ｜ 🎯 **重心**：`{focus_lbl}`",
        f"📊 ❤️健康 `{run['health']}` ｜ 😊快樂 `{run['happiness']}` ｜ 📚知識 `{run['knowledge']}` ｜ 💰財富 `{run['wealth']}` ｜ 🤝人際 `{run['social']}`",
    ]
    if note:
        desc_parts.append(f"\n📌 **上一步結果**：{note}")

    embed = discord.Embed(
        title=f"🌍 80億分之1 (1in8B) — {run['char_name']}（{run['age']}歲）",
        description=truncate_embed_text("\n".join(desc_parts), max_len=3800),
        color=0x2ECC71 if run["health"] >= 60 else (0xF1C40F if run["health"] >= 30 else 0xE74C3C),
    )
    ev_title = f"⏳ {pe.get('stage', '人生轉折')}｜{pe.get('title', '命運的十字路口')}"
    ev_body = f"{pe.get('story', '')}\n\n" + "\n".join(choice_lines)
    if story_mode in ("llm", "hybrid"):
        display_bot_name = (bot_name or "").strip()[:24] or "機器人"
        ev_body += f"\n\n💬 *可點下方 `[A]/[B]/[C]`、按 `✍️ 自訂行動`，或直接對 {display_bot_name} 打字說出你的決定！*"
    embed.add_field(name=ev_title[:250], value=truncate_embed_text(ev_body, max_len=1000), inline=False)
    if include_birth_cert:
        embed.set_image(url="attachment://birth_cert.png")
    embed.set_footer(text=f"人生 #{run['id']} | {mode_lbl} | 圖鑑 {unlocked_count}/{ls.TOTAL_COUNTRIES_COUNT} | 玩家：{run['user_name']}")
    return embed


def build_atlas_embed(target_user: discord.abc.User, entries: list[dict]) -> discord.Embed:
    """建立玩家《80億分之1》世界國家圖鑑 Embed。"""
    unlocked = len(entries)
    pct = (unlocked / float(ls.TOTAL_COUNTRIES_COUNT)) * 100.0
    if not entries:
        return discord.Embed(
            title=f"🗺️ {target_user.display_name} 的世界國家圖鑑（0 / {ls.TOTAL_COUNTRIES_COUNT}）",
            description="還沒有在《80億分之1》出生或移居過任何國家！輸入 `!life` 或點擊「🌍 80億分之1」開始你的第一場投胎體驗吧。",
            color=0x3498DB,
        )
    top_lines = [
        f"• **{e['country_name']}** (`{e['country_code']}`) — 造訪 `{e['runs_count']}` 次｜最高 `{e['best_age']}` 歲｜幸運 `PR {e['best_luck_pr']}`"
        for e in entries[:15]
    ]
    extra = f"\n…以及另外 `{unlocked - 15}` 個已探索國家！" if unlocked > 15 else ""
    return discord.Embed(
        title=f"🗺️ {target_user.display_name} 的世界國家圖鑑（{unlocked} / {ls.TOTAL_COUNTRIES_COUNT}｜{pct:.1f}%）",
        description=truncate_embed_text("\n".join(top_lines) + extra, max_len=3800),
        color=0x2ECC71,
    )


def build_lifesim_setup_embed(era: str, draw_method: str, story_mode: str) -> discord.Embed:
    """建立《80億分之1》開局參數預覽 Embed（大廳與 !play 1in8b / !life 共用）。"""
    sm_key = ls.resolve_story_mode(story_mode)
    sm_label = ls.STORY_MODE_LABELS.get(sm_key, ls.STORY_MODE_LABELS["fast"])
    era_key = ls.resolve_era(era)
    dm_key = ls.resolve_draw_method(draw_method)
    return discord.Embed(
        title="🌍 80億分之1 (1 in 8 Billion) — 投胎設定",
        description=(
            "你即將根據 **聯合國/世界銀行 197 國真實統計權重**，隨機降生在地球上的某個家庭（含城鄉機率、先天特質、歷史浪潮與跨國移居）。\n\n"
            f"• **時代**：`{ls.ERA_CONFIG[era_key]['label']}`\n"
            f"• **機率基準**：`{ls.DRAW_METHOD_LABELS[dm_key]}`\n"
            f"• **玩法模式**：`{sm_label}`\n"
            "  - `⚡ 經典深度版`：78+ 條件事件＋檢定擲骰＋副行動，節奏流暢免等待（預設）\n"
            "  - `🎭 精華劇情版`：日常輕快推進，在 18/32/48 歲關鍵轉折與生命終章展開專屬劇情\n"
            "  - `🧠 全程故事版`：每個階段即時演繹當地文化事件，隨時可自由輸入行動\n\n"
            "可直接點擊下方 **「🌍 立即投胎出生」**，或點 **「🧵 開專屬串投胎」** 在獨立討論串體驗完整一生！"
        ),
        color=0x2ECC71,
    )


def create_lifesim_option_selects(era: str, draw_method: str, story_mode: str, start_row: int = 0):
    """建構時代、機率分配基準與故事引擎三個 Discord 下拉選單（預設 story_mode='fast'）。"""
    sm_key = ls.resolve_story_mode(story_mode)
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
        discord.SelectOption(
            label=ls.STORY_MODE_LABELS["fast"],
            value="fast",
            description="78+條件事件・檢定擲骰・副行動與移居（流暢免等待）",
            default=(sm_key == "fast"),
        ),
        discord.SelectOption(
            label=ls.STORY_MODE_LABELS["hybrid"],
            value="hybrid",
            description="在關鍵人生轉折與生命終章展開專屬劇情",
            default=(sm_key == "hybrid"),
        ),
        discord.SelectOption(
            label=ls.STORY_MODE_LABELS["llm"],
            value="llm",
            description="每階段即時演繹文化事件並支援自由輸入行動",
            default=(sm_key == "llm"),
        ),
    ]
    story_select = discord.ui.Select(placeholder="3️⃣ 選擇玩法模式...", options=story_opts, row=start_row + 2)
    return era_select, method_select, story_select


class LifeSimSetupView(discord.ui.View):
    """《80億分之1》獨立設定面板（供 !life 與 !play 1in8b 直接呼叫）。"""

    def __init__(self, cog: "LifeSimCog", owner_id: int):
        super().__init__(timeout=600)
        self.cog = cog
        self.owner_id = owner_id
        self.message: Optional[discord.Message] = None
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

    async def on_timeout(self) -> None:
        await _disable_view_on_timeout(self)

    def _build_setup_embed(self) -> discord.Embed:
        return build_lifesim_setup_embed(self.era, self.draw_method, self.story_mode)

    async def _check_owner(self, interaction: discord.Interaction) -> bool:
        if self.owner_id and interaction.user.id != self.owner_id:
            await interaction.response.send_message("ℹ️ 請輸入 `!life` 或 `!play` 開啟你自己的設定面板，才不會點到別人的喔！", ephemeral=True)
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
        try:
            await self.cog.start_life_and_send(
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

    @discord.ui.button(label="開專屬串投胎", emoji="🧵", style=discord.ButtonStyle.primary, row=3)
    async def start_thread_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_owner(interaction):
            return
        try:
            msg = await self.cog.start_life_in_new_thread(
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

    @discord.ui.button(label="國家圖鑑", emoji="🗺️", style=discord.ButtonStyle.secondary, row=3)
    async def atlas_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        entries = await asyncio.to_thread(ls.get_user_country_dex, interaction.user.id)
        await interaction.response.send_message(embed=build_atlas_embed(interaction.user, entries), ephemeral=True)

    @discord.ui.button(label="排行榜", emoji="🏆", style=discord.ButtonStyle.secondary, row=3)
    async def leaderboard_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        rows = await asyncio.to_thread(ls.get_lifesim_leaderboard, "age", 10)
        await interaction.response.send_message(
            embed=build_leaderboard_embed("age", rows),
            view=LifeSimLeaderboardView("age"),
            ephemeral=True,
        )

    @discord.ui.button(label="回遊戲大廳", emoji="⬅️", style=discord.ButtonStyle.secondary, row=3)
    async def back_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_owner(interaction):
            return
        bg_cog = self.cog.bot.get_cog("BoardGameCog")
        if bg_cog:
            self.message = None
            self.stop()
            await bg_cog.show_lobby_from_interaction(interaction, edit=True)


class LifeSimCustomDecisionModal(discord.ui.Modal, title="✍️ 自訂你的人生行動"):
    """供 Hybrid / LLM 模式玩家直接輸入自由行動的彈窗。"""

    decision_input = discord.ui.TextInput(
        label="你打算在這個階段怎麼做？",
        placeholder="例如：把積蓄拿去申請當地語言學校，準備考跨國公司實習",
        style=discord.TextStyle.paragraph,
        max_length=180,
        required=True,
    )

    def __init__(self, cog: "LifeSimCog", run_id: int, owner_id: int, parent_view: "LifeSimActiveView"):
        super().__init__()
        self.cog = cog
        self.run_id = run_id
        self.owner_id = owner_id
        self.parent_view = parent_view

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("ℹ️ 只能操作你自己的人生模擬喔！", ephemeral=True)
            return
        lock = self.cog._get_run_lock(self.run_id)
        if lock.locked():
            await interaction.response.send_message("⏳ 人生事件仍在結算中，請稍候再試！", ephemeral=True)
            return
        async with lock:
            await interaction.response.defer()
            run = await asyncio.to_thread(ls.get_life_run_by_id, self.run_id)
            if not run or run["status"] != "active":
                await interaction.followup.send("⚠️ 這場人生模擬已經結束了。", ephemeral=True)
                return
            decision_text = str(self.decision_input.value or "").strip()
            outcome, deltas, new_occ = await self.cog.evaluate_llm_custom_choice_full(run, decision_text)
            try:
                updated, note = await asyncio.to_thread(
                    ls.resolve_life_step,
                    self.run_id,
                    interaction.user.id,
                    custom_deltas=deltas,
                    custom_outcome_text=outcome,
                    new_occ=new_occ,
                )
            except ValueError as e:
                await interaction.followup.send(f"⚠️ {e}", ephemeral=True)
                return

            if ls.should_use_llm_for_stage(updated):
                updated = await self.cog.populate_llm_next_event(updated)
            await self.cog.retire_and_send_life_card(
                interaction,
                updated,
                note=note,
                old_view=self.parent_view,
            )


class LifeSimActiveView(discord.ui.View):
    """《80億分之1》進行中互動卡片：A/B/C、自訂行動、階段副行動、重心與排行榜。"""

    def __init__(self, cog: "LifeSimCog", run: dict):
        super().__init__(timeout=1800)
        self.cog = cog
        self.run_id = run["id"]
        self.owner_id = run["user_id"]
        self.story_mode = run.get("story_mode", "fast")
        self.message: Optional[discord.Message] = None

        # Row 0: A, B, C (+ optional custom action button in hybrid/llm)
        choices = (run.get("pending_event") or {}).get("choices") or []
        for idx in range(3):
            letter = chr(65 + idx)
            if idx < len(choices):
                lbl_raw = choices[idx].get("label", f"選項 {letter}")
                btn_lbl = f"{letter}. {lbl_raw[:20]}{'…' if len(lbl_raw) > 20 else ''}"
                disabled = False
            else:
                btn_lbl = f"{letter}. （無）"
                disabled = True
            btn = discord.ui.Button(label=btn_lbl, style=discord.ButtonStyle.primary, row=0, disabled=disabled)
            btn.callback = self._make_choice_cb(idx)
            self.add_item(btn)

        if self.story_mode in ("llm", "hybrid"):
            custom_btn = discord.ui.Button(label="自訂行動", emoji="✍️", style=discord.ButtonStyle.success, row=0)
            custom_btn.callback = self._on_custom_action
            self.add_item(custom_btn)

        # Row 1: Once-per-stage Side Actions Select
        used_side = int(run.get("side_action_age", -1)) == int(run["age"])
        side_opts = [
            discord.SelectOption(label=lbl, value=key)
            for key, lbl in ls.SIDE_ACTIONS.items()
        ]
        side_placeholder = (
            f"✅ {run['age']}歲已執行過本階段副行動"
            if used_side
            else "🎯 本階段副行動（可選 1 次）..."
        )
        self.side_action_select = discord.ui.Select(
            placeholder=side_placeholder,
            options=side_opts,
            disabled=used_side,
            row=1,
        )
        self.side_action_select.callback = self._on_side_action
        self.add_item(self.side_action_select)

        # Row 2 buttons are declared via @discord.ui.button below
        # Row 3: Focus Select
        focus_opts = [
            discord.SelectOption(
                label=lbl,
                value=key,
                default=(key == (run.get("focus") or "balance")),
            )
            for key, lbl in ls.FOCUS_LABELS.items()
        ]
        self.focus_select = discord.ui.Select(placeholder="🧭 調整下一階段人生重心...", options=focus_opts, row=3)
        self.focus_select.callback = self._on_focus
        self.add_item(self.focus_select)

    async def on_timeout(self) -> None:
        await _disable_view_on_timeout(self)

    def _make_choice_cb(self, idx: int):
        async def _cb(interaction: discord.Interaction):
            if interaction.user.id != self.owner_id:
                await interaction.response.send_message("ℹ️ 這段人生屬於原玩家，輸入 `!life` 或 `!play 1in8b` 可開啟你自己的投胎！", ephemeral=True)
                return
            lock = self.cog._get_run_lock(self.run_id)
            if lock.locked():
                await interaction.response.send_message("⏳ 這個選項或劇情仍在結算中，請稍候再點喔！", ephemeral=True)
                return
            async with lock:
                await interaction.response.defer()
                try:
                    updated, note = await asyncio.to_thread(ls.resolve_life_step, self.run_id, interaction.user.id, choice_idx=idx)
                except ValueError as e:
                    await interaction.followup.send(f"⚠️ {e}", ephemeral=True)
                    return
                if ls.should_use_llm_for_stage(updated):
                    updated = await self.cog.populate_llm_next_event(updated)
                await self.cog.retire_and_send_life_card(interaction, updated, note=note, old_view=self)
        return _cb

    async def _on_custom_action(self, interaction: discord.Interaction):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("ℹ️ 這段人生屬於原玩家，輸入 `!life` 可開啟你自己的投胎！", ephemeral=True)
            return
        await interaction.response.send_modal(
            LifeSimCustomDecisionModal(self.cog, self.run_id, self.owner_id, self)
        )

    async def _on_side_action(self, interaction: discord.Interaction):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("ℹ️ 只能操作你自己的人生副行動喔！", ephemeral=True)
            return
        lock = self.cog._get_run_lock(self.run_id)
        if lock.locked():
            await interaction.response.send_message("⏳ 人生事件仍在結算中，請稍候再試！", ephemeral=True)
            return
        async with lock:
            action_key = self.side_action_select.values[0]
            try:
                updated, action_note = await asyncio.to_thread(
                    ls.apply_side_action, self.run_id, interaction.user.id, action_key
                )
            except ValueError as e:
                await interaction.response.send_message(f"⚠️ {e}", ephemeral=True)
                return
            unlocked = len(await asyncio.to_thread(ls.get_user_country_dex, updated["user_id"]))
            bot_name = resolve_bot_display_name(
                self.cog.bot,
                guild=getattr(interaction.channel, "guild", None) or interaction.guild,
                channel=interaction.channel,
                settings=self.cog.settings,
            )
            embed = build_life_embed(updated, note=action_note, unlocked_count=unlocked, bot_name=bot_name)
            new_view = LifeSimActiveView(self.cog, updated)
            new_view.message = self.message or interaction.message
            await interaction.response.edit_message(embed=embed, view=new_view, attachments=[])
            self.message = None
            self.stop()

    async def _on_focus(self, interaction: discord.Interaction):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("ℹ️ 只能調整你自己的人生重心喔！", ephemeral=True)
            return
        lock = self.cog._get_run_lock(self.run_id)
        if lock.locked():
            await interaction.response.send_message("⏳ 人生事件仍在結算中，請稍候再調整重心！", ephemeral=True)
            return
        async with lock:
            focus_val = self.focus_select.values[0]
            try:
                updated = await asyncio.to_thread(ls.set_life_focus, self.run_id, interaction.user.id, focus_val)
            except ValueError as e:
                await interaction.response.send_message(f"⚠️ {e}", ephemeral=True)
                return
            unlocked = len(await asyncio.to_thread(ls.get_user_country_dex, updated["user_id"]))
            bot_name = resolve_bot_display_name(
                self.cog.bot,
                guild=getattr(interaction.channel, "guild", None) or interaction.guild,
                channel=interaction.channel,
                settings=self.cog.settings,
            )
            embed = build_life_embed(
                updated,
                note=f"已將人生重心調整為：{ls.FOCUS_LABELS[focus_val]}",
                unlocked_count=unlocked,
                bot_name=bot_name,
            )
            new_view = LifeSimActiveView(self.cog, updated)
            new_view.message = self.message or interaction.message
            await interaction.response.edit_message(embed=embed, view=new_view, attachments=[])
            self.message = None
            self.stop()

    @discord.ui.button(label="快轉此生", emoji="⚡", style=discord.ButtonStyle.secondary, row=2)
    async def auto_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("ℹ️ 只能快轉你自己的人生！", ephemeral=True)
            return
        lock = self.cog._get_run_lock(self.run_id)
        if lock.locked():
            await interaction.response.send_message("⏳ 人生事件仍在結算中，請稍候！", ephemeral=True)
            return
        async with lock:
            await interaction.response.defer()
            try:
                finished = await asyncio.to_thread(ls.auto_finish_life, self.run_id, interaction.user.id)
            except ValueError as e:
                await interaction.followup.send(f"⚠️ {e}", ephemeral=True)
                return
            await self.cog.retire_and_send_life_card(
                interaction,
                finished,
                note="⚡ 已快速模擬完畢剩餘人生。",
                old_view=self,
                skip_llm_epitaph=True,
            )

    @discord.ui.button(label="國家圖鑑", emoji="🗺️", style=discord.ButtonStyle.secondary, row=2)
    async def dex_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        entries = await asyncio.to_thread(ls.get_user_country_dex, interaction.user.id)
        await interaction.response.send_message(embed=build_atlas_embed(interaction.user, entries), ephemeral=True)

    @discord.ui.button(label="排行榜", emoji="🏆", style=discord.ButtonStyle.secondary, row=2)
    async def leaderboard_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        rows = await asyncio.to_thread(ls.get_lifesim_leaderboard, "age", 10)
        await interaction.response.send_message(
            embed=build_leaderboard_embed("age", rows),
            view=LifeSimLeaderboardView("age"),
            ephemeral=True,
        )

    @discord.ui.button(label="投胎重開", emoji="🔁", style=discord.ButtonStyle.danger, row=2)
    async def restart_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("ℹ️ 只能重開你自己的人生！", ephemeral=True)
            return
        lock = self.cog._get_run_lock(self.run_id)
        if lock.locked():
            await interaction.response.send_message("⏳ 人生事件仍在結算中，請稍候！", ephemeral=True)
            return
        async with lock:
            await interaction.response.defer()
            try:
                finished = await asyncio.to_thread(ls.abandon_life_run, self.run_id, interaction.user.id)
            except ValueError as e:
                await interaction.followup.send(f"⚠️ {e}", ephemeral=True)
                return
            await self.cog.retire_and_send_life_card(
                interaction,
                finished,
                note="已結束本輪人生，準備重新投胎！",
                old_view=self,
                skip_llm_epitaph=True,
            )


class LifeSimFinishedView(discord.ui.View):
    """《80億分之1》終局謝幕卡片按鈕。"""

    def __init__(self, cog: "LifeSimCog", run: dict):
        super().__init__(timeout=1800)
        self.cog = cog
        self.story_mode = run.get("story_mode", "fast")
        self.era = run.get("era", "present")
        self.draw_method = run.get("draw_method", "births")
        self.message: Optional[discord.Message] = None

    async def on_timeout(self) -> None:
        await _disable_view_on_timeout(self)

    @discord.ui.button(label="再投胎一次", emoji="🔁", style=discord.ButtonStyle.success, row=0)
    async def replay_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        try:
            await self.cog.start_life_and_send(
                interaction.channel,
                interaction.user,
                story_mode=self.story_mode,
                era=self.era,
                draw_method=self.draw_method,
            )
        except ValueError as e:
            await interaction.followup.send(f"⚠️ {e}", ephemeral=True)
            return

    @discord.ui.button(label="我的世界圖鑑", emoji="🗺️", style=discord.ButtonStyle.primary, row=0)
    async def dex_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        entries = await asyncio.to_thread(ls.get_user_country_dex, interaction.user.id)
        await interaction.response.send_message(embed=build_atlas_embed(interaction.user, entries), ephemeral=True)

    @discord.ui.button(label="排行榜", emoji="🏆", style=discord.ButtonStyle.secondary, row=0)
    async def leaderboard_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        rows = await asyncio.to_thread(ls.get_lifesim_leaderboard, "age", 10)
        await interaction.response.send_message(
            embed=build_leaderboard_embed("age", rows),
            view=LifeSimLeaderboardView("age"),
            ephemeral=True,
        )

    @discord.ui.button(label="回遊戲大廳", emoji="🎮", style=discord.ButtonStyle.secondary, row=0)
    async def lobby_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        bg_cog = self.cog.bot.get_cog("BoardGameCog")
        if bg_cog:
            await bg_cog.show_lobby_from_interaction(interaction, edit=False)


class LifeSimCog(commands.Cog):
    """《80億分之1 (1in8B)》全球人生模擬系統，支援 0-Token 深度版、Hybrid 精華混合版與 Gemini 全程沉浸版。"""

    def __init__(self, bot: commands.Bot, gemini_client: genai.Client, settings: BotSettings, logger: logging.Logger):
        self.bot = bot
        self.gemini_client = gemini_client
        self.settings = settings
        self.logger = logger
        self._run_locks: dict[int, asyncio.Lock] = {}

    def _get_run_lock(self, run_id: int) -> asyncio.Lock:
        lock = self._run_locks.get(run_id)
        if lock is None:
            lock = asyncio.Lock()
            self._run_locks[run_id] = lock
        return lock

    async def populate_llm_next_event(self, run: dict) -> dict:
        """在 Hybrid 關鍵轉折或 LLM 沉浸劇情模式下，為當前年齡生成在地文化轉折事件（失敗自動保留本地事件）。"""
        if not ls.should_use_llm_for_stage(run):
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
                final_fallback_model=self.settings.final_fallback_model,
            )
            ev = ls.parse_lifesim_event_json(getattr(resp, "text", "") or "", run["age"])
            if ev:
                return await asyncio.to_thread(ls.set_pending_event, run["id"], ev)
        except Exception as e:
            self.logger.warning(f"⚠️ [1in8B LLM] 事件生成失敗，改用本地統計事件: {e}")
        return run

    async def generate_llm_epitaph(self, run: dict) -> str:
        """在 Hybrid / LLM 終局時生成 2~3 句墓誌銘總結（失敗或逾時則安靜省略）。"""
        if not run or run.get("status") != "finished" or run.get("story_mode") not in ("hybrid", "llm"):
            return ""
        prompt = ls.format_lifesim_epitaph_prompt(run)
        try:
            resp, _ = await generate_with_fallback(
                client=self.gemini_client,
                models_to_try=self.settings.models_to_try,
                contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=0.75,
                    safety_settings=get_safety_settings(self.settings.disable_safety_settings),
                ),
                timeout=15.0,
                context_label="80億分之1 終局墓誌銘",
                logger=self.logger,
                final_fallback_model=self.settings.final_fallback_model,
            )
            text = (getattr(resp, "text", "") or "").strip()
            return text[:240]
        except Exception as e:
            self.logger.warning(f"⚠️ [1in8B LLM] 墓誌銘生成失敗，改用本地總結: {e}")
            return ""

    async def evaluate_llm_custom_choice_full(self, run: dict, user_decision: str) -> tuple[str, dict, str]:
        """在 Hybrid / LLM 模式下評估玩家自然語言自訂決定的合理後果，回傳 (outcome, deltas, new_occ)。"""
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
                final_fallback_model=self.settings.final_fallback_model,
            )
            return ls.parse_lifesim_custom_payload(getattr(resp, "text", "") or "")
        except Exception as e:
            self.logger.warning(f"⚠️ [1in8B LLM] 自訂決定判定失敗，改用中性結果: {e}")
            return f"你選擇了「{user_decision[:40]}」，並為這段人生留下特別的足跡。", {"happiness": 3}, ""

    async def evaluate_llm_custom_choice(self, run: dict, user_decision: str) -> tuple[str, dict]:
        """評估玩家自然語言自訂決定的合理後果（維持 2-tuple 相容介面）。"""
        outcome, deltas, new_occ = await self.evaluate_llm_custom_choice_full(run, user_decision)
        if new_occ:
            deltas = dict(deltas)
            deltas["_new_occ"] = new_occ
        return outcome, deltas

    async def send_life_card(
        self,
        channel: discord.abc.Messageable,
        run: dict,
        note: str = "",
        include_birth_cert: bool = False,
        content_text: Optional[str] = None,
        skip_llm_epitaph: bool = False,
    ) -> discord.Message:
        unlocked = len(await asyncio.to_thread(ls.get_user_country_dex, run["user_id"]))
        llm_epitaph = ""
        if run["status"] == "finished" and not skip_llm_epitaph and run.get("story_mode") in ("hybrid", "llm"):
            llm_epitaph = await self.generate_llm_epitaph(run)
        bot_name = resolve_bot_display_name(
            self.bot,
            guild=getattr(channel, "guild", None),
            channel=channel,
            settings=self.settings,
        )
        embed = build_life_embed(
            run,
            note=note,
            unlocked_count=unlocked,
            include_birth_cert=include_birth_cert,
            llm_epitaph=llm_epitaph,
            bot_name=bot_name,
        )
        view = LifeSimActiveView(self, run) if run["status"] == "active" else LifeSimFinishedView(self, run)
        clean_content = (content_text or "").strip() or None
        if clean_content and len(clean_content) > 1900:
            clean_content = clean_content[:1900] + "..."

        files: list[discord.File] = []
        if run["status"] == "finished":
            png_bytes = await asyncio.to_thread(ls.render_life_epitaph_png, run, unlocked, ls.TOTAL_COUNTRIES_COUNT)
            files.append(discord.File(io.BytesIO(png_bytes), filename="life_summary.png"))
        elif include_birth_cert:
            png_bytes = await asyncio.to_thread(ls.render_birth_certificate_png, run)
            files.append(discord.File(io.BytesIO(png_bytes), filename="birth_cert.png"))

        if files:
            sent = await channel.send(content=clean_content, files=files, embed=embed, view=view)
        else:
            sent = await channel.send(content=clean_content, embed=embed, view=view)
        view.message = sent
        return sent

    async def retire_and_send_life_card(
        self,
        interaction: discord.Interaction,
        run: dict,
        note: str = "",
        include_birth_cert: bool = False,
        content_text: Optional[str] = None,
        old_view: Optional[discord.ui.View] = None,
        skip_llm_epitaph: bool = False,
    ) -> discord.Message:
        if old_view is not None:
            old_view.message = None
            old_view.stop()
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
            skip_llm_epitaph=skip_llm_epitaph,
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
        if ls.should_use_llm_for_stage(run):
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
    ) -> Optional[discord.Message]:
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
        try:
            msg = await self.start_life_and_send(
                target_ch,
                interaction.user,
                story_mode=story_mode,
                era=era,
                draw_method=draw_method,
                retire_interaction=retire_interaction,
            )
        except ValueError as e:
            await interaction.followup.send(f"⚠️ {e}", ephemeral=True)
            return None
        if created_thread:
            await interaction.followup.send(f"🧵 已為你開啟專屬投胎討論串：{target_ch.mention}", ephemeral=True)
        return msg

    @commands.command(name="life", aliases=["1in8b", "人生", "投胎"])
    async def life_command(self, ctx: commands.Context, *, args: str = None):
        """《80億分之1》專屬指令：!life [start|top [age|comeback|dex]|atlas]"""
        if not is_target_channel(ctx.channel, self.settings):
            return

        raw = (args or "").strip()
        tokens = raw.split() if raw else []
        sub = tokens[0].lower() if tokens else "start"

        if sub in ("top", "rank", "leaderboard", "排行", "排行榜", "榜單"):
            metric_raw = tokens[1] if len(tokens) > 1 else "age"
            metric = _resolve_leaderboard_metric(metric_raw)
            rows = await asyncio.to_thread(ls.get_lifesim_leaderboard, metric, 10)
            view = LifeSimLeaderboardView(metric)
            sent = await ctx.send(embed=build_leaderboard_embed(metric, rows), view=view)
            view.message = sent
            return

        if sub in ("atlas", "dex", "圖鑑", "國家圖鑑", "世界圖鑑"):
            entries = await asyncio.to_thread(ls.get_user_country_dex, ctx.author.id)
            await ctx.send(embed=build_atlas_embed(ctx.author, entries))
            return

        if sub in ("resume", "card", "進度", "目前"):
            active = await asyncio.to_thread(ls.get_active_life_run, ctx.channel.id, ctx.author.id)
            if active:
                await self.send_life_card(ctx.channel, active, note="已重新顯示你目前進行中的人生進度")
                return

        setup_view = LifeSimSetupView(self, owner_id=ctx.author.id)
        sent_setup = await ctx.send(embed=setup_view._build_setup_embed(), view=setup_view)
        setup_view.message = sent_setup
