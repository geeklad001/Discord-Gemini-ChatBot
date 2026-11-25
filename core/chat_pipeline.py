"""Chat routing, dynamic system instructions, history building, and response dispatch."""

import asyncio
import logging
import re
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Optional

import discord
from discord.ext import commands
from google import genai
from google.genai import types

from engines import (
    assistant_db,
    assistant_tools,
    boardgame_engine,
    game_wiki_engine,
    lifesim_engine,
    rpg_engine,
    safety_guardrail,
    video_engine,
)
from core.config import BASE_DIR, BotSettings, resolve_prompt_path, truncate_embed_text
from core.llm_client import generate_with_fallback, get_safety_settings, has_visible_text
from core.media import (
    build_current_message_media_parts,
    fetch_url_preview,
    is_audio_attachment,
    is_gif_or_media_share_url,
)

# In-memory cache of channel reset timestamps, backed by SQLite in assistant_db
channel_reset_timestamps: dict[int, datetime] = {}


def _get_tts_cog(bot: discord.Client, settings: BotSettings):
    if not settings.enable_voice_tts:
        return None
    get_cog = getattr(bot, "get_cog", None)
    if not callable(get_cog):
        return None
    cog = get_cog("TtsCog")
    if isinstance(cog, commands.Cog):
        return cog
    return None


def get_effective_reset_timestamp(channel_id: Optional[int]) -> Optional[datetime]:
    """Return the latest !reset timestamp for a channel, loading from SQLite on cache miss."""
    if not channel_id:
        return None
    if channel_id in channel_reset_timestamps:
        return channel_reset_timestamps[channel_id]
    persisted = assistant_db.get_channel_reset(channel_id)
    if persisted:
        channel_reset_timestamps[channel_id] = persisted
    return persisted


def record_channel_reset_timestamp(channel_id: int, reset_dt: datetime) -> None:
    """Update both in-memory cache and persistent SQLite reset timestamp."""
    channel_reset_timestamps[channel_id] = reset_dt
    assistant_db.set_channel_reset(channel_id, reset_dt)


def is_target_channel(channel: discord.abc.Messageable, settings: BotSettings) -> bool:
    """判斷發言位置是否為指定目標頻道、TTS朗讀頻道、一般日常群聊頻道、或其旗下的討論串 (Thread)。"""
    cid = getattr(channel, "id", None)
    valid_ids = {settings.target_channel_id}
    if settings.tts_channel_id:
        valid_ids.add(settings.tts_channel_id)
    if settings.general_channel_id:
        valid_ids.add(settings.general_channel_id)
    if cid in valid_ids:
        return True
    if hasattr(channel, "parent_id") and channel.parent_id in valid_ids:
        return True
    return False


def is_rpg_context(channel: discord.abc.Messageable, message_content: str = "") -> bool:
    """精準判斷當前發言是否處於 TRPG 跑團冒險情境中。"""
    cid = getattr(channel, "id", 0)

    if isinstance(channel, discord.Thread):
        tname = channel.name.lower()
        if any(kw in tname for kw in ("冒險", "rpg", "trpg", "跑團", "地下城", "副本")):
            return True

    if rpg_engine.is_rpg_session_active(cid):
        return True

    text = (message_content or "").lower()
    rpg_triggers = (
        "!rpg", "!roll", "!sheet", "!party", "!save", "!load", "!lore",
        "玩文字冒險", "玩rpg", "玩 trpg", "跑團", "開局冒險", "展開冒險", "擲骰檢定", "我的角色卡"
    )
    return any(trigger in text for trigger in rpg_triggers)


def _resolve_boardgame_channel_id(settings: BotSettings, channel: discord.abc.Messageable) -> int:
    cid = getattr(channel, "id", 0)
    if boardgame_engine.get_latest_game_in_channel(cid, include_finished_hours=4) is not None:
        return cid
    if settings.enable_japanese_mode and settings.tts_channel_id and cid == settings.tts_channel_id and settings.target_channel_id:
        if boardgame_engine.get_latest_game_in_channel(settings.target_channel_id, include_finished_hours=4) is not None:
            return settings.target_channel_id
    return cid


def build_dynamic_system_instruction(
    settings: BotSettings,
    prompt_filename: str,
    default_fallback: str,
    message: Optional[discord.Message] = None,
    ocr_text: str = "",
    recent_context_text: str = "",
    include_rpg_state: bool = True,
) -> str:
    """統一封裝動態 System Instruction 組裝邏輯（人設檔、安全護欄、時間、長期記憶、Lorebook、Wiki RAG、RPG 狀態卡）。"""
    prompt_path = resolve_prompt_path(prompt_filename)
    base_prompt = ""
    if prompt_path.exists():
        try:
            base_prompt = prompt_path.read_text(encoding="utf-8").strip()
        except Exception:
            pass
    if not base_prompt:
        base_prompt = default_fallback

    now_str = datetime.now(assistant_db.TZ_TAIPEI).strftime("%Y-%m-%d %H:%M:%S")
    time_info = f"\n\n【當前時間資訊】\n台北時間 (UTC+8)：{now_str}。若使用者要求提醒，請據此換算為未來的 ISO 8601 時間字串。"
    mem_info = assistant_db.format_memories_for_prompt()
    if mem_info:
        mem_info = f"\n\n{mem_info}"

    guardrail_info = (
        safety_guardrail.get_safety_guardrail_prompt(
            enable_japanese_mode=settings.enable_japanese_mode,
            enable_game_wiki=settings.enable_game_wiki,
        )
        if settings.enable_safety_guardrail
        else ""
    )

    extra_parts = [base_prompt, guardrail_info, time_info, mem_info]
    if message:
        matched_lore = rpg_engine.match_lorebook_for_text(message.clean_content)
        if matched_lore:
            extra_parts.append("\n\n【當前場景世界觀設定 (Lorebook)】\n" + "\n".join(f"- {l}" for l in matched_lore))

        if settings.enable_game_wiki:
            wiki_rag_block = game_wiki_engine.build_auto_rag_context(
                query_text=message.clean_content,
                ocr_extracted_text=ocr_text,
                recent_context_text=recent_context_text,
                is_vision=bool(ocr_text),
            )
            if wiki_rag_block:
                extra_parts.append(wiki_rag_block)

        if include_rpg_state and is_rpg_context(message.channel, message.clean_content):
            rpg_context = rpg_engine.format_rpg_context_for_prompt(
                message.channel.id, message.author.id, message.author.display_name
            )
            extra_parts.append(f"\n\n{rpg_context}")

        if settings.enable_board_games:
            bg_cid = _resolve_boardgame_channel_id(settings, message.channel)
            recent_bg = boardgame_engine.get_latest_game_in_channel(bg_cid, include_finished_hours=4)
            if (
                recent_bg
                or boardgame_engine.is_boardgame_intent(message.clean_content)
                or boardgame_engine.is_boardgame_intent(recent_context_text)
            ):
                bg_summary = boardgame_engine.get_board_status_summary_for_llm(bg_cid)
                extra_parts.append(
                    f"\n\n【本機權威棋盤桌遊狀態（嚴禁文字手畫棋盤或作弊）】\n{bg_summary}"
                )
            active_life = lifesim_engine.get_active_life_run(bg_cid, user_id=message.author.id)
            if active_life:
                life_summary = lifesim_engine.format_lifesim_status_for_llm(bg_cid, user_id=message.author.id)
                if life_summary:
                    extra_parts.append(
                        f"\n\n【《80億分之1》權威人生狀態（請使用 lifesim_start / lifesim_decide / lifesim_atlas，嚴禁自行編造數值或國籍結果）】\n{life_summary}"
                    )

    return "".join(extra_parts)


def get_current_system_prompt(
    settings: BotSettings,
    message: Optional[discord.Message] = None,
    ocr_text: str = "",
    recent_context_text: str = "",
) -> str:
    """動態讀取日語教學提示詞。"""
    return build_dynamic_system_instruction(
        settings=settings,
        prompt_filename=settings.system_prompt_file,
        default_fallback="你是一位住在日本、性格隨和平實的日本同齡朋友兼日語生活學伴。",
        message=message,
        ocr_text=ocr_text,
        recent_context_text=recent_context_text,
        include_rpg_state=True,
    )


def get_current_general_prompt(
    settings: BotSettings,
    message: Optional[discord.Message] = None,
    ocr_text: str = "",
    recent_context_text: str = "",
) -> str:
    """動態讀取日常群聊提示詞。"""
    return build_dynamic_system_instruction(
        settings=settings,
        prompt_filename=settings.general_prompt_file,
        default_fallback="你是 Discord 社群裡的常駐打工機器人，說話平實隨性帶點微吐槽，不刻意討好，平時正常群聊，隨選提供 TRPG 跑團服務。",
        message=message,
        ocr_text=ocr_text,
        recent_context_text=recent_context_text,
        include_rpg_state=True,
    )


async def _build_user_message_parts(
    msg: discord.Message,
    *,
    include_current_media: bool,
    enable_audio: bool = False,
    context_label: str,
    logger: Optional[logging.Logger] = None,
) -> list[types.Part]:
    """建構單一使用者訊息的 Gemini Parts（文字、網址預覽與當前訊息多模態附件）。"""
    parts: list[types.Part] = []
    author_name = msg.author.display_name
    msg_stickers = getattr(msg, "stickers", None) or []
    clean_content = (getattr(msg, "clean_content", "") or "").strip()
    if not clean_content and msg_stickers:
        st_names = "、".join(getattr(s, "name", "貼圖") for s in msg_stickers)
        raw_text = f"(傳送了貼圖【{st_names}】，請直接觀看貼圖畫面並自然回應)"
    elif not clean_content and getattr(msg, "attachments", None):
        has_vid = any(video_engine.is_video_attachment(a) for a in msg.attachments)
        has_audio = any(is_audio_attachment(a) for a in msg.attachments)
        has_gif = any((a.filename or "").lower().endswith(".gif") or (a.content_type or "").lower() == "image/gif" for a in msg.attachments)
        if has_vid:
            raw_text = "(傳送了一段短影片，請直接觀看並回應)"
        elif has_audio:
            raw_text = "(傳送了一段日語/雙語語音訊息，請聆聽發音與內容並回應)"
        elif has_gif:
            raw_text = "(傳送了一張 GIF 動圖，請直接觀看畫面並自然回應)"
        else:
            raw_text = "(傳送了一張圖片，請直接觀看並回應)"
    elif clean_content and is_gif_or_media_share_url(clean_content):
        raw_text = f"{clean_content} (傳送了一張 GIF 動圖，請觀看下方動圖畫面並自然回應)"
    else:
        raw_text = clean_content
    text = f"[{author_name}]: {raw_text}"

    if include_current_media and "http" in (getattr(msg, "clean_content", "") or ""):
        url_summary = await fetch_url_preview(msg.clean_content, logger=logger)
        if url_summary:
            text += f"\n\n{url_summary}"

    parts.append(types.Part.from_text(text=text))

    if include_current_media:
        media_parts = await build_current_message_media_parts(
            msg,
            context_label=context_label,
            logger=logger,
            enable_audio=enable_audio,
        )
        parts.extend(media_parts)

    return parts


async def build_chat_history_contents(
    bot: discord.Client,
    settings: BotSettings,
    channel: discord.abc.Messageable,
    current_message: discord.Message,
    is_japanese_dual_mode: bool = False,
    logger: Optional[logging.Logger] = None,
    anchor_message_id: Optional[int] = None,
) -> list[types.Content]:
    """統一抓取滑動視窗歷史訊息並格式化為 Gemini SDK 的 Content 清單。"""
    if is_japanese_dual_mode and not isinstance(channel, discord.Thread):
        source_channel = bot.get_channel(settings.target_channel_id) or channel
    else:
        source_channel = channel

    reset_after = await asyncio.to_thread(get_effective_reset_timestamp, getattr(source_channel, "id", None))
    raw_messages: list[discord.Message] = []
    history_limit = settings.thread_window_limit if isinstance(source_channel, discord.Thread) else settings.sliding_window_limit

    same_channel = getattr(source_channel, "id", None) == getattr(channel, "id", None)
    curr_msg_id = getattr(current_message, "id", None)
    if isinstance(anchor_message_id, int):
        effective_anchor_id: Optional[int] = anchor_message_id
    elif same_channel and isinstance(curr_msg_id, int):
        effective_anchor_id = curr_msg_id
    else:
        effective_anchor_id = None

    history_kwargs: dict = {"limit": history_limit, "oldest_first": False}
    if isinstance(effective_anchor_id, int):
        history_kwargs["before"] = discord.Object(id=effective_anchor_id + 1)

    async for msg in source_channel.history(**history_kwargs):
        if reset_after and msg.created_at <= reset_after:
            break
        msg_id = getattr(msg, "id", None)
        if isinstance(effective_anchor_id, int) and isinstance(msg_id, int) and msg_id > effective_anchor_id:
            continue
        if msg.content.strip().startswith("!"):
            continue
        has_stickers = bool(getattr(msg, "stickers", None))
        has_board_embed = bool(
            msg.author == bot.user
            and msg.embeds
            and (
                ((msg.embeds[0].footer.text or "") if msg.embeds[0].footer else "").startswith("棋局 #")
                or "桌遊與棋盤大廳" in (msg.embeds[0].title or "")
            )
        )
        if not has_board_embed:
            if is_japanese_dual_mode:
                if not msg.content.strip() and not msg.embeds and not msg.attachments and not has_stickers:
                    continue
            else:
                if not msg.content.strip() and not msg.attachments and not has_stickers:
                    continue
        raw_messages.append(msg)

    raw_messages.reverse()
    turns: list[types.Content] = []
    context_label = "日語共學" if is_japanese_dual_mode else "日常群聊"
    included_current = False

    for msg in raw_messages:
        parts: list[types.Part] = []
        if msg.author == bot.user:
            if msg.embeds:
                embed = msg.embeds[0]
                footer_txt = embed.footer.text if embed.footer and embed.footer.text else ""
                title_txt = embed.title or ""
                if (
                    footer_txt.startswith("棋局 #")
                    or footer_txt.startswith("人生 #")
                    or "桌遊與棋盤大廳" in title_txt
                    or "Discord 遊戲大廳" in title_txt
                ):
                    moves_field = next((f.value for f in (embed.fields or []) if "最近著法" in (f.name or "")), "")
                    summary_line = f"（系統已貼出{title_txt}｜近期著法：{moves_field}）" if moves_field else f"（系統已貼出{title_txt}）"
                    parts.append(types.Part.from_text(text=summary_line))
                    turns.append(types.Content(role="model", parts=parts))
                    continue

            if is_japanese_dual_mode and msg.embeds and not msg.content.strip():
                embed = msg.embeds[0]
                author_name = embed.author.name if embed.author and embed.author.name else ""
                if "(來自綜合頻道)" in author_name or "(來自日文頻道)" in author_name:
                    clean_author = author_name.replace("(來自綜合頻道)", "").replace("(來自日文頻道)", "").strip()
                    role = "user"
                    mirror_text = f"[{clean_author}]: {embed.description}"
                    if isinstance(anchor_message_id, int) and getattr(msg, "id", None) == anchor_message_id:
                        included_current = True
                        if "http" in (getattr(current_message, "clean_content", "") or ""):
                            url_summary = await fetch_url_preview(current_message.clean_content, logger=logger)
                            if url_summary:
                                mirror_text += f"\n\n{url_summary}"
                        parts.append(types.Part.from_text(text=mirror_text))
                        media_parts = await build_current_message_media_parts(
                            current_message,
                            context_label=context_label,
                            logger=logger,
                            enable_audio=is_japanese_dual_mode,
                        )
                        parts.extend(media_parts)
                    else:
                        parts.append(types.Part.from_text(text=mirror_text))
                else:
                    continue
            else:
                role = "model"
                model_text = msg.clean_content.strip()
                if settings.enable_safety_guardrail:
                    model_text = safety_guardrail.sanitize_model_output(
                        model_text,
                        enable_japanese_mode=settings.enable_japanese_mode,
                    )
                if not model_text:
                    continue
                parts.append(types.Part.from_text(text=model_text))
        else:
            role = "user"
            is_current = isinstance(curr_msg_id, int) and getattr(msg, "id", None) == curr_msg_id
            if is_current:
                included_current = True
            parts = await _build_user_message_parts(
                msg,
                include_current_media=is_current,
                enable_audio=is_japanese_dual_mode,
                context_label=context_label,
                logger=logger,
            )

        if parts:
            turns.append(types.Content(role=role, parts=parts))

    if not included_current and current_message is not None:
        curr_content = (getattr(current_message, "content", "") or "").strip()
        has_payload = bool(
            curr_content
            or getattr(current_message, "attachments", None)
            or getattr(current_message, "stickers", None)
        )
        curr_created_at = getattr(current_message, "created_at", None)
        is_before_reset = bool(reset_after and curr_created_at and curr_created_at <= reset_after)
        eligible = (
            getattr(current_message, "author", None) != bot.user
            and not curr_content.startswith("!")
            and has_payload
            and not is_before_reset
        )
        if eligible:
            should_append = same_channel
            if not same_channel:
                expected_prefix = f"[{current_message.author.display_name}]:"
                last_turn = turns[-1] if turns else None
                last_first_text = ""
                if last_turn and last_turn.role == "user" and last_turn.parts:
                    last_first_text = getattr(last_turn.parts[0], "text", "") or ""
                if not last_first_text.startswith(expected_prefix):
                    should_append = True

            if should_append:
                fallback_parts = await _build_user_message_parts(
                    current_message,
                    include_current_media=True,
                    enable_audio=is_japanese_dual_mode,
                    context_label=context_label,
                    logger=logger,
                )
                if fallback_parts:
                    turns.append(types.Content(role="user", parts=fallback_parts))

    while turns and turns[-1].role != "user":
        dropped = turns.pop()
        if logger:
            logger.warning(
                f"⚠️ ({context_label}) 偵測到歷史對話結尾為非 user 回合 (role={dropped.role})，已自動移除以符合 Gemini API 規範。"
            )

    return turns


async def send_split_messages(
    destination: discord.abc.Messageable,
    text: str,
    *,
    timeout: float = 30.0,
) -> int:
    """防止超過 Discord 單則訊息 2000 字元限制，自動切分段落並濾除空白區塊送出。"""
    cleaned = (text or "").strip()
    if not has_visible_text(cleaned):
        raise ValueError("回覆內容為空白，無法發送至 Discord。")

    max_len = 1900
    if len(cleaned) <= max_len:
        await asyncio.wait_for(destination.send(cleaned), timeout=timeout)
        return 1

    lines = cleaned.split("\n")
    chunk = ""
    sent_count = 0
    for line in lines:
        if len(chunk) + len(line) + 1 > max_len:
            piece = chunk.strip()
            if has_visible_text(piece):
                await asyncio.wait_for(destination.send(piece), timeout=timeout)
                sent_count += 1
            chunk = ""
            while len(line) > max_len:
                sub_piece = line[:max_len].strip()
                if has_visible_text(sub_piece):
                    await asyncio.wait_for(destination.send(sub_piece), timeout=timeout)
                    sent_count += 1
                line = line[max_len:]
            chunk = line
        else:
            chunk = f"{chunk}\n{line}" if chunk else line

    tail = chunk.strip()
    if has_visible_text(tail):
        await asyncio.wait_for(destination.send(tail), timeout=timeout)
        sent_count += 1

    if sent_count == 0:
        raise ValueError("切分後無可發送的非空白訊息內容。")
    return sent_count


async def send_synchronized_response(
    bot: discord.Client,
    settings: BotSettings,
    origin_channel: discord.abc.Messageable,
    text: str,
    logger: Optional[logging.Logger] = None,
) -> None:
    """雙頻道智能回覆：主頻道發送日語 + 中文解說小卡；TTS 頻道僅發送純日語正文。"""
    if "[ZH]" in text:
        parts = text.split("[ZH]", 1)
        jp_part = parts[0].replace("[JP]", "").strip()
        zh_part = parts[1].strip()
    else:
        raw_clean = text.replace("[JP]", "").strip()
        has_kana = bool(re.search(r"[\u3040-\u309F\u30A0-\u30FF]", raw_clean))
        if not has_kana and "[JP]" not in text:
            jp_part = ""
            zh_part = raw_clean
        else:
            jp_part = raw_clean
            zh_part = None

    embed = None
    if has_visible_text(zh_part):
        embed = discord.Embed(
            description=truncate_embed_text(zh_part),
            color=0xFFB6C1,
        )
        embed.set_footer(text="🌸 繁體中文對照與單字註記")

    if not has_visible_text(jp_part) and not embed:
        raise ValueError("日語共學回覆內容為空白，無法發送至 Discord。")

    main_channel = bot.get_channel(settings.target_channel_id)
    tts_channel = bot.get_channel(settings.tts_channel_id) if settings.tts_channel_id else None

    if isinstance(origin_channel, discord.Thread):
        if len(jp_part) <= 1900:
            await origin_channel.send(content=jp_part or None, embed=embed)
        else:
            await send_split_messages(origin_channel, jp_part)
            if embed:
                await origin_channel.send(embed=embed)
        if has_visible_text(jp_part) and tts_channel and tts_channel.id != origin_channel.id:
            if len(jp_part) <= 1900:
                await tts_channel.send(content=jp_part)
            else:
                await send_split_messages(tts_channel, jp_part)
        if has_visible_text(jp_part):
            tts_cog = _get_tts_cog(bot, settings)
            guild = getattr(origin_channel, "guild", None)
            if tts_cog and guild:
                await tts_cog.enqueue_ai_speech(guild, jp_part, is_japanese=True, channel=origin_channel)
        return

    target_main = main_channel or origin_channel
    if len(jp_part) <= 1900:
        await target_main.send(content=jp_part or None, embed=embed)
    else:
        await send_split_messages(target_main, jp_part)
        if embed:
            await target_main.send(embed=embed)

    if has_visible_text(jp_part) and tts_channel and tts_channel.id != target_main.id:
        if len(jp_part) <= 1900:
            await tts_channel.send(content=jp_part)
        else:
            await send_split_messages(tts_channel, jp_part)
        if logger:
            logger.info(f"📻 已同步推播純日語至朗讀頻道 (ID: {settings.tts_channel_id})")
    if has_visible_text(jp_part):
        tts_cog = _get_tts_cog(bot, settings)
        guild = getattr(target_main, "guild", None) or getattr(origin_channel, "guild", None)
        if tts_cog and guild:
            await tts_cog.enqueue_ai_speech(guild, jp_part, is_japanese=True, channel=target_main)


def _extract_current_image_parts(message: discord.Message, contents: list[types.Content]) -> list[types.Part]:
    """從當前訊息的 Content 中提取使用者上傳的圖片附件 Part（供 Pass-1 純視覺 OCR 預提取使用）。"""
    has_img_att = any(
        att.content_type and att.content_type.startswith("image/")
        for att in getattr(message, "attachments", [])
    )
    if not has_img_att or not contents:
        return []

    last_turn = contents[-1]
    img_parts: list[types.Part] = []
    for p in (last_turn.parts or []):
        inline = getattr(p, "inline_data", None)
        if inline and getattr(inline, "mime_type", "") and inline.mime_type.startswith("image/"):
            img_parts.append(p)
    return img_parts


def _extract_recent_context_text(contents: list[types.Content], max_turns: int = 3) -> str:
    """提取最近幾輪對話的純文字脈絡，讓代名詞追問也能命中知識庫。"""
    if not contents or len(contents) <= 1:
        return ""
    recent_turns = contents[max(0, len(contents) - max_turns):-1]
    snippets: list[str] = []
    for turn in recent_turns:
        for p in (turn.parts or []):
            t = getattr(p, "text", None)
            if t:
                snippets.append(t[:300])
    return "\n".join(snippets)


async def prepare_and_generate_chat_response(
    gemini_client: genai.Client,
    settings: BotSettings,
    message: discord.Message,
    contents: list[types.Content],
    system_prompt_fn,
    base_temperature: float,
    context_label: str,
    logger: Optional[logging.Logger] = None,
) -> tuple[str, str]:
    """統一執行聊天回合的 Pass-1 OCR（若啟用 Wiki）、RAG 與工具瘦身、Gemini 呼叫與輸出護欄。"""
    models_to_try = settings.models_to_try

    ocr_text = ""
    if settings.enable_game_wiki:
        image_parts = _extract_current_image_parts(message, contents)
        if image_parts:
            ocr_text = await game_wiki_engine.extract_image_text_pass1(
                gemini_client=gemini_client,
                models_to_try=models_to_try,
                image_parts=image_parts,
                disable_safety_settings=settings.disable_safety_settings,
                final_fallback_model=settings.final_fallback_model,
            )

    recent_text = _extract_recent_context_text(contents)
    in_rpg = await asyncio.to_thread(is_rpg_context, message.channel, message.clean_content)
    in_boardgame = False
    in_lifesim = False
    if settings.enable_board_games:
        bg_cid = await asyncio.to_thread(_resolve_boardgame_channel_id, settings, message.channel)
        recent_bg = await asyncio.to_thread(
            boardgame_engine.get_latest_game_in_channel, bg_cid, include_finished_hours=4
        )
        in_boardgame = bool(
            recent_bg is not None
            or boardgame_engine.is_boardgame_intent(message.clean_content)
            or boardgame_engine.is_boardgame_intent(recent_text)
        )
        active_life = await asyncio.to_thread(
            lifesim_engine.get_active_life_run, bg_cid, user_id=message.author.id
        )
        in_lifesim = bool(
            active_life is not None
            or lifesim_engine.is_lifesim_intent(message.clean_content)
            or lifesim_engine.is_lifesim_intent(recent_text)
        )
    scoped_tools = assistant_tools.get_scoped_tools(
        enable_flashcards=settings.enable_flashcard_mode,
        include_rpg=in_rpg,
        enable_game_wiki=settings.enable_game_wiki,
        include_boardgames=in_boardgame,
        include_lifesim=in_lifesim,
    )

    is_factual_game_query = bool(
        settings.enable_game_wiki
        and (
            game_wiki_engine.is_game_or_korean_context(ocr_text)
            or game_wiki_engine.is_game_or_korean_context(message.clean_content)
            or game_wiki_engine.is_game_or_korean_context(recent_text)
        )
    )
    thinking_lvl = "medium" if (in_rpg or is_factual_game_query) else "low"
    system_instruction = await asyncio.to_thread(
        system_prompt_fn,
        settings,
        message,
        ocr_text=ocr_text,
        recent_context_text=recent_text,
    )
    config = types.GenerateContentConfig(
        system_instruction=system_instruction,
        thinking_config=types.ThinkingConfig(thinking_level=thinking_lvl),
        temperature=0.15 if is_factual_game_query else base_temperature,
        tools=scoped_tools,
        safety_settings=get_safety_settings(settings.disable_safety_settings),
    )

    token = assistant_tools.current_discord_msg.set(message)
    try:
        response, used_model = await generate_with_fallback(
            client=gemini_client,
            models_to_try=models_to_try,
            contents=contents,
            config=config,
            timeout=45.0,
            context_label=context_label,
            logger=logger,
            final_fallback_model=settings.final_fallback_model,
        )
    finally:
        assistant_tools.current_discord_msg.reset(token)

    if response and has_visible_text(response.text):
        thoughts_count = getattr(response.usage_metadata, "thoughts_token_count", 0) if response.usage_metadata else 0
        reply = response.text.strip()
        if settings.enable_safety_guardrail:
            reply = safety_guardrail.sanitize_model_output(
                reply,
                enable_japanese_mode=settings.enable_japanese_mode,
            ).strip()
        if not has_visible_text(reply):
            raise RuntimeError(f"({context_label}) 輸出經安全護欄處理後為空白。")
        if logger:
            if thoughts_count:
                logger.info(f"💭 {used_model} 深度思考完成（思考 {thoughts_count} tokens｜輸出 {len(reply)} 字）")
            else:
                logger.info(f"💬 {used_model} 回覆生成完成（輸出 {len(reply)} 字）")
        return reply, (used_model or settings.primary_model)

    raise RuntimeError(f"({context_label}) 無法回應訊息。")


def _strip_hallucinated_board_card_echo(text: str) -> str:
    """移除輕量模型偶爾誤複述的系統棋盤卡片文字（實際卡片與 PNG 由程式自動發送）。"""
    if not text:
        return ""
    cleaned = re.sub(r"\[棋盤卡片\][\s\S]*$", "", text).strip()
    card_line_re = re.compile(
        r"^(?:[⚫⚪♟️🔴🟡⭕👑🎲👉📌📊📜🧠]\s*)?"
        r"(?:(?:黑白棋|西洋棋|四子棋|五子棋)\s*\([^)]+\)\s*[—\-]|"
        r"(?:黑棋|白棋|紅方|黃方|白方|黑方)\s*[:：].*🆚|"
        r"目前輪到\s*[:：]|最新動態\s*[:：]|目前子數\s*[:：]|最近著法（第|"
        r"(?:LLM|我方)\s*\([^)]+\)\s*[⚫⚪♟️🔴🟡⭕:]?)"
    )
    kept_lines = [line for line in cleaned.splitlines() if not card_line_re.match(line.strip())]
    return "\n".join(kept_lines).strip()


async def _flush_pending_board_cards(
    bot: discord.Client,
    channel: discord.abc.Messageable,
    pending_updates: list,
    reply_text: str = "",
    logger: Optional[logging.Logger] = None,
) -> bool:
    """
    將自然語言工具呼叫產生的最新棋盤狀態（含非同步 LLM 對手回合）與對話文字合併為單一 Discord 訊息發送。
    回傳 True 表示已成功將 reply_text 一併附帶於棋盤卡片訊息送出，呼叫端無須再重複發送純文字訊息。
    """
    if not pending_updates:
        return False
    latest_items: dict[int, dict] = {}
    for item in pending_updates:
        if isinstance(item, dict):
            gid = item["game_id"]
            prev = latest_items.get(gid)
            latest_items[gid] = {
                "game_id": gid,
                "note": item.get("note", ""),
                "run_ai": bool(item.get("run_ai")) or (bool(prev.get("run_ai")) if prev else False),
            }
        else:
            gid, note = item
            latest_items[gid] = {"game_id": gid, "note": note, "run_ai": False}

    bg_cog = bot.get_cog("BoardGameCog") if hasattr(bot, "get_cog") else None
    if not bg_cog:
        return False

    cleaned_reply = _strip_hallucinated_board_card_echo(reply_text)
    sent_reply_with_card = False
    items_list = list(latest_items.items())

    for idx, (gid, info) in enumerate(items_list):
        try:
            game = await asyncio.to_thread(boardgame_engine.get_game_by_id, gid)
            if not game:
                continue
            card_note = info["note"]
            llm_thoughts = ""
            if info["run_ai"] and game["status"] == "active" and game["mode"] == "pve" and game["turn"] == 2:
                game, card_note, llm_thoughts = await bg_cog._run_pve_turn_if_needed(game, card_note)

            msg_parts = []
            if idx == 0 and cleaned_reply:
                msg_parts.append(cleaned_reply)
            if llm_thoughts:
                msg_parts.append(llm_thoughts)
            merged_content = "\n".join(msg_parts).strip()

            if merged_content and len(merged_content) > 1900:
                await send_split_messages(channel, merged_content)
                merged_content = ""

            await bg_cog.send_game_card(channel, game, last_note=card_note, content_text=merged_content)
            if idx == 0:
                sent_reply_with_card = True
        except Exception as e:
            if logger:
                logger.error(f"❌ 發送圖形棋盤卡失敗 (game_id={gid}): {e}", exc_info=True)
    return sent_reply_with_card


async def _flush_pending_lifesim_cards(
    bot: discord.Client,
    channel: discord.abc.Messageable,
    pending_updates: list[dict],
    reply_text: str = "",
    logger: Optional[logging.Logger] = None,
) -> bool:
    """將自然語言觸發的《80億分之1》人生卡片（含非同步 LLM 劇情/自訂判定）與回覆合併發送。"""
    if not pending_updates:
        return False
    life_cog = bot.get_cog("LifeSimCog") if hasattr(bot, "get_cog") else None
    if not life_cog:
        return False

    latest_by_run: dict[int, dict] = {}
    for item in pending_updates:
        rid = item.get("run_id")
        if not rid:
            continue
        prev = latest_by_run.get(rid) or {}
        latest_by_run[rid] = {
            "run_id": rid,
            "note": item.get("note") or prev.get("note", ""),
            "include_birth_cert": bool(item.get("include_birth_cert")) or bool(prev.get("include_birth_cert")),
            "custom_decision": item.get("custom_decision") or prev.get("custom_decision", ""),
            "needs_llm_next": bool(item.get("needs_llm_next")) or bool(prev.get("needs_llm_next")),
        }

    cleaned_reply = _strip_hallucinated_board_card_echo(reply_text)
    sent_reply_with_card = False
    items_list = list(latest_by_run.items())

    for idx, (rid, info) in enumerate(items_list):
        try:
            run = await asyncio.to_thread(lifesim_engine.get_life_run_by_id, rid)
            if not run:
                continue
            note = info["note"]
            if info["custom_decision"] and run["status"] == "active":
                outcome, deltas = await life_cog.evaluate_llm_custom_choice(run, info["custom_decision"])
                run, note = await asyncio.to_thread(
                    lifesim_engine.resolve_life_step,
                    rid,
                    run["user_id"],
                    custom_deltas=deltas,
                    custom_outcome_text=outcome,
                )
            if info["needs_llm_next"] and lifesim_engine.should_use_llm_for_stage(run):
                run = await life_cog.populate_llm_next_event(run)

            merged_content = cleaned_reply if (idx == 0 and cleaned_reply) else ""
            if merged_content and len(merged_content) > 1900:
                await send_split_messages(channel, merged_content)
                merged_content = ""

            await life_cog.send_life_card(
                channel,
                run,
                note=note,
                include_birth_cert=info["include_birth_cert"],
                content_text=merged_content,
            )
            if idx == 0:
                sent_reply_with_card = True
        except Exception as e:
            if logger:
                logger.error(f"❌ 發送《80億分之1》人生卡片失敗 (run_id={rid}): {e}", exc_info=True)
    return sent_reply_with_card


async def _flush_pending_rpg_checks(
    bot: discord.Client,
    channel: discord.abc.Messageable,
    pending_checks: Optional[list],
    reply_text: str = "",
    logger: Optional[logging.Logger] = None,
) -> bool:
    """將 AI DM 透過 `rpg_request_check` 排程的互動擲骰檢定按鈕與敘事文字合併送出。"""
    if not pending_checks:
        return False
    rpg_cog = bot.get_cog("RpgCog") if hasattr(bot, "get_cog") else None
    if not rpg_cog or not hasattr(rpg_cog, "send_check_prompt_view"):
        return False
    cleaned_reply = _strip_hallucinated_board_card_echo(reply_text)
    try:
        sent = await rpg_cog.send_check_prompt_view(
            channel,
            list(pending_checks)[:3],
            content_text=cleaned_reply,
        )
        return sent is not None
    except Exception as e:
        if logger:
            logger.error(f"❌ 發送 TRPG 互動擲骰檢定按鈕失敗: {e}", exc_info=True)
        return False


async def _flush_all_pending_cards(
    bot: discord.Client,
    channel: discord.abc.Messageable,
    pending_cards: list,
    pending_life: list,
    reply_text: str,
    logger: Optional[logging.Logger] = None,
    pending_rpg_checks: Optional[list] = None,
) -> bool:
    """統一派送排程中的棋盤卡、《80億分之1》人生卡與 TRPG 互動檢定按鈕，避免重複發送回覆文字。"""
    sent_with_card = await _flush_pending_board_cards(
        bot, channel, pending_cards, reply_text=reply_text, logger=logger
    )
    sent_with_life = await _flush_pending_lifesim_cards(
        bot,
        channel,
        pending_life,
        reply_text="" if sent_with_card else reply_text,
        logger=logger,
    )
    sent_with_rpg = await _flush_pending_rpg_checks(
        bot,
        channel,
        pending_rpg_checks,
        reply_text="" if (sent_with_card or sent_with_life) else reply_text,
        logger=logger,
    )
    return sent_with_card or sent_with_life or sent_with_rpg


async def handle_general_chat(
    bot: discord.Client,
    gemini_client: genai.Client,
    settings: BotSettings,
    message: discord.Message,
    logger: Optional[logging.Logger] = None,
) -> None:
    """處理日常群聊（支援多模態、按需 RAG 與動態工具瘦身）。"""
    author_name = message.author.display_name
    if logger:
        logger.info(f"💬 收到來自 [{author_name}] 的日常閒聊: {message.clean_content}")

    async with message.channel.typing():
        pending_cards: list = []
        pending_life: list = []
        pending_rpg_checks: list = []
        bg_token = boardgame_engine.pending_board_card_updates.set(pending_cards)
        ls_token = lifesim_engine.pending_lifesim_card_updates.set(pending_life)
        rpg_token = rpg_engine.pending_rpg_check_requests.set(pending_rpg_checks)
        try:
            contents = await build_chat_history_contents(
                bot=bot,
                settings=settings,
                channel=message.channel,
                current_message=message,
                is_japanese_dual_mode=False,
                logger=logger,
            )
            if not contents:
                return

            reply_text, _ = await prepare_and_generate_chat_response(
                gemini_client=gemini_client,
                settings=settings,
                message=message,
                contents=contents,
                system_prompt_fn=get_current_general_prompt,
                base_temperature=0.75,
                context_label="日常群聊",
                logger=logger,
            )
            sent_with_any = await _flush_all_pending_cards(
                bot,
                message.channel,
                pending_cards,
                pending_life,
                reply_text=reply_text,
                logger=logger,
                pending_rpg_checks=pending_rpg_checks,
            )
            if not sent_with_any:
                stripped_reply = _strip_hallucinated_board_card_echo(reply_text).strip()
                final_reply = stripped_reply if has_visible_text(stripped_reply) else reply_text.strip()
                await send_split_messages(message.channel, final_reply)
                if logger:
                    logger.info(f"✅ (日常群聊) 已送出回覆 ({len(final_reply)} 字)")
            elif logger:
                logger.info("✅ (日常群聊) 已隨互動卡片一併送出回覆")

            tts_cog = _get_tts_cog(bot, settings)
            if tts_cog and message.guild:
                spoken_text = _strip_hallucinated_board_card_echo(reply_text).strip() or reply_text.strip()
                if has_visible_text(spoken_text):
                    await tts_cog.enqueue_ai_speech(
                        message.guild,
                        spoken_text,
                        is_japanese=False,
                        channel=message.channel,
                    )

        except Exception as e:
            if logger:
                logger.error(f"❌ 日常群聊呼叫或送出回覆失敗: {e}", exc_info=True)
            try:
                await message.channel.send("抱歉，我剛才斷線了一下，請再說一次。")
            except Exception as send_err:
                if logger:
                    logger.error(f"❌ 日常群聊錯誤提示發送失敗: {send_err}", exc_info=True)
        finally:
            boardgame_engine.pending_board_card_updates.reset(bg_token)
            lifesim_engine.pending_lifesim_card_updates.reset(ls_token)
            rpg_engine.pending_rpg_check_requests.reset(rpg_token)


async def handle_japanese_learning_chat(
    bot: discord.Client,
    gemini_client: genai.Client,
    settings: BotSettings,
    message: discord.Message,
    logger: Optional[logging.Logger] = None,
) -> None:
    """處理日語共學雙頻道對話（音字分離、小卡鏡像同步、HornBot 朗讀）。"""
    author_name = message.author.display_name
    if logger:
        logger.info(f"🌸 收到來自 [{author_name}] 的日語學習訊息: {message.clean_content}")

    main_channel = bot.get_channel(settings.target_channel_id)
    tts_channel = bot.get_channel(settings.tts_channel_id) if settings.tts_channel_id else None

    mirror_msg = None
    if tts_channel and main_channel:
        author_avatar = message.author.display_avatar.url if message.author.display_avatar else None
        if not message.clean_content.strip() and message.attachments:
            has_vid = any(video_engine.is_video_attachment(a) for a in message.attachments)
            has_audio = any(is_audio_attachment(a) for a in message.attachments)
            if has_vid:
                embed_desc = "🎬 [分享了一段短影片]"
            elif has_audio:
                embed_desc = "🎙️ [傳送了一段語音訊息]"
            else:
                embed_desc = "🖼️ [分享了一張圖片]"
        else:
            embed_desc = message.clean_content.strip() or "🖼️ [分享了一張圖片]"

        if message.channel.id == settings.target_channel_id:
            mirror_embed = discord.Embed(description=embed_desc, color=0xA0C4FF)
            mirror_embed.set_author(name=f"{author_name} (來自綜合頻道)", icon_url=author_avatar)
            if message.attachments and message.attachments[0].content_type and message.attachments[0].content_type.startswith("image/"):
                mirror_embed.set_image(url=message.attachments[0].url)
            try:
                await tts_channel.send(embed=mirror_embed)
            except Exception as e:
                if logger:
                    logger.error(f"❌ 鏡像小卡發送至日文頻道失敗: {e}")

        elif message.channel.id == settings.tts_channel_id:
            mirror_embed = discord.Embed(description=embed_desc, color=0xBDB2FF)
            mirror_embed.set_author(name=f"{author_name} (來自日文頻道)", icon_url=author_avatar)
            if message.attachments and message.attachments[0].content_type and message.attachments[0].content_type.startswith("image/"):
                mirror_embed.set_image(url=message.attachments[0].url)
            try:
                mirror_msg = await main_channel.send(embed=mirror_embed)
            except Exception as e:
                if logger:
                    logger.error(f"❌ 鏡像小卡發送至綜合頻道失敗: {e}")

    async with message.channel.typing():
        pending_cards: list = []
        pending_life: list = []
        pending_rpg_checks: list = []
        bg_token = boardgame_engine.pending_board_card_updates.set(pending_cards)
        ls_token = lifesim_engine.pending_lifesim_card_updates.set(pending_life)
        rpg_token = rpg_engine.pending_rpg_check_requests.set(pending_rpg_checks)
        try:
            contents = await build_chat_history_contents(
                bot=bot,
                settings=settings,
                channel=message.channel,
                current_message=message,
                is_japanese_dual_mode=True,
                logger=logger,
                anchor_message_id=getattr(mirror_msg, "id", None),
            )
            if not contents:
                return

            reply_text, _ = await prepare_and_generate_chat_response(
                gemini_client=gemini_client,
                settings=settings,
                message=message,
                contents=contents,
                system_prompt_fn=get_current_system_prompt,
                base_temperature=0.7,
                context_label="日語共學",
                logger=logger,
            )
            if pending_cards or pending_life or pending_rpg_checks:
                if "[ZH]" in reply_text:
                    jp_raw, zh_raw = reply_text.split("[ZH]", 1)
                    jp_clean = _strip_hallucinated_board_card_echo(jp_raw.replace("[JP]", "").strip())
                    zh_clean = _strip_hallucinated_board_card_echo(zh_raw.strip())
                    if jp_clean and tts_channel and tts_channel.id != message.channel.id:
                        try:
                            await tts_channel.send(content=jp_clean)
                        except discord.HTTPException:
                            pass
                    if jp_clean:
                        tts_cog = _get_tts_cog(bot, settings)
                        if tts_cog and message.guild:
                            await tts_cog.enqueue_ai_speech(
                                message.guild,
                                jp_clean,
                                is_japanese=True,
                                channel=message.channel,
                            )
                    merged_reply = "\n".join(p for p in (jp_clean, zh_clean) if p)
                else:
                    merged_reply = _strip_hallucinated_board_card_echo(reply_text.replace("[JP]", "").strip())
                sent_with_any = await _flush_all_pending_cards(
                    bot,
                    message.channel,
                    pending_cards,
                    pending_life,
                    reply_text=merged_reply,
                    logger=logger,
                    pending_rpg_checks=pending_rpg_checks,
                )
                if not sent_with_any:
                    await send_synchronized_response(bot, settings, message.channel, reply_text, logger=logger)
            else:
                await send_synchronized_response(bot, settings, message.channel, reply_text, logger=logger)
            if logger:
                logger.info(f"✅ (日語共學) 已送出回覆 ({len(reply_text.strip())} 字)")

        except Exception as e:
            if logger:
                logger.error(f"❌ 日語共學呼叫或送出回覆失敗: {e}", exc_info=True)
            try:
                await message.channel.send("すみません、剛才稍微斷線了一下，請再傳一次試試看。")
            except Exception as send_err:
                if logger:
                    logger.error(f"❌ 日語共學錯誤提示發送失敗: {send_err}", exc_info=True)
        finally:
            boardgame_engine.pending_board_card_updates.reset(bg_token)
            lifesim_engine.pending_lifesim_card_updates.reset(ls_token)
            rpg_engine.pending_rpg_check_requests.reset(rpg_token)


async def continue_rpg_after_dice_roll(
    bot: discord.Client,
    gemini_client: genai.Client,
    settings: BotSettings,
    channel: discord.abc.Messageable,
    user: discord.abc.User,
    result_dict: dict,
    logger: Optional[logging.Logger] = None,
) -> None:
    """Option A：玩家點擊 TRPG 擲骰檢定按鈕完成本地真隨機判定後，自動喚醒 DM 續寫後續劇情。"""
    if gemini_client is None or channel is None:
        return

    channel_id = getattr(channel, "id", 0)
    is_general_ch = bool(
        settings.general_channel_id
        and (
            channel_id == settings.general_channel_id
            or getattr(channel, "parent_id", None) == settings.general_channel_id
        )
    )
    is_jp_channel = bool(settings.enable_japanese_mode and not is_general_ch)
    prompt_fn = get_current_system_prompt if is_jp_channel else get_current_general_prompt

    user_name = getattr(user, "display_name", None) or result_dict.get("user_name") or "冒險者"
    summary_text = str(result_dict.get("summary_text") or "").strip()
    if not summary_text:
        passed_txt = "成功" if result_dict.get("passed") else "失敗"
        summary_text = f"{user_name} 進行「{result_dict.get('check_name', '檢定')}」擲出 {result_dict.get('total', '?')}（{passed_txt}）"

    synth_text = (
        f"[{user_name}]: ⚡【玩家已完成本地真實擲骰檢定，請以此權威結果直接接續劇情演出與世界變化，嚴禁重骰】：\n"
        f"{summary_text}"
    )
    pseudo_msg = SimpleNamespace(
        channel=channel,
        author=user,
        clean_content=synth_text,
        content=synth_text,
        attachments=[],
        stickers=[],
    )

    reset_after = await asyncio.to_thread(get_effective_reset_timestamp, channel_id)
    raw_messages: list[discord.Message] = []
    history_fn = getattr(channel, "history", None)
    if callable(history_fn):
        try:
            async for msg in history_fn(limit=12, oldest_first=False):
                if reset_after and msg.created_at <= reset_after:
                    break
                if (msg.content or "").strip().startswith("!"):
                    continue
                if msg.author == bot.user and not (msg.clean_content or "").strip():
                    continue
                if msg.author != bot.user and not (msg.clean_content or "").strip():
                    continue
                raw_messages.append(msg)
        except Exception as e:
            if logger:
                logger.debug(f"⚠️ 抓取 TRPG 擲骰前歷史紀錄略過: {e}")

    raw_messages.reverse()
    contents: list[types.Content] = []
    bot_user = getattr(bot, "user", None)
    for msg in raw_messages:
        txt = (getattr(msg, "clean_content", "") or "").strip()
        if not txt:
            continue
        if bot_user is not None and msg.author == bot_user:
            contents.append(types.Content(role="model", parts=[types.Part.from_text(text=txt)]))
        else:
            author_lbl = getattr(msg.author, "display_name", "冒險者")
            contents.append(types.Content(role="user", parts=[types.Part.from_text(text=f"[{author_lbl}]: {txt}")]))
    contents.append(types.Content(role="user", parts=[types.Part.from_text(text=synth_text)]))

    pending_rpg_checks: list = []
    rpg_token = rpg_engine.pending_rpg_check_requests.set(pending_rpg_checks)
    try:
        typing_fn = getattr(channel, "typing", None)
        typing_cm = typing_fn() if callable(typing_fn) else None
        if typing_cm is not None and hasattr(typing_cm, "__aenter__"):
            async with typing_cm:
                reply_text, _ = await prepare_and_generate_chat_response(
                    gemini_client=gemini_client,
                    settings=settings,
                    message=pseudo_msg,
                    contents=contents,
                    system_prompt_fn=prompt_fn,
                    base_temperature=0.75,
                    context_label="TRPG擲骰後DM續寫",
                    logger=logger,
                )
        else:
            reply_text, _ = await prepare_and_generate_chat_response(
                gemini_client=gemini_client,
                settings=settings,
                message=pseudo_msg,
                contents=contents,
                system_prompt_fn=prompt_fn,
                base_temperature=0.75,
                context_label="TRPG擲骰後DM續寫",
                logger=logger,
            )

        if pending_rpg_checks:
            if is_jp_channel and "[ZH]" in reply_text:
                jp_raw, zh_raw = reply_text.split("[ZH]", 1)
                jp_clean = _strip_hallucinated_board_card_echo(jp_raw.replace("[JP]", "").strip())
                zh_clean = _strip_hallucinated_board_card_echo(zh_raw.strip())
                tts_channel = bot.get_channel(settings.tts_channel_id) if settings.tts_channel_id else None
                if jp_clean and tts_channel and tts_channel.id != channel_id:
                    try:
                        await tts_channel.send(content=jp_clean)
                    except discord.HTTPException:
                        pass
                if jp_clean:
                    tts_cog = _get_tts_cog(bot, settings)
                    guild = getattr(channel, "guild", None)
                    if tts_cog and guild:
                        await tts_cog.enqueue_ai_speech(guild, jp_clean, is_japanese=True, channel=channel)
                merged_reply = "\n".join(p for p in (jp_clean, zh_clean) if p)
            else:
                merged_reply = _strip_hallucinated_board_card_echo(reply_text.replace("[JP]", "").strip()) if is_jp_channel else reply_text
            sent_check = await _flush_pending_rpg_checks(
                bot, channel, pending_rpg_checks, reply_text=merged_reply, logger=logger
            )
            if sent_check:
                return

        if is_jp_channel:
            await send_synchronized_response(bot, settings, channel, reply_text, logger=logger)
        else:
            stripped_reply = _strip_hallucinated_board_card_echo(reply_text).strip()
            final_reply = stripped_reply if has_visible_text(stripped_reply) else reply_text.strip()
            await send_split_messages(channel, final_reply)
            tts_cog = _get_tts_cog(bot, settings)
            guild = getattr(channel, "guild", None)
            if tts_cog and guild and has_visible_text(final_reply):
                await tts_cog.enqueue_ai_speech(guild, final_reply, is_japanese=False, channel=channel)
    finally:
        rpg_engine.pending_rpg_check_requests.reset(rpg_token)
