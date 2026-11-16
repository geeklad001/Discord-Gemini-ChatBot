"""Flashcard Cog: interactive SRS flashcards (!card, !decks) and Kotoba quiz (!quiz)."""

import asyncio
import json
import logging
import random
import re
from pathlib import Path
from typing import Optional

import discord
from discord.ext import commands
from google import genai
from google.genai import types

import assistant_db
import flashcard_engine
from core.chat_pipeline import is_target_channel
from core.config import (
    MAX_APKG_ATTACHMENT_BYTES,
    MAX_TEXT_ATTACHMENT_BYTES,
    BotSettings,
    truncate_embed_text,
)
from core.llm_client import generate_with_fallback, get_safety_settings


def check_quiz_answer_match(user_text: str, card: dict) -> bool:
    """檢查使用者回答是否命中單字卡讀音或中文意思。"""
    ans = user_text.strip().lower()
    if not ans:
        return False

    ans_clean = re.sub(r"[\[\]\(\)（）\s。！？~～\.,!]", "", ans)
    if not ans_clean:
        return False

    card_reading = card.get("reading", "").strip().lower()
    card_reading_clean = re.sub(r"[\[\]\(\)（）\s]", "", card_reading)
    if card_reading_clean and ans_clean == card_reading_clean:
        return True

    card_front_clean = re.sub(r"\s+", "", card.get("front", "").strip().lower())
    if ans_clean == card_front_clean:
        return True

    card_back = card.get("back", "").strip().lower()
    tokens = [t.strip() for t in re.split(r"[/,;、，；\(\)（）\s]+", card_back) if len(t.strip()) >= 1]

    for token in tokens:
        if len(token) == 1:
            if ans_clean == token:
                return True
        elif token == ans_clean or token in ans_clean or ans_clean in token:
            if len(ans_clean) >= 2 or ans_clean == token:
                return True

    return False


class FlashcardStudyView(discord.ui.View):
    """日語單字卡互動複習檢視 (支援翻面、TTS 朗讀、AI 解說、SRS 熟練度更新與下拉一鍵換牌組)。"""

    def __init__(
        self,
        bot: commands.Bot,
        gemini_client: genai.Client,
        settings: BotSettings,
        logger: logging.Logger,
        user_id: int,
        deck_id: int,
        card: dict,
        is_flipped: bool = False,
    ):
        super().__init__(timeout=300)
        self.bot = bot
        self.gemini_client = gemini_client
        self.settings = settings
        self.logger = logger
        self.user_id = user_id
        self.deck_id = deck_id
        self.card = card
        self.is_flipped = is_flipped
        self.update_components()

    def update_components(self):
        self.clear_items()
        tts_label = "🔊 真人發音" if self.card.get("audio_filename") else "🔊 發音朗讀"

        if not self.is_flipped:
            btn_flip = discord.ui.Button(label="👁️ 翻面查看", style=discord.ButtonStyle.primary, custom_id="fc_flip", row=0)
            btn_flip.callback = self.on_flip
            self.add_item(btn_flip)
        else:
            btn_again = discord.ui.Button(label="🔴 待複習 (Again)", style=discord.ButtonStyle.danger, custom_id="fc_again", row=0)
            btn_again.callback = self.on_again
            self.add_item(btn_again)

            btn_good = discord.ui.Button(label="🟢 記住了 (Good)", style=discord.ButtonStyle.success, custom_id="fc_good", row=0)
            btn_good.callback = self.on_good
            self.add_item(btn_good)

        btn_tts = discord.ui.Button(label=tts_label, style=discord.ButtonStyle.secondary, custom_id="fc_tts", row=0)
        btn_tts.callback = self.on_tts
        self.add_item(btn_tts)

        btn_ai = discord.ui.Button(label="✨ AI 家教解說", style=discord.ButtonStyle.secondary, custom_id="fc_ai", row=0)
        btn_ai.callback = self.on_ai
        self.add_item(btn_ai)

        btn_next = discord.ui.Button(label="⏭️ 下一張", style=discord.ButtonStyle.secondary, custom_id="fc_next", row=0)
        btn_next.callback = self.on_next
        self.add_item(btn_next)

        decks = flashcard_engine.list_decks()[:25]
        options = []
        for d in decks:
            is_cur = d["id"] == self.deck_id
            label = f"{'📌 ' if is_cur else ''}{d['name'][:22]}"
            desc = f"共 {d['card_count']} 張卡片" + (" (目前牌組)" if is_cur else "")
            options.append(discord.SelectOption(
                label=label,
                value=str(d["id"]),
                description=desc[:50],
                default=is_cur,
            ))

        if options:
            select = discord.ui.Select(
                placeholder="📂 移至其他牌組...",
                min_values=1,
                max_values=1,
                options=options,
                row=1,
            )
            select.callback = self.on_select_deck
            self.add_item(select)

    def build_embed(self) -> discord.Embed:
        deck = flashcard_engine.get_deck(str(self.deck_id))
        deck_name = deck["name"] if deck else "預設牌組"

        with assistant_db.get_conn() as conn:
            row = conn.execute(
                "SELECT box, review_count FROM flashcard_progress WHERE user_id = ? AND card_id = ?",
                (self.user_id, self.card["id"]),
            ).fetchone()
            box_lvl = row[0] if row else 1
            rev_cnt = row[1] if row else 0

        if not self.is_flipped:
            desc = f"# {self.card['front']}\n"
            if self.card.get("pos"):
                desc += f"*{self.card['pos']}*\n"
            desc += "\n點擊下方「👁️ 翻面查看」確認假名讀音與中文意思。"

            embed = discord.Embed(
                title=f"📖 日語單字卡 ｜ {deck_name}",
                description=desc,
                color=0x5865F2,
            )
            if self.card.get("notes"):
                embed.add_field(name="💡 提示", value=self.card["notes"], inline=False)
        else:
            desc = f"# {self.card['front']}\n\n"
            reading_line = f"**讀音**：{('【' + self.card['reading'] + '】') if self.card.get('reading') else '（無）'}"
            if self.card.get("pitch"):
                reading_line += f"  *(音調：{self.card['pitch']})*"
            desc += reading_line + "\n"
            if self.card.get("pos"):
                desc += f"**詞性**：{self.card['pos']}\n"
            desc += f"**意思**：{self.card['back']}"

            embed = discord.Embed(
                title=f"📖 日語單字卡 ｜ {deck_name}",
                description=desc,
                color=0x57F287,
            )
            if self.card.get("example_jp"):
                ex_text = f"> {self.card['example_jp']}"
                if self.card.get("example_zh"):
                    ex_text += f"\n> *{self.card['example_zh']}*"
                embed.add_field(name="📝 例句", value=ex_text, inline=False)
            if self.card.get("notes"):
                embed.add_field(name="💡 備註/補語", value=self.card["notes"], inline=False)

        embed.set_footer(text=f"卡片 ID: #{self.card['id']} ｜ 熟練度: 📦 箱子 {box_lvl} ｜ 複習次數: {rev_cnt}")
        if self.card.get("image_filename"):
            img_path = flashcard_engine.MEDIA_DIR / self.card["image_filename"]
            if img_path.is_file():
                embed.set_image(url=f"attachment://{img_path.name}")
        return embed

    async def on_flip(self, interaction: discord.Interaction):
        self.is_flipped = True
        self.update_components()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    async def on_tts(self, interaction: discord.Interaction):
        audio_filename = self.card.get("audio_filename")
        if audio_filename:
            audio_path = flashcard_engine.get_card_audio_path(self.card)
            if audio_path and audio_path.is_file():
                try:
                    await interaction.response.send_message(
                        content=f"🔊 **{self.card['front']}** 的真人原音：",
                        file=discord.File(str(audio_path), filename=audio_path.name),
                        ephemeral=True,
                    )
                    return
                except Exception as e:
                    self.logger.warning(f"發送原生音訊失敗: {e}")

        text_to_speak = self.card.get("reading") or self.card.get("front")
        tts_ch = self.bot.get_channel(self.settings.tts_channel_id) if self.settings.tts_channel_id else None
        if tts_ch:
            try:
                await tts_ch.send(text_to_speak)
                await interaction.response.send_message(
                    f"🔊 已傳送「**{text_to_speak}**」至 HornBot 朗讀頻道！",
                    ephemeral=True,
                )
            except Exception as e:
                await interaction.response.send_message(f"⚠️ 朗讀發送失敗：{e}", ephemeral=True)
        else:
            await interaction.response.send_message(
                f"🔊 朗讀發音：`{text_to_speak}`\n（目前無內建音檔且未設置獨立 TTS 專用頻道）",
                ephemeral=True,
            )

    async def on_ai(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        front = self.card.get("front", "")
        reading = self.card.get("reading", "")
        back = self.card.get("back", "")

        prompt = (
            f"請扮演溫柔耐心的日語家教，針對單字「{front}」（讀音：{reading}，中文：{back}）進行 150 字內的精練解說：\n"
            f"1. 記憶技巧或聯想口訣\n"
            f"2. 日本人日常使用情境、細微語感或常見誤區\n"
            f"3. 1 句道地自然的口語短例句\n"
            f"請用親切溫暖、簡明易懂的繁體中文回答。"
        )
        try:
            resp, _ = await generate_with_fallback(
                client=self.gemini_client,
                models_to_try=self.settings.models_to_try,
                contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=0.7,
                    safety_settings=get_safety_settings(self.settings.disable_safety_settings),
                ),
                context_label="單字卡AI家教",
                logger=self.logger,
            )
            ai_text = resp.text.strip() if resp and resp.text else "抱歉，家教暫時無法提供解說～"
            ai_embed = discord.Embed(
                title=f"✨ AI 家教解說：{front} {('【' + reading + '】') if reading else ''}",
                description=truncate_embed_text(ai_text),
                color=0x9B59B6,
            )
            await interaction.followup.send(embed=ai_embed, ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"⚠️ AI 解說取得失敗：{e}", ephemeral=True)

    async def _advance_card(self, interaction: discord.Interaction, empty_message: str) -> None:
        next_c = flashcard_engine.get_random_card_from_deck(self.deck_id)
        if not next_c:
            await interaction.response.edit_message(content=empty_message, embed=None, view=None)
            return
        self.card = next_c
        self.is_flipped = False
        self.update_components()
        new_embed = self.build_embed()
        new_attachments = []
        if self.card.get("image_filename"):
            img_path = flashcard_engine.MEDIA_DIR / self.card["image_filename"]
            if img_path.is_file():
                new_attachments.append(discord.File(str(img_path), filename=img_path.name))
        await interaction.response.edit_message(embed=new_embed, view=self, attachments=new_attachments)

    async def on_again(self, interaction: discord.Interaction):
        flashcard_engine.record_review_result(interaction.user.id, self.card["id"], is_good=False)
        await self._advance_card(interaction, "🎉 這個牌組內沒有更多卡片囉！")

    async def on_good(self, interaction: discord.Interaction):
        flashcard_engine.record_review_result(interaction.user.id, self.card["id"], is_good=True)
        await self._advance_card(interaction, "🎉 太棒了！這個牌組內的卡片都看過一輪囉！")

    async def on_next(self, interaction: discord.Interaction):
        await self._advance_card(interaction, "🎉 這個牌組內沒有卡片可供學習！")

    async def on_select_deck(self, interaction: discord.Interaction):
        target_deck_id = int(interaction.data["values"][0])
        if target_deck_id == self.deck_id:
            await interaction.response.send_message("這張卡片本來就在這個牌組中喔！", ephemeral=True)
            return
        ok = flashcard_engine.move_card(self.card["id"], target_deck_id)
        target_deck = flashcard_engine.get_deck(str(target_deck_id))
        target_name = target_deck["name"] if target_deck else "新牌組"
        if ok:
            self.deck_id = target_deck_id
            self.card["deck_id"] = target_deck_id
            self.update_components()
            await interaction.response.send_message(
                f"✅ 已成功將卡片「**{self.card['front']}**」移至牌組：`{target_name}`！",
                ephemeral=True,
            )
            await interaction.message.edit(embed=self.build_embed(), view=self)
        else:
            await interaction.response.send_message("❌ 移動失敗，請稍後再試。", ephemeral=True)


class FlashcardCog(commands.Cog):
    """日語單字卡 (Anki/SRS) 與 Kotoba 多人限時搶答測驗系統。"""

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
        self.active_quiz_channels: set[int] = set()

    async def _send_import_result(self, ctx: commands.Context, res: dict, source_label: str = "") -> None:
        if not res.get("success"):
            await ctx.send(f"❌ 匯入失敗：{res.get('error', '未知錯誤')}")
            return

        if res.get("is_batch_all"):
            new_added = res.get("new_cards", res.get("total_cards", 0))
            deck_lines = []
            for d in res["decks_summary"]:
                new_cnt = d.get("new_count", d.get("card_count", 0))
                if new_cnt > 0:
                    deck_lines.append(f"• **{d['name']}**：現有 `{d['card_count']}` 張（本次新增 +`{new_cnt}`）")
                else:
                    deck_lines.append(f"• **{d['name']}**：現有 `{d['card_count']}` 張（已存在，無重複新增）")
            title_str = "🎉 Anki 全牌組一鍵全自動分類匯入成功！" if new_added > 0 else "ℹ️ Anki 全牌組掃描與去重檢查完畢！"
            desc_str = (
                f"已掃描 {source_label} 並維護 **{res['total_decks']}** 個等級牌組（本次新增 **{new_added}** 張單字卡）！\n\n"
                f"**各等級牌組清單：**\n" + "\n".join(deck_lines) + "\n\n"
                f"👉 隨時輸入 `!card <等級名稱>`（例如 `!card N5` 或 `!card N3高頻`）即可開始複習！\n"
                f"👉 或輸入 `!decks` 查看所有牌組清單～🌸"
            )
            embed = discord.Embed(
                title=title_str,
                description=truncate_embed_text(desc_str),
                color=0x57F287 if new_added > 0 else 0x3498DB,
            )
            await ctx.send(embed=embed)
            return

        new_cnt = res.get("imported_count", 0)
        if new_cnt == 0:
            await ctx.send(
                f"ℹ️ 本次未新增任何單字卡，牌組 **`{res['deck_name']}`** 內的單字卡均已存在（自動去重保護）！\n"
                f"隨時可以輸入 `!card {res['deck_name']}` 開始複習～🌸"
            )
            return

        media_info = f"（已同時提取 **{res.get('media_count', 0)}** 個真人原音/圖檔）" if res.get("media_count") else ""
        origin_txt = f"自 {source_label} " if source_label else ""
        await ctx.send(
            f"🎉 成功{origin_txt}匯入 **{new_cnt}** 張單字卡！{media_info}\n"
            f"📚 牌組名稱：`{res['deck_name']}`\n"
            f"隨時可以輸入 `!card {res['deck_name']}` 開始複習～🌸"
        )

    async def _handle_decks(self, ctx: commands.Context) -> None:
        decks = flashcard_engine.list_decks()
        user_deck = flashcard_engine.get_user_active_deck(ctx.author.id)
        lines = []
        for d in decks:
            is_active = d["id"] == user_deck["id"]
            tag = "📌 **[目前預設]** " if is_active else "▫️ "
            desc = f"（{d['description']}）" if d["description"] else ""
            lines.append(f"{tag}**{d['name']}**：共 **{d['card_count']}** 張卡片 {desc}")

        embed = discord.Embed(
            title="📚 日語單字卡牌組清單",
            description=truncate_embed_text("\n".join(lines)) if lines else "目前沒有任何牌組。",
            color=0x5865F2,
        )
        embed.set_footer(text="提示：使用 !card deck <牌組名> 切換預設牌組，或直接 !card <牌組名> 開始複習！")
        await ctx.send(embed=embed)

    async def _handle_set_deck(self, ctx: commands.Context, arg: Optional[str]) -> None:
        if not arg or not arg.strip():
            user_deck = flashcard_engine.get_user_active_deck(ctx.author.id)
            await ctx.send(f"📌 您目前的預設收集牌組為：`{user_deck['name']}`。\n若要切換，請輸入 `!card deck <牌組名或編號>`。")
            return
        target = flashcard_engine.get_deck(arg.strip())
        if not target:
            await ctx.send(f"❌ 找不到名稱或編號為 `{arg.strip()}` 的牌組！請先用 `!card decks` 查看清單。")
            return
        flashcard_engine.set_user_active_deck(ctx.author.id, target["id"])
        await ctx.send(f"📌 已將您的預設牌組切換為：`{target['name']}`！之後收錄的單字都會優先加入此處。")

    async def _handle_new_deck(self, ctx: commands.Context, arg: Optional[str]) -> None:
        if not arg or not arg.strip():
            await ctx.send("❓ 請輸入新牌組名稱，例如：`!card newdeck 日本旅行生活日常`")
            return
        parts = arg.strip().split(maxsplit=1)
        dname = parts[0]
        ddesc = parts[1] if len(parts) > 1 else ""
        d = flashcard_engine.get_or_create_deck(dname, ddesc, created_by=ctx.author.display_name)
        await ctx.send(f"✅ 牌組「**{d['name']}**」已就緒！可以使用 `!card deck {d['name']}` 設為預設。")

    async def _handle_del_deck(self, ctx: commands.Context, arg: Optional[str]) -> None:
        if not arg or not arg.strip():
            await ctx.send("❓ 請輸入要刪除的牌組名稱或編號。")
            return
        target = flashcard_engine.get_deck(arg.strip())
        if not target:
            await ctx.send(f"❌ 找不到牌組 `{arg.strip()}`。")
            return
        if "兩人的日常收藏" in target["name"]:
            await ctx.send("⚠️ 這是系統預設收納箱，不能刪除喔！")
            return
        async with ctx.channel.typing():
            await asyncio.to_thread(flashcard_engine.delete_deck, target["id"])
        await ctx.send(f"🗑️ 已刪除牌組「**{target['name']}**」及其所有卡片與關聯媒體檔。")

    async def _handle_import(self, ctx: commands.Context, arg: Optional[str]) -> None:
        raw_arg = (arg or "").strip()

        # 情況 A：直接在指令後方貼多行文字
        if not ctx.message.attachments and "\n" in raw_arg:
            lines = raw_arg.splitlines()
            first_line = lines[0].strip()
            if any(sep in first_line for sep in ("\t", " - ", "/", ":", "：", ",")):
                user_deck = flashcard_engine.get_user_active_deck(ctx.author.id)
                deck_label = user_deck["name"]
                text_body = raw_arg
            else:
                deck_label = first_line or "自訂文字匯入"
                text_body = "\n".join(lines[1:])

            res = flashcard_engine.import_text_cards(
                text_body,
                target_deck_name=deck_label,
                user_name=ctx.author.display_name,
            )
            await self._send_import_result(ctx, res)
            return

        # 情況 B：從本機 data/imports/ 讀取
        if not ctx.message.attachments and raw_arg:
            parts = raw_arg.split()
            candidate_file = Path(parts[0]).name
            local_path = flashcard_engine.IMPORTS_DIR / candidate_file
            subdeck_filter = None
            custom_deck_name = None

            if local_path.is_file():
                if len(parts) > 1:
                    subdeck_filter = parts[1]
                if len(parts) > 2:
                    custom_deck_name = " ".join(parts[2:])
            else:
                apkg_files = [
                    p for p in flashcard_engine.IMPORTS_DIR.iterdir()
                    if p.is_file() and p.suffix.lower() == ".apkg"
                ]
                if apkg_files:
                    local_path = apkg_files[0]
                    subdeck_filter = parts[0]
                    if len(parts) > 1:
                        custom_deck_name = " ".join(parts[1:])

            if local_path and local_path.is_file():
                fname = local_path.name.lower()
                async with ctx.channel.typing():
                    try:
                        if fname.endswith(".apkg"):
                            res = await asyncio.to_thread(
                                flashcard_engine.import_anki_apkg,
                                file_path=local_path,
                                subdeck_filter=subdeck_filter,
                                target_deck_name=custom_deck_name,
                                user_name=ctx.author.display_name,
                                extract_media=True,
                            )
                        elif fname.endswith((".txt", ".tsv", ".csv")):
                            text_content = local_path.read_text(encoding="utf-8", errors="ignore")
                            deck_label = custom_deck_name or local_path.stem
                            res = flashcard_engine.import_text_cards(
                                text_content,
                                target_deck_name=deck_label,
                                user_name=ctx.author.display_name,
                            )
                        else:
                            await ctx.send("⚠️ 檔案格式不支援，僅支援 `.apkg`、`.tsv`、`.csv` 或 `.txt`！")
                            return
                        await self._send_import_result(ctx, res, source_label=f"`data/imports/{local_path.name}`")
                    except Exception as e:
                        self.logger.error(f"匯入本機單字卡失敗: {e}", exc_info=True)
                        await ctx.send(f"❌ 讀取本機檔案時發生錯誤：{e}")
                return

        # 無附件且無匹配檔案：顯示指南
        if not ctx.message.attachments:
            local_files = flashcard_engine.list_local_import_files()
            local_lines = []
            for p in local_files[:10]:
                if p.suffix.lower() == ".apkg":
                    subdecks = flashcard_engine.get_anki_subdecks(p)
                    if subdecks:
                        subdeck_sample = "、".join(
                            s["title"].replace("🌸 JLPT ", "").replace("單字", "") for s in subdecks[:6]
                        )
                        if len(subdecks) > 6:
                            subdeck_sample += f" 等 {len(subdecks)} 個等級"
                        local_lines.append(
                            f"• `{p.name}`\n  └ 內含等級子牌組：{subdeck_sample}\n"
                            f"  └ 一鍵全自動分類：`!card import {p.name} all` 或 `!card import all`\n"
                            f"  └ 單一等級匯入：`!card import {p.name} N3高頻` 或 `!card import N5`"
                        )
                    else:
                        local_lines.append(f"• `{p.name}` ➔ 直接輸入 `!card import {p.name}` 即可匯入！")
                else:
                    local_lines.append(f"• `{p.name}` ➔ 直接輸入 `!card import {p.name}` 即可匯入！")

            local_hint = (
                "📂 **目前偵測到 `data/imports/` 資料夾內可直接匯入的檔案：**\n" + "\n".join(local_lines)
                if local_lines
                else "📂 *(目前本機 `data/imports/` 資料夾內尚無檔案，你隨時可以把 `.apkg` 或 `.txt` 丟進去)*"
            )
            guide_embed = discord.Embed(
                title="📥 單字卡批次匯入指南 (`!card import`)",
                description=(
                    "支援 **3 種超方便的匯入方式**，不一定要在 Discord 傳檔案喔！\n\n"
                    "**方式 1️⃣：直接在聊天室貼上多行文字（最簡單免檔案！）**\n"
                    "```text\n!card import 日本旅行\n桜 - さくら - 櫻花\n美味しい - おいしい - 好吃的\n乾杯 - かんぱい - 乾杯\n```\n"
                    "**方式 2️⃣：把檔案放進電腦的 `data/imports/` 資料夾（支援指定等級與原生發音！）**\n"
                    "直接把 Anki `.apkg` 丟進 `data/imports/` 資料夾，然後打：\n"
                    "`!card import 檔名.apkg [等級/子牌組名]`（例如 `!card import NEW-JLPT.apkg N3高頻`）\n\n"
                    "**方式 3️⃣：在 Discord 拖曳上傳檔案**\n"
                    "按聊天框左邊的 `➕` 上傳檔案，並在發送留言打：`!card import [自訂牌組名]`\n\n"
                    f"{local_hint}"
                ),
                color=0x5865F2,
            )
            await ctx.send(embed=guide_embed)
            return

        # 情況 C：Discord 附件
        att = ctx.message.attachments[0]
        fname = att.filename.lower()
        target_deck_name = raw_arg if raw_arg else None

        async with ctx.channel.typing():
            try:
                if fname.endswith(".apkg"):
                    if att.size and att.size > MAX_APKG_ATTACHMENT_BYTES:
                        await ctx.send(
                            f"❌ `.apkg` 檔案過大（超過 {MAX_APKG_ATTACHMENT_BYTES // (1024 * 1024)} MB 上限）！建議改放進本機 `data/imports/` 資料夾。"
                        )
                        return
                    file_bytes = await att.read()
                    res = await asyncio.to_thread(
                        flashcard_engine.import_anki_apkg_bytes,
                        file_bytes,
                        target_deck_name=target_deck_name,
                        user_name=ctx.author.display_name,
                    )
                    await self._send_import_result(ctx, res, source_label="Anki 牌組檔")
                elif fname.endswith((".txt", ".tsv", ".csv")):
                    if att.size and att.size > MAX_TEXT_ATTACHMENT_BYTES:
                        await ctx.send("❌ 文字檔超過 2MB 上限！")
                        return
                    file_bytes = await att.read()
                    text_content = file_bytes.decode("utf-8", errors="ignore")
                    deck_label = target_deck_name or Path(att.filename).stem
                    res = flashcard_engine.import_text_cards(
                        text_content,
                        target_deck_name=deck_label,
                        user_name=ctx.author.display_name,
                    )
                    await self._send_import_result(ctx, res)
                else:
                    await ctx.send("⚠️ 檔案格式不支援，請上傳 `.apkg`、`.tsv`、`.csv` 或 `.txt` 檔案！")
            except Exception as e:
                self.logger.error(f"匯入單字卡失敗: {e}", exc_info=True)
                await ctx.send(f"❌ 讀取檔案時發生錯誤：{e}")

    async def _handle_add(self, ctx: commands.Context, arg: Optional[str]) -> None:
        if not arg or not arg.strip():
            await ctx.send("❓ 請提供單字內容，例如：`!card add 桜 さくら 櫻花` 或直接 `!card add 桜` 由 AI 補全！")
            return

        user_deck = flashcard_engine.get_user_active_deck(ctx.author.id)
        tokens = arg.strip().split()

        if len(tokens) >= 3:
            front, reading, back = tokens[0], tokens[1], " ".join(tokens[2:])
            flashcard_engine.add_card(deck_id=user_deck["id"], front=front, reading=reading, back=back, notes="手動新增")
            await ctx.send(f"✅ 已將「**{front}**」【{reading}】＝ {back} 收錄至牌組：`{user_deck['name']}`！")
            return

        if len(tokens) == 2:
            front, back = tokens[0], tokens[1]
            flashcard_engine.add_card(deck_id=user_deck["id"], front=front, reading="", back=back, notes="手動新增")
            await ctx.send(f"✅ 已將「**{front}**」＝ {back} 收錄至牌組：`{user_deck['name']}`！")
            return

        word = tokens[0]
        async with ctx.channel.typing():
            prompt = (
                f"請分析日語單字或詞組「{word}」，精準回傳 JSON 物件：\n"
                f'{{"front": "{word}", "reading": "平假名讀音", "meaning": "繁體中文意思", "example_jp": "實用日文例句", "example_zh": "例句中文翻譯"}}\n'
                f"嚴格以 JSON 格式輸出，不要加任何其他文字說明。"
            )
            try:
                resp, _ = await generate_with_fallback(
                    client=self.gemini_client,
                    models_to_try=self.settings.models_to_try,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        temperature=0.3,
                        safety_settings=get_safety_settings(self.settings.disable_safety_settings),
                    ),
                    context_label="單字自動補齊",
                    logger=self.logger,
                )
                text = resp.text.strip()
                m = re.search(r"\{.*\}", text, re.DOTALL)
                if m:
                    data = json.loads(m.group(0))
                    flashcard_engine.add_card(
                        deck_id=user_deck["id"],
                        front=data.get("front", word),
                        reading=data.get("reading", ""),
                        back=data.get("meaning", ""),
                        example_jp=data.get("example_jp", ""),
                        example_zh=data.get("example_zh", ""),
                        notes="AI 自動補齊",
                    )
                    reading_tag = f"【{data.get('reading')}】" if data.get("reading") else ""
                    await ctx.send(
                        f"✨ **AI 家教已為您補齊資訊並收錄！**\n"
                        f"📚 牌組：`{user_deck['name']}`\n"
                        f"📖 單字：**{word}** {reading_tag} ＝ {data.get('meaning')}\n"
                        f"> 例句：{data.get('example_jp')}\n"
                        f"> 翻譯：{data.get('example_zh')}"
                    )
                    return
            except Exception as e:
                self.logger.warning(f"AI 單字解析失敗 ({e})，以基礎單字存入。")

        flashcard_engine.add_card(deck_id=user_deck["id"], front=word, reading="", back="待補充", notes="速記收錄")
        await ctx.send(f"✅ 已將「**{word}**」收錄至牌組：`{user_deck['name']}`！")

    async def _handle_generate(self, ctx: commands.Context, arg: Optional[str]) -> None:
        if not arg or not arg.strip():
            await ctx.send("❓ 請提供想學習的主題與數量，例如：`!card generate 日本居酒屋必備 8`")
            return

        parts = arg.strip().rsplit(maxsplit=1)
        count = 5
        theme = arg.strip()
        if len(parts) == 2 and parts[1].isdigit():
            count = max(1, min(15, int(parts[1])))
            theme = parts[0]

        async with ctx.channel.typing():
            await ctx.send(f"🪄 正在請 AI 家教為您量身打造「**{theme}**」專屬單字庫（共 {count} 題），請稍候...")
            prompt = (
                f"請扮演專業細心的日語教學專家，針對主題「{theme}」，精選 {count} 個實用道地的日語單字或生活短語。\n"
                f"請嚴格以 JSON 陣列格式輸出，不要加任何 markdown 標記（除了 json 區塊外），格式如下：\n"
                f"[\n"
                f'  {{"front": "生ビール", "reading": "なまビール", "back": "生啤酒", "example_jp": "とりあえず生ビール二つください。", "example_zh": "總之先來兩杯生啤酒。"}}\n'
                f"]"
            )
            try:
                resp, _ = await generate_with_fallback(
                    client=self.gemini_client,
                    models_to_try=self.settings.models_to_try,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        temperature=0.7,
                        safety_settings=get_safety_settings(self.settings.disable_safety_settings),
                    ),
                    context_label="AI生成主題牌組",
                    logger=self.logger,
                )
                text = resp.text.strip()
                m = re.search(r"\[.*\]", text, re.DOTALL)
                if not m:
                    raise ValueError("未能解析出有效的 JSON 單字陣列。")

                cards_data = json.loads(m.group(0))
                deck_name = f"🌸 {theme}"
                deck = flashcard_engine.get_or_create_deck(
                    deck_name,
                    description=f"由 AI 自動生成的「{theme}」主題牌組",
                    created_by=ctx.author.display_name,
                )
                imported = 0
                for item in cards_data:
                    if item.get("front") and item.get("back"):
                        flashcard_engine.add_card(
                            deck_id=deck["id"],
                            front=str(item["front"]).strip(),
                            reading=str(item.get("reading", "")).strip(),
                            back=str(item["back"]).strip(),
                            example_jp=str(item.get("example_jp", "")).strip(),
                            example_zh=str(item.get("example_zh", "")).strip(),
                            notes=f"主題：{theme}",
                        )
                        imported += 1

                await ctx.send(
                    f"🎉 成功打造主題牌組「**{deck_name}**」！共收錄 **{imported}** 張實用單字卡。\n"
                    f"立即輸入 `!card {deck_name}` 開始複習，或是輸入 `!quiz {deck_name}` 進行搶答測驗！"
                )
            except Exception as e:
                self.logger.error(f"AI 生成單字卡失敗: {e}", exc_info=True)
                await ctx.send(f"❌ 牌組生成失敗：{e}")

    async def _handle_study(self, ctx: commands.Context, sub: str, arg: Optional[str]) -> None:
        target_deck = None
        if sub:
            if sub in ("study", "複習", "學習", "play"):
                if arg:
                    target_deck = flashcard_engine.get_deck(arg.strip())
            else:
                possible_name = (sub + (" " + arg if arg else "")).strip()
                target_deck = flashcard_engine.get_deck(possible_name) or flashcard_engine.get_deck(sub)

        if not target_deck:
            target_deck = flashcard_engine.get_user_active_deck(ctx.author.id)

        card = flashcard_engine.get_random_card_from_deck(target_deck["id"])
        if not card:
            await ctx.send(
                f"🌸 牌組「**{target_deck['name']}**」目前還沒有卡片喔！\n"
                f"• 使用 `!card add <單字>` 新增單字\n"
                f"• 夾帶 `.apkg` 檔案並輸入 `!card import` 匯入\n"
                f"• 輸入 `!card decks` 查看其他可用牌組"
            )
            return

        view = FlashcardStudyView(
            bot=self.bot,
            gemini_client=self.gemini_client,
            settings=self.settings,
            logger=self.logger,
            user_id=ctx.author.id,
            deck_id=target_deck["id"],
            card=card,
        )
        img_filename = card.get("image_filename")
        discord_file = None
        if img_filename:
            img_path = flashcard_engine.MEDIA_DIR / img_filename
            if img_path.is_file():
                discord_file = discord.File(str(img_path), filename=img_path.name)
        if discord_file:
            await ctx.send(embed=view.build_embed(), view=view, file=discord_file)
        else:
            await ctx.send(embed=view.build_embed(), view=view)

    @commands.command(name="card", aliases=["flashcard", "單字", "卡片"])
    async def card_command(self, ctx: commands.Context, subcmd: Optional[str] = None, *, arg: Optional[str] = None):
        """日語單字卡系統 (Flashcards / Anki)。"""
        if not is_target_channel(ctx.channel, self.settings):
            return
        if not self.settings.enable_flashcard_mode:
            await ctx.send("⚠️ 此伺服器未啟用單字卡與搶答測驗功能喔！")
            return

        sub = (subcmd or "").strip().lower()
        if sub in ("decks", "list", "牌組", "清單"):
            await self._handle_decks(ctx)
        elif sub in ("deck", "use", "切換"):
            await self._handle_set_deck(ctx, arg)
        elif sub in ("newdeck", "createdeck", "adddeck", "新增牌組"):
            await self._handle_new_deck(ctx, arg)
        elif sub in ("deldeck", "rmdeck", "刪除牌組"):
            await self._handle_del_deck(ctx, arg)
        elif sub in ("import", "匯入", "load"):
            await self._handle_import(ctx, arg)
        elif sub in ("add", "new", "+", "新增"):
            await self._handle_add(ctx, arg)
        elif sub in ("generate", "gen", "生成", "造詞"):
            await self._handle_generate(ctx, arg)
        else:
            await self._handle_study(ctx, sub, arg)

    @commands.command(name="decks", aliases=["牌組清單", "卡組"])
    async def decks_command(self, ctx: commands.Context):
        """查看所有可用單字牌組。"""
        if not is_target_channel(ctx.channel, self.settings):
            return
        await self.card_command(ctx, subcmd="decks")

    @commands.command(name="quiz", aliases=["搶答", "測驗", "kotoba"])
    async def quiz_command(self, ctx: commands.Context, deck_or_count: Optional[str] = None, count_arg: Optional[str] = None):
        """Kotoba 風格多人限時日語單字搶答測驗。"""
        if not is_target_channel(ctx.channel, self.settings):
            return
        if not self.settings.enable_flashcard_mode:
            await ctx.send("⚠️ 此伺服器未啟用單字卡與搶答測驗功能喔！")
            return

        cid = ctx.channel.id
        if cid in self.active_quiz_channels:
            await ctx.send("⚠️ 這個頻道已經有正在進行中的搶答測驗囉！請先完成當前測驗。")
            return

        requested_count = 5
        target_deck = None
        if deck_or_count:
            if deck_or_count.isdigit():
                requested_count = int(deck_or_count)
            else:
                target_deck = flashcard_engine.get_deck(deck_or_count)
                if count_arg and count_arg.isdigit():
                    requested_count = int(count_arg)

        if not target_deck:
            target_deck = flashcard_engine.get_user_active_deck(ctx.author.id)

        cards = flashcard_engine.get_cards_in_deck(target_deck["id"], limit=200)
        if not cards:
            await ctx.send(f"❌ 牌組「**{target_deck['name']}**」內沒有卡片可供測驗！請先新增或匯入單字。")
            return

        num_questions = max(1, min(requested_count, len(cards), 15))
        selected_cards = random.sample(cards, num_questions)

        self.active_quiz_channels.add(cid)
        try:
            intro_embed = discord.Embed(
                title="🌸 Kotoba 日語單字限時搶答即將開始！",
                description=(
                    f"📚 **測驗牌組**：`{target_deck['name']}`\n"
                    f"📝 **題目數量**：共 **{num_questions}** 題 ｜ ⏱️ **作答限時**：每題 20 秒\n"
                    f"💡 **搶答規則**：看到題目後，直接在聊天室打出「**平假名讀音**」或「**中文意思**」！\n\n"
                    f"🏁 **3 秒後即將出題，請大家準備好鍵盤～！**"
                ),
                color=0xFF77A9,
            )
            await ctx.send(embed=intro_embed)
            await asyncio.sleep(3)

            scores: dict[int, dict] = {}
            loop = asyncio.get_running_loop()

            for idx, card in enumerate(selected_cards):
                q_embed = discord.Embed(
                    title=f"🎯【第 {idx + 1} / {num_questions} 題】 請搶答！",
                    description=f"# {card['front']}\n\n請在聊天室打出【平假名讀音】或【中文意思】：",
                    color=0x5865F2,
                )
                await ctx.send(embed=q_embed)

                start_t = loop.time()
                answered = False

                while loop.time() - start_t < 20.0:
                    remaining = 20.0 - (loop.time() - start_t)
                    if remaining <= 0:
                        break
                    try:
                        msg = await self.bot.wait_for(
                            "message",
                            check=lambda m: m.channel.id == cid and not m.author.bot,
                            timeout=remaining,
                        )
                        if check_quiz_answer_match(msg.content, card):
                            answered = True
                            uid = msg.author.id
                            if uid not in scores:
                                scores[uid] = {"name": msg.author.display_name, "points": 0}
                            scores[uid]["points"] += 1

                            reading_disp = f"【{card['reading']}】" if card.get("reading") else ""
                            await ctx.send(
                                f"🎉 **答對了！** 由 {msg.author.mention} 搶先得分！（+1 分）\n"
                                f"✅ 正確解答：**{card['front']}** {reading_disp} ＝ {card['back']}"
                            )
                            break
                    except asyncio.TimeoutError:
                        break

                if not answered:
                    reading_disp = f"【{card['reading']}】" if card.get("reading") else ""
                    await ctx.send(
                        f"⏰ **時間到！** 本題沒有人搶答成功～\n"
                        f"💡 正確解答：**{card['front']}** {reading_disp} ＝ {card['back']}"
                    )

                if idx < num_questions - 1:
                    await asyncio.sleep(2.5)

            if not scores:
                await ctx.send("🌸 **測驗結束！** 本次測驗無人得分，下次再接再厲！💪")
            else:
                sorted_scores = sorted(scores.values(), key=lambda x: x["points"], reverse=True)
                medals = ["🥇", "🥈", "🥉", "🎖️", "🎖️"]
                ranking_lines = [
                    f"{medals[r_idx] if r_idx < len(medals) else '▫️'} **{s['name']}**：{s['points']} 分"
                    for r_idx, s in enumerate(sorted_scores)
                ]
                end_embed = discord.Embed(
                    title="🏆 Kotoba 測驗排行榜結算！",
                    description="\n".join(ranking_lines) + "\n\n大家的表現都太厲害了！繼續保持喔～🌸",
                    color=0xF1C40F,
                )
                await ctx.send(embed=end_embed)
        finally:
            self.active_quiz_channels.discard(cid)
