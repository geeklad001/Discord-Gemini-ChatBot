"""Chat routing, dynamic system instructions, history building, and response dispatch."""

import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

import discord
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
from core.llm_client import generate_with_fallback, get_safety_settings
from core.media import build_current_message_media_parts, fetch_url_preview, is_gif_or_media_share_url

# In-memory cache of channel reset timestamps, backed by SQLite in assistant_db
channel_reset_timestamps: dict[int, datetime] = {}


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
        safety_guardrail.get_safety_guardrail_prompt(enable_japanese_mode=settings.enable_japanese_mode)
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


async def build_chat_history_contents(
    bot: discord.Client,
    settings: BotSettings,
    channel: discord.abc.Messageable,
    current_message: discord.Message,
    is_japanese_dual_mode: bool = False,
    logger: Optional[logging.Logger] = None,
) -> list[types.Content]:
    """統一抓取滑動視窗歷史訊息並格式化為 Gemini SDK 的 Content 清單。"""
    if is_japanese_dual_mode and not isinstance(channel, discord.Thread):
        source_channel = bot.get_channel(settings.target_channel_id) or channel
    else:
        source_channel = channel

    reset_after = get_effective_reset_timestamp(getattr(source_channel, "id", None))
    raw_messages: list[discord.Message] = []
    history_limit = settings.thread_window_limit if isinstance(source_channel, discord.Thread) else settings.sliding_window_limit

    async for msg in source_channel.history(limit=history_limit, oldest_first=False):
        if reset_after and msg.created_at <= reset_after:
            break
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
                    parts.append(types.Part.from_text(text=f"[{clean_author}]: {embed.description}"))
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
            author_name = msg.author.display_name
            msg_stickers = getattr(msg, "stickers", None) or []
            if not msg.clean_content.strip() and msg_stickers:
                st_names = "、".join(getattr(s, "name", "貼圖") for s in msg_stickers)
                raw_text = f"(傳送了貼圖【{st_names}】，請直接觀看貼圖畫面並自然回應)"
            elif not msg.clean_content.strip() and msg.attachments:
                has_vid = any(video_engine.is_video_attachment(a) for a in msg.attachments)
                has_gif = any((a.filename or "").lower().endswith(".gif") or (a.content_type or "").lower() == "image/gif" for a in msg.attachments)
                if has_vid:
                    raw_text = "(傳送了一段短影片，請直接觀看並回應)"
                elif has_gif:
                    raw_text = "(傳送了一張 GIF 動圖，請直接觀看畫面並自然回應)"
                else:
                    raw_text = "(傳送了一張圖片，請直接觀看並回應)"
            elif msg.clean_content.strip() and is_gif_or_media_share_url(msg.clean_content.strip()):
                raw_text = f"{msg.clean_content.strip()} (傳送了一張 GIF 動圖，請觀看下方動圖畫面並自然回應)"
            else:
                raw_text = msg.clean_content.strip()
            text = f"[{author_name}]: {raw_text}"

            if msg.id == current_message.id and "http" in msg.clean_content:
                url_summary = await fetch_url_preview(msg.clean_content, logger=logger)
                if url_summary:
                    text += f"\n\n{url_summary}"

            parts.append(types.Part.from_text(text=text))

            if msg.id == current_message.id:
                media_parts = await build_current_message_media_parts(msg, context_label=context_label, logger=logger)
                parts.extend(media_parts)

        if parts:
            turns.append(types.Content(role=role, parts=parts))

    return turns


async def send_split_messages(destination: discord.abc.Messageable, text: str) -> None:
    """防止超過 Discord 單則訊息 2000 字元限制，自動切分段落送出。"""
    max_len = 1900
    if len(text) <= max_len:
        await destination.send(text)
        return

    lines = text.split("\n")
    chunk = ""
    for line in lines:
        if len(chunk) + len(line) + 1 > max_len:
            if chunk:
                await destination.send(chunk)
                chunk = ""
            while len(line) > max_len:
                await destination.send(line[:max_len])
                line = line[max_len:]
            chunk = line
        else:
            chunk = f"{chunk}\n{line}" if chunk else line

    if chunk.strip():
        await destination.send(chunk)


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
    if zh_part:
        embed = discord.Embed(
            description=truncate_embed_text(zh_part),
            color=0xFFB6C1,
        )
        embed.set_footer(text="🌸 繁體中文對照與單字註記")

    main_channel = bot.get_channel(settings.target_channel_id)
    tts_channel = bot.get_channel(settings.tts_channel_id) if settings.tts_channel_id else None

    if isinstance(origin_channel, discord.Thread):
        if len(jp_part) <= 1900:
            await origin_channel.send(content=jp_part or None, embed=embed)
        else:
            await send_split_messages(origin_channel, jp_part)
            if embed:
                await origin_channel.send(embed=embed)
        if jp_part and tts_channel and tts_channel.id != origin_channel.id:
            await tts_channel.send(content=jp_part)
        return

    target_main = main_channel or origin_channel
    if len(jp_part) <= 1900:
        await target_main.send(content=jp_part or None, embed=embed)
    else:
        await send_split_messages(target_main, jp_part)
        if embed:
            await target_main.send(embed=embed)

    if jp_part and tts_channel and tts_channel.id != target_main.id:
        if len(jp_part) <= 1900:
            await tts_channel.send(content=jp_part)
        else:
            await send_split_messages(tts_channel, jp_part)
        if logger:
            logger.info(f"📻 已同步推播純日語至朗讀頻道 (ID: {settings.tts_channel_id})")


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
            )

    recent_text = _extract_recent_context_text(contents)
    in_rpg = is_rpg_context(message.channel, message.clean_content)
    bg_cid = _resolve_boardgame_channel_id(settings, message.channel)
    in_boardgame = bool(
        settings.enable_board_games
        and (
            boardgame_engine.get_latest_game_in_channel(bg_cid, include_finished_hours=4) is not None
            or boardgame_engine.is_boardgame_intent(message.clean_content)
            or boardgame_engine.is_boardgame_intent(recent_text)
        )
    )
    in_lifesim = bool(
        settings.enable_board_games
        and (
            lifesim_engine.get_active_life_run(bg_cid, user_id=message.author.id) is not None
            or lifesim_engine.is_lifesim_intent(message.clean_content)
            or lifesim_engine.is_lifesim_intent(recent_text)
        )
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
    config = types.GenerateContentConfig(
        system_instruction=system_prompt_fn(
            settings,
            message,
            ocr_text=ocr_text,
            recent_context_text=recent_text,
        ),
        thinking_config=types.ThinkingConfig(thinking_level="medium"),
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
        )
    finally:
        assistant_tools.current_discord_msg.reset(token)

    if response and response.text:
        thoughts_count = getattr(response.usage_metadata, "thoughts_token_count", 0) if response.usage_metadata else 0
        if thoughts_count and logger:
            logger.info(f"💭 {used_model} 深度思考完成，思考消耗了 {thoughts_count} 個 tokens！")
        reply = response.text
        if settings.enable_safety_guardrail:
            reply = safety_guardrail.sanitize_model_output(
                reply,
                enable_japanese_mode=settings.enable_japanese_mode,
            )
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
            game = boardgame_engine.get_game_by_id(gid)
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
            run = lifesim_engine.get_life_run_by_id(rid)
            if not run:
                continue
            note = info["note"]
            if info["custom_decision"] and run["status"] == "active":
                outcome, deltas = await life_cog.evaluate_llm_custom_choice(run, info["custom_decision"])
                run, note = lifesim_engine.resolve_life_step(
                    rid,
                    run["user_id"],
                    custom_deltas=deltas,
                    custom_outcome_text=outcome,
                )
            if info["needs_llm_next"] and run["status"] == "active" and run["story_mode"] == "llm":
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
        bg_token = boardgame_engine.pending_board_card_updates.set(pending_cards)
        ls_token = lifesim_engine.pending_lifesim_card_updates.set(pending_life)
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
            sent_with_card = await _flush_pending_board_cards(
                bot, message.channel, pending_cards, reply_text=reply_text, logger=logger
            )
            sent_with_life = await _flush_pending_lifesim_cards(
                bot,
                message.channel,
                pending_life,
                reply_text="" if sent_with_card else reply_text,
                logger=logger,
            )
            if not sent_with_card and not sent_with_life:
                await send_split_messages(message.channel, _strip_hallucinated_board_card_echo(reply_text) or reply_text)

        except Exception as e:
            if logger:
                logger.error(f"❌ 日常群聊呼叫 Gemini 失敗: {e}", exc_info=True)
            await message.channel.send("抱歉，我剛才斷線了一下，請再說一次。")
        finally:
            boardgame_engine.pending_board_card_updates.reset(bg_token)
            lifesim_engine.pending_lifesim_card_updates.reset(ls_token)


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

    if tts_channel and main_channel:
        author_avatar = message.author.display_avatar.url if message.author.display_avatar else None
        if not message.clean_content.strip() and message.attachments:
            has_vid = any(video_engine.is_video_attachment(a) for a in message.attachments)
            embed_desc = "🎬 [分享了一段短影片]" if has_vid else "🖼️ [分享了一張圖片]"
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
                await main_channel.send(embed=mirror_embed)
            except Exception as e:
                if logger:
                    logger.error(f"❌ 鏡像小卡發送至綜合頻道失敗: {e}")

    async with message.channel.typing():
        pending_cards: list = []
        pending_life: list = []
        bg_token = boardgame_engine.pending_board_card_updates.set(pending_cards)
        ls_token = lifesim_engine.pending_lifesim_card_updates.set(pending_life)
        try:
            contents = await build_chat_history_contents(
                bot=bot,
                settings=settings,
                channel=message.channel,
                current_message=message,
                is_japanese_dual_mode=True,
                logger=logger,
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
            if pending_cards or pending_life:
                if "[ZH]" in reply_text:
                    jp_raw, zh_raw = reply_text.split("[ZH]", 1)
                    jp_clean = _strip_hallucinated_board_card_echo(jp_raw.replace("[JP]", "").strip())
                    zh_clean = _strip_hallucinated_board_card_echo(zh_raw.strip())
                    if jp_clean and tts_channel and tts_channel.id != message.channel.id:
                        try:
                            await tts_channel.send(content=jp_clean)
                        except Exception:
                            pass
                    merged_reply = "\n".join(p for p in (jp_clean, zh_clean) if p)
                else:
                    merged_reply = _strip_hallucinated_board_card_echo(reply_text.replace("[JP]", "").strip())
                sent_with_card = await _flush_pending_board_cards(
                    bot, message.channel, pending_cards, reply_text=merged_reply, logger=logger
                )
                sent_with_life = await _flush_pending_lifesim_cards(
                    bot,
                    message.channel,
                    pending_life,
                    reply_text="" if sent_with_card else merged_reply,
                    logger=logger,
                )
                if not sent_with_card and not sent_with_life:
                    await send_synchronized_response(bot, settings, message.channel, reply_text, logger=logger)
            else:
                await send_synchronized_response(bot, settings, message.channel, reply_text, logger=logger)

        except Exception as e:
            if logger:
                logger.error(f"❌ 日語共學呼叫 Gemini 失敗: {e}", exc_info=True)
            await message.channel.send("すみません、剛才稍微斷線了一下，請再傳一次試試看。")
        finally:
            boardgame_engine.pending_board_card_updates.reset(bg_token)
            lifesim_engine.pending_lifesim_card_updates.reset(ls_token)
