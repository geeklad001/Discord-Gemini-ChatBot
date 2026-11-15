"""Game Wiki Cog: !wiki command for Zombie High School Contextual-LightRAG knowledge base."""

import logging
from pathlib import Path
from typing import Optional

import discord
from discord.ext import commands
from google import genai

import game_wiki_engine
from core.chat_pipeline import is_target_channel
from core.config import MAX_TEXT_ATTACHMENT_BYTES, BotSettings, truncate_embed_text


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
                    + "\n\n🔍 **查詢用法**：\n"
                    "• `!wiki ㄷㅋㅈ` / `!wiki 클모` / `!wiki 올치어`（查韓服開房黑話與縮寫）\n"
                    "• `!wiki 南東進` / `!wiki 鄭東碩`（查角色設定與人際關係）\n"
                    "• `!wiki 進化感染` / `!wiki APO` / `!wiki Reboot`（查模式與重製版機制）\n\n"
                    "📥 **群友補充文本／擴充資料庫用法**：\n"
                    "• **單筆新增**：`!wiki add 關鍵字1, 關鍵字2 | 條目內容說明`\n"
                    "• **批次貼文或傳檔**：輸入 `!wiki import [主題名稱]` 並在後方換行貼上長文（支援 `【標題】內容`、`# 標題` 分段或整篇攻略），或直接**夾帶 `.txt` / `.md` 檔案上傳**！\n"
                    "• **管理補充條目**：`!wiki list`（查看群友補充清單）、`!wiki del <編號>`（刪除條目）"
                ),
                color=0x2ECC71,
            )
            await ctx.send(embed=embed)
            return

        parts = raw_arg.split(maxsplit=1)
        sub = parts[0].lower()
        rest = parts[1].strip() if len(parts) > 1 else ""

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

        res_text = game_wiki_engine.lookup_game_wiki_tool_impl(raw_arg)
        embed = discord.Embed(
            title=f"🔍 《殭屍學園》查詢：{raw_arg[:50]}",
            description=truncate_embed_text(res_text),
            color=0x3498DB,
        )
        await ctx.send(embed=embed)
