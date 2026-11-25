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

from cogs.flashcard_views import (
    FlashcardStudyView,
    ShadowingResultView,
    build_shadowing_prompt_embed,
)
from engines import flashcard_engine, safety_guardrail, shadowing_engine
from core import media
from core.chat_pipeline import is_target_channel
from core.config import (
    MAX_APKG_ATTACHMENT_BYTES,
    MAX_TEXT_ATTACHMENT_BYTES,
    BotSettings,
    is_bot_owner,
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
    card_front_clean = re.sub(r"[\[\]\(\)（）\s。！？~～\.,!]", "", card.get("front", "").strip().lower())

    # 防止玩家複製貼上漢字題面直接混分（僅在題面本身就是純假名即 front == reading 時允許）
    if ans_clean == card_front_clean and card_front_clean != card_reading_clean:
        return False

    if card_reading_clean and ans_clean == card_reading_clean:
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
            await ctx.send(f"❌ 匯入失敗：{str(res.get('error', '未知錯誤'))[:500]}")
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
                f"已掃描 {source_label[:120]} 並維護 **{res['total_decks']}** 個等級牌組（本次新增 **{new_added}** 張單字卡）！\n\n"
                f"**各等級牌組清單：**\n" + "\n".join(deck_lines) + "\n\n"
                f"👉 隨時輸入 `!card <等級名稱>`（例如 `!card N5` 或 `!card N3高頻`）即可開始複習！\n"
                f"👉 或輸入 `!decks` 查看所有牌組清單～🌸"
            )
            embed = discord.Embed(
                title=title_str[:256],
                description=truncate_embed_text(desc_str),
                color=0x57F287 if new_added > 0 else 0x3498DB,
            )
            await ctx.send(embed=embed)
            return

        new_cnt = res.get("imported_count", 0)
        deck_disp = str(res.get("deck_name", ""))[:120]
        if new_cnt == 0:
            await ctx.send(
                f"ℹ️ 本次未新增任何單字卡，牌組 **`{deck_disp}`** 內的單字卡均已存在（自動去重保護）！\n"
                f"隨時可以輸入 `!card {deck_disp}` 開始複習～🌸"
            )
            return

        media_info = f"（已同時提取 **{res.get('media_count', 0)}** 個真人原音/圖檔）" if res.get("media_count") else ""
        origin_txt = f"自 {source_label[:120]} " if source_label else ""
        await ctx.send(
            f"🎉 成功{origin_txt}匯入 **{new_cnt}** 張單字卡！{media_info}\n"
            f"📚 牌組名稱：`{deck_disp}`\n"
            f"隨時可以輸入 `!card {deck_disp}` 開始複習～🌸"
        )

    async def _handle_decks(self, ctx: commands.Context) -> None:
        decks = await asyncio.to_thread(flashcard_engine.list_decks)
        user_deck = await asyncio.to_thread(flashcard_engine.get_user_active_deck, ctx.author.id)
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
            user_deck = await asyncio.to_thread(flashcard_engine.get_user_active_deck, ctx.author.id)
            await ctx.send(f"📌 您目前的預設收集牌組為：`{user_deck['name'][:120]}`。\n若要切換，請輸入 `!card deck <牌組名或編號>`。")
            return
        clean_q = arg.strip()
        target = await asyncio.to_thread(flashcard_engine.get_deck, clean_q)
        if not target:
            await ctx.send(f"❌ 找不到名稱或編號為 `{clean_q[:120]}` 的牌組！請先用 `!card decks` 查看清單。")
            return
        await asyncio.to_thread(flashcard_engine.set_user_active_deck, ctx.author.id, target["id"])
        await ctx.send(f"📌 已將您的預設牌組切換為：`{target['name'][:120]}`！之後收錄的單字都會優先加入此處。")

    async def _handle_new_deck(self, ctx: commands.Context, arg: Optional[str]) -> None:
        if not arg or not arg.strip():
            await ctx.send("❓ 請輸入新牌組名稱，例如：`!card newdeck 日本旅行生活日常`")
            return
        parts = arg.strip().split(maxsplit=1)
        dname = parts[0][:100]
        ddesc = parts[1][:300] if len(parts) > 1 else ""
        d = await asyncio.to_thread(
            flashcard_engine.get_or_create_deck,
            dname,
            ddesc,
            created_by=ctx.author.display_name,
        )
        await ctx.send(f"✅ 牌組「**{d['name']}**」已就緒！可以使用 `!card deck {d['name']}` 設為預設。")

    async def _handle_del_deck(self, ctx: commands.Context, arg: Optional[str]) -> None:
        if not self.settings.enable_japanese_mode:
            perms = getattr(ctx.author, "guild_permissions", None)
            is_mod = bool(
                perms
                and (
                    getattr(perms, "administrator", False)
                    or getattr(perms, "manage_guild", False)
                    or getattr(perms, "manage_messages", False)
                )
            )
            if not is_mod and not await is_bot_owner(self.bot, self.settings, ctx.author):
                await ctx.send("🔒 社群模式下僅限管理員（管理訊息權限）或機器人擁有者可刪除牌組。")
                return
        if not arg or not arg.strip():
            await ctx.send("❓ 請輸入要刪除的牌組名稱或編號。")
            return
        target = await asyncio.to_thread(flashcard_engine.get_deck, arg.strip(), exact_only=True)
        if not target:
            await ctx.send(f"❌ 找不到完全符合「`{arg.strip()[:80]}`」的牌組。")
            return
        if "兩人的日常收藏" in target["name"] or "預設單字收集箱" in target["name"]:
            await ctx.send("⚠️ 這是系統預設收納箱，不能刪除喔！")
            return
        async with ctx.channel.typing():
            await asyncio.to_thread(flashcard_engine.delete_deck, target["id"])
        await ctx.send(f"🗑️ 已刪除牌組「**{target['name'][:120]}**」及其所有卡片與關聯媒體檔。")

    async def _import_inline_text(self, ctx: commands.Context, raw_arg: str) -> None:
        """從指令後方直接貼上的多行文字批次匯入單字卡。"""
        lines = raw_arg.splitlines()
        first_line = lines[0].strip()
        if any(sep in first_line for sep in ("\t", " - ", "/", ":", "：", ",")):
            user_deck = await asyncio.to_thread(flashcard_engine.get_user_active_deck, ctx.author.id)
            deck_label = user_deck["name"]
            text_body = raw_arg
        else:
            deck_label = (first_line or "自訂文字匯入")[:100]
            text_body = "\n".join(lines[1:])

        res = await asyncio.to_thread(
            flashcard_engine.import_text_cards,
            text_body,
            target_deck_name=deck_label,
            user_name=ctx.author.display_name,
        )
        await self._send_import_result(ctx, res)

    async def _import_local_file(self, ctx: commands.Context, raw_arg: str) -> bool:
        """從本機 data/imports/ 匯入單字卡；若無匹配檔案回傳 False。"""
        parts = raw_arg.split()

        def _resolve_local_import_sync(toks: list[str]) -> tuple[Optional[Path], Optional[str], Optional[str]]:
            flashcard_engine.IMPORTS_DIR.mkdir(parents=True, exist_ok=True)
            candidate_file = Path(toks[0]).name
            cand_path = flashcard_engine.IMPORTS_DIR / candidate_file
            sub_filter = None
            custom_name = None
            if cand_path.is_file():
                if len(toks) > 1:
                    sub_filter = toks[1]
                if len(toks) > 2:
                    custom_name = " ".join(toks[2:])[:100]
                return cand_path, sub_filter, custom_name
            apkg_files = sorted([
                p for p in flashcard_engine.IMPORTS_DIR.iterdir()
                if p.is_file() and p.suffix.lower() == ".apkg"
            ])
            if apkg_files:
                sub_filter = toks[0]
                if len(toks) > 1:
                    custom_name = " ".join(toks[1:])[:100]
                return apkg_files[0], sub_filter, custom_name
            return None, None, None

        local_path, subdeck_filter, custom_deck_name = await asyncio.to_thread(_resolve_local_import_sync, parts)
        if local_path is None:
            return False

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
                    def _read_and_import_local_text() -> dict:
                        if local_path.stat().st_size > MAX_TEXT_ATTACHMENT_BYTES:
                            return {
                                "success": False,
                                "error": f"文字檔 `{local_path.name}` 超過 {MAX_TEXT_ATTACHMENT_BYTES // (1024 * 1024)} MB 上限！",
                            }
                        text_content = local_path.read_text(encoding="utf-8", errors="ignore")
                        deck_label = (custom_deck_name or local_path.stem)[:100]
                        return flashcard_engine.import_text_cards(
                            text_content,
                            target_deck_name=deck_label,
                            user_name=ctx.author.display_name,
                        )

                    res = await asyncio.to_thread(_read_and_import_local_text)
                else:
                    await ctx.send("⚠️ 檔案格式不支援，僅支援 `.apkg`、`.tsv`、`.csv` 或 `.txt`！")
                    return True
                await self._send_import_result(ctx, res, source_label=f"`data/imports/{local_path.name}`")
            except Exception as e:
                self.logger.error(f"匯入本機單字卡失敗: {e}", exc_info=True)
                await ctx.send("❌ 讀取或解析本機檔案失敗，請確認格式後再試。")
        return True

    async def _send_import_guide(self, ctx: commands.Context) -> None:
        """顯示 `!card import` 匯入教學與本機 data/imports/ 可用檔案清單。"""
        def _load_import_guide_sync() -> list[tuple[Path, list[dict]]]:
            local_files = flashcard_engine.list_local_import_files()
            items: list[tuple[Path, list[dict]]] = []
            for p in local_files[:10]:
                subdecks = flashcard_engine.get_anki_subdecks(p) if p.suffix.lower() == ".apkg" else []
                items.append((p, subdecks))
            return items

        guide_items = await asyncio.to_thread(_load_import_guide_sync)
        local_lines = []
        for p, subdecks in guide_items:
            if p.suffix.lower() == ".apkg" and subdecks:
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

        local_hint = (
            "📂 **目前偵測到 `data/imports/` 資料夾內可直接匯入的檔案：**\n" + "\n".join(local_lines)
            if local_lines
            else "📂 *(目前本機 `data/imports/` 資料夾內尚無檔案，你隨時可以把 `.apkg` 或 `.txt` 丟進去)*"
        )
        guide_embed = discord.Embed(
            title="📥 單字卡批次匯入指南 (`!card import`)",
            description=truncate_embed_text(
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

    async def _import_attachment(self, ctx: commands.Context, raw_arg: str) -> None:
        """從 Discord 附件匯入 `.apkg` 或純文字單字卡。"""
        att = ctx.message.attachments[0]
        fname = att.filename.lower()
        target_deck_name = raw_arg[:100] if raw_arg else None

        async with ctx.channel.typing():
            try:
                if fname.endswith(".apkg"):
                    max_apkg_mb = MAX_APKG_ATTACHMENT_BYTES // (1024 * 1024)
                    if att.size and att.size > MAX_APKG_ATTACHMENT_BYTES:
                        await ctx.send(
                            f"❌ `.apkg` 檔案過大（超過 {max_apkg_mb} MB 上限）！建議改放進本機 `data/imports/` 資料夾。"
                        )
                        return
                    file_bytes = await att.read()
                    if len(file_bytes) > MAX_APKG_ATTACHMENT_BYTES:
                        await ctx.send(
                            f"❌ `.apkg` 檔案過大（超過 {max_apkg_mb} MB 上限）！建議改放進本機 `data/imports/` 資料夾。"
                        )
                        return
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
                    if len(file_bytes) > MAX_TEXT_ATTACHMENT_BYTES:
                        await ctx.send("❌ 文字檔超過 2MB 上限！")
                        return
                    text_content = file_bytes.decode("utf-8", errors="ignore")
                    deck_label = (target_deck_name or Path(att.filename).stem)[:100]
                    res = await asyncio.to_thread(
                        flashcard_engine.import_text_cards,
                        text_content,
                        target_deck_name=deck_label,
                        user_name=ctx.author.display_name,
                    )
                    await self._send_import_result(ctx, res)
                else:
                    await ctx.send("⚠️ 檔案格式不支援，請上傳 `.apkg`、`.tsv`、`.csv` 或 `.txt` 檔案！")
            except Exception as e:
                self.logger.error(f"匯入單字卡失敗: {e}", exc_info=True)
                await ctx.send("❌ 讀取或解析檔案失敗，請確認檔案格式正確後再試。")

    async def _handle_import(self, ctx: commands.Context, arg: Optional[str]) -> None:
        raw_arg = (arg or "").strip()

        if not ctx.message.attachments:
            if "\n" in raw_arg:
                await self._import_inline_text(ctx, raw_arg)
                return
            if raw_arg and await self._import_local_file(ctx, raw_arg):
                return
            await self._send_import_guide(ctx)
            return

        await self._import_attachment(ctx, raw_arg)

    async def _handle_add(self, ctx: commands.Context, arg: Optional[str]) -> None:
        if not arg or not arg.strip():
            await ctx.send("❓ 請提供單字內容，例如：`!card add 桜 さくら 櫻花` 或直接 `!card add 桜` 由 AI 補全！")
            return

        user_deck = await asyncio.to_thread(flashcard_engine.get_user_active_deck, ctx.author.id)
        tokens = arg.strip().split()

        if len(tokens) >= 3:
            front, reading, back = tokens[0][:120], tokens[1][:120], " ".join(tokens[2:])[:300]
            await asyncio.to_thread(
                flashcard_engine.add_card,
                deck_id=user_deck["id"],
                front=front,
                reading=reading,
                back=back,
                notes="手動新增",
            )
            await ctx.send(f"✅ 已將「**{front}**」【{reading}】＝ {back} 收錄至牌組：`{user_deck['name'][:100]}`！")
            return

        if len(tokens) == 2:
            front, back = tokens[0][:120], tokens[1][:300]
            await asyncio.to_thread(
                flashcard_engine.add_card,
                deck_id=user_deck["id"],
                front=front,
                reading="",
                back=back,
                notes="手動新增",
            )
            await ctx.send(f"✅ 已將「**{front}**」＝ {back} 收錄至牌組：`{user_deck['name'][:100]}`！")
            return

        word = tokens[0]
        if self.settings.enable_safety_guardrail:
            inj = safety_guardrail.check_prompt_injection(
                word,
                enable_japanese_mode=self.settings.enable_japanese_mode,
                max_chars=120,
            )
            if not inj.allowed:
                await ctx.send(inj.reply or "⚠️ 單字輸入包含不允許的指令語法或過長。")
                return
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
                        response_mime_type="application/json",
                        safety_settings=get_safety_settings(self.settings.disable_safety_settings),
                    ),
                    context_label="單字自動補齊",
                    logger=self.logger,
                    final_fallback_model=self.settings.final_fallback_model,
                )
                text = (resp.text or "").strip() if resp else ""
                m = re.search(r"\{.*\}", text, re.DOTALL)
                if m:
                    data = json.loads(m.group(0))
                    if isinstance(data, dict):
                        c_front = str(data.get("front") or word).strip()[:120] or word[:120]
                        c_reading = str(data.get("reading") or "").strip()[:120]
                        c_back = str(data.get("meaning") or data.get("back") or "").strip()[:300]
                        c_ex_jp = str(data.get("example_jp") or "").strip()[:400]
                        c_ex_zh = str(data.get("example_zh") or "").strip()[:400]
                        if c_back:
                            await asyncio.to_thread(
                                flashcard_engine.add_card,
                                deck_id=user_deck["id"],
                                front=c_front,
                                reading=c_reading,
                                back=c_back,
                                example_jp=c_ex_jp,
                                example_zh=c_ex_zh,
                                notes="AI 自動補齊",
                            )
                            reading_tag = f"【{c_reading}】" if c_reading else ""
                            await ctx.send(
                                f"✨ **AI 家教已為您補齊資訊並收錄！**\n"
                                f"📚 牌組：`{user_deck['name'][:100]}`\n"
                                f"📖 單字：**{word[:120]}** {reading_tag} ＝ {c_back}\n"
                                f"> 例句：{c_ex_jp or '（無）'}\n"
                                f"> 翻譯：{c_ex_zh or '（無）'}"
                            )
                            return
            except Exception as e:
                self.logger.warning(f"AI 單字解析失敗 ({e})，以基礎單字存入。")

        await asyncio.to_thread(
            flashcard_engine.add_card,
            deck_id=user_deck["id"],
            front=word[:120],
            reading="",
            back="待補充",
            notes="速記收錄",
        )
        await ctx.send(f"✅ 已將「**{word[:120]}**」收錄至牌組：`{user_deck['name'][:100]}`！")

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

        if self.settings.enable_safety_guardrail:
            inj = safety_guardrail.check_prompt_injection(
                theme,
                enable_japanese_mode=self.settings.enable_japanese_mode,
                max_chars=120,
            )
            if not inj.allowed:
                await ctx.send(inj.reply or "⚠️ 主題輸入包含不允許的指令語法或過長。")
                return

        theme = theme[:100]
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
                        response_mime_type="application/json",
                        safety_settings=get_safety_settings(self.settings.disable_safety_settings),
                    ),
                    context_label="AI生成主題牌組",
                    logger=self.logger,
                    final_fallback_model=self.settings.final_fallback_model,
                )
                text = (resp.text or "").strip() if resp else ""
                m = re.search(r"\[.*\]", text, re.DOTALL)
                if not m:
                    raise ValueError("未能解析出有效的 JSON 單字陣列。")

                cards_data = json.loads(m.group(0))
                if not isinstance(cards_data, list):
                    raise ValueError("AI 回傳的單字資料格式非 JSON 陣列。")
                deck_name = f"🌸 {theme}"
                deck = await asyncio.to_thread(
                    flashcard_engine.get_or_create_deck,
                    deck_name,
                    description=f"由 AI 自動生成的「{theme}」主題牌組",
                    created_by=ctx.author.display_name,
                )
                imported = 0
                for item in cards_data:
                    if not isinstance(item, dict):
                        continue
                    c_front = str(item.get("front") or "").strip()
                    c_back = str(item.get("back") or item.get("meaning") or "").strip()
                    if c_front and c_back:
                        await asyncio.to_thread(
                            flashcard_engine.add_card,
                            deck_id=deck["id"],
                            front=c_front[:120],
                            reading=str(item.get("reading") or "").strip()[:120],
                            back=c_back[:300],
                            example_jp=str(item.get("example_jp") or "").strip()[:400],
                            example_zh=str(item.get("example_zh") or "").strip()[:400],
                            notes=f"主題：{theme}",
                        )
                        imported += 1

                await ctx.send(
                    f"🎉 成功打造主題牌組「**{deck_name}**」！共收錄 **{imported}** 張實用單字卡。\n"
                    f"立即輸入 `!card {deck_name}` 開始複習，或是輸入 `!quiz {deck_name}` 進行搶答測驗！"
                )
            except Exception as e:
                self.logger.error(f"AI 生成單字卡失敗: {e}", exc_info=True)
                await ctx.send("❌ 牌組生成暫時失敗，請稍後再試。")

    async def _handle_study(self, ctx: commands.Context, sub: str, arg: Optional[str]) -> None:
        def _prepare_study_sync():
            t_deck = None
            if sub:
                if sub in ("study", "複習", "學習", "play"):
                    if arg:
                        t_deck = flashcard_engine.get_deck(arg.strip())
                else:
                    possible_name = (sub + (" " + arg if arg else "")).strip()
                    t_deck = flashcard_engine.get_deck(possible_name) or flashcard_engine.get_deck(sub)

            if not t_deck:
                t_deck = flashcard_engine.get_user_active_deck(ctx.author.id)

            chosen_card = flashcard_engine.get_random_card_from_deck(t_deck["id"])
            if not chosen_card:
                return t_deck, None, [], None
            decks_list = flashcard_engine.list_decks()[:25]
            progress = flashcard_engine.get_card_progress(ctx.author.id, chosen_card["id"])
            return t_deck, chosen_card, decks_list, progress

        target_deck, card, preloaded_decks, preloaded_progress = await asyncio.to_thread(_prepare_study_sync)
        if not card:
            await ctx.send(
                f"🌸 牌組「**{target_deck['name'][:100]}**」目前還沒有卡片喔！\n"
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
            preloaded_decks=preloaded_decks,
            preloaded_deck_name=target_deck["name"],
            preloaded_progress=preloaded_progress,
        )
        img_filename = card.get("image_filename")
        discord_file = None
        if img_filename:
            img_path = flashcard_engine._safe_media_path(img_filename)
            if img_path and img_path.is_file():
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
        if deck_or_count:
            if deck_or_count.isdigit():
                requested_count = int(deck_or_count)
            elif count_arg and count_arg.isdigit():
                requested_count = int(count_arg)

        def _load_quiz_cards_sync():
            t_deck = None
            if deck_or_count and not deck_or_count.isdigit():
                t_deck = flashcard_engine.get_deck(deck_or_count)
            if not t_deck:
                t_deck = flashcard_engine.get_user_active_deck(ctx.author.id)
            deck_cards = flashcard_engine.get_cards_in_deck(t_deck["id"], limit=200)
            return t_deck, deck_cards

        target_deck, cards = await asyncio.to_thread(_load_quiz_cards_sync)
        if not cards:
            await ctx.send(f"❌ 牌組「**{target_deck['name'][:100]}**」內沒有卡片可供測驗！請先新增或匯入單字。")
            return

        num_questions = max(1, min(requested_count, len(cards), 15))
        selected_cards = random.sample(cards, num_questions)

        self.active_quiz_channels.add(cid)
        try:
            intro_embed = discord.Embed(
                title="🌸 Kotoba 日語單字限時搶答即將開始！",
                description=truncate_embed_text(
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
                    description=truncate_embed_text(
                        f"# {card['front']}\n\n請在聊天室打出【平假名讀音】或【中文意思】：",
                        max_len=3800,
                    ),
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

                            reading_disp = f"【{str(card['reading'])[:120]}】" if card.get("reading") else ""
                            await ctx.send(
                                f"🎉 **答對了！** 由 {msg.author.mention} 搶先得分！（+1 分）\n"
                                f"✅ 正確解答：**{str(card['front'])[:120]}** {reading_disp} ＝ {str(card['back'])[:300]}"
                            )
                            break
                    except asyncio.TimeoutError:
                        break

                if not answered:
                    reading_disp = f"【{str(card['reading'])[:120]}】" if card.get("reading") else ""
                    await ctx.send(
                        f"⏰ **時間到！** 本題沒有人搶答成功～\n"
                        f"💡 正確解答：**{str(card['front'])[:120]}** {reading_disp} ＝ {str(card['back'])[:300]}"
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
                    description=truncate_embed_text(
                        "\n".join(ranking_lines) + "\n\n大家的表現都太厲害了！繼續保持喔～🌸"
                    ),
                    color=0xF1C40F,
                )
                await ctx.send(embed=end_embed)
        finally:
            self.active_quiz_channels.discard(cid)

    @commands.command(name="shadow", aliases=["跟讀", "口說", "發音練習"])
    async def shadow_command(self, ctx: commands.Context, *, target_text: Optional[str] = None):
        """啟動 90 秒日語跟讀發音練習（可自訂句子或從目前牌組抽題）。"""
        if not is_target_channel(ctx.channel, self.settings):
            return
        if not self.settings.enable_japanese_mode:
            return

        raw_target = (target_text or "").strip()
        if raw_target:
            clean_target = raw_target[:200]
            if self.settings.enable_safety_guardrail:
                inj = safety_guardrail.check_prompt_injection(
                    clean_target,
                    enable_japanese_mode=self.settings.enable_japanese_mode,
                    max_chars=200,
                )
                if not inj.allowed:
                    await ctx.send(inj.reply or "⚠️ 跟讀句子輸入包含不允許的指令語法或過長。")
                    return
            session = shadowing_engine.start_shadowing_session(
                ctx.channel.id,
                ctx.author.id,
                target_jp=clean_target,
            )
        else:
            card = None
            if self.settings.enable_flashcard_mode:
                def _pick_shadowing_card_sync():
                    deck = flashcard_engine.get_user_active_deck(ctx.author.id)
                    return flashcard_engine.get_random_card_from_deck(deck["id"]) if deck else None

                card = await asyncio.to_thread(_pick_shadowing_card_sync)

            if card:
                target_jp = str(
                    card.get("front") or card.get("example_jp") or "お疲れ様です！今日もいい天気ですね。"
                ).strip()
                session = shadowing_engine.start_shadowing_session(
                    ctx.channel.id,
                    ctx.author.id,
                    target_jp=target_jp,
                    reading=str(card.get("reading") or ""),
                    pitch=str(card.get("pitch") or ""),
                    meaning=str(card.get("back") or ""),
                    example_jp=str(card.get("example_jp") or ""),
                    card_id=card.get("id"),
                )
            else:
                session = shadowing_engine.start_shadowing_session(
                    ctx.channel.id,
                    ctx.author.id,
                    target_jp="お疲れ様です！今日もいい天気ですね。",
                    reading="おつかれさまです！きょうもいいてんきですね。",
                    meaning="辛苦了！今天天氣也真好呢。",
                )

        await ctx.send(embed=build_shadowing_prompt_embed(session))

    async def handle_shadowing_audio_message(self, message: discord.Message) -> bool:
        """攔截正在跟讀會話中的語音/音檔訊息，送交 Gemini 進行日語發音評測並回傳結果卡。"""
        if not self.settings.enable_japanese_mode:
            return False
        if getattr(message.author, "bot", False):
            return False
        attachments = getattr(message, "attachments", None) or []
        if not attachments:
            return False

        session = shadowing_engine.get_active_shadowing_session(message.channel.id, message.author.id)
        if not session:
            return False

        audio_att = next((att for att in attachments if media.is_audio_attachment(att)), None)
        if audio_att is None:
            return False

        async with message.channel.typing():
            audio_bytes, mime_type, reject_reason = await media.load_bounded_audio_attachment(
                audio_att,
                logger=self.logger,
            )
            if reject_reason:
                await message.channel.send(
                    f"⚠️ {reject_reason}（跟讀練習仍在等待中，請重傳 30 秒內的語音）"
                )
                return True

            shadowing_engine.clear_shadowing_session(message.channel.id, message.author.id)
            try:
                result = await shadowing_engine.evaluate_shadowing_audio(
                    self.gemini_client,
                    self.settings,
                    session,
                    audio_bytes,
                    mime_type,
                    logger=self.logger,
                )
            except Exception as exc:
                self.logger.warning(f"日語跟讀發音評測失敗: {exc}", exc_info=True)
                await message.channel.send("⚠️ 語音發音評測暫時無法連線，請稍後再輸入 `!shadow` 重試喔！")
                return True

            score = int(result["score"])
            score_bar = shadowing_engine.format_score_bar(score)
            color = 0x57F287 if score >= 85 else (0x3498DB if score >= 65 else 0xF39C12)

            desc_lines = [f"**目標日語**：`{session['target_jp']}`"]
            if session.get("reading") and not (
                session.get("example_jp") and session["target_jp"] == session["example_jp"]
            ):
                pitch_note = f" *(音調：{session['pitch']})*" if session.get("pitch") else ""
                desc_lines.append(f"**假名讀音**：【{session['reading']}】{pitch_note}")
            if session.get("meaning"):
                desc_lines.append(f"**中文意思**：{session['meaning']}")
            desc_lines.append(f"\n**綜合評分**：`{score_bar}`")

            author_name = getattr(message.author, "display_name", "學習者")
            embed = discord.Embed(
                title=f"🎙️ 日語跟讀發音評測 ｜ {author_name}"[:256],
                description=truncate_embed_text("\n".join(desc_lines), max_len=3800),
                color=color,
            )
            embed.add_field(
                name="👂 AI 聽到的內容",
                value=truncate_embed_text(str(result["transcript"]), max_len=950),
                inline=False,
            )
            embed.add_field(
                name="🎵 音調與節奏",
                value=truncate_embed_text(str(result["pitch_and_rhythm"]), max_len=950),
                inline=False,
            )
            embed.add_field(
                name="✨ 發音優點",
                value=truncate_embed_text(str(result["strengths"]), max_len=950),
                inline=False,
            )
            embed.add_field(
                name="💡 微調建議",
                value=truncate_embed_text(str(result["improvements"]), max_len=950),
                inline=False,
            )
            embed.add_field(
                name="🌸 老師鼓勵",
                value=truncate_embed_text(str(result["encouragement_jp"]), max_len=950),
                inline=False,
            )
            embed.set_footer(text="點擊下方按鈕可試聽標準發音或再跟讀一次！")

            view = ShadowingResultView(
                settings=self.settings,
                owner_id=message.author.id,
                session_data=session,
                bot=self.bot,
                logger=self.logger,
            )
            sent = await message.channel.send(embed=embed, view=view)
            view.message = sent
            return True
