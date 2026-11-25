"""Interactive UI Views for the Flashcard SRS study system (!card)."""

import asyncio
import io
import logging
import re
from typing import Optional

import discord
from discord.ext import commands
from google import genai
from google.genai import types

from core.config import BotSettings, truncate_embed_text
from core.llm_client import generate_with_fallback, get_safety_settings
from engines import flashcard_engine, shadowing_engine, tts_engine


def build_shadowing_prompt_embed(session: dict, *, is_retry: bool = False) -> discord.Embed:
    """建構統一的日語跟讀練習提示 Embed（供單字卡按鈕、重練按鈕與 !shadow 指令共用）。"""
    target_jp = str(session.get("target_jp") or "").strip()
    reading = str(session.get("reading") or "").strip()
    pitch = str(session.get("pitch") or "").strip()
    meaning = str(session.get("meaning") or "").strip()
    example_jp = str(session.get("example_jp") or "").strip()

    title = "🔁 再跟讀練習一次！" if is_retry else "🎙️ 日語跟讀發音練習已啟動！"
    lines = [f"# {target_jp}"]

    if example_jp and target_jp == example_jp:
        if reading:
            lines.append(f"**單字讀音**：【{reading}】" + (f" *(音調：{pitch})*" if pitch else ""))
    else:
        if reading:
            lines.append(f"**假名讀音**：【{reading}】" + (f" *(音調：{pitch})*" if pitch else ""))
        elif pitch:
            lines.append(f"**音調**：{pitch}")

    if meaning:
        lines.append(f"**中文意思**：{meaning}")
    if example_jp and target_jp != example_jp:
        lines.append(f"**參考例句**：> {example_jp}")

    lines.append(
        "\n⏱️ **請於 90 秒內**，直接在此頻道傳送 **Discord 語音訊息**（或上傳 30 秒 / 3 MB 內的短音檔）跟著唸一遍，AI 教練會立即聽寫與講評！"
    )

    embed = discord.Embed(
        title=title[:256],
        description=truncate_embed_text("\n".join(lines), max_len=3800),
        color=0xFF77A9,
    )
    embed.set_footer(text="支援格式：Discord 語音訊息 / .ogg / .m4a / .mp3 / .wav（限 30 秒內）")
    return embed


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
        *,
        preloaded_decks: Optional[list[dict]] = None,
        preloaded_deck_name: Optional[str] = None,
        preloaded_progress: Optional[dict] = None,
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
        self._decks_cache = preloaded_decks
        self._deck_name_cache = preloaded_deck_name
        self._progress_cache = preloaded_progress
        self.update_components()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user and interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "🔒 這張單字複習卡屬於原發起人，請輸入 `!card` 開啟你自己的複習面板喔！",
                ephemeral=True,
            )
            return False
        return True

    async def _refresh_view_data(self) -> None:
        def _fetch_sync():
            decks = flashcard_engine.list_decks()[:25]
            deck = flashcard_engine.get_deck(str(self.deck_id), exact_only=True)
            progress = flashcard_engine.get_card_progress(self.user_id, self.card["id"])
            return decks, (deck["name"] if deck else "預設牌組"), progress

        self._decks_cache, self._deck_name_cache, self._progress_cache = await asyncio.to_thread(_fetch_sync)

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

        decks = self._decks_cache if self._decks_cache is not None else flashcard_engine.list_decks()[:25]
        options = []
        for d in decks[:25]:
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

        if self.settings.enable_japanese_mode:
            btn_shadow = discord.ui.Button(
                label="🎙️ 跟讀練習",
                style=discord.ButtonStyle.secondary,
                custom_id="fc_shadow",
                row=2,
            )
            btn_shadow.callback = self.on_shadow
            self.add_item(btn_shadow)

    def build_embed(self) -> discord.Embed:
        if self._deck_name_cache is not None:
            deck_name = self._deck_name_cache
        else:
            deck = flashcard_engine.get_deck(str(self.deck_id), exact_only=True)
            deck_name = deck["name"] if deck else "預設牌組"

        progress = (
            self._progress_cache
            if self._progress_cache is not None
            else flashcard_engine.get_card_progress(self.user_id, self.card["id"])
        )
        box_lvl = progress.get("box", 1)
        rev_cnt = progress.get("review_count", 0)

        if not self.is_flipped:
            desc = f"# {self.card['front']}\n"
            if self.card.get("pos"):
                desc += f"*{self.card['pos']}*\n"
            desc += "\n點擊下方「👁️ 翻面查看」確認假名讀音與中文意思。"

            embed = discord.Embed(
                title=f"📖 日語單字卡 ｜ {deck_name}"[:256],
                description=truncate_embed_text(desc, max_len=3800),
                color=0x5865F2,
            )
            if self.card.get("notes"):
                embed.add_field(
                    name="💡 提示",
                    value=truncate_embed_text(str(self.card["notes"]), max_len=950),
                    inline=False,
                )
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
                title=f"📖 日語單字卡 ｜ {deck_name}"[:256],
                description=truncate_embed_text(desc, max_len=3800),
                color=0x57F287,
            )
            if self.card.get("example_jp"):
                ex_text = f"> {self.card['example_jp']}"
                if self.card.get("example_zh"):
                    ex_text += f"\n> *{self.card['example_zh']}*"
                embed.add_field(
                    name="📝 例句",
                    value=truncate_embed_text(ex_text, max_len=950),
                    inline=False,
                )
            if self.card.get("notes"):
                embed.add_field(
                    name="💡 備註/補語",
                    value=truncate_embed_text(str(self.card["notes"]), max_len=950),
                    inline=False,
                )

        embed.set_footer(text=f"卡片 ID: #{self.card['id']} ｜ 熟練度: 📦 箱子 {box_lvl} ｜ 複習次數: {rev_cnt}"[:2000])
        if self.card.get("image_filename"):
            img_path = flashcard_engine._safe_media_path(self.card["image_filename"])
            if img_path and img_path.is_file():
                embed.set_image(url=f"attachment://{img_path.name}")
        return embed

    async def on_flip(self, interaction: discord.Interaction):
        self.is_flipped = True
        await self._refresh_view_data()
        self.update_components()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    async def on_tts(self, interaction: discord.Interaction):
        tts_cog = self.bot.get_cog("TtsCog") if self.settings.enable_voice_tts else None
        if not isinstance(tts_cog, commands.Cog):
            tts_cog = None
        in_vc = bool(tts_cog and interaction.guild and tts_cog.is_connected_in_guild(interaction.guild.id))

        audio_filename = self.card.get("audio_filename")
        if audio_filename:
            audio_path = await asyncio.to_thread(flashcard_engine.get_card_audio_path, self.card)
            if audio_path and audio_path.is_file():
                if in_vc:
                    ok = await tts_cog.enqueue_local_audio_file(
                        interaction.guild,
                        audio_path,
                        label=f"單字卡:{self.card.get('front', '')}",
                    )
                    if ok:
                        await interaction.response.send_message(
                            f"🔊 已於語音頻道播放 **{str(self.card['front'])[:200]}** 的真人原音！",
                            ephemeral=True,
                        )
                        return
                try:
                    await interaction.response.send_message(
                        content=f"🔊 **{str(self.card['front'])[:200]}** 的真人原音：",
                        file=discord.File(str(audio_path), filename=audio_path.name),
                        ephemeral=True,
                    )
                    return
                except Exception as e:
                    self.logger.warning(f"發送原生音訊失敗: {e}")

        text_to_speak = self.card.get("reading") or self.card.get("front")
        if in_vc and text_to_speak:
            ok = await tts_cog.enqueue_ai_speech(
                interaction.guild,
                text_to_speak,
                is_japanese=True,
                force=True,
            )
            if ok:
                await interaction.response.send_message(
                    f"🔊 已於語音頻道朗讀「**{str(text_to_speak)[:200]}**」！",
                    ephemeral=True,
                )
                return

        if tts_cog and text_to_speak:
            await interaction.response.defer(ephemeral=True)
            try:
                res = await tts_cog.synthesize_ephemeral_jp_audio(
                    text_to_speak,
                    guild_id=interaction.guild.id if interaction.guild else None,
                )
                if res:
                    audio_bytes, ext = res
                    await interaction.followup.send(
                        content=f"🔊 **{str(self.card['front'])[:200]}** 的日語語音試聽（`{str(text_to_speak)[:200]}`）：",
                        file=discord.File(io.BytesIO(audio_bytes), filename=f"card_tts.{ext}"),
                        ephemeral=True,
                    )
                    return
                await interaction.followup.send(
                    f"🔊 朗讀發音：`{str(text_to_speak)[:200]}`（內容無可合成日語字元）",
                    ephemeral=True,
                )
                return
            except Exception as e:
                self.logger.warning(f"單字卡 Edge-TTS 合成失敗: {e}")
                await interaction.followup.send("⚠️ 語音合成暫時無法使用，請稍後再試。", ephemeral=True)
                return

        tts_ch = self.bot.get_channel(self.settings.tts_channel_id) if self.settings.tts_channel_id else None
        if tts_ch:
            try:
                await tts_ch.send(str(text_to_speak)[:1900])
                await interaction.response.send_message(
                    f"🔊 已傳送「**{str(text_to_speak)[:200]}**」至朗讀同步頻道！",
                    ephemeral=True,
                )
            except Exception as e:
                self.logger.warning(f"單字卡朗讀發送至同步頻道失敗: {e}")
                await interaction.response.send_message("⚠️ 朗讀發送失敗，請稍後再試。", ephemeral=True)
        else:
            await interaction.response.send_message(
                f"🔊 朗讀發音：`{str(text_to_speak)[:200]}`\n（目前無內建音檔且未啟用語音 TTS 或獨立同步頻道）",
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
                final_fallback_model=self.settings.final_fallback_model,
            )
            ai_text = resp.text.strip() if resp and resp.text else "抱歉，家教暫時無法提供解說～"
            ai_embed = discord.Embed(
                title=f"✨ AI 家教解說：{front} {('【' + reading + '】') if reading else ''}"[:256],
                description=truncate_embed_text(ai_text),
                color=0x9B59B6,
            )
            await interaction.followup.send(embed=ai_embed, ephemeral=True)
        except Exception as e:
            self.logger.warning(f"單字卡 AI 家教解說失敗 (front={front}): {e}")
            await interaction.followup.send("⚠️ AI 家教解說暫時無法取得，請稍後再試。", ephemeral=True)

    async def _advance_card(self, interaction: discord.Interaction, empty_message: str) -> None:
        next_c = await asyncio.to_thread(flashcard_engine.get_random_card_from_deck, self.deck_id)
        if not next_c:
            await interaction.response.edit_message(content=empty_message, embed=None, view=None)
            return
        self.card = next_c
        self.is_flipped = False
        await self._refresh_view_data()
        self.update_components()
        new_embed = self.build_embed()
        new_attachments = []
        if self.card.get("image_filename"):
            img_path = flashcard_engine._safe_media_path(self.card["image_filename"])
            if img_path and img_path.is_file():
                new_attachments.append(discord.File(str(img_path), filename=img_path.name))
        await interaction.response.edit_message(embed=new_embed, view=self, attachments=new_attachments)

    async def on_again(self, interaction: discord.Interaction):
        await asyncio.to_thread(flashcard_engine.record_review_result, interaction.user.id, self.card["id"], is_good=False)
        await self._advance_card(interaction, "🎉 這個牌組內沒有更多卡片囉！")

    async def on_good(self, interaction: discord.Interaction):
        await asyncio.to_thread(flashcard_engine.record_review_result, interaction.user.id, self.card["id"], is_good=True)
        await self._advance_card(interaction, "🎉 太棒了！這個牌組內的卡片都看過一輪囉！")

    async def on_next(self, interaction: discord.Interaction):
        await self._advance_card(interaction, "🎉 這個牌組內沒有卡片可供學習！")

    async def on_select_deck(self, interaction: discord.Interaction):
        target_deck_id = int(interaction.data["values"][0])
        if target_deck_id == self.deck_id:
            await interaction.response.send_message("這張卡片本來就在這個牌組中喔！", ephemeral=True)
            return
        ok = await asyncio.to_thread(flashcard_engine.move_card, self.card["id"], target_deck_id)
        target_deck = await asyncio.to_thread(flashcard_engine.get_deck, str(target_deck_id), exact_only=True)
        target_name = target_deck["name"] if target_deck else "新牌組"
        if ok:
            self.deck_id = target_deck_id
            self.card["deck_id"] = target_deck_id
            await self._refresh_view_data()
            self.update_components()
            await interaction.response.send_message(
                f"✅ 已成功將卡片「**{str(self.card['front'])[:100]}**」移至牌組：`{target_name[:100]}`！",
                ephemeral=True,
            )
            await interaction.message.edit(embed=self.build_embed(), view=self)
        else:
            await interaction.response.send_message("❌ 移動失敗，請稍後再試。", ephemeral=True)

    async def on_shadow(self, interaction: discord.Interaction):
        if not interaction.channel:
            return
        if self.is_flipped and self.card.get("example_jp"):
            target_jp = str(self.card["example_jp"]).strip()
        else:
            target_jp = str(self.card.get("front") or self.card.get("reading") or "").strip()
        if not target_jp:
            await interaction.response.send_message("⚠️ 這張卡片目前沒有可跟讀的日語內容。", ephemeral=True)
            return

        session = shadowing_engine.start_shadowing_session(
            interaction.channel.id,
            interaction.user.id,
            target_jp=target_jp,
            reading=self.card.get("reading", ""),
            pitch=self.card.get("pitch", ""),
            meaning=self.card.get("back", ""),
            example_jp=self.card.get("example_jp", ""),
            card_id=self.card.get("id"),
        )
        await interaction.response.send_message(
            embed=build_shadowing_prompt_embed(session),
            ephemeral=True,
        )


class ShadowingResultView(discord.ui.View):
    """日語跟讀評測結果互動視圖（提供標準發音試聽與一鍵再練一次）。"""

    def __init__(
        self,
        settings: BotSettings,
        owner_id: int,
        session_data: dict,
        *,
        bot: Optional[commands.Bot] = None,
        logger: Optional[logging.Logger] = None,
    ):
        super().__init__(timeout=300)
        self.settings = settings
        self.owner_id = owner_id
        self.session_data = dict(session_data or {})
        self.bot = bot
        self.logger = logger or logging.getLogger("DiscordGeminiBot.flashcard")
        self.message: Optional[discord.Message] = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user and interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "🔒 這是對方的跟讀評測面板，輸入 `!shadow` 即可開始你自己的日語口說練習！",
                ephemeral=True,
            )
            return False
        return True

    async def on_timeout(self) -> None:
        for child in self.children:
            if hasattr(child, "disabled"):
                child.disabled = True
        if self.message is not None:
            try:
                await self.message.edit(view=self)
            except Exception:
                pass

    def _resolve_tts_preview_text(self) -> str:
        target_jp = str(self.session_data.get("target_jp") or "").strip()
        reading = str(self.session_data.get("reading") or "").strip()
        example_jp = str(self.session_data.get("example_jp") or "").strip()
        if example_jp and target_jp == example_jp:
            return target_jp
        if reading and target_jp and not re.search(r"[\u3040-\u30FF]", target_jp):
            return reading
        return target_jp or reading

    @discord.ui.button(label="🔊 聽標準發音", style=discord.ButtonStyle.secondary, custom_id="shadow_tts", row=0)
    async def on_listen_tts(self, interaction: discord.Interaction, button: discord.ui.Button):
        speak_text = self._resolve_tts_preview_text()
        target_disp = str(self.session_data.get("target_jp") or speak_text)[:120]
        if not speak_text:
            await interaction.response.send_message("⚠️ 找不到可朗讀的日語內容。", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        try:
            res = None
            tts_cog = (
                self.bot.get_cog("TtsCog")
                if (self.bot is not None and self.settings.enable_voice_tts)
                else None
            )
            if isinstance(tts_cog, commands.Cog) and hasattr(tts_cog, "synthesize_ephemeral_jp_audio"):
                res = await tts_cog.synthesize_ephemeral_jp_audio(
                    speak_text,
                    guild_id=interaction.guild.id if interaction.guild else None,
                )
            else:
                cleaned = tts_engine.clean_text_for_tts(speak_text, max_chars=self.settings.tts_max_chars)
                if cleaned:
                    synth = tts_engine.HybridTtsSynthesizer(
                        default_voice=self.settings.tts_default_voice,
                        jp_voice=self.settings.tts_jp_voice,
                    )
                    try:
                        jp_short = tts_engine.normalize_edge_short_name(
                            self.settings.tts_jp_voice,
                            fallback=tts_engine.DEFAULT_JP_EDGE_VOICE,
                        )
                        audio_bytes, ext = await synth.synthesize_edge(cleaned, jp_short)
                        res = (audio_bytes, ext)
                    finally:
                        await synth.close()

            if res:
                audio_bytes, ext = res
                filename = "tts_preview.mp3" if ext == "mp3" else f"tts_preview.{ext}"
                await interaction.followup.send(
                    content=f"🔊 標準日語發音示範（`{target_disp}`）：",
                    file=discord.File(io.BytesIO(audio_bytes), filename=filename),
                    ephemeral=True,
                )
            else:
                await interaction.followup.send(
                    f"⚠️ `{target_disp}` 目前無可合成的有效日語發音字元。",
                    ephemeral=True,
                )
        except Exception as exc:
            self.logger.warning(f"跟讀標準發音試聽合成失敗: {exc}")
            await interaction.followup.send("⚠️ 標準發音合成暫時無法使用，請稍後再試。", ephemeral=True)

    @discord.ui.button(label="🔁 再跟讀一次", style=discord.ButtonStyle.primary, custom_id="shadow_retry", row=0)
    async def on_retry(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.channel:
            return
        target_jp = str(self.session_data.get("target_jp") or "").strip()
        if not target_jp:
            await interaction.response.send_message("⚠️ 找不到要跟讀的目標句子，請重新輸入 `!shadow`。", ephemeral=True)
            return
        new_session = shadowing_engine.start_shadowing_session(
            interaction.channel.id,
            interaction.user.id,
            target_jp=target_jp,
            reading=self.session_data.get("reading", ""),
            pitch=self.session_data.get("pitch", ""),
            meaning=self.session_data.get("meaning", ""),
            example_jp=self.session_data.get("example_jp", ""),
            card_id=self.session_data.get("card_id"),
        )
        await interaction.response.send_message(
            embed=build_shadowing_prompt_embed(new_session, is_retry=True),
            ephemeral=True,
        )
