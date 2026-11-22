"""Game Wiki Cog: !wiki command for Zombie High School Contextual-LightRAG knowledge base."""

import asyncio
import logging
from pathlib import Path
from typing import Optional

import discord
from discord.ext import commands
from google import genai

from engines import game_wiki_engine
from core.chat_pipeline import is_target_channel
from core.config import MAX_TEXT_ATTACHMENT_BYTES, BotSettings, truncate_embed_text


def _split_long_text_pages(text: str, page_size: int = 3200) -> list[str]:
    """將長篇知識卡全文依換行邊界切分為不超過 Discord Embed 上限的多頁內容。"""
    cleaned = (text or "").strip()
    if not cleaned:
        return [""]
    if len(cleaned) <= page_size:
        return [cleaned]

    pages: list[str] = []
    current_lines: list[str] = []
    current_len = 0
    for line in cleaned.splitlines():
        add_len = len(line) + (1 if current_lines else 0)
        if current_len + add_len > page_size and current_lines:
            pages.append("\n".join(current_lines))
            current_lines = []
            current_len = 0
        while len(line) > page_size:
            pages.append(line[:page_size])
            line = line[page_size:]
        if line or not current_lines:
            current_lines.append(line)
            current_len += len(line) + (1 if len(current_lines) > 1 else 0)
    if current_lines:
        pages.append("\n".join(current_lines))
    return pages or [cleaned[:page_size]]


def _build_wiki_entry_embed(
    *,
    query: str,
    entries: list[dict],
    idx: int,
    focused_snippets: dict[str, tuple[str, bool]],
    dynamic_notes: list[str],
    expanded_full: bool = False,
    full_subpage: int = 0,
    is_suggestion_mode: bool = False,
    related_ids: Optional[set[str]] = None,
) -> discord.Embed:
    """建構單筆聚焦顯示的 `!wiki` 查詢結果 Embed（含摘要/全文切換與相符總覽）。"""
    entry = entries[idx]
    is_rel = bool(related_ids and entry["id"] in related_ids)
    full_body = (entry.get("body") or "").strip()
    excerpt_body, was_focused = focused_snippets.get(entry["id"], (full_body, False))

    if expanded_full and was_focused:
        pages = _split_long_text_pages(full_body, page_size=3200)
        sub_idx = max(0, min(full_subpage, len(pages) - 1))
        body_text = pages[sub_idx]
        if len(pages) > 1:
            body_text += f"\n\n*(📖 全文第 {sub_idx + 1}/{len(pages)} 頁)*"
    else:
        body_text = excerpt_body

    desc_parts: list[str] = []
    if is_suggestion_mode and idx == 0:
        desc_parts.append(
            f"💡 查無與「`{query[:30]}`」完全相符的條目，以下為最相近的建議條目（可用下方選單或翻頁切換）："
        )
    if idx == 0 and dynamic_notes:
        notes_joined = "\n\n".join(dynamic_notes)
        if len(notes_joined) > 900:
            notes_joined = notes_joined[:897] + "..."
        desc_parts.append(notes_joined)

    desc_parts.append(body_text)

    guide_url = str(entry.get("guide_url") or "").strip()
    if guide_url and guide_url not in body_text:
        desc_parts.append(f"📍 **站內圖文攻略導覽**：{guide_url}")

    if is_suggestion_mode:
        prefix = "💡 相似推薦"
        color = 0xF39C12
    elif is_rel:
        prefix = "🔗 延伸關聯"
        color = 0x9B59B6
    else:
        prefix = "🔍 《殭屍學園》查詢"
        color = 0x3498DB

    entry_title = (entry.get("name_zh") or entry["id"]).strip()
    embed_title = f"{prefix}：{query[:30]}｜{entry_title}"
    if len(embed_title) > 250:
        embed_title = embed_title[:247] + "..."

    embed = discord.Embed(
        title=embed_title,
        description=truncate_embed_text("\n\n".join(p for p in desc_parts if p), max_len=3800),
        color=color,
    )

    if len(entries) > 1:
        total = len(entries)
        if total <= 8:
            indices_to_show = list(range(total))
        else:
            start = max(0, idx - 3)
            end = min(total, start + 7)
            start = max(0, end - 7)
            indices_to_show = list(range(start, end))

        list_lines: list[str] = []
        if indices_to_show[0] > 0:
            list_lines.append(f"…（前面尚有 {indices_to_show[0]} 筆）")
        for i in indices_to_show:
            item = entries[i]
            pointer = "👉 " if i == idx else ""
            rel_tag = "🔗 " if (related_ids and item["id"] in related_ids) else ""
            item_name = (item.get("name_zh") or item["id"]).strip()
            if len(item_name) > 52:
                item_name = item_name[:49] + "..."
            line = f"{pointer}**{i + 1}.** {rel_tag}{item_name} (`{item['id']}`)"
            if sum(len(x) + 1 for x in list_lines) + len(line) > 960:
                break
            list_lines.append(line)
        if indices_to_show[-1] < total - 1:
            rem = total - 1 - indices_to_show[-1]
            tail = f"…（後面尚有 {rem} 筆）"
            if sum(len(x) + 1 for x in list_lines) + len(tail) <= 1000:
                list_lines.append(tail)

        embed.add_field(
            name=f"📋 相符條目總覽（共 {total} 筆，可用下方按鈕/選單切換）",
            value="\n".join(list_lines)[:1020],
            inline=False,
        )

    src = entry.get("source") or entry.get("source_file") or "本地知識庫"
    upd = entry.get("updated_at") or "基礎庫"
    fresh_tag = "（隨改版變動）" if entry.get("freshness") == "volatile" else ""
    footer_text = f"第 {idx + 1} / {len(entries)} 筆 ｜ ID: {entry['id']} ｜ 📂 {src} ｜ 📅 {upd}{fresh_tag}"
    embed.set_footer(text=footer_text[:2000])
    return embed


def _resolve_browse_categories() -> list[tuple[str, str, int]]:
    """取得可瀏覽之知識庫分類與各分類有效條目數量。"""
    stats = game_wiki_engine.get_wiki_stats()
    by_cat = stats.get("by_category", {})
    result: list[tuple[str, str, int]] = []
    for cat_key, label in game_wiki_engine.CATEGORY_LABELS.items():
        cnt = by_cat.get(cat_key, 0)
        if cnt > 0 or cat_key == "community_contrib":
            result.append((cat_key, label, cnt))
    return result


class WikiSearchView(discord.ui.View):
    """支援條目下拉切換、上一筆/下一筆翻頁、長表重點/全文切換的互動式 `!wiki` 檢視器。"""

    def __init__(
        self,
        *,
        query: str,
        entries: list[dict],
        focused_snippets: dict[str, tuple[str, bool]],
        dynamic_notes: Optional[list[str]] = None,
        related_ids: Optional[set[str]] = None,
        is_suggestion_mode: bool = False,
        initial_idx: int = 0,
        timeout: float = 180.0,
    ):
        super().__init__(timeout=timeout)
        self.query = query
        self.entries = entries[:25]
        self.focused_snippets = focused_snippets
        self.dynamic_notes = dynamic_notes or []
        self.related_ids = related_ids or set()
        self.is_suggestion_mode = is_suggestion_mode
        self.idx = max(0, min(initial_idx, max(0, len(self.entries) - 1)))
        self.expanded_full = False
        self.full_subpage = 0
        self.message: Optional[discord.Message] = None
        self._rebuild_items()

    def build_embed(self) -> discord.Embed:
        return _build_wiki_entry_embed(
            query=self.query,
            entries=self.entries,
            idx=self.idx,
            focused_snippets=self.focused_snippets,
            dynamic_notes=self.dynamic_notes,
            expanded_full=self.expanded_full,
            full_subpage=self.full_subpage,
            is_suggestion_mode=self.is_suggestion_mode,
            related_ids=self.related_ids,
        )

    def _current_was_focused(self) -> tuple[bool, int]:
        entry = self.entries[self.idx]
        full_body = (entry.get("body") or "").strip()
        _, was_focused = self.focused_snippets.get(entry["id"], (full_body, False))
        pages = _split_long_text_pages(full_body, page_size=3200) if was_focused else [full_body]
        return was_focused, len(pages)

    def _rebuild_items(self) -> None:
        self.clear_items()
        if not self.entries:
            return

        if len(self.entries) > 1:
            options: list[discord.SelectOption] = []
            for i, e in enumerate(self.entries[:25]):
                rel_prefix = "🔗 " if e["id"] in self.related_ids else ""
                name = (e.get("name_zh") or e["id"]).strip()
                label = f"{rel_prefix}{i + 1}. {name}"
                if len(label) > 95:
                    label = label[:92] + "..."
                cat_label = e.get("category_label") or e.get("category") or ""
                desc = f"{cat_label}｜ID: {e['id']}"
                if len(desc) > 95:
                    desc = desc[:92] + "..."
                options.append(
                    discord.SelectOption(
                        label=label,
                        description=desc,
                        value=str(i),
                        default=(i == self.idx),
                    )
                )
            select = discord.ui.Select(
                placeholder="快速切換相符條目...",
                min_values=1,
                max_values=1,
                options=options,
                row=0,
            )
            select.callback = self._on_select_entry
            self.add_item(select)

            prev_btn = discord.ui.Button(
                label="◀️ 上一筆",
                style=discord.ButtonStyle.secondary,
                disabled=(self.idx <= 0),
                row=1,
            )
            prev_btn.callback = self._on_prev
            self.add_item(prev_btn)

            counter_btn = discord.ui.Button(
                label=f"第 {self.idx + 1} / {len(self.entries)} 筆",
                style=discord.ButtonStyle.secondary,
                disabled=True,
                row=1,
            )
            self.add_item(counter_btn)

            next_btn = discord.ui.Button(
                label="下一筆 ▶️",
                style=discord.ButtonStyle.secondary,
                disabled=(self.idx >= len(self.entries) - 1),
                row=1,
            )
            next_btn.callback = self._on_next
            self.add_item(next_btn)

        was_focused, total_full_pages = self._current_was_focused()
        if was_focused:
            toggle_btn = discord.ui.Button(
                label="🔎 只看重點" if self.expanded_full else "📖 展開全文",
                style=discord.ButtonStyle.primary,
                row=1,
            )
            toggle_btn.callback = self._on_toggle_full
            self.add_item(toggle_btn)

            if self.expanded_full and total_full_pages > 1:
                sub_prev = discord.ui.Button(
                    label="⬅️ 全文上頁",
                    style=discord.ButtonStyle.secondary,
                    disabled=(self.full_subpage <= 0),
                    row=2,
                )
                sub_prev.callback = self._on_full_prev
                self.add_item(sub_prev)

                sub_next = discord.ui.Button(
                    label="全文下頁 ➡️",
                    style=discord.ButtonStyle.secondary,
                    disabled=(self.full_subpage >= total_full_pages - 1),
                    row=2,
                )
                sub_next.callback = self._on_full_next
                self.add_item(sub_next)

    async def _refresh(self, interaction: discord.Interaction) -> None:
        self._rebuild_items()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    async def _on_select_entry(self, interaction: discord.Interaction) -> None:
        self.idx = int(interaction.data["values"][0])
        self.expanded_full = False
        self.full_subpage = 0
        await self._refresh(interaction)

    async def _on_prev(self, interaction: discord.Interaction) -> None:
        if self.idx > 0:
            self.idx -= 1
            self.expanded_full = False
            self.full_subpage = 0
        await self._refresh(interaction)

    async def _on_next(self, interaction: discord.Interaction) -> None:
        if self.idx < len(self.entries) - 1:
            self.idx += 1
            self.expanded_full = False
            self.full_subpage = 0
        await self._refresh(interaction)

    async def _on_toggle_full(self, interaction: discord.Interaction) -> None:
        self.expanded_full = not self.expanded_full
        self.full_subpage = 0
        await self._refresh(interaction)

    async def _on_full_prev(self, interaction: discord.Interaction) -> None:
        if self.full_subpage > 0:
            self.full_subpage -= 1
        await self._refresh(interaction)

    async def _on_full_next(self, interaction: discord.Interaction) -> None:
        _, total_full_pages = self._current_was_focused()
        if self.full_subpage < total_full_pages - 1:
            self.full_subpage += 1
        await self._refresh(interaction)

    async def on_timeout(self) -> None:
        for child in self.children:
            if hasattr(child, "disabled"):
                child.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except Exception:
                pass


class WikiBrowseView(discord.ui.View):
    """《殭屍學園》知識庫分類目錄瀏覽器（支援分類切換、分頁與點選直開條目卡片）。"""

    def __init__(
        self,
        *,
        category_key: str = "evolution",
        page: int = 0,
        per_page: int = 10,
        timeout: float = 180.0,
    ):
        super().__init__(timeout=timeout)
        self.per_page = per_page
        self.categories = _resolve_browse_categories()
        valid_keys = {k for k, _, _ in self.categories}
        if category_key not in valid_keys and self.categories:
            category_key = self.categories[0][0]
        self.category_key = category_key
        self.page = max(0, page)
        self.message: Optional[discord.Message] = None
        self._rebuild_items()

    def _current_entries(self) -> list[dict]:
        return game_wiki_engine.list_wiki_entries_by_category(self.category_key)

    def _current_page_entries(self, entries: list[dict]) -> tuple[list[dict], int, int]:
        total_pages = max(1, (len(entries) + self.per_page - 1) // self.per_page)
        safe_page = max(0, min(self.page, total_pages - 1))
        self.page = safe_page
        start = safe_page * self.per_page
        return entries[start : start + self.per_page], safe_page, total_pages

    def build_embed(self) -> discord.Embed:
        entries = self._current_entries()
        page_entries, safe_page, total_pages = self._current_page_entries(entries)
        cat_label = game_wiki_engine.CATEGORY_LABELS.get(self.category_key, self.category_key)

        if not entries:
            desc = f"📭「**{cat_label}**」分類目前尚無有效條目。"
        else:
            lines: list[str] = []
            start_num = safe_page * self.per_page + 1
            for offset, e in enumerate(page_entries):
                num = start_num + offset
                name = (e.get("name_zh") or e["id"]).strip()
                if len(name) > 55:
                    name = name[:52] + "..."
                body_lines = [
                    ln.strip()
                    for ln in (e.get("body") or "").splitlines()
                    if ln.strip() and not ln.strip().startswith("【《殭屍學園》")
                ]
                preview = " ".join(body_lines)
                if len(preview) > 55:
                    preview = preview[:52] + "..."
                lines.append(f"**{num}.** 【{name}】 (`{e['id']}`)\n  └ {preview}")
            desc = truncate_embed_text("\n".join(lines), max_len=3800)

        embed = discord.Embed(
            title=f"📚 《殭屍學園》知識庫目錄 — {cat_label}",
            description=desc,
            color=0x2ECC71,
        )
        embed.set_footer(
            text=f"第 {safe_page + 1} / {total_pages} 頁（本分類共 {len(entries)} 筆）｜可於下方選單直接開啟條目"
        )
        return embed

    def _rebuild_items(self) -> None:
        self.clear_items()
        if self.categories:
            cat_options = [
                discord.SelectOption(
                    label=f"{label} ({cnt} 筆)"[:95],
                    value=cat_key,
                    default=(cat_key == self.category_key),
                )
                for cat_key, label, cnt in self.categories[:25]
            ]
            cat_select = discord.ui.Select(
                placeholder="📂 切換知識庫分類...",
                min_values=1,
                max_values=1,
                options=cat_options,
                row=0,
            )
            cat_select.callback = self._on_select_category
            self.add_item(cat_select)

        entries = self._current_entries()
        page_entries, safe_page, total_pages = self._current_page_entries(entries)

        if page_entries:
            start_num = safe_page * self.per_page + 1
            entry_options: list[discord.SelectOption] = []
            for offset, e in enumerate(page_entries):
                num = start_num + offset
                name = (e.get("name_zh") or e["id"]).strip()
                label = f"{num}. {name}"
                if len(label) > 95:
                    label = label[:92] + "..."
                kws_preview = "、".join(e.get("keywords", [])[:4])
                desc = f"ID: {e['id']}" + (f"｜{kws_preview}" if kws_preview else "")
                if len(desc) > 95:
                    desc = desc[:92] + "..."
                entry_options.append(
                    discord.SelectOption(
                        label=label,
                        description=desc,
                        value=str(offset),
                    )
                )
            entry_select = discord.ui.Select(
                placeholder="🔎 選擇此頁條目直接查看完整內容...",
                min_values=1,
                max_values=1,
                options=entry_options,
                row=1,
            )
            entry_select.callback = self._on_open_entry
            self.add_item(entry_select)

        prev_btn = discord.ui.Button(
            label="◀️ 上一頁",
            style=discord.ButtonStyle.secondary,
            disabled=(safe_page <= 0),
            row=2,
        )
        prev_btn.callback = self._on_prev
        self.add_item(prev_btn)

        page_btn = discord.ui.Button(
            label=f"第 {safe_page + 1} / {total_pages} 頁",
            style=discord.ButtonStyle.secondary,
            disabled=True,
            row=2,
        )
        self.add_item(page_btn)

        next_btn = discord.ui.Button(
            label="下一頁 ▶️",
            style=discord.ButtonStyle.secondary,
            disabled=(safe_page >= total_pages - 1),
            row=2,
        )
        next_btn.callback = self._on_next
        self.add_item(next_btn)

    async def _refresh(self, interaction: discord.Interaction) -> None:
        self._rebuild_items()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    async def _on_select_category(self, interaction: discord.Interaction) -> None:
        self.category_key = interaction.data["values"][0]
        self.page = 0
        await self._refresh(interaction)

    async def _on_open_entry(self, interaction: discord.Interaction) -> None:
        offset = int(interaction.data["values"][0])
        entries = self._current_entries()
        page_entries, _, _ = self._current_page_entries(entries)
        if not page_entries:
            await self._refresh(interaction)
            return
        offset = max(0, min(offset, len(page_entries) - 1))
        cat_label = game_wiki_engine.CATEGORY_LABELS.get(self.category_key, self.category_key)
        focused_snippets = {
            e["id"]: game_wiki_engine.extract_focused_entry_body(e, e.get("name_zh") or e["id"], max_chars=1200)
            for e in page_entries
        }
        search_view = WikiSearchView(
            query=cat_label,
            entries=page_entries,
            focused_snippets=focused_snippets,
            initial_idx=offset,
            timeout=self.timeout,
        )
        target_msg = self.message
        search_view.message = target_msg
        await interaction.response.edit_message(embed=search_view.build_embed(), view=search_view)
        self.message = None
        self.stop()

    async def _on_prev(self, interaction: discord.Interaction) -> None:
        if self.page > 0:
            self.page -= 1
        await self._refresh(interaction)

    async def _on_next(self, interaction: discord.Interaction) -> None:
        entries = self._current_entries()
        total_pages = max(1, (len(entries) + self.per_page - 1) // self.per_page)
        if self.page < total_pages - 1:
            self.page += 1
        await self._refresh(interaction)

    async def on_timeout(self) -> None:
        for child in self.children:
            if hasattr(child, "disabled"):
                child.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except Exception:
                pass


class WikiOverviewView(discord.ui.View):
    """附掛於 `!wiki` 總覽或查無結果畫面上的分類目錄快捷選單。"""

    def __init__(self, timeout: float = 180.0):
        super().__init__(timeout=timeout)
        self.message: Optional[discord.Message] = None
        categories = _resolve_browse_categories()
        if categories:
            options = [
                discord.SelectOption(
                    label=f"{label} ({cnt} 筆)"[:95],
                    value=cat_key,
                )
                for cat_key, label, cnt in categories[:25]
            ]
            select = discord.ui.Select(
                placeholder="📂 選擇分類瀏覽知識庫目錄...",
                min_values=1,
                max_values=1,
                options=options,
                row=0,
            )
            select.callback = self._on_select_category
            self.add_item(select)

    async def _on_select_category(self, interaction: discord.Interaction) -> None:
        cat_key = interaction.data["values"][0]
        browse_view = WikiBrowseView(category_key=cat_key, page=0, timeout=self.timeout)
        target_msg = self.message
        browse_view.message = target_msg
        await interaction.response.edit_message(embed=browse_view.build_embed(), view=browse_view)
        self.message = None
        self.stop()

    async def on_timeout(self) -> None:
        for child in self.children:
            if hasattr(child, "disabled"):
                child.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except Exception:
                pass


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

    @commands.command(name="wiki", aliases=["圖鑑", "黑話", "좀고"])
    async def wiki_command(self, ctx: commands.Context, *, query: Optional[str] = None):
        """查詢與擴充《殭屍學園 (좀비고등학교)》知識庫。"""
        if not is_target_channel(ctx.channel, self.settings):
            return

        if not self.settings.enable_game_wiki:
            await ctx.send("ℹ️ 此伺服器未啟用《殭屍學園》知識庫功能（`ENABLE_GAME_WIKI=false`）。")
            return

        raw_arg = (query or "").strip()
        has_text_attachment = any(
            att.filename.lower().endswith((".txt", ".md")) for att in ctx.message.attachments
        )
        if not raw_arg and has_text_attachment:
            raw_arg = "import"

        if not raw_arg:
            stats = game_wiki_engine.get_wiki_stats()
            cat_lines = []
            for cat_key, label in game_wiki_engine.CATEGORY_LABELS.items():
                cnt = stats["by_category"].get(cat_key, 0)
                if cnt > 0 or cat_key == "community_contrib":
                    cat_lines.append(f"• **{label}**：共 **{cnt}** 筆")
            embed = discord.Embed(
                title="🧟 《殭屍學園 (좀비고등학교)》知識庫",
                description=(
                    f"目前共收錄 **{stats['total']}** 筆資料：\n\n"
                    + "\n".join(cat_lines)
                    + "\n\n🔍 **查詢與目錄用法**：\n"
                    "• `!wiki 阿修羅` / `!wiki 燒瓶 2技` / `!wiki 教室 紅房`（精準查機制、技能與報位）\n"
                    "• `!wiki ㄷㅋㅈ` / `!wiki ㅎㅇ` / `!wiki 椅子`（自動聚焦單詞釋義，長表可切換全文）\n"
                    "• `!wiki 南東進` / `!wiki 鄭東碩`（查台服角色譯名與特徵）\n"
                    "• `!wiki 目錄` / `!wiki 角色` / `!wiki 黑話`（或點下方選單直接瀏覽各分類目錄）\n\n"
                    "📥 **群友補充文本／擴充資料庫用法**：\n"
                    "• **單筆新增**：`!wiki add 關鍵字1, 關鍵字2 | 條目內容說明`\n"
                    "• **批次貼文或傳檔**：輸入 `!wiki import [主題名稱]` 並在後方換行貼上長文（支援 `【標題】內容`、`# 標題` 分段或整篇攻略），或直接**夾帶 `.txt` / `.md` 檔案上傳**！\n"
                    "• **改版勘誤／熱修補覆寫**：`!wiki replace <ID或關鍵字> | <新內容>`（軟屏蔽舊條目並以新內容優先回應）\n"
                    "• **管理補充條目**：`!wiki list`（查看群友補充清單）、`!wiki del <編號>`（刪除條目或撤銷覆寫）"
                ),
                color=0x2ECC71,
            )
            overview_view = WikiOverviewView()
            sent_msg = await ctx.send(embed=embed, view=overview_view)
            overview_view.message = sent_msg
            return

        parts = raw_arg.split(maxsplit=1)
        sub = parts[0].lower()
        rest = parts[1].strip() if len(parts) > 1 else ""

        if sub in ("browse", "dir", "cat", "category", "目錄", "分類", "瀏覽"):
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
            browse_view = WikiBrowseView(category_key=target_cat, page=0)
            sent_msg = await ctx.send(embed=browse_view.build_embed(), view=browse_view)
            browse_view.message = sent_msg
            return

        if sub in ("list", "contrib", "清單", "補充清單"):
            contribs = game_wiki_engine.list_community_wiki_entries()
            if not contribs:
                await ctx.reply(
                    "📭 目前還沒有群友透過指令補充的條目！\n"
                    "💡 歡迎使用 `!wiki add <關鍵字> | <內容>` 或 `!wiki import [主題]`（可夾帶 `.txt`/`.md` 檔）來擴充資料庫！",
                    mention_author=False,
                )
                return
            lines = []
            for item in contribs[-25:]:
                kws_preview = "、".join(item.get("keywords", [])[:5])
                body_lines = [ln for ln in item.get("body", "").splitlines() if not ln.startswith("【《殭屍學園》")]
                preview = " ".join(body_lines)[:80]
                if len(" ".join(body_lines)) > 80:
                    preview += "..."
                lines.append(f"• **`{item['id']}`** 【{item['name_zh']}】（關鍵字：`{kws_preview}`）\n  └ {preview}")
            embed = discord.Embed(
                title=f"📥 群友補充《殭屍學園》知識庫清單（共 {len(contribs)} 筆）",
                description=truncate_embed_text("\n".join(lines)),
                color=0x2ECC71,
            )
            embed.set_footer(text="💡 如需刪除特定補充條目，請輸入 !wiki del <編號>（例如 !wiki del contrib_1）")
            await ctx.send(embed=embed)
            return

        if sub in ("del", "delete", "remove", "rm", "刪除"):
            if not rest:
                await ctx.reply("⚠️ 請指定要刪除的補充條目編號，例如：`!wiki del contrib_1`（可先用 `!wiki list` 查看編號）。", mention_author=False)
                return
            ok = game_wiki_engine.delete_community_wiki_entry(rest)
            if ok:
                await ctx.reply(f"🗑️ 已從《殭屍學園》知識庫刪除群友補充條目 **`{rest}`**，並即時更新檢索索引！", mention_author=False)
            else:
                await ctx.reply(f"⚠️ 找不到編號為 `{rest}` 的群友補充條目（僅限刪除透過 `!wiki add/import` 新增的 `contrib_N` 條目）。", mention_author=False)
            return

        if sub in ("replace", "hotfix", "override", "update", "替換", "覆寫", "勘誤"):
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

            replaced_entry = res["replaced_entry"]
            new_entry = res["new_entry"]
            kws_str = "、".join(new_entry.get("keywords", [])[:7])
            embed = discord.Embed(
                title="🔄 已完成《殭屍學園》知識庫熱修補覆寫！",
                description=truncate_embed_text(
                    f"• **已軟屏蔽舊條目**：`{replaced_entry['id']}` 【{replaced_entry['name_zh']}】\n"
                    f"• **生效中新條目**：`{new_entry['id']}` 【{new_entry['name_zh']}】\n"
                    f"• **繼承與索引詞**：`{kws_str}`\n"
                    f"• **整理基準日期**：{new_entry.get('updated_at', '')}\n\n"
                    "⚡ 後續查詢與 AI 自動預檢索將優先採用新條目內容。"
                ),
                color=0xF39C12,
            )
            embed.set_footer(text=f"💡 若要撤銷此覆寫並還原舊條目，請輸入 !wiki del {new_entry['id']}")
            await ctx.reply(embed=embed, mention_author=False)
            return

        if sub in ("add", "import", "新增", "匯入", "補充"):
            raw_text_sources: list[str] = []
            default_title = ""

            for att in ctx.message.attachments:
                if att.filename.lower().endswith((".txt", ".md")):
                    if att.size and att.size > MAX_TEXT_ATTACHMENT_BYTES:
                        await ctx.reply(f"⚠️ 附件 `{att.filename}` 超過 2MB 上限，已略過。", mention_author=False)
                        continue
                    try:
                        file_bytes = await att.read()
                        file_str = file_bytes.decode("utf-8", errors="replace").strip()
                        if file_str:
                            raw_text_sources.append(file_str)
                            if not default_title:
                                default_title = Path(att.filename).stem
                    except Exception as e:
                        await ctx.reply(f"⚠️ 讀取附件 `{att.filename}` 失敗：{e}", mention_author=False)
                        return

            if rest:
                if raw_text_sources and "\n" not in rest and "|" not in rest and "｜" not in rest and "【" not in rest:
                    default_title = rest.strip()
                else:
                    raw_text_sources.insert(0, rest)

            combined_text = "\n\n".join(raw_text_sources).strip()
            if not combined_text:
                await ctx.reply(
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
                    "在訊息框輸入 `!wiki import [主題名稱]` 並**夾帶 `.txt` 或 `.md` 檔案**發送，系統會自動分段建立索引！",
                    mention_author=False,
                )
                return

            parsed_entries = game_wiki_engine.parse_community_wiki_blocks(combined_text, default_title=default_title)
            if not parsed_entries:
                await ctx.reply("⚠️ 無法從輸入內容解析出有效條目，請確認文本不為空。", mention_author=False)
                return

            parsed_entries = await game_wiki_engine.enrich_community_entries_with_ai(
                self.gemini_client,
                self.settings.models_to_try,
                parsed_entries,
            )

            author_display = getattr(ctx.author, "display_name", str(ctx.author))
            added = game_wiki_engine.add_community_wiki_entries(parsed_entries, author_name=author_display)
            stats = game_wiki_engine.get_wiki_stats()

            preview_lines = []
            for item in added[:8]:
                kws_str = "、".join(item["keywords"][:7])
                preview_lines.append(f"• **`{item['id']}`** 【{item['name_zh']}】（索引詞：`{kws_str}`）")
            if len(added) > 8:
                preview_lines.append(f"…以及其他 {len(added) - 8} 筆條目")

            embed = discord.Embed(
                title=f"✅ 成功補充 {len(added)} 筆《殭屍學園》知識庫資料！",
                description=(
                    "已寫入本地知識庫並完成熱重載，現在直接問機器人或用 `!wiki` 都查得到囉：\n\n"
                    + "\n".join(preview_lines)
                    + f"\n\n📚 目前《殭屍學園》知識庫總收錄數：**{stats['total']}** 筆"
                ),
                color=0x2ECC71,
            )
            embed.set_footer(text="💡 輸入 !wiki list 可檢視所有群友補充條目；輸入 !wiki del <編號> 可刪除誤植條目。")
            await ctx.reply(embed=embed, mention_author=False)
            return

        search_res = game_wiki_engine.search_wiki_interactive(raw_arg)
        mode = search_res["mode"]

        if mode == "category":
            browse_view = WikiBrowseView(category_key=search_res["category_key"], page=0)
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
                embed = discord.Embed(
                    title=f"🔀 《殭屍學園》查詢消歧義：{raw_arg[:50]}",
                    description=truncate_embed_text("\n\n".join(dynamic_notes), max_len=3800),
                    color=0xF39C12,
                )
                await ctx.send(embed=embed)
                return

        empty_embed = discord.Embed(
            title=f"🔍 《殭屍學園》查詢：{raw_arg[:50]}",
            description=(
                f"目前知識庫尚未收錄與「**{raw_arg}**」相符的《殭屍學園》條目。\n\n"
                "💡 **你可以這樣試試**：\n"
                "• 縮短關鍵字或改查韓文縮寫（如 `!wiki 燒瓶 2技`、`!wiki ㅎㅇ`、`!wiki 椅子`）\n"
                "• 輸入 `!wiki 目錄` 或使用下方分類選單瀏覽全部已收錄資料\n"
                "• 使用 `!wiki add <關鍵字> | <內容>` 或 `!wiki import` 補充此條目"
            ),
            color=0x95A5A6,
        )
        overview_view = WikiOverviewView()
        sent_msg = await ctx.send(embed=empty_embed, view=overview_view)
        overview_view.message = sent_msg
