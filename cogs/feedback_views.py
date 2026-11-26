"""Interactive UI Views and Embed builders for the public !feedback board."""

import asyncio
from typing import Optional

import discord

from core.config import truncate_embed_text
from engines import assistant_db

_STATUS_SHORT_EMOJI = {
    "open": "🟡",
    "triaged": "🔵",
    "resolved": "✅",
    "wontfix": "⚪",
}

_STATUS_FILTER_LABELS = {
    "open": "待處理／排程中",
    "triaged": "僅已確認／排程中",
    "resolved": "僅已解決",
    "wontfix": "僅暫不處理／關閉",
    "all": "全部狀態",
}


def build_feedback_list_embed(
    reports: list[dict],
    *,
    page: int = 0,
    page_size: int = 8,
    status_filter: str = "open",
    category_filter: Optional[str] = None,
    counts: Optional[dict[str, int]] = None,
) -> discord.Embed:
    """建構公開回饋與建議看板 Embed（嚴格隱藏所有回報者身分，不提及任何成員）。"""
    safe_page_size = max(1, min(page_size, 15))
    total_items = len(reports)
    total_pages = max(1, (total_items + safe_page_size - 1) // safe_page_size)
    safe_page = max(0, min(page, total_pages - 1))
    start = safe_page * safe_page_size
    page_items = reports[start : start + safe_page_size]

    counts = counts or {}
    open_cnt = counts.get("open", 0)
    triaged_cnt = counts.get("triaged", 0)
    resolved_cnt = counts.get("resolved", 0)
    status_desc = _STATUS_FILTER_LABELS.get(status_filter or "open", status_filter or "open")
    cat_desc = (
        assistant_db.FEEDBACK_CATEGORY_LABELS.get(category_filter, category_filter)
        if category_filter
        else "全部分類"
    )

    header = (
        f"📊 **統計**：🟡 待處理 `{open_cnt}` ｜ 🔵 排程中 `{triaged_cnt}` ｜ ✅ 已解決 `{resolved_cnt}`\n"
        f"🔎 **篩選**：`{status_desc}` ／ `{cat_desc}`（共 **{total_items}** 筆）"
    )

    if not page_items:
        body = "目前這個分類與狀態下還沒有任何回饋紀錄。\n💬 可直接用自然語言告訴我「建議...」或「這裡有錯」，或輸入 `!feedback <內容>` 提交。"
        color = 0x95A5A6
    else:
        blocks: list[str] = []
        for item in page_items:
            st = str(item.get("status") or "open")
            st_icon = _STATUS_SHORT_EMOJI.get(st, "📝")
            cat_key = str(item.get("category") or "other")
            cat_label = assistant_db.FEEDBACK_CATEGORY_LABELS.get(cat_key, cat_key)
            summary = str(item.get("summary") or "").strip()
            detail = str(item.get("detail") or "").strip()
            created_short = str(item.get("created_at") or "")[5:16]
            line = f"{st_icon} **#{item['id']}** `[{cat_label}]` **{summary}**"

            meta_bits: list[str] = []
            if created_short:
                meta_bits.append(f"🕒 {created_short}")
            jump_url = str(item.get("message_jump_url") or "").strip()
            if jump_url:
                meta_bits.append(f"[原訊息]({jump_url})")
            if detail and detail != summary:
                snippet = " ".join(detail.split())
                if len(snippet) > 90:
                    snippet = snippet[:87] + "..."
                line += f"\n└ {snippet}"
            note = str(item.get("resolution_note") or "").strip()
            if note:
                short_note = note if len(note) <= 80 else note[:77] + "..."
                meta_bits.append(f"💬 備註：{short_note}")
            if meta_bits:
                line += f"\n└ {' ｜ '.join(meta_bits)}"
            blocks.append(line)

        body = "\n\n".join(blocks)
        color = 0x5865F2 if status_filter in ("open", "triaged", "all") else 0x2ECC71

    embed = discord.Embed(
        title="📮 非即時使用者回饋與建議看板",
        description=truncate_embed_text(f"{header}\n\n{body}", max_len=3800),
        color=color,
    )
    embed.set_footer(
        text=f"第 {safe_page + 1} / {total_pages} 頁 ｜ 查看詳情：!feedback view <編號> ｜ 提交：直接告訴我建議/錯誤或 !feedback <內容>"
    )
    return embed


def build_feedback_detail_embed(report: dict) -> discord.Embed:
    """建構單筆回饋與建議詳情 Embed（嚴格隱藏所有回報者身分）。"""
    st = str(report.get("status") or "open")
    st_label = assistant_db.FEEDBACK_STATUS_LABELS.get(st, st)
    cat_key = str(report.get("category") or "other")
    cat_label = assistant_db.FEEDBACK_CATEGORY_LABELS.get(cat_key, cat_key)
    summary = str(report.get("summary") or "").strip()
    detail = str(report.get("detail") or "").strip()
    excerpt = str(report.get("context_excerpt") or "").strip()
    jump_url = str(report.get("message_jump_url") or "").strip()
    note = str(report.get("resolution_note") or "").strip()

    parts = [
        f"**狀態**：{st_label} ｜ **分類**：{cat_label}",
        f"**內容說明**：\n{detail or summary}",
    ]
    if excerpt:
        quoted = "\n".join(f"> {ln}" for ln in excerpt.splitlines()[:8])
        parts.append(f"**現場脈絡摘錄**：\n{quoted}")
    if jump_url:
        parts.append(f"**來源訊息**：[前往當時對話位置]({jump_url})")
    if note:
        parts.append(f"**處理備註**：{note}")

    color = {
        "open": 0xF1C40F,
        "triaged": 0x3498DB,
        "resolved": 0x2ECC71,
        "wontfix": 0x95A5A6,
    }.get(st, 0x5865F2)

    title = f"📮 回饋詳情 #{report['id']}｜{summary}"
    if len(title) > 250:
        title = title[:247] + "..."

    embed = discord.Embed(
        title=title,
        description=truncate_embed_text("\n\n".join(parts), max_len=3800),
        color=color,
    )
    created_at = str(report.get("created_at") or "")
    updated_at = str(report.get("updated_at") or "")
    source_label = {
        "ai_tool": "對話自動收錄",
        "command": "指令提交",
    }.get(report.get("source"), "其他")
    embed.set_footer(text=f"建立於 {created_at} ｜ 最後更新 {updated_at} ｜ 來源：{source_label}")
    return embed


class FeedbackListView(discord.ui.View):
    """公開互動分頁與篩選視圖（全體成員皆可操作，匿名展示回饋進度）。"""

    def __init__(
        self,
        *,
        status_filter: str = "open",
        category_filter: Optional[str] = None,
        page: int = 0,
        page_size: int = 8,
        timeout: float = 180.0,
    ):
        super().__init__(timeout=timeout)
        self.status_filter = status_filter or "open"
        self.category_filter = category_filter
        self.page = page
        self.page_size = page_size
        self.message: Optional[discord.Message] = None
        self.reports: list[dict] = []
        self.counts: dict[str, int] = {}
        self._build_components()

    def _build_components(self) -> None:
        self.clear_items()

        status_select = discord.ui.Select(
            placeholder="篩選處理狀態...",
            min_values=1,
            max_values=1,
            row=0,
            options=[
                discord.SelectOption(label="待處理／排程中", value="open", emoji="🟡", default=self.status_filter == "open"),
                discord.SelectOption(label="已確認／排程中", value="triaged", emoji="🔵", default=self.status_filter == "triaged"),
                discord.SelectOption(label="已解決", value="resolved", emoji="✅", default=self.status_filter == "resolved"),
                discord.SelectOption(label="暫不處理／關閉", value="wontfix", emoji="⚪", default=self.status_filter == "wontfix"),
                discord.SelectOption(label="全部狀態", value="all", emoji="📋", default=self.status_filter == "all"),
            ],
        )
        status_select.callback = self._on_status_change
        self.add_item(status_select)

        cat_options = [
            discord.SelectOption(
                label="全部分類",
                value="__all__",
                emoji="🗂️",
                default=self.category_filter is None,
            )
        ]
        for cat_key in assistant_db.FEEDBACK_CATEGORIES:
            cat_label = assistant_db.FEEDBACK_CATEGORY_LABELS.get(cat_key, cat_key)
            cat_options.append(
                discord.SelectOption(
                    label=cat_label,
                    value=cat_key,
                    default=self.category_filter == cat_key,
                )
            )
        cat_select = discord.ui.Select(
            placeholder="篩選回饋分類...",
            min_values=1,
            max_values=1,
            row=1,
            options=cat_options,
        )
        cat_select.callback = self._on_category_change
        self.add_item(cat_select)

        total_pages = max(1, (len(self.reports) + self.page_size - 1) // self.page_size)
        prev_btn = discord.ui.Button(
            label="⬅️ 上一頁",
            style=discord.ButtonStyle.secondary,
            disabled=self.page <= 0,
            row=2,
        )
        prev_btn.callback = self._on_prev_page
        self.add_item(prev_btn)

        next_btn = discord.ui.Button(
            label="➡️ 下一頁",
            style=discord.ButtonStyle.secondary,
            disabled=self.page >= total_pages - 1,
            row=2,
        )
        next_btn.callback = self._on_next_page
        self.add_item(next_btn)

    async def reload_data(self) -> discord.Embed:
        """從資料庫載入最新回饋清單與計數並重建按鈕狀態。"""
        reports = await asyncio.to_thread(
            assistant_db.list_feedback_reports,
            status=self.status_filter,
            category=self.category_filter,
            limit=200,
        )
        counts = await asyncio.to_thread(assistant_db.get_feedback_counts)
        self.reports = reports
        self.counts = counts
        total_pages = max(1, (len(self.reports) + self.page_size - 1) // self.page_size)
        self.page = max(0, min(self.page, total_pages - 1))
        self._build_components()
        return build_feedback_list_embed(
            self.reports,
            page=self.page,
            page_size=self.page_size,
            status_filter=self.status_filter,
            category_filter=self.category_filter,
            counts=self.counts,
        )

    async def _refresh_interaction(self, interaction: discord.Interaction) -> None:
        embed = await self.reload_data()
        await interaction.response.edit_message(
            embed=embed,
            view=self,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    async def _on_status_change(self, interaction: discord.Interaction) -> None:
        selected = (interaction.data or {}).get("values", ["open"])[0]
        self.status_filter = selected
        self.page = 0
        await self._refresh_interaction(interaction)

    async def _on_category_change(self, interaction: discord.Interaction) -> None:
        selected = (interaction.data or {}).get("values", ["__all__"])[0]
        self.category_filter = None if selected == "__all__" else selected
        self.page = 0
        await self._refresh_interaction(interaction)

    async def _on_prev_page(self, interaction: discord.Interaction) -> None:
        if self.page > 0:
            self.page -= 1
        await self._refresh_interaction(interaction)

    async def _on_next_page(self, interaction: discord.Interaction) -> None:
        self.page += 1
        await self._refresh_interaction(interaction)

    async def on_timeout(self) -> None:
        for child in self.children:
            if hasattr(child, "disabled"):
                child.disabled = True
        if self.message is not None:
            try:
                await self.message.edit(view=self)
            except Exception:
                pass
