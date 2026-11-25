"""Interactive UI Views and Embed builders for the Zombie High School Wiki (!wiki)."""

import asyncio
from typing import Optional

import discord

from core.config import truncate_embed_text
from engines import game_wiki_engine


async def _disable_view_on_timeout(view: discord.ui.View) -> None:
    """停用逾時 `!wiki` 互動面板上的元件，避免舊訊息按鈕殘留。"""
    for child in view.children:
        if hasattr(child, "disabled"):
            child.disabled = True
    msg = getattr(view, "message", None)
    if msg is not None:
        try:
            await msg.edit(view=view)
        except Exception:
            pass


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


def build_wiki_overview_embed(stats: dict) -> discord.Embed:
    """Build the main `!wiki` statistics & usage overview Embed."""
    cat_lines = []
    for cat_key, label in game_wiki_engine.CATEGORY_LABELS.items():
        cnt = stats["by_category"].get(cat_key, 0)
        if cnt > 0 or cat_key == "community_contrib":
            cat_lines.append(f"• **{label}**：共 **{cnt}** 筆")
    return discord.Embed(
        title="🧟 《殭屍學園 (좀비고등학교)》知識庫",
        description=truncate_embed_text(
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
            "• **管理補充條目**：`!wiki list`（查看群友補充清單）、`!wiki del <編號>`（刪除條目或撤銷覆寫）",
            max_len=3800,
        ),
        color=0x2ECC71,
    )


def build_wiki_list_embed(contribs: list[dict]) -> discord.Embed:
    """Build the `!wiki list` community contributions Embed."""
    lines = []
    for item in contribs[-25:]:
        kws_preview = "、".join(item.get("keywords", [])[:5])
        body_lines = [ln for ln in item.get("body", "").splitlines() if not ln.startswith("【《殭屍學園》")]
        joined_body = " ".join(body_lines)
        preview = joined_body[:80] + ("..." if len(joined_body) > 80 else "")
        lines.append(f"• **`{item['id']}`** 【{item['name_zh']}】（關鍵字：`{kws_preview}`）\n  └ {preview}")
    embed = discord.Embed(
        title=f"📥 群友補充《殭屍學園》知識庫清單（共 {len(contribs)} 筆）",
        description=truncate_embed_text("\n".join(lines)),
        color=0x2ECC71,
    )
    embed.set_footer(text="💡 如需刪除特定補充條目，請輸入 !wiki del <編號>（例如 !wiki del contrib_1）")
    return embed


def build_wiki_replace_embed(replaced_entry: dict, new_entry: dict) -> discord.Embed:
    """Build the `!wiki replace` hotfix confirmation Embed."""
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
    return embed


def build_wiki_import_result_embed(added: list[dict], total_count: int) -> discord.Embed:
    """Build the `!wiki add` / `!wiki import` confirmation Embed."""
    preview_lines = []
    for item in added[:8]:
        kws_str = "、".join(item["keywords"][:7])
        preview_lines.append(f"• **`{item['id']}`** 【{item['name_zh']}】（索引詞：`{kws_str}`）")
    if len(added) > 8:
        preview_lines.append(f"…以及其他 {len(added) - 8} 筆條目")

    embed = discord.Embed(
        title=f"✅ 成功補充 {len(added)} 筆《殭屍學園》知識庫資料！",
        description=truncate_embed_text(
            "已寫入本地知識庫並完成熱重載，現在直接問機器人或用 `!wiki` 都查得到囉：\n\n"
            + "\n".join(preview_lines)
            + f"\n\n📚 目前《殭屍學園》知識庫總收錄數：**{total_count}** 筆",
            max_len=3800,
        ),
        color=0x2ECC71,
    )
    embed.set_footer(text="💡 輸入 !wiki list 可檢視所有群友補充條目；輸入 !wiki del <編號> 可刪除誤植條目。")
    return embed


def build_wiki_disambiguation_embed(query: str, dynamic_notes: list[str]) -> discord.Embed:
    """Build the disambiguation Embed when search yields dynamic notes without entries."""
    return discord.Embed(
        title=f"🔀 《殭屍學園》查詢消歧義：{query[:50]}",
        description=truncate_embed_text("\n\n".join(dynamic_notes), max_len=3800),
        color=0xF39C12,
    )


def build_wiki_empty_search_embed(query: str) -> discord.Embed:
    """Build the empty search fallback Embed."""
    return discord.Embed(
        title=f"🔍 《殭屍學園》查詢：{query[:50]}",
        description=truncate_embed_text(
            f"目前知識庫尚未收錄與「**{query}**」相符的《殭屍學園》條目。\n\n"
            "💡 **你可以這樣試試**：\n"
            "• 縮短關鍵字或改查韓文縮寫（如 `!wiki 燒瓶 2技`、`!wiki ㅎㅇ`、`!wiki 椅子`）\n"
            "• 輸入 `!wiki 目錄` 或使用下方分類選單瀏覽全部已收錄資料\n"
            "• 使用 `!wiki add <關鍵字> | <內容>` 或 `!wiki import` 補充此條目",
            max_len=3800,
        ),
        color=0x95A5A6,
    )


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
        await _disable_view_on_timeout(self)


class WikiBrowseView(discord.ui.View):
    """《殭屍學園》知識庫分類目錄瀏覽器（支援分類切換、分頁與點選直開條目卡片）。"""

    def __init__(
        self,
        *,
        category_key: str = "evolution",
        page: int = 0,
        per_page: int = 10,
        timeout: float = 180.0,
        categories: Optional[list[tuple[str, str, int]]] = None,
        entries: Optional[list[dict]] = None,
    ):
        super().__init__(timeout=timeout)
        self.per_page = per_page
        self.categories = categories if categories is not None else _resolve_browse_categories()
        valid_keys = {k for k, _, _ in self.categories}
        if category_key not in valid_keys and self.categories:
            category_key = self.categories[0][0]
        self.category_key = category_key
        self._entries: Optional[list[dict]] = entries
        self.page = max(0, page)
        self.message: Optional[discord.Message] = None
        self._rebuild_items()

    @classmethod
    async def create(
        cls,
        *,
        category_key: str = "evolution",
        page: int = 0,
        per_page: int = 10,
        timeout: float = 180.0,
    ) -> "WikiBrowseView":
        categories = await asyncio.to_thread(_resolve_browse_categories)
        valid_keys = {k for k, _, _ in categories}
        if category_key not in valid_keys and categories:
            category_key = categories[0][0]
        entries = await asyncio.to_thread(game_wiki_engine.list_wiki_entries_by_category, category_key)
        return cls(
            category_key=category_key,
            page=page,
            per_page=per_page,
            timeout=timeout,
            categories=categories,
            entries=entries,
        )

    async def _reload_entries(self) -> None:
        self._entries = await asyncio.to_thread(game_wiki_engine.list_wiki_entries_by_category, self.category_key)

    def _current_entries(self) -> list[dict]:
        if self._entries is None:
            self._entries = game_wiki_engine.list_wiki_entries_by_category(self.category_key)
        return self._entries

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
        await self._reload_entries()
        await self._refresh(interaction)

    async def _on_open_entry(self, interaction: discord.Interaction) -> None:
        offset = int(interaction.data["values"][0])
        await self._reload_entries()
        entries = self._current_entries()
        page_entries, _, _ = self._current_page_entries(entries)
        if not page_entries:
            await self._refresh(interaction)
            return
        offset = max(0, min(offset, len(page_entries) - 1))
        cat_label = game_wiki_engine.CATEGORY_LABELS.get(self.category_key, self.category_key)
        focused_snippets = await asyncio.to_thread(
            lambda: {
                e["id"]: game_wiki_engine.extract_focused_entry_body(e, e.get("name_zh") or e["id"], max_chars=1200)
                for e in page_entries
            }
        )
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
        await self._reload_entries()
        await self._refresh(interaction)

    async def _on_next(self, interaction: discord.Interaction) -> None:
        await self._reload_entries()
        entries = self._current_entries()
        total_pages = max(1, (len(entries) + self.per_page - 1) // self.per_page)
        if self.page < total_pages - 1:
            self.page += 1
        await self._refresh(interaction)

    async def on_timeout(self) -> None:
        await _disable_view_on_timeout(self)


class WikiOverviewView(discord.ui.View):
    """附掛於 `!wiki` 總覽或查無結果畫面上的分類目錄快捷選單。"""

    def __init__(
        self,
        timeout: float = 180.0,
        categories: Optional[list[tuple[str, str, int]]] = None,
    ):
        super().__init__(timeout=timeout)
        self.message: Optional[discord.Message] = None
        resolved_categories = categories if categories is not None else _resolve_browse_categories()
        if resolved_categories:
            options = [
                discord.SelectOption(
                    label=f"{label} ({cnt} 筆)"[:95],
                    value=cat_key,
                )
                for cat_key, label, cnt in resolved_categories[:25]
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

    @classmethod
    async def create(cls, timeout: float = 180.0) -> "WikiOverviewView":
        categories = await asyncio.to_thread(_resolve_browse_categories)
        return cls(timeout=timeout, categories=categories)

    async def _on_select_category(self, interaction: discord.Interaction) -> None:
        cat_key = interaction.data["values"][0]
        browse_view = await WikiBrowseView.create(category_key=cat_key, page=0, timeout=self.timeout)
        target_msg = self.message
        browse_view.message = target_msg
        await interaction.response.edit_message(embed=browse_view.build_embed(), view=browse_view)
        self.message = None
        self.stop()

    async def on_timeout(self) -> None:
        await _disable_view_on_timeout(self)
