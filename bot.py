import os
import sys
import re
import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional
from pathlib import Path
from dotenv import load_dotenv
import httpx

import discord
from discord.ext import commands, tasks
from google import genai
from google.genai import types

import assistant_db
import assistant_tools
import rpg_engine
import flashcard_engine

# -------------------------------------------------------------
# 1. 基礎設定與環境變數載入 (支援 Profile 與自訂 .env)
# -------------------------------------------------------------
# 在 Windows CP950 控制台下強制以 UTF-8 輸出，避免 Emoji 導致 UnicodeEncodeError
if sys.platform == "win32":
    try:
        if sys.stdout and sys.stdout.encoding.lower() != "utf-8":
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        if sys.stderr and sys.stderr.encoding.lower() != "utf-8":
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# 支援命令列指定設定檔：python bot.py --profile=community 或 --env=.env.community
env_file = os.getenv("BOT_ENV_FILE")
for arg in sys.argv[1:]:
    if arg.startswith("--profile="):
        prof = arg.split("=", 1)[1].strip()
        env_file = f".env.{prof}"
    elif arg.startswith("--env="):
        env_file = arg.split("=", 1)[1].strip()

if not env_file:
    env_file = ".env"

load_dotenv(dotenv_path=env_file)

bot_instance_name = os.getenv("BOT_INSTANCE_NAME", "DiscordGeminiBot")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(bot_instance_name)

ENABLE_JAPANESE_MODE = os.getenv("ENABLE_JAPANESE_MODE", "true").strip().lower() not in ("false", "0", "no")
if not ENABLE_JAPANESE_MODE:
    logger.info("🌐 日語教學模式已關閉，機器人將以繁體中文日常助理與 TRPG 冒險模式運作。")

ENABLE_FLASHCARD_MODE = os.getenv("ENABLE_FLASHCARD_MODE", "true").strip().lower() not in ("false", "0", "no")
if not ENABLE_FLASHCARD_MODE:
    logger.info("🎴 單字卡與搶答測驗功能已關閉。")

# 初始化生活助理資料庫、RPG 引擎與單字卡 (SQLite，自動依 DB_NAME 環境變數隔離資料庫)
assistant_db.init_db()
rpg_engine.init_rpg_tables()
if ENABLE_FLASHCARD_MODE:
    flashcard_engine.init_flashcard_tables()

# 依開關動態配置 Gemini 工具清單
CURRENT_TOOLS = assistant_tools.get_assistant_tools(enable_flashcards=ENABLE_FLASHCARD_MODE)

logger.info(f"🚀 已成功載入設定檔: {env_file} (實例: {bot_instance_name} | 資料庫: {assistant_db.get_db_path().name})")

DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
TARGET_CHANNEL_ID_RAW = os.getenv("TARGET_CHANNEL_ID")
TTS_CHANNEL_ID_RAW = os.getenv("TTS_CHANNEL_ID")
GENERAL_CHANNEL_ID_RAW = os.getenv("GENERAL_CHANNEL_ID")

if not DISCORD_BOT_TOKEN or not GEMINI_API_KEY or not TARGET_CHANNEL_ID_RAW:
    logger.error("❌ 缺少環境變數！請確認 .env 檔案中是否已設定 DISCORD_BOT_TOKEN、GEMINI_API_KEY 與 TARGET_CHANNEL_ID。")
    sys.exit(1)

try:
    TARGET_CHANNEL_ID = int(TARGET_CHANNEL_ID_RAW.strip())
except ValueError:
    logger.error("❌ TARGET_CHANNEL_ID 必須為純數字！請檢查 .env 設定。")
    sys.exit(1)

TTS_CHANNEL_ID = None
if TTS_CHANNEL_ID_RAW and TTS_CHANNEL_ID_RAW.strip():
    try:
        TTS_CHANNEL_ID = int(TTS_CHANNEL_ID_RAW.strip())
        logger.info(f"📻 已設定第二頻道 (TTS 專用日語朗讀頻道) ID: {TTS_CHANNEL_ID}")
    except ValueError:
        logger.warning("⚠️ TTS_CHANNEL_ID 格式不正確，將僅在單一頻道運作。")

GENERAL_CHANNEL_ID = None
if GENERAL_CHANNEL_ID_RAW and GENERAL_CHANNEL_ID_RAW.strip():
    try:
        GENERAL_CHANNEL_ID = int(GENERAL_CHANNEL_ID_RAW.strip())
        logger.info(f"💬 已設定一般日常群聊頻道 ID: {GENERAL_CHANNEL_ID}")
    except ValueError:
        logger.warning("⚠️ GENERAL_CHANNEL_ID 格式不正確。")

def get_current_system_prompt(message: Optional[discord.Message] = None) -> str:
    """動態讀取日語教學提示詞 (預設 system_prompt.txt) 並注入時間與長期記憶"""
    p_name = os.getenv("SYSTEM_PROMPT_FILE", "system_prompt.txt").strip()
    prompt_path = Path(__file__).parent / p_name
    base_prompt = ""
    if prompt_path.exists():
        try:
            with open(prompt_path, "r", encoding="utf-8") as f:
                base_prompt = f.read().strip()
        except Exception:
            pass
    if not base_prompt:
        base_prompt = "你是一位住在日本、性格隨和平實的日本同齡朋友兼日語生活學伴。"

    now_str = datetime.now(assistant_db.TZ_TAIPEI).strftime("%Y-%m-%d %H:%M:%S")
    time_info = f"\n\n【當前時間資訊】\n台北時間 (UTC+8)：{now_str}。若使用者要求提醒，請據此換算為未來的 ISO 8601 時間字串。"
    mem_info = assistant_db.format_memories_for_prompt()
    if mem_info:
        mem_info = f"\n\n{mem_info}"

    extra_parts = [base_prompt, time_info, mem_info]
    if message:
        matched_lore = rpg_engine.match_lorebook_for_text(message.clean_content)
        if matched_lore:
            extra_parts.append("\n\n【當前場景世界觀設定 (Lorebook)】\n" + "\n".join(f"- {l}" for l in matched_lore))
    return "".join(extra_parts)


def get_current_general_prompt(message: Optional[discord.Message] = None) -> str:
    """動態讀取日常群聊提示詞 (預設 general_prompt.txt) 並注入時間、長期記憶、權威 RPG 狀態與世界觀 Lorebook"""
    p_name = os.getenv("GENERAL_PROMPT_FILE", "general_prompt.txt").strip()
    prompt_path = Path(__file__).parent / p_name
    base_prompt = ""
    if prompt_path.exists():
        try:
            with open(prompt_path, "r", encoding="utf-8") as f:
                base_prompt = f.read().strip()
        except Exception:
            pass
    if not base_prompt:
        base_prompt = "你是 Discord 群聊裡的一位普通朋友，跟大家一起隨意閒聊或玩 TRPG 冒險。"

    now_str = datetime.now(assistant_db.TZ_TAIPEI).strftime("%Y-%m-%d %H:%M:%S")
    time_info = f"\n\n【當前時間資訊】\n台北時間 (UTC+8)：{now_str}。若使用者要求提醒，請據此換算為未來的 ISO 8601 時間字串。"
    mem_info = assistant_db.format_memories_for_prompt()
    if mem_info:
        mem_info = f"\n\n{mem_info}"

    extra_parts = [base_prompt, time_info, mem_info]
    if message:
        # 動態掃描 Lorebook 關鍵字注入
        matched_lore = rpg_engine.match_lorebook_for_text(message.clean_content)
        if matched_lore:
            extra_parts.append("\n\n【當前場景世界觀設定 (Lorebook)】\n" + "\n".join(f"- {l}" for l in matched_lore))

        # 注入本機權威 RPG 狀態卡 (背包、血量、任務)
        rpg_context = rpg_engine.format_rpg_context_for_prompt(
            message.channel.id, message.author.id, message.author.display_name
        )
        extra_parts.append(f"\n\n{rpg_context}")

    return "".join(extra_parts)

# -------------------------------------------------------------
# 2. 初始化 Gemini Client (google-genai SDK 2026 最新規格)
# -------------------------------------------------------------
# 自動載入 GEMINI_API_KEY
gemini_client = genai.Client(api_key=GEMINI_API_KEY)

# 優先使用 .env 中的 PRIMARY_MODEL 設定，預設為旗艦 gemini-3.8-flash
# 若遇到 429 當日次數超標或 503 尖峰，則由 FALLBACK_MODELS 接棒保底，絕不當機
PRIMARY_MODEL = os.getenv("PRIMARY_MODEL", "gemini-3.8-flash").strip()
FALLBACK_MODELS = ["gemini-flash-lite-latest", "gemini-3.5-flash-lite", "gemini-3.6-flash"]

# -------------------------------------------------------------
# 安全過濾設定：全部設定為 BLOCK_NONE (完全關閉審查過濾)
# 讓聊天、開玩笑、情侶私密話題與文字冒險劇情能無拘無束自由暢聊
# -------------------------------------------------------------
DEFAULT_SAFETY_SETTINGS = [
    types.SafetySetting(
        category=types.HarmCategory.HARM_CATEGORY_HARASSMENT,
        threshold=types.HarmBlockThreshold.BLOCK_NONE,
    ),
    types.SafetySetting(
        category=types.HarmCategory.HARM_CATEGORY_HATE_SPEECH,
        threshold=types.HarmBlockThreshold.BLOCK_NONE,
    ),
    types.SafetySetting(
        category=types.HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT,
        threshold=types.HarmBlockThreshold.BLOCK_NONE,
    ),
    types.SafetySetting(
        category=types.HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT,
        threshold=types.HarmBlockThreshold.BLOCK_NONE,
    ),
]

# -------------------------------------------------------------
# 3. 初始化 Discord Bot
# -------------------------------------------------------------
intents = discord.Intents.default()
intents.message_content = True  # 必須在 Discord 開發者後台開啟 Message Content Intent
intents.guilds = True

bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)

# 記錄每個頻道最後一次手動重設 (Reset) 的時間點，用於切割上下文
channel_reset_timestamps: dict[int, datetime] = {}

# 滑動視窗最大保留訊息數量 (一般頻道預設 50 則，討論串預設 80 則，大幅擴充長期記憶)
SLIDING_WINDOW_LIMIT = int(os.getenv("SLIDING_WINDOW_LIMIT", "50"))
THREAD_WINDOW_LIMIT = int(os.getenv("THREAD_WINDOW_LIMIT", "80"))


def is_target_channel(channel: discord.abc.Messageable) -> bool:
    """判斷發言位置是否為指定日文頻道、TTS朗讀頻道、一般日常群聊頻道、或其旗下的討論串 (Thread)。"""
    cid = getattr(channel, "id", None)
    valid_ids = {TARGET_CHANNEL_ID}
    if TTS_CHANNEL_ID:
        valid_ids.add(TTS_CHANNEL_ID)
    if GENERAL_CHANNEL_ID:
        valid_ids.add(GENERAL_CHANNEL_ID)
    if cid in valid_ids:
        return True
    if hasattr(channel, "parent_id") and channel.parent_id in valid_ids:
        return True
    return False


async def fetch_url_preview(text: str) -> str:
    """自動偵測訊息中的網址，並在背景非同步抓取網頁文字摘要。"""
    urls = re.findall(r'https?://[^\s<>"]+', text)
    if not urls:
        return ""

    previews = []
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    async with httpx.AsyncClient(follow_redirects=True, timeout=5.0, headers=headers) as client:
        for url in urls[:2]:
            try:
                resp = await client.get(url)
                if resp.status_code == 200:
                    raw = re.sub(r'<script.*?</script>', ' ', resp.text, flags=re.DOTALL | re.I)
                    raw = re.sub(r'<style.*?</style>', ' ', raw, flags=re.DOTALL | re.I)
                    clean = re.sub(r'<[^>]+>', ' ', raw)
                    clean = re.sub(r'\s+', ' ', clean).strip()
                    if clean:
                        previews.append(f"【網頁內容預覽 ({url})】：\n{clean[:1200]}")
                        logger.info(f"🌐 成功解析網址內容: {url}")
            except Exception as e:
                logger.debug(f"網址 {url} 解析略過: {e}")

    return "\n\n".join(previews)


async def fetch_custom_emojis_and_stickers(message: discord.Message) -> list[tuple[str, bytes]]:
    """從 Discord 訊息中抓取自定義表情符號 (<:name:id>) 與貼圖，下載其圖片資料供 Gemini Vision 觀看"""
    items: list[tuple[str, bytes]] = []

    # 1. 抓取訊息中的自定義表情符號 (<:name:id> 或 <a:name:id>)
    custom_emojis = re.findall(r'<(a?):(\w+):(\d+)>', message.content)

    # 2. 抓取訊息中的 Discord 貼圖
    stickers = getattr(message, "stickers", [])

    if not custom_emojis and not stickers:
        return items

    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    async with httpx.AsyncClient(follow_redirects=True, timeout=4.0, headers=headers) as client:
        # 下載自定義表情符號圖片 (最多前 3 個)
        for is_anim, name, eid in custom_emojis[:3]:
            url = f"https://cdn.discordapp.com/emojis/{eid}.png?size=96&quality=lossless"
            try:
                resp = await client.get(url)
                if resp.status_code == 200 and resp.content:
                    items.append((f"自定義表情 :{name}:", resp.content))
                    logger.info(f"🎨 成功加載自定義表情符號圖片: :{name}: ({len(resp.content)} bytes)")
            except Exception as e:
                logger.debug(f"下載表情符號 :{name}: 略過: {e}")

        # 下載貼圖圖片 (最多前 2 個)
        for st in stickers[:2]:
            try:
                resp = await client.get(st.url)
                if resp.status_code == 200 and resp.content:
                    items.append((f"Discord 貼圖【{st.name}】", resp.content))
                    logger.info(f"🎨 成功加載貼圖圖片: {st.name} ({len(resp.content)} bytes)")
            except Exception as e:
                logger.debug(f"下載貼圖 {st.name} 略過: {e}")

    return items


async def build_general_contents(channel: discord.abc.Messageable, current_message: discord.Message) -> list[types.Content]:
    """從一般日常群聊頻道抓取歷史訊息，格式化為三人日常對話的 Content 物件 (支援看圖與網址解析)。"""
    reset_after = channel_reset_timestamps.get(getattr(channel, "id", None))
    raw_messages: list[discord.Message] = []

    history_limit = THREAD_WINDOW_LIMIT if isinstance(channel, discord.Thread) else SLIDING_WINDOW_LIMIT
    async for msg in channel.history(limit=history_limit, oldest_first=False):
        if reset_after and msg.created_at <= reset_after:
            break
        if msg.content.strip().startswith("!"):
            continue
        if not msg.content.strip() and not msg.attachments:
            continue
        raw_messages.append(msg)

    raw_messages.reverse()

    turns: list[types.Content] = []

    for msg in raw_messages:
        parts: list[types.Part] = []
        if msg.author == bot.user:
            role = "model"
            parts.append(types.Part.from_text(text=msg.clean_content.strip()))
        else:
            role = "user"
            author_name = msg.author.display_name
            raw_text = msg.clean_content.strip() or ("(傳送了一張圖片，請直接觀看並回應)" if msg.attachments else "")
            text = f"[{author_name}]: {raw_text}"

            # 若當前訊息包含網址，抓取預覽附在文字後
            if msg.id == current_message.id and "http" in msg.clean_content:
                url_summary = await fetch_url_preview(msg.clean_content)
                if url_summary:
                    text += f"\n\n{url_summary}"

            parts.append(types.Part.from_text(text=text))

            # 若當前訊息包含圖片附件，載入圖片二進制資料 (Vision 多模態看圖)
            if msg.id == current_message.id and msg.attachments:
                for att in msg.attachments:
                    if att.content_type and att.content_type.startswith("image/"):
                        try:
                            img_data = await att.read()
                            parts.append(types.Part.from_bytes(data=img_data, mime_type=att.content_type))
                            logger.info(f"🖼️ (日常群聊) 成功加載圖片: {att.filename} ({len(img_data)} bytes)")
                        except Exception as img_err:
                            logger.error(f"❌ 加載圖片失敗: {img_err}")

            # 若當前訊息包含自定義表情符號或貼圖，載入圖片供 Gemini Vision 親眼觀看
            if msg.id == current_message.id:
                visual_items = await fetch_custom_emojis_and_stickers(msg)
                for label, img_data in visual_items:
                    parts.append(types.Part.from_text(text=f"({label} 的畫面內容如下)："))
                    parts.append(types.Part.from_bytes(data=img_data, mime_type="image/png"))

        if parts:
            turns.append(types.Content(role=role, parts=parts))

    return turns


async def build_gemini_contents(channel: discord.TextChannel | discord.Thread, current_message: discord.Message) -> list[types.Content]:
    """
    動態抓取滑動視窗歷史訊息，格式化為 Gemini SDK 的 Content 物件 (支援看圖與網址解析)。
    在雙頻道模式下，一律以綜合聊天頻道 (TARGET_CHANNEL_ID) 作為全域上下文基準，確保雙邊對話完全同步。
    """
    source_channel = channel if isinstance(channel, discord.Thread) else (bot.get_channel(TARGET_CHANNEL_ID) or channel)
    reset_after = channel_reset_timestamps.get(source_channel.id)

    raw_messages: list[discord.Message] = []
    history_limit = THREAD_WINDOW_LIMIT if isinstance(source_channel, discord.Thread) else SLIDING_WINDOW_LIMIT
    async for msg in source_channel.history(limit=history_limit, oldest_first=False):
        if reset_after and msg.created_at <= reset_after:
            break
        if msg.content.strip().startswith("!"):
            continue
        if not msg.content.strip() and not msg.embeds and not msg.attachments:
            continue
        raw_messages.append(msg)

    raw_messages.reverse()

    turns: list[types.Content] = []

    for msg in raw_messages:
        parts: list[types.Part] = []
        if msg.author == bot.user:
            if msg.embeds and not msg.content.strip():
                embed = msg.embeds[0]
                author_name = embed.author.name if embed.author else "學員"
                role = "user"
                parts.append(types.Part.from_text(text=f"[{author_name}]: {embed.description}"))
            else:
                role = "model"
                parts.append(types.Part.from_text(text=msg.clean_content.strip()))
        else:
            role = "user"
            author_name = msg.author.display_name
            raw_text = msg.clean_content.strip() or ("(傳送了一張圖片，請直接觀看並回應)" if msg.attachments else "")
            text = f"[{author_name}]: {raw_text}"

            # 若當前訊息包含網址，抓取預覽
            if msg.id == current_message.id and "http" in msg.clean_content:
                url_summary = await fetch_url_preview(msg.clean_content)
                if url_summary:
                    text += f"\n\n{url_summary}"

            parts.append(types.Part.from_text(text=text))

            # 若當前訊息包含圖片附件，載入圖片二進制資料 (日語看圖學習、菜單翻譯、照片解說)
            if msg.id == current_message.id and msg.attachments:
                for att in msg.attachments:
                    if att.content_type and att.content_type.startswith("image/"):
                        try:
                            img_data = await att.read()
                            parts.append(types.Part.from_bytes(data=img_data, mime_type=att.content_type))
                            logger.info(f"🖼️ (日語共學) 成功加載圖片: {att.filename} ({len(img_data)} bytes)")
                        except Exception as img_err:
                            logger.error(f"❌ 加載圖片失敗: {img_err}")

            # 若當前訊息包含自定義表情符號或貼圖，載入圖片供 Gemini Vision 親眼觀看
            if msg.id == current_message.id:
                visual_items = await fetch_custom_emojis_and_stickers(msg)
                for label, img_data in visual_items:
                    parts.append(types.Part.from_text(text=f"({label} 的畫面內容如下)："))
                    parts.append(types.Part.from_bytes(data=img_data, mime_type="image/png"))

        if parts:
            turns.append(types.Content(role=role, parts=parts))

    return turns


async def send_split_messages(destination: discord.abc.Messageable, text: str):
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


async def send_synchronized_response(origin_channel: discord.abc.Messageable, text: str, used_model: str = ""):
    """
    雙頻道智能回覆：
    1. 綜合頻道 (TARGET_CHANNEL_ID)：發送純日語正文 + 中文解說小卡 (Embed)。
    2. 日文頻道 (TTS_CHANNEL_ID)：只發送純日語正文 (供 HornBot 專注朗讀，無小卡、無中文雜音)。
    """
    if "[ZH]" in text:
        parts = text.split("[ZH]", 1)
        jp_part = parts[0].replace("[JP]", "").strip()
        zh_part = parts[1].strip()
    else:
        jp_part = text.strip()
        zh_part = None

    embed = None
    if zh_part:
        embed = discord.Embed(
            description=zh_part,
            color=0xFFB6C1  # 櫻花粉
        )
        embed.set_footer(text="🌸 繁體中文對照與單字註記")

    main_channel = bot.get_channel(TARGET_CHANNEL_ID)
    tts_channel = bot.get_channel(TTS_CHANNEL_ID) if TTS_CHANNEL_ID else None

    # 如果是在討論串 (Thread) 內發言，優先回覆該討論串
    if isinstance(origin_channel, discord.Thread):
        if len(jp_part) <= 1900:
            await origin_channel.send(content=jp_part, embed=embed)
        else:
            await send_split_messages(origin_channel, jp_part)
            if embed:
                await origin_channel.send(embed=embed)
        if tts_channel and tts_channel.id != origin_channel.id:
            await tts_channel.send(content=jp_part)
        return

    # 1. 綜合頻道 (TARGET_CHANNEL_ID)：發送純日文 + 中文註解小卡
    target_main = main_channel or origin_channel
    if len(jp_part) <= 1900:
        await target_main.send(content=jp_part, embed=embed)
    else:
        await send_split_messages(target_main, jp_part)
        if embed:
            await target_main.send(embed=embed)

    # 2. 日文專用頻道 (TTS_CHANNEL_ID)：僅發送純日文 (HornBot 朗讀，不帶中文小卡)
    if tts_channel and tts_channel.id != target_main.id:
        if len(jp_part) <= 1900:
            await tts_channel.send(content=jp_part)
        else:
            await send_split_messages(tts_channel, jp_part)
        logger.info(f"📻 已同步推播純日語至朗讀頻道 (ID: {TTS_CHANNEL_ID})")


# -------------------------------------------------------------
# 4. 事件與指令監聽
# -------------------------------------------------------------
@tasks.loop(seconds=10)
async def reminder_check_loop():
    """背景定時排程：每 10 秒檢查是否有到期的提醒事項並推播至 Discord"""
    due_items = assistant_db.get_pending_due_reminders()
    for rem in due_items:
        try:
            channel = bot.get_channel(rem["channel_id"])
            origin_note = ""

            # 邊界可靠度保護：若原討論串或頻道已被使用者手動刪除，自動降級發送到一般日常群聊或主頻道，確保提醒絕不遺失
            if not channel:
                fallback_id = GENERAL_CHANNEL_ID or TARGET_CHANNEL_ID
                channel = bot.get_channel(fallback_id) if fallback_id else None
                origin_note = "（原討論串已刪除，自動轉移至此通知）"

            if channel:
                user_ping = f"<@{rem['user_id']}>"
                embed = discord.Embed(
                    title="⏰ 提醒時間到了！",
                    description=f"**{rem['content']}**",
                    color=0xFFD166  # 亮金黃
                )
                footer_text = f"來自 {rem['user_name']} 的定時提醒"
                if origin_note:
                    footer_text += f" • {origin_note}"
                embed.set_footer(text=footer_text)
                await channel.send(content=user_ping, embed=embed)
                logger.info(f"⏰ 已成功發送定時提醒給 {rem['user_name']} (ID: {rem['id']}): {rem['content']}")
            assistant_db.mark_reminder_sent(rem["id"])
        except Exception as e:
            logger.error(f"❌ 發送定時提醒失敗 (ID: {rem['id']}): {e}")


@bot.event
async def on_thread_delete(thread: discord.Thread):
    """邊界保護：當使用者在 Discord 刪除討論串時，安全記錄日誌"""
    logger.info(f"🗑️ 偵測到討論串已被刪除: #{thread.name} (ID: {thread.id})")


@bot.event
async def on_ready():
    logger.info(f"🌸 機器人已成功上線！登入身分：{bot.user} (ID: {bot.user.id})")
    logger.info(f"🎯 日語教學綜合頻道 ID: {TARGET_CHANNEL_ID}")
    if TTS_CHANNEL_ID:
        logger.info(f"📻 HornBot 朗讀專用日文頻道 ID: {TTS_CHANNEL_ID}")
    if GENERAL_CHANNEL_ID:
        logger.info(f"💬 日常三人閒聊群聊頻道 ID: {GENERAL_CHANNEL_ID}")
    logger.info(f"🤖 首選模型: {PRIMARY_MODEL} (備選: {', '.join(FALLBACK_MODELS)})")

    if not reminder_check_loop.is_running():
        reminder_check_loop.start()
        logger.info("⏰ 定時提醒背景排程器已啟動！")


@bot.command(name="reset", aliases=["clear", "重設", "重置"])
async def reset_context(ctx: commands.Context):
    """手動重設當前頻道的上下文記憶。"""
    if not is_target_channel(ctx.channel):
        return

    now = datetime.now(timezone.utc)
    cid = ctx.channel.id

    # 若是在一般日常群聊頻道下重設，只重設一般群聊
    if GENERAL_CHANNEL_ID and (cid == GENERAL_CHANNEL_ID or getattr(ctx.channel, "parent_id", None) == GENERAL_CHANNEL_ID):
        channel_reset_timestamps[cid] = now
        await ctx.send("🧹 **日常群聊記憶已重設！** 隨時可以開啟新話題。")
        logger.info(f"日常群聊頻道 {cid} 已重設上下文。")
        return

    # 若是在日語學習頻道下重設，同步重設日語學習的雙頻道
    channel_reset_timestamps[TARGET_CHANNEL_ID] = now
    main_ch = bot.get_channel(TARGET_CHANNEL_ID)
    if main_ch:
        channel_reset_timestamps[main_ch.id] = now
        await main_ch.send("🧹 **日語共學記憶已重設！** 過去的對話已清除，可以隨時開啟新話題。")

    if TTS_CHANNEL_ID:
        channel_reset_timestamps[TTS_CHANNEL_ID] = now
        tts_ch = bot.get_channel(TTS_CHANNEL_ID)
        if tts_ch and tts_ch.id != ctx.channel.id:
            await tts_ch.send("🧹 **日語共學記憶已重設！**")

    if cid not in (TARGET_CHANNEL_ID, TTS_CHANNEL_ID):
        await ctx.send("🧹 **已重設上下文記憶！**")

    logger.info("已重設日語學習雙頻道上下文記憶。")


# -------------------------------------------------------------
# 5. 生活助理快捷指令 (待辦清單、定時提醒、長期記憶庫)
# -------------------------------------------------------------
@bot.command(name="todo", aliases=["td", "todos"])
async def todo_command(ctx: commands.Context, action: str = None, *, arg: str = None):
    """待辦事項清單指令"""
    if not is_target_channel(ctx.channel):
        return

    # 若未提供 action，預設為列出未完成事項
    if not action or action.lower() in ("list", "show", "ls"):
        todos = assistant_db.list_todos(only_pending=True)
        if not todos:
            await ctx.send("📋 目前沒有任何未完成的待辦事項！太棒了～✨")
            return
        desc_lines = []
        for t in todos:
            desc_lines.append(f"**#{t['id']}** {t['content']}\n└ *由 {t['user_name']} 於 {t['created_at'][5:16]} 新增*")
        embed = discord.Embed(
            title="📋 共同待辦事項清單",
            description="\n".join(desc_lines),
            color=0x48CAE4
        )
        embed.set_footer(text="💡 完成請輸入 !todo done <編號>，或直接跟機器人說「我做完了」")
        await ctx.send(embed=embed)
        return

    act = action.lower()
    if act in ("add", "new", "+"):
        if not arg:
            await ctx.send("❓ 請輸入要新增的待辦事項內容，例如：`!todo add 買牛奶`")
            return
        tid = assistant_db.add_todo(arg.strip(), user_name=ctx.author.display_name)
        await ctx.send(f"✅ 已成功新增待辦事項 **#{tid}**：{arg.strip()}")

    elif act in ("done", "finish", "ok", "check", "v"):
        if not arg:
            await ctx.send("❓ 請輸入要完成的待辦編號或關鍵字，例如：`!todo done 1` 或 `!todo done 牛奶`")
            return
        target = arg.strip()
        completed = None
        if target.isdigit():
            completed = assistant_db.complete_todo_by_id(int(target))
        if not completed:
            completed = assistant_db.complete_todo_by_keyword(target)

        if completed:
            await ctx.send(f"🎉 已完成待辦事項 **#{completed['id']}**：~~{completed['content']}~~")
        else:
            await ctx.send(f"❌ 找不到符合「{target}」的未完成待辦事項。")

    elif act in ("del", "delete", "rm", "remove", "-"):
        if not arg or not arg.strip().isdigit():
            await ctx.send("❓ 請輸入要刪除的待辦事項編號，例如：`!todo del 1`")
            return
        tid = int(arg.strip())
        if assistant_db.delete_todo(tid):
            await ctx.send(f"🗑️ 已刪除待辦事項 **#{tid}**。")
        else:
            await ctx.send(f"❌ 找不到待辦事項 **#{tid}**。")

    elif act in ("all", "history"):
        todos = assistant_db.list_todos(only_pending=False)
        if not todos:
            await ctx.send("📋 目前待辦清單完全是空的。")
            return
        desc_lines = []
        for t in todos[:25]:
            status_icon = "✅" if t["is_done"] else "⬜"
            content_display = f"~~{t['content']}~~" if t["is_done"] else f"**{t['content']}**"
            desc_lines.append(f"{status_icon} **#{t['id']}** {content_display} ({t['user_name']})")
        embed = discord.Embed(
            title="📋 待辦事項歷史紀錄（最近 25 項）",
            description="\n".join(desc_lines),
            color=0x48CAE4
        )
        await ctx.send(embed=embed)
    else:
        # 如果使用者直接輸入 !todo 買牛奶（漏打 add）
        full_content = f"{action} {arg}" if arg else action
        tid = assistant_db.add_todo(full_content.strip(), user_name=ctx.author.display_name)
        await ctx.send(f"✅ 已成功新增待辦事項 **#{tid}**：{full_content.strip()}")


@bot.command(name="remind", aliases=["reminder", "rem"])
async def remind_command(ctx: commands.Context, time_spec: str = None, *, content: str = None):
    """定時提醒設定指令"""
    if not is_target_channel(ctx.channel):
        return

    if not time_spec or time_spec.lower() in ("list", "show", "ls"):
        rems = assistant_db.list_upcoming_reminders()
        if not rems:
            await ctx.send("⏰ 目前沒有任何預約中的定時提醒。")
            return
        lines = []
        for r in rems:
            lines.append(f"**#{r['id']}** 預定於 `{r['remind_at'][:16]}`\n└ 提醒內容：{r['content']} ({r['user_name']})")
        embed = discord.Embed(
            title="⏰ 預約中的定時提醒清單",
            description="\n".join(lines),
            color=0xFFD166
        )
        embed.set_footer(text="💡 取消提醒請輸入 !remind del <編號>")
        await ctx.send(embed=embed)
        return

    if time_spec.lower() in ("del", "delete", "rm", "cancel"):
        if not content or not content.strip().isdigit():
            await ctx.send("❓ 請輸入要取消的提醒編號，例如：`!remind del 1`")
            return
        rid = int(content.strip())
        assistant_db.mark_reminder_sent(rid)
        await ctx.send(f"🗑️ 已取消提醒事項 **#{rid}**。")
        return

    if not content:
        await ctx.send("❓ 請提供提醒時間與內容，例如：`!remind 10m 關火` 或 `!remind 18:30 倒垃圾`")
        return

    target_dt = assistant_tools.parse_time_argument(time_spec)
    if not target_dt:
        await ctx.send(f"❌ 無法辨識時間格式「{time_spec}」，請嘗試：`10m`、`2h`、`18:30` 或 `2026-10-31 15:00`")
        return

    now = datetime.now(assistant_db.TZ_TAIPEI)
    if target_dt <= now:
        await ctx.send("❌ 設定的時間已經過去了，請設定未來的時間。")
        return

    rid = assistant_db.add_reminder(
        channel_id=ctx.channel.id,
        user_id=ctx.author.id,
        user_name=ctx.author.display_name,
        remind_at_iso=target_dt.isoformat(),
        content=content.strip()
    )
    formatted = target_dt.strftime("%Y-%m-%d %H:%M:%S")
    await ctx.send(f"⏰ 已設定定時提醒 **#{rid}**！\n我會在 `{formatted}` 於此頻道提醒你：**{content.strip()}**")


@bot.command(name="reminds", aliases=["reminders"])
async def list_reminds_shortcut(ctx: commands.Context):
    """查看預約中的提醒清單"""
    await remind_command(ctx, time_spec="list")


@bot.command(name="memory", aliases=["mem", "memories"])
async def memory_command(ctx: commands.Context, action: str = None, *, arg: str = None):
    """長期記憶庫管理指令"""
    if not is_target_channel(ctx.channel):
        return

    if not action or action.lower() in ("list", "show", "ls"):
        memories = assistant_db.get_all_memories()
        if not memories:
            await ctx.send("🧠 目前長期記憶庫是空的。可以在聊天中隨口說「記住喔，我喜歡...」，或輸入 `!remember <事情>` 來記錄！")
            return
        lines = []
        for m in memories:
            user_part = f"[{m['user_name']}] " if m['user_name'] else ""
            lines.append(f"**#{m['id']}** {user_part}{m['content']}\n└ *存於 {m['created_at'][5:16]}*")
        embed = discord.Embed(
            title="🧠 情侶長期記憶庫",
            description="\n".join(lines),
            color=0xBDB2FF
        )
        embed.set_footer(text="💡 刪除記憶請輸入 !forget <編號>，或隨時在對話中更新")
        await ctx.send(embed=embed)
        return

    act = action.lower()
    if act in ("add", "new", "+"):
        if not arg:
            await ctx.send("❓ 請輸入要記住的事實，例如：`!remember 女友最愛吃抹茶`")
            return
        mid = assistant_db.add_memory(arg.strip(), user_name=ctx.author.display_name)
        await ctx.send(f"🧠 已存入長期記憶庫 **#{mid}**：{arg.strip()}")

    elif act in ("del", "delete", "rm", "forget", "-"):
        if not arg or not arg.strip().isdigit():
            await ctx.send("❓ 請輸入要刪除的記憶編號，例如：`!forget 1`")
            return
        mid = int(arg.strip())
        if assistant_db.delete_memory(mid):
            await ctx.send(f"🗑️ 已從長期記憶庫刪除 **#{mid}**。")
        else:
            await ctx.send(f"❌ 找不到記憶編號 **#{mid}**。")
    else:
        # 如果使用者直接打 !memory 女友喜歡草莓蛋糕
        full_content = f"{action} {arg}" if arg else action
        mid = assistant_db.add_memory(full_content.strip(), user_name=ctx.author.display_name)
        await ctx.send(f"🧠 已存入長期記憶庫 **#{mid}**：{full_content.strip()}")


@bot.command(name="remember")
async def remember_shortcut(ctx: commands.Context, *, content: str):
    """快速存入長期記憶：!remember <事實>"""
    await memory_command(ctx, action="add", arg=content)


@bot.command(name="forget")
async def forget_shortcut(ctx: commands.Context, memory_id: int):
    """快速刪除長期記憶：!forget <編號>"""
    await memory_command(ctx, action="del", arg=str(memory_id))


# -------------------------------------------------------------
# 6. RPG 跑團與文字冒險指令 (擲骰、角色卡、動態設定集)
# -------------------------------------------------------------
@bot.command(name="roll", aliases=["r", "dice", "擲骰"])
async def roll_command(ctx: commands.Context, dice_expr: str = "1d20"):
    """擲骰子指令：!roll [表達式]（例如 !roll、!roll 2d6、!roll 1d100、!roll 1d20+3）"""
    if not is_target_channel(ctx.channel):
        return

    try:
        total, rolls, desc = rpg_engine.roll_dice(dice_expr)
        user_name = ctx.author.display_name
        embed = discord.Embed(
            title="🎲 擲骰結果",
            description=f"**{user_name}** 擲出 `{dice_expr}`\n\n🎯 點數：**{desc}**",
            color=0x9B5DE5
        )
        if "1d20" in dice_expr.lower():
            if total == 20:
                embed.set_footer(text="🌟 滿分 20！大成功 (Critical Success)！")
            elif total == 1:
                embed.set_footer(text="💀 擲出 1 點！大失敗 (Critical Fumble)！")
        await ctx.send(embed=embed)
    except Exception as e:
        await ctx.send(f"❌ 擲骰失敗：{e}（請嘗試 `!roll`、`!roll 2d6` 或 `!roll 1d20+3`）")


async def handle_start_adventure(ctx: commands.Context, topic: Optional[str] = None):
    """建立專屬獨立討論串開啟全新文字冒險"""
    if not is_target_channel(ctx.channel):
        return

    if isinstance(ctx.channel, discord.Thread):
        await ctx.send("⚔️ 你目前已經在獨立冒險討論串中囉！可以隨時輸入 `!rpg` 查看狀態卡，或直接說「我想探索四周」推進冒險！")
        return

    adventure_topic = topic.strip() if topic and topic.strip() else "未知的迷宮遺跡"
    clean_topic = re.sub(r'[\/\\:\*\?"<>\|]', '', adventure_topic)

    thread_name = f"🗡️-冒險-{clean_topic[:25]}"
    thread = None
    try:
        thread = await ctx.message.create_thread(name=thread_name, auto_archive_duration=1440)
    except Exception as e:
        logger.warning(f"建立討論串失敗: {e}，改在當前頻道開始冒險。")
        thread = ctx.channel

    uname = ctx.author.display_name
    char = rpg_engine.get_or_create_rpg_character(thread.id, ctx.author.id, uname)

    opening_embed = discord.Embed(
        title=f"🗡️ 【{adventure_topic}】冒險篇章正式展開！",
        description=(
            f"歡迎來到全新的冒險世界，**{uname}**！\n\n"
            f"🏰 **冒險起點**：`{char['location']}`\n"
            f"🎯 **當前使命**：`{char['current_quest']}`\n\n"
            f"✨ **獨立隔離特性**：\n"
            f"• 在此討論串內的所有對話、背包物品與故事線索均為**專屬獨立**，絕不干擾日常群聊！\n"
            f"• 隨時輸入 `!save <存檔名>` 保存進度，或在未來開新討論串輸入 `!load <存檔名>` 繼承進度。\n"
            f"• 輸入 `!note <線索>` 記錄旅程筆記，或輸入 `!roll` 擲骰判定！"
        ),
        color=0xF15BB5
    )
    opening_embed.set_footer(text="💡 輸入 !sheet 檢視或匯入角色卡，直接在下方說話即可讓 AI 引導冒險！")

    if thread != ctx.channel:
        await ctx.send(f"🎮 專屬冒險討論串已建立！點擊前往：{thread.mention}")
    await thread.send(content=f"<@{ctx.author.id}> 冒險者已就位！", embed=opening_embed)


@bot.command(name="save", aliases=["存檔", "儲存"])
async def save_command(ctx: commands.Context, *, save_name: str = None):
    """保存當前冒險進度：!save <存檔名稱>"""
    if not is_target_channel(ctx.channel):
        return
    if not save_name:
        await ctx.send("❓ 請提供存檔名稱，例如：`!save 森林遺跡` 或 `!save 第一章通關`")
        return
    res = rpg_engine.save_game(ctx.channel.id, ctx.author.id, ctx.author.display_name, save_name)
    embed = discord.Embed(
        title="💾 冒險進度已保存！",
        description=f"存檔名稱：**【{res['save_name']}】**",
        color=0x06D6A0
    )
    embed.add_field(name="👤 角色", value=f"{res['char_name']}（{res['char_class']}）", inline=True)
    embed.add_field(name="❤️ 血量", value=f"{res['hp']}/{res['max_hp']}", inline=True)
    embed.add_field(name="🎒 背包物品", value=f"{len(res['inventory'])} 件隨身物品", inline=True)
    embed.add_field(name="📍 當前地點", value=res['location'], inline=True)
    embed.add_field(name="🎯 當前目標", value=res['current_quest'], inline=True)
    embed.add_field(name="📓 旅程手記", value=f"{res['notes_count']} 條獨立線索快照", inline=True)
    embed.set_footer(text=f"💡 隨時在任何頻道或討論串輸入 !load {res['save_name']} 即可接續進度！")
    await ctx.send(embed=embed)


@bot.command(name="load", aliases=["讀檔", "載入"])
async def load_command(ctx: commands.Context, *, save_name: str = None):
    """載入冒險進度：!load <存檔名稱>"""
    if not is_target_channel(ctx.channel):
        return
    if not save_name:
        await ctx.send("❓ 請提供要載入的存檔名稱，例如：`!load 森林遺跡`（可先輸入 `!saves` 查看所有存檔）")
        return
    res = rpg_engine.load_game(ctx.channel.id, ctx.author.id, ctx.author.display_name, save_name)
    if not res:
        await ctx.send(f"❌ 找不到存檔「{save_name}」。輸入 `!saves` 可查看目前所有保存的存檔清單！")
        return
    inv_str = "、".join(res["inventory"]) if res["inventory"] else "（背包空）"
    embed = discord.Embed(
        title="📂 冒險進度載入成功！",
        description=f"已將存檔 **【{res['save_name']}】** 還原至當前頻道/討論串！",
        color=0x118AB2
    )
    embed.add_field(name="👤 角色", value=f"**{res['char_name']}**（{res['char_class']}）", inline=True)
    embed.add_field(name="❤️ 血量", value=f"`{res['hp']}/{res['max_hp']}`", inline=True)
    embed.add_field(name="📍 當前地點", value=f"`{res['location']}`", inline=False)
    embed.add_field(name="🎯 當前目標", value=f"`{res['current_quest']}`", inline=False)
    embed.add_field(name="🎒 背包物品", value=inv_str, inline=False)
    embed.add_field(name="📓 旅程手記", value=f"已同步還原 {res['notes_count']} 條冒險線索（輸入 `!journal` 查看）", inline=False)
    embed.set_footer(text=f"存檔時間：{res['saved_at']} | 冒險者已整裝待發！")
    await ctx.send(embed=embed)


@bot.command(name="saves", aliases=["存檔清單", "存檔列表"])
async def saves_command(ctx: commands.Context):
    """查看所有冒險存檔清單：!saves"""
    if not is_target_channel(ctx.channel):
        return
    saves = rpg_engine.list_saves(ctx.author.id)
    if not saves:
        await ctx.send("💾 目前沒有任何冒險存檔。在冒險中輸入 `!save <名稱>` 即可隨時保存進度！")
        return
    lines = []
    for s in saves:
        creator = f" (由 {s['user_name']})" if s.get('user_name') else ""
        lines.append(
            f"📁 **【{s['save_name']}】**{creator}\n"
            f"└ 角色：{s['char_name']} ({s['char_class']}) | HP: {s['hp']}/{s['max_hp']} | 🎒 物品: {len(s['inventory'])} 件\n"
            f"└ 地點：{s['location']} | 手記: {len(s.get('journey_notes', []))} 條\n"
            f"└ 時間：`{s['saved_at'][:16]}`"
        )
    embed = discord.Embed(
        title="💾 冒險存檔清單",
        description="\n\n".join(lines),
        color=0x06D6A0
    )
    embed.set_footer(text="💡 載入請輸入 !load <存檔名>，刪除請輸入 !rpg delsave <存檔名>")
    await ctx.send(embed=embed)


@bot.command(name="note", aliases=["線索", "手記", "筆記"])
async def note_command(ctx: commands.Context, *, content: str = None):
    """記錄當前旅程的重要線索或手記：!note <內容>"""
    if not is_target_channel(ctx.channel):
        return
    if not content:
        await ctx.send("❓ 請輸入要記錄的冒險線索或手記，例如：`!note 守衛老伯最怕老鼠` 或 `!note 發現了發光的紫水晶手鐲`")
        return
    nid = rpg_engine.add_journey_note(
        channel_id=ctx.channel.id,
        user_id=ctx.author.id,
        user_name=ctx.author.display_name,
        content=content.strip(),
        note_type="clue"
    )
    await ctx.send(f"📝 已記錄當前旅程手記 **#{nid}**：{content.strip()}\n💡 此線索已永久鎖定在本次冒險記憶中，AI 推進劇情時會自動融入！")


@bot.command(name="journal", aliases=["notes", "手記本", "冒險筆記"])
async def journal_command(ctx: commands.Context):
    """查看當前冒險的所有手記與線索：!journal"""
    if not is_target_channel(ctx.channel):
        return
    notes = rpg_engine.get_journey_notes(ctx.channel.id)
    if not notes:
        await ctx.send("📓 目前這趟旅程還沒有任何手記或線索。\nAI 在冒險中發現線索會自動記下，你也可以隨時輸入 `!note <筆記>` 手動記錄！")
        return
    lines = []
    for n in notes:
        tag = "重要線索" if n["note_type"] == "clue" else n["note_type"]
        lines.append(f"**#{n['id']} [{tag}]** {n['content']}\n└ *由 {n['user_name']} 記於 {n['created_at'][5:16]}*")
    embed = discord.Embed(
        title="📓 當前冒險旅程手記與關鍵線索 (Journey Journal)",
        description="\n\n".join(lines),
        color=0xFFB703
    )
    embed.set_footer(text="💡 新增請輸入 !note <內容>，刪除請輸入 !rpg delnote <編號>")
    await ctx.send(embed=embed)


@bot.command(name="party", aliases=["team", "隊伍", "小隊"])
async def party_command(ctx: commands.Context):
    """查看當前冒險小隊全員狀態卡：!party"""
    if not is_target_channel(ctx.channel):
        return
    chars = rpg_engine.get_all_characters_in_channel(ctx.channel.id)
    if not chars:
        await ctx.send("🏕️ 當前頻道或討論串中尚未有任何冒險者就位。輸入 `!rpg` 即可建立角色出發！")
        return
    embed = discord.Embed(
        title=f"👥 冒險小隊隊伍名冊（共 {len(chars)} 位冒險者）",
        description=f"📍 **小隊當前地點**：`{chars[0]['location']}`\n🎯 **當前使命**：`{chars[0]['current_quest']}`",
        color=0x06D6A0
    )
    for c in chars:
        hp_ratio = max(0.0, min(1.0, c["hp"] / max(1, c["max_hp"])))
        filled_blocks = int(hp_ratio * 10)
        bar = "█" * filled_blocks + "░" * (10 - filled_blocks)
        inv_str = "、".join(c["inventory"]) if c["inventory"] else "（無隨身物品）"
        embed.add_field(
            name=f"👤 {c['char_name']}（{c['char_class']}）- {c['user_name']}",
            value=f"❤️ 血量：`{c['hp']}/{c['max_hp']}` [{bar}]\n🎒 背包：{inv_str}",
            inline=False
        )
    notes = rpg_engine.get_journey_notes(ctx.channel.id)
    embed.set_footer(text=f"📓 本趟旅程共累積 {len(notes)} 條線索手記（輸入 !journal 查看）| 保存隊伍進度 !save <存檔名>")
    await ctx.send(embed=embed)


@bot.command(name="adventure", aliases=["adv", "冒險"])
async def adventure_command(ctx: commands.Context, *, topic: str = None):
    """快速開啟專屬文字冒險討論串：!adventure [主題]"""
    await handle_start_adventure(ctx, topic)


async def send_sheet_embed(ctx: commands.Context, char: dict, title_prefix: str = "👑 冒險者角色卡"):
    """發送 Discord 角色卡 Embed，排版清晰美觀，自適應 D&D、修仙、賽博龐克、CoC 等各種世界觀"""
    prof = char.get("profile") or {}
    race = prof.get("race", "")
    char_class = char.get("char_class", "冒險者")
    alignment = prof.get("alignment", "")
    background = prof.get("background", "")
    backstory = prof.get("backstory", "")
    ac = prof.get("ac")
    stats = prof.get("stats") or {}
    skills = prof.get("skills") or []
    inv = char.get("inventory") or []
    custom_traits = prof.get("custom_traits") or {}

    embed = discord.Embed(
        title=f"{title_prefix} - 【{char['char_name']}】",
        description=backstory if backstory else "一位踏上未知道路的神秘旅者...",
        color=0xF15BB5 if char.get("hp", 100) > 25 else 0xE63946
    )

    # 1. 基本身份
    id_lines = [f"• **身分/定位**：{char_class}"]
    if race:
        id_lines.append(f"• **種族/靈根**：{race}")
    if alignment:
        id_lines.append(f"• **陣營/派系**：{alignment}")
    if background:
        id_lines.append(f"• **出身背景**：{background}")
    embed.add_field(name="👤 基本身分", value="\n".join(id_lines), inline=True)

    # 2. 狀態與體力
    hp_ratio = max(0.0, min(1.0, char.get("hp", 100) / max(1, char.get("max_hp", 100))))
    filled = int(hp_ratio * 10)
    bar = "█" * filled + "░" * (10 - filled)
    combat_lines = [f"• **體力/狀態值**：`{char.get('hp', 100)}/{char.get('max_hp', 100)}`\n  [{bar}]"]
    if ac is not None:
        combat_lines.append(f"• **防禦 (AC)**：`{ac}`")
    embed.add_field(name="⚔️ 狀態與數值", value="\n".join(combat_lines), inline=True)

    # 3. 自定義宇宙/專屬世界觀設定 (如修仙靈根/境界/法寶、賽博義體、CoC 理智值等)
    if custom_traits:
        trait_lines = [f"• **{k}**：{v}" for k, v in custom_traits.items() if k not in ("武器", "防具", "道具", "裝備", "隨身裝備", "隨身物品")]
        if trait_lines:
            embed.add_field(
                name="🔮 專屬世界觀特質 / 設定",
                value="\n".join(trait_lines[:8]),
                inline=False
            )

    # 4. 六大核心屬性 (僅在有 D&D 屬性時顯示)
    if stats:
        stat_lines = (
            f"`STR 力量`：{stats.get('STR', 10):>2}   |   `INT 智力`：{stats.get('INT', 10):>2}\n"
            f"`DEX 敏捷`：{stats.get('DEX', 10):>2}   |   `WIS 感知`：{stats.get('WIS', 10):>2}\n"
            f"`CON 體質`：{stats.get('CON', 10):>2}   |   `CHA 魅力`：{stats.get('CHA', 10):>2}"
        )
        embed.add_field(name="📊 核心能力屬性 (Ability Scores)", value=stat_lines, inline=False)

    # 5. 專長技能 / 法術 / 異能
    if skills:
        embed.add_field(
            name="🎭 專長技能 / 功法法術",
            value="、".join(skills),
            inline=False
        )

    # 6. 隨身背包與裝備 / 法寶 / 義體
    if inv:
        inv_display = "\n".join([f"• {it}" for it in inv])
        if len(inv_display) > 800:
            inv_display = "、".join(inv)
        embed.add_field(name="🎒 隨身持有物 / 法寶裝備", value=inv_display, inline=False)

    embed.set_footer(text="💡 支援修仙、賽博龐克、克蘇魯、奇幻冒險等任意世界觀！輸入 !sheet 查看或更新卡片")
    await ctx.send(embed=embed)


@bot.command(name="sheet", aliases=["角色卡", "人物卡", "charsheet"])
async def sheet_command(ctx: commands.Context, *, sheet_text: str = None):
    """
    極詳細冒險者角色卡 (Character Sheet) 檢視與匯入
    用法：
      !sheet                查看當前角色的極詳細角色卡
      !sheet <文字或附檔>   批次匯入/更新角色卡（支援直接貼上或上傳 .txt/.md 檔案）
    """
    if not is_target_channel(ctx.channel):
        return

    cid = ctx.channel.id
    uid = ctx.author.id
    uname = ctx.author.display_name

    content = (sheet_text or "").strip()

    # 支援從附件讀取
    if ctx.message.attachments:
        for att in ctx.message.attachments:
            if att.filename.endswith(('.txt', '.md', '.json')):
                try:
                    file_bytes = await att.read()
                    file_str = file_bytes.decode('utf-8', errors='ignore')
                    if file_str.strip():
                        content = file_str.strip() if not content else content + "\n\n" + file_str.strip()
                except Exception as e:
                    await ctx.send(f"⚠️ 讀取附件 `{att.filename}` 失敗：{e}")

    # 若有傳入文字或附檔，執行匯入
    if content:
        parsed = rpg_engine.parse_character_sheet(content, default_name=uname)
        applied = rpg_engine.apply_character_sheet(cid, uid, uname, parsed)
        await send_sheet_embed(ctx, applied, title_prefix="✨ 角色卡匯入成功！")
        return

    # 若無內容，查看當前角色卡
    char = rpg_engine.get_or_create_rpg_character(cid, uid, uname)
    await send_sheet_embed(ctx, char, title_prefix="👑 極詳細冒險者角色卡")


@bot.command(name="rpg", aliases=["status", "角色", "狀態"])
async def rpg_command(ctx: commands.Context, action: str = None, *, arg: str = None):
    """
    RPG 角色狀態卡與冒險管理
    用法：
      !rpg               查看當前角色狀態卡（血量、裝備背包、地點、任務、手記）
      !rpg sheet [內容]  匯入或檢視極詳細角色卡
      !rpg party         查看隊伍全員名冊與全體數值（雙人/多人合作冒險）
      !rpg start [主題]   自動建立專屬獨立討論串開啟冒險
      !rpg save <名稱>   儲存當前冒險進度（支援全隊存檔）
      !rpg load <名稱>   載入存檔進度（全隊角色與手記一併還原）
      !rpg saves         查看所有冒險存檔
      !rpg note <內容>   記錄冒險線索手記
      !rpg journal       查看當前冒險手記本
      !rpg name <角色名> 修改角色名字
      !rpg class <職業>  修改職業名稱
      !rpg reset         重置當前頻道冒險開新局
    """
    if not is_target_channel(ctx.channel):
        return

    cid = ctx.channel.id
    uid = ctx.author.id
    uname = ctx.author.display_name

    act = (action or "").lower()

    # 1. 角色卡匯入與檢視
    if act in ("sheet", "card", "import", "角色卡", "人物卡", "匯入角色卡"):
        await sheet_command(ctx, sheet_text=arg)
        return

    # 2. 快速開局
    if act in ("start", "new", "adv", "adventure", "開局", "開始"):
        await handle_start_adventure(ctx, arg)
        return

    # 3. 隊伍全員名冊
    if act in ("party", "team", "隊伍", "小隊", "全員"):
        await party_command(ctx)
        return

    # 4. 存檔與讀檔
    if act in ("save", "存檔", "儲存"):
        await save_command(ctx, save_name=arg)
        return

    if act in ("load", "讀檔", "載入"):
        await load_command(ctx, save_name=arg)
        return

    if act in ("saves", "list", "存檔清單", "存檔列表"):
        await saves_command(ctx)
        return

    if act in ("delsave", "deletesave", "rmsave", "刪除存檔"):
        if not arg:
            await ctx.send("❓ 請輸入要刪除的存檔名稱，例如：`!rpg delsave 森林遺跡`")
            return
        if rpg_engine.delete_save(arg.strip(), uid):
            await ctx.send(f"🗑️ 已刪除存檔 **【{arg.strip()}】**。")
        else:
            await ctx.send(f"❌ 找不到存檔 **【{arg.strip()}】**。")
        return

    # 5. 旅程手記與線索
    if act in ("note", "clue", "線索", "手記", "筆記"):
        await note_command(ctx, content=arg)
        return

    if act in ("journal", "notes", "手記本", "筆記本"):
        await journal_command(ctx)
        return

    if act in ("delnote", "rmnote", "刪除手記"):
        if not arg or not arg.strip().isdigit():
            await ctx.send("❓ 請輸入要刪除的手記編號，例如：`!rpg delnote 1`")
            return
        nid = int(arg.strip())
        if rpg_engine.delete_journey_note(cid, nid):
            await ctx.send(f"🗑️ 已刪除當前旅程手記 **#{nid}**。")
        else:
            await ctx.send(f"❌ 找不到當前旅程手記 **#{nid}**。")
        return

    # 6. 重置角色狀態與手記
    if act in ("reset", "restart", "重置", "重開"):
        rpg_engine.reset_rpg_character(cid, uid)
        await ctx.send(f"🔄 **{uname}** 的冒險紀錄與旅程手記已重置！可以隨時開始一場全新的冒險～✨")
        return

    # 7. 修改角色名與職業
    if act in ("name", "rename"):
        if not arg:
            await ctx.send("❓ 請輸入新角色名，例如：`!rpg name 艾莉絲`")
            return
        char = rpg_engine.update_rpg_character(cid, uid, uname, char_name=arg.strip())
        await ctx.send(f"✅ 角色名稱已更改為：**{char['char_name']}**")
        return

    if act in ("class", "job"):
        if not arg:
            await ctx.send("❓ 請輸入新職業名稱，例如：`!rpg class 精靈弓箭手`")
            return
        char = rpg_engine.update_rpg_character(cid, uid, uname, char_class=arg.strip())
        await ctx.send(f"✅ 角色職業已更改為：**{char['char_class']}**")
        return

    # 8. 預設：顯示角色狀態卡
    char = rpg_engine.get_or_create_rpg_character(cid, uid, uname)
    notes = rpg_engine.get_journey_notes(cid)
    inv_str = "、".join(char["inventory"]) if char["inventory"] else "（背包空空如也）"

    # 計算血量條 [████████░░]
    hp_ratio = max(0.0, min(1.0, char["hp"] / max(1, char["max_hp"])))
    filled_blocks = int(hp_ratio * 10)
    bar = "█" * filled_blocks + "░" * (10 - filled_blocks)

    embed = discord.Embed(
        title=f"🗡️ 【{char['char_name']}】的冒險者狀態卡",
        color=0xF15BB5 if char["hp"] > 25 else 0xE63946
    )
    prof = char.get("profile") or {}
    race_align = f"（{prof.get('race', '')} {char['char_class']} | {prof.get('alignment', '')}）" if (prof.get('race') or prof.get('alignment')) else f"（{char['char_class']}）"
    embed.add_field(name="👤 角色身分", value=f"**{char['char_name']}** {race_align}", inline=True)
    embed.add_field(name="❤️ 生命值 (HP)", value=f"`{char['hp']}/{char['max_hp']}` [{bar}]", inline=True)
    embed.add_field(name="📍 當前地點", value=f"`{char['location']}`", inline=False)
    embed.add_field(name="🎯 當前目標", value=f"`{char['current_quest']}`", inline=False)
    embed.add_field(name="🎒 背包隨身裝備", value=inv_str, inline=False)
    embed.add_field(name="📓 本趟旅程手記", value=f"{len(notes)} 條線索與情報（輸入 `!journal` 查看）", inline=False)
    all_chars = rpg_engine.get_all_characters_in_channel(cid)
    footer_text = "💡 完整角色卡 !sheet | 保存 !save <名> | 載入 !load <名> | 擲骰 !roll"
    if len(all_chars) > 1:
        footer_text = f"👥 目前小隊共有 {len(all_chars)} 位成員！輸入 !party 查看全隊名冊 | !sheet 查看角色卡 | !save <名> 存檔"
    embed.set_footer(text=footer_text)
    await ctx.send(embed=embed)


@bot.command(name="lore", aliases=["world", "設定", "世界觀"])
async def lore_command(ctx: commands.Context, action: str = None, *, arg: str = None):
    """
    動態世界設定集（Lorebook）管理
    用法：
      !lore                         列出所有世界設定
      !lore add <關鍵字> | <設定內容> 新增單筆設定
      !lore import <多筆文字或附檔>  批次匯入大量世界觀設定（支援 .txt/.md 附件）
      !lore clear                   清空世界觀設定集
      !lore del <編號>              刪除設定
    """
    if not is_target_channel(ctx.channel):
        return

    act = (action or "").lower()

    if not action or act in ("list", "show", "ls"):
        entries = rpg_engine.get_all_lorebook_entries()
        if not entries:
            await ctx.send("📖 目前世界設定集（Lorebook）是空的。可以輸入 `!lore add 關鍵字 | 設定內容` 或 `!lore import` 批次匯入！")
            return
        lines = []
        for e in entries:
            lines.append(f"**#{e['id']} 關鍵字：`{e['keywords']}`**\n└ {e['content']}")
        embed = discord.Embed(
            title=f"📖 世界觀設定集 (Lorebook) - 共 {len(entries)} 筆設定",
            description="\n\n".join(lines[:20]),
            color=0x00BBF9
        )
        if len(entries) > 20:
            embed.set_footer(text=f"💡 僅顯示前 20 筆（共 {len(entries)} 筆）| 聊天提到關鍵字時 AI 會自動調用！")
        else:
            embed.set_footer(text="💡 聊天提到關鍵字時，AI 會自動調用該設定，完全不浪費多餘 Token！")
        await ctx.send(embed=embed)
        return

    # 批次匯入一整個大設定集（支援文字或檔案附件）
    if act in ("import", "batch", "匯入", "批次"):
        raw_text = arg or ""
        # 檢查是否有夾帶 .txt 或 .md 檔案附件
        if ctx.message.attachments:
            att = ctx.message.attachments[0]
            if att.filename.lower().endswith((".txt", ".md", ".json", ".text")):
                try:
                    file_bytes = await att.read()
                    file_text = file_bytes.decode("utf-8", errors="replace")
                    raw_text = file_text + ("\n" + raw_text if raw_text else "")
                    logger.info(f"📖 成功從附件 {att.filename} 讀取世界觀設定 ({len(file_bytes)} bytes)")
                except Exception as e:
                    await ctx.send(f"❌ 讀取附件檔案失敗：{e}")
                    return

        if not raw_text.strip():
            await ctx.send(
                "❓ 請提供欲匯入的世界觀文字，或附加 `.txt` / `.md` 設定檔案！\n"
                "💡 支援格式：`【名稱】說明內容...` 或 `名稱：說明內容...`\n"
                "例如：\n"
                "`!lore import`\n"
                "`【艾爾達王國】南方大陸的古老帝國...`\n"
                "`【蒼銀龍騎士團】帝國最精銳的騎兵...`"
            )
            return

        parsed = rpg_engine.parse_lore_text_blocks(raw_text)
        if not parsed:
            await ctx.send("❌ 未能識別出有效的設定條目。請確認使用 `【名稱】內容` 或 `名稱：內容` 格式！")
            return

        count = rpg_engine.batch_add_lorebook_entries(parsed)
        preview_lines = [f"• **{kw}**：{cnt[:50]}..." if len(cnt) > 50 else f"• **{kw}**：{cnt}" for kw, cnt in parsed[:5]]
        desc = f"🎉 成功批次解析並存入 **{count}** 筆世界觀設定！\n\n**前幾筆設定預覽：**\n" + "\n".join(preview_lines)
        if count > 5:
            desc += f"\n\n*...以及其他 {count - 5} 筆設定已全部寫入資料庫！*"
        embed = discord.Embed(
            title="📖 世界觀設定集批次匯入成功！",
            description=desc,
            color=0x06D6A0
        )
        embed.set_footer(text="輸入 !lore 可查看所有設定清單 | 冒險中提到名稱時 AI 自動調用！")
        await ctx.send(embed=embed)
        return

    if act in ("clear", "reset", "清空", "重置"):
        count = rpg_engine.clear_lorebook()
        await ctx.send(f"🧹 已清空世界觀設定集（共刪除 {count} 筆設定）。隨時可以重新匯入新世界觀！")
        return

    if act in ("add", "new", "+"):
        if not arg or "|" not in arg:
            await ctx.send("❓ 請依照格式新增：`!lore add 關鍵字1, 關鍵字2 | 設定內容`\n例如：`!lore add 星露草 | 深山中的發光罕見藥草，能解百毒。`\n若要一次匯入多筆，請使用 `!lore import`！")
            return
        parts = arg.split("|", 1)
        kws = parts[0].strip()
        cnt = parts[1].strip()
        lid = rpg_engine.add_lorebook_entry(kws, cnt)
        await ctx.send(f"✅ 已成功將世界設定 **#{lid}** 存入設定集！關鍵字：`{kws}`")
        return

    if act in ("del", "delete", "rm", "-"):
        if not arg or not arg.strip().isdigit():
            await ctx.send("❓ 請輸入要刪除的設定編號，例如：`!lore del 1`")
            return
        lid = int(arg.strip())
        if rpg_engine.delete_lorebook_entry(lid):
            await ctx.send(f"🗑️ 已刪除設定 **#{lid}**。")
        else:
            await ctx.send(f"❌ 找不到設定編號 **#{lid}**。")
        return


# -------------------------------------------------------------
# 4.3 日語單字卡 (Flashcard / Anki) 與 Kotoba 搶答測驗系統
# -------------------------------------------------------------
active_quiz_channels: set[int] = set()


def check_quiz_answer_match(user_text: str, card: dict) -> bool:
    """檢查使用者回答是否命中單字卡讀音或中文意思"""
    ans = user_text.strip().lower()
    if not ans:
        return False

    # 消除標點符號與多餘空白
    ans_clean = re.sub(r"[\[\]\(\)（）\s。！？~～\.,!]", "", ans)
    if not ans_clean:
        return False

    # 1. 比對平假名/片假名讀音 (去掉注音括號)
    card_reading = card.get("reading", "").strip().lower()
    card_reading_clean = re.sub(r"[\[\]\(\)（）\s]", "", card_reading)
    if card_reading_clean and ans_clean == card_reading_clean:
        return True

    # 2. 比對日文漢字/正面字詞
    card_front_clean = re.sub(r"\s+", "", card.get("front", "").strip().lower())
    if ans_clean == card_front_clean:
        return True

    # 3. 比對中文背面的關鍵詞
    card_back = card.get("back", "").strip().lower()
    delimiters = r"[/,;、，；\(\)（）\s]+"
    tokens = [t.strip() for t in re.split(delimiters, card_back) if len(t.strip()) >= 1]

    for token in tokens:
        if len(token) == 1:
            if ans_clean == token:
                return True
        else:
            if token == ans_clean or token in ans_clean or ans_clean in token:
                if len(ans_clean) >= 2 or ans_clean == token:
                    return True

    return False


class FlashcardStudyView(discord.ui.View):
    """日語單字卡互動複習檢視 (支援翻面、TTS 朗讀、AI 解說、SRS 熟練度更新與下拉一鍵換牌組)"""
    def __init__(self, user_id: int, deck_id: int, card: dict, is_flipped: bool = False):
        super().__init__(timeout=300)
        self.user_id = user_id
        self.deck_id = deck_id
        self.card = card
        self.is_flipped = is_flipped
        self.update_components()

    def update_components(self):
        self.clear_items()
        if not self.is_flipped:
            btn_flip = discord.ui.Button(label="👁️ 翻面查看", style=discord.ButtonStyle.primary, custom_id="fc_flip", row=0)
            btn_flip.callback = self.on_flip
            self.add_item(btn_flip)

            btn_tts = discord.ui.Button(label="🔊 發音朗讀", style=discord.ButtonStyle.secondary, custom_id="fc_tts", row=0)
            btn_tts.callback = self.on_tts
            self.add_item(btn_tts)

            btn_ai = discord.ui.Button(label="✨ AI 家教解說", style=discord.ButtonStyle.secondary, custom_id="fc_ai", row=0)
            btn_ai.callback = self.on_ai
            self.add_item(btn_ai)

            btn_next = discord.ui.Button(label="⏭️ 下一張", style=discord.ButtonStyle.secondary, custom_id="fc_next", row=0)
            btn_next.callback = self.on_next
            self.add_item(btn_next)
        else:
            btn_again = discord.ui.Button(label="🔴 待複習 (Again)", style=discord.ButtonStyle.danger, custom_id="fc_again", row=0)
            btn_again.callback = self.on_again
            self.add_item(btn_again)

            btn_good = discord.ui.Button(label="🟢 記住了 (Good)", style=discord.ButtonStyle.success, custom_id="fc_good", row=0)
            btn_good.callback = self.on_good
            self.add_item(btn_good)

            btn_tts = discord.ui.Button(label="🔊 發音朗讀", style=discord.ButtonStyle.secondary, custom_id="fc_tts", row=0)
            btn_tts.callback = self.on_tts
            self.add_item(btn_tts)

            btn_ai = discord.ui.Button(label="✨ AI 家教解說", style=discord.ButtonStyle.secondary, custom_id="fc_ai", row=0)
            btn_ai.callback = self.on_ai
            self.add_item(btn_ai)

            btn_next = discord.ui.Button(label="⏭️ 下一張", style=discord.ButtonStyle.secondary, custom_id="fc_next", row=0)
            btn_next.callback = self.on_next
            self.add_item(btn_next)

        # Row 1: 下拉選單「移至其他牌組」
        decks = flashcard_engine.list_decks()[:25]
        options = []
        for d in decks:
            is_cur = (d["id"] == self.deck_id)
            label = f"{'📌 ' if is_cur else ''}{d['name'][:22]}"
            desc = f"共 {d['card_count']} 張卡片" + (" (目前牌組)" if is_cur else "")
            options.append(discord.SelectOption(
                label=label,
                value=str(d["id"]),
                description=desc[:50],
                default=is_cur
            ))

        if options:
            select = discord.ui.Select(
                placeholder="📂 移至其他牌組...",
                min_values=1,
                max_values=1,
                options=options,
                row=1
            )
            select.callback = self.on_select_deck
            self.add_item(select)

    def build_embed(self) -> discord.Embed:
        deck = flashcard_engine.get_deck(str(self.deck_id))
        deck_name = deck["name"] if deck else "預設牌組"

        with assistant_db.get_conn() as conn:
            row = conn.execute(
                "SELECT box, review_count FROM flashcard_progress WHERE user_id = ? AND card_id = ?",
                (self.user_id, self.card["id"])
            ).fetchone()
            box_lvl = row[0] if row else 1
            rev_cnt = row[1] if row else 0

        if not self.is_flipped:
            embed = discord.Embed(
                title=f"📖 日語單字卡 ｜ {deck_name}",
                description=f"# {self.card['front']}\n\n點擊下方「👁️ 翻面查看」確認假名讀音與中文意思。",
                color=0x5865F2
            )
            if self.card.get("notes"):
                embed.add_field(name="💡 提示", value=self.card["notes"], inline=False)
            embed.set_footer(text=f"卡片 ID: #{self.card['id']} ｜ 熟練度: 📦 箱子 {box_lvl} ｜ 複習次數: {rev_cnt}")
            return embed
        else:
            embed = discord.Embed(
                title=f"📖 日語單字卡 ｜ {deck_name}",
                description=(
                    f"# {self.card['front']}\n\n"
                    f"**讀音**：{('【' + self.card['reading'] + '】') if self.card.get('reading') else '（無）'}\n"
                    f"**意思**：{self.card['back']}"
                ),
                color=0x57F287
            )
            if self.card.get("example_jp"):
                ex_text = f"> {self.card['example_jp']}"
                if self.card.get("example_zh"):
                    ex_text += f"\n> *{self.card['example_zh']}*"
                embed.add_field(name="📝 例句", value=ex_text, inline=False)
            if self.card.get("notes"):
                embed.add_field(name="💡 備註", value=self.card["notes"], inline=False)
            embed.set_footer(text=f"卡片 ID: #{self.card['id']} ｜ 熟練度: 📦 箱子 {box_lvl} ｜ 複習次數: {rev_cnt}")
            return embed

    async def on_flip(self, interaction: discord.Interaction):
        self.is_flipped = True
        self.update_components()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    async def on_tts(self, interaction: discord.Interaction):
        text_to_speak = self.card.get("reading") or self.card.get("front")
        tts_ch = bot.get_channel(TTS_CHANNEL_ID) if TTS_CHANNEL_ID else None
        if tts_ch:
            try:
                await tts_ch.send(text_to_speak)
                await interaction.response.send_message(
                    f"🔊 已傳送「**{text_to_speak}**」至朗讀頻道！",
                    ephemeral=True
                )
            except Exception as e:
                await interaction.response.send_message(f"⚠️ 朗讀發送失敗：{e}", ephemeral=True)
        else:
            await interaction.response.send_message(
                f"🔊 朗讀發音：`{text_to_speak}`\n（目前未設置獨立 TTS 專用頻道）",
                ephemeral=True
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
            resp = await gemini_client.aio.models.generate_content(
                model=PRIMARY_MODEL,
                contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=0.7,
                    safety_settings=DEFAULT_SAFETY_SETTINGS
                )
            )
            ai_text = resp.text.strip() if resp and resp.text else "抱歉，家教暫時無法提供解說～"
            ai_embed = discord.Embed(
                title=f"✨ AI 家教解說：{front} {('【' + reading + '】') if reading else ''}",
                description=ai_text,
                color=0x9B59B6
            )
            await interaction.followup.send(embed=ai_embed, ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"⚠️ AI 解說取得失敗：{e}", ephemeral=True)

    async def on_again(self, interaction: discord.Interaction):
        flashcard_engine.record_review_result(interaction.user.id, self.card["id"], is_good=False)
        next_c = flashcard_engine.get_random_card_from_deck(self.deck_id)
        if not next_c:
            await interaction.response.edit_message(content="🎉 這個牌組內沒有更多卡片囉！", embed=None, view=None)
            return
        self.card = next_c
        self.is_flipped = False
        self.update_components()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    async def on_good(self, interaction: discord.Interaction):
        flashcard_engine.record_review_result(interaction.user.id, self.card["id"], is_good=True)
        next_c = flashcard_engine.get_random_card_from_deck(self.deck_id)
        if not next_c:
            await interaction.response.edit_message(content="🎉 太棒了！這個牌組內的卡片都看過一輪囉！", embed=None, view=None)
            return
        self.card = next_c
        self.is_flipped = False
        self.update_components()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    async def on_next(self, interaction: discord.Interaction):
        next_c = flashcard_engine.get_random_card_from_deck(self.deck_id)
        if not next_c:
            await interaction.response.edit_message(content="🎉 這個牌組內沒有卡片可供學習！", embed=None, view=None)
            return
        self.card = next_c
        self.is_flipped = False
        self.update_components()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

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
                ephemeral=True
            )
            await interaction.message.edit(embed=self.build_embed(), view=self)
        else:
            await interaction.response.send_message("❌ 移動失敗，請稍後再試。", ephemeral=True)


@bot.command(name="card", aliases=["flashcard", "單字", "卡片"])
async def card_command(ctx: commands.Context, subcmd: Optional[str] = None, *, arg: Optional[str] = None):
    """
    日語單字卡系統 (Flashcards / Anki)
    用法：
      !card                     開啟互動式單字卡學習 (翻面、發音、AI解說)
      !card [牌組名]            複習特定牌組
      !card add <單字> [讀音] [意思]  手動新增單字卡 (若僅輸入單字會由 AI 自動補完讀音與例句)
      !card import [牌組名]     夾帶 .apkg (Anki) 或 .txt/.tsv 匯入牌組
      !card decks               查看所有可用牌組清單
      !card deck <牌組名>       設定個人的預設收集牌組
      !card newdeck <牌組名>    建立全新牌組
      !card deldeck <牌組名>    刪除牌組
      !card generate <主題> [數量] 由 AI 依主題量身打造專屬單字庫
    """
    if not is_target_channel(ctx.channel):
        return

    if not ENABLE_FLASHCARD_MODE:
        await ctx.send("⚠️ 此伺服器未啟用單字卡與搶答測驗功能喔！")
        return

    sub = (subcmd or "").strip().lower()

    # 1. 查看所有牌組清單 (!card decks)
    if sub in ("decks", "list", "牌組", "清單"):
        decks = flashcard_engine.list_decks()
        user_deck = flashcard_engine.get_user_active_deck(ctx.author.id)
        lines = []
        for d in decks:
            is_active = (d["id"] == user_deck["id"])
            tag = "📌 **[目前預設]** " if is_active else "▫️ "
            desc = f"（{d['description']}）" if d["description"] else ""
            lines.append(f"{tag}**{d['name']}**：共 **{d['card_count']}** 張卡片 {desc}")

        embed = discord.Embed(
            title="📚 日語單字卡牌組清單",
            description="\n".join(lines) if lines else "目前沒有任何牌組。",
            color=0x5865F2
        )
        embed.set_footer(text="提示：使用 !card deck <牌組名> 切換預設牌組，或直接 !card <牌組名> 開始複習！")
        await ctx.send(embed=embed)
        return

    # 2. 設定個人預設收集牌組 (!card deck <牌組名>)
    if sub in ("deck", "use", "切換"):
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
        return

    # 3. 建立全新牌組 (!card newdeck <牌組名> [說明])
    if sub in ("newdeck", "createdeck", "adddeck", "新增牌組"):
        if not arg or not arg.strip():
            await ctx.send("❓ 請輸入新牌組名稱，例如：`!card newdeck 日本旅行生活日常`")
            return
        parts = arg.strip().split(maxsplit=1)
        dname = parts[0]
        ddesc = parts[1] if len(parts) > 1 else ""
        d = flashcard_engine.get_or_create_deck(dname, ddesc, created_by=ctx.author.display_name)
        await ctx.send(f"✅ 牌組「**{d['name']}**」已就緒！可以使用 `!card deck {d['name']}` 設為預設。")
        return

    # 4. 刪除自訂牌組 (!card deldeck <牌組名>)
    if sub in ("deldeck", "rmdeck", "刪除牌組"):
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
        flashcard_engine.delete_deck(target["id"])
        await ctx.send(f"🗑️ 已刪除牌組「**{target['name']}**」及其所有卡片。")
        return

    # 5. 匯入 Anki (.apkg) 或文字檔案 (!card import [牌組名])
    if sub in ("import", "匯入", "load"):
        if not ctx.message.attachments:
            await ctx.send(
                "📎 請在發送指令時夾帶檔案喔！\n"
                "• 支援 Anki 牌組檔 (`.apkg`)\n"
                "• 支援純文字/表格檔 (`.txt`, `.tsv`, `.csv`)\n"
                "例如上傳檔案並附上留言：`!card import N3高頻單字`"
            )
            return

        att = ctx.message.attachments[0]
        fname = att.filename.lower()
        target_deck_name = arg.strip() if arg and arg.strip() else None

        async with ctx.channel.typing():
            try:
                file_bytes = await att.read()
                if fname.endswith(".apkg"):
                    res = flashcard_engine.import_anki_apkg_bytes(
                        file_bytes,
                        target_deck_name=target_deck_name,
                        user_name=ctx.author.display_name
                    )
                    if res["success"]:
                        await ctx.send(
                            f"🎉 成功自 Anki 牌組檔匯入 **{res['imported_cards']}** 張單字卡！\n"
                            f"📚 牌組名稱：`{res['deck_name']}`\n"
                            f"隨時可以輸入 `!card {res['deck_name']}` 開始複習～🌸"
                        )
                    else:
                        await ctx.send(f"❌ Anki 匯入失敗：{res.get('error', '未知錯誤')}")
                elif fname.endswith((".txt", ".tsv", ".csv")):
                    text_content = file_bytes.decode("utf-8", errors="ignore")
                    deck_label = target_deck_name or Path(att.filename).stem
                    res = flashcard_engine.import_text_cards(
                        text_content,
                        target_deck_name=deck_label,
                        user_name=ctx.author.display_name
                    )
                    if res["success"]:
                        await ctx.send(
                            f"🎉 成功批次匯入 **{res['imported_cards']}** 張單字卡！\n"
                            f"📚 牌組名稱：`{res['deck_name']}`"
                        )
                    else:
                        await ctx.send(f"❌ 匯入失敗：{res.get('error', '未知錯誤')}")
                else:
                    await ctx.send("⚠️ 檔案格式不支援，請上傳 `.apkg`、`.tsv`、`.csv` 或 `.txt` 檔案！")
            except Exception as e:
                logger.error(f"匯入單字卡失敗: {e}", exc_info=True)
                await ctx.send(f"❌ 讀取檔案時發生錯誤：{e}")
        return

    # 6. 新增單字卡 (!card add <單字> [讀音] [意思])
    if sub in ("add", "new", "+", "新增"):
        if not arg or not arg.strip():
            await ctx.send("❓ 請提供單字內容，例如：`!card add 桜 さくら 櫻花` 或直接 `!card add 桜` 由 AI 補全！")
            return

        user_deck = flashcard_engine.get_user_active_deck(ctx.author.id)
        tokens = arg.strip().split()

        if len(tokens) >= 3:
            # 手動輸入完整欄位: 單字 讀音 意思
            front = tokens[0]
            reading = tokens[1]
            back = " ".join(tokens[2:])
            cid = flashcard_engine.add_card(
                deck_id=user_deck["id"],
                front=front,
                reading=reading,
                back=back,
                notes="手動新增"
            )
            await ctx.send(f"✅ 已將「**{front}**」【{reading}】＝ {back} 收錄至牌組：`{user_deck['name']}`！")
            return
        elif len(tokens) == 2:
            front = tokens[0]
            back = tokens[1]
            cid = flashcard_engine.add_card(
                deck_id=user_deck["id"],
                front=front,
                reading="",
                back=back,
                notes="手動新增"
            )
            await ctx.send(f"✅ 已將「**{front}**」＝ {back} 收錄至牌組：`{user_deck['name']}`！")
            return
        else:
            # 僅提供單字，由 AI 自動補齊讀音、釋義與實用例句
            word = tokens[0]
            async with ctx.channel.typing():
                prompt = (
                    f"請分析日語單字或詞組「{word}」，精準回傳 JSON 物件：\n"
                    f'{{"front": "{word}", "reading": "平假名讀音", "meaning": "繁體中文意思", "example_jp": "實用日文例句", "example_zh": "例句中文翻譯"}}\n'
                    f"嚴格以 JSON 格式輸出，不要加任何其他文字說明。"
                )
                try:
                    resp = await gemini_client.aio.models.generate_content(
                        model=PRIMARY_MODEL,
                        contents=prompt,
                        config=types.GenerateContentConfig(
                            temperature=0.3,
                            safety_settings=DEFAULT_SAFETY_SETTINGS
                        )
                    )
                    text = resp.text.strip()
                    m = re.search(r"\{.*\}", text, re.DOTALL)
                    if m:
                        data = json.loads(m.group(0))
                        cid = flashcard_engine.add_card(
                            deck_id=user_deck["id"],
                            front=data.get("front", word),
                            reading=data.get("reading", ""),
                            back=data.get("meaning", ""),
                            example_jp=data.get("example_jp", ""),
                            example_zh=data.get("example_zh", ""),
                            notes="AI 自動補齊"
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
                    logger.warning(f"AI 單字解析失敗 ({e})，以基礎單字存入。")

            cid = flashcard_engine.add_card(
                deck_id=user_deck["id"],
                front=word,
                reading="",
                back="待補充",
                notes="速記收錄"
            )
            await ctx.send(f"✅ 已將「**{word}**」收錄至牌組：`{user_deck['name']}`！")
            return

    # 7. 由 AI 根據主題生成牌組 (!card generate <主題> [數量])
    if sub in ("generate", "gen", "生成", "造詞"):
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
                resp = await gemini_client.aio.models.generate_content(
                    model=PRIMARY_MODEL,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        temperature=0.7,
                        safety_settings=DEFAULT_SAFETY_SETTINGS
                    )
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
                    created_by=ctx.author.display_name
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
                            notes=f"主題：{theme}"
                        )
                        imported += 1

                await ctx.send(
                    f"🎉 成功打造主題牌組「**{deck_name}**」！共收錄 **{imported}** 張實用單字卡。\n"
                    f"立即輸入 `!card {deck_name}` 開始複習，或是輸入 `!quiz {deck_name}` 進行搶答測驗！"
                )
                return
            except Exception as e:
                logger.error(f"AI 生成單字卡失敗: {e}", exc_info=True)
                await ctx.send(f"❌ 牌組生成失敗：{e}")
                return

    # 8. 預設行為：開啟互動式單字學習卡 (!card 或 !card <牌組名>)
    # 若使用者輸入 !card [某牌組]，檢查是否存在該牌組
    target_deck = None
    if sub:
        # 如果 sub 是 study，使用 arg 作為牌組
        if sub in ("study", "複習", "學習", "play"):
            if arg:
                target_deck = flashcard_engine.get_deck(arg.strip())
        else:
            # 使用者直接打 !card N5 或 !card 居酒屋
            possible_name = (sub + (" " + arg if arg else "")).strip()
            target_deck = flashcard_engine.get_deck(possible_name)
            if not target_deck:
                target_deck = flashcard_engine.get_deck(sub)

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

    view = FlashcardStudyView(user_id=ctx.author.id, deck_id=target_deck["id"], card=card)
    await ctx.send(embed=view.build_embed(), view=view)


@bot.command(name="decks", aliases=["牌組清單", "卡組"])
async def decks_command(ctx: commands.Context):
    """查看所有可用單字牌組"""
    if not is_target_channel(ctx.channel):
        return
    await card_command(ctx, subcmd="decks")


@bot.command(name="quiz", aliases=["搶答", "測驗", "kotoba"])
async def quiz_command(ctx: commands.Context, deck_or_count: Optional[str] = None, count_arg: Optional[str] = None):
    """
    Kotoba 風格多人限時日語單字搶答測驗
    用法：
      !quiz                  使用預設牌組進行 5 題快速搶答
      !quiz 10               進行 10 題搶答測驗
      !quiz [牌組名] [題數]   指定牌組與題數 (例如 !quiz N5 8)
    """
    if not is_target_channel(ctx.channel):
        return

    if not ENABLE_FLASHCARD_MODE:
        await ctx.send("⚠️ 此伺服器未啟用單字卡與搶答測驗功能喔！")
        return

    cid = ctx.channel.id
    if cid in active_quiz_channels:
        await ctx.send("⚠️ 這個頻道已經有正在進行中的搶答測驗囉！請先完成當前測驗。")
        return

    # 解析參數：牌組與題數
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

    active_quiz_channels.add(cid)
    try:
        intro_embed = discord.Embed(
            title="🌸 Kotoba 日語單字限時搶答即將開始！",
            description=(
                f"📚 **測驗牌組**：`{target_deck['name']}`\n"
                f"📝 **題目數量**：共 **{num_questions}** 題 ｜ ⏱️ **作答限時**：每題 20 秒\n"
                f"💡 **搶答規則**：看到題目後，直接在聊天室打出「**平假名讀音**」或「**中文意思**」！\n\n"
                f"🏁 **3 秒後即將出題，請大家準備好鍵盤～！**"
            ),
            color=0xFF77A9
        )
        await ctx.send(embed=intro_embed)
        await asyncio.sleep(3)

        scores: dict[int, dict] = {}

        for idx, card in enumerate(selected_cards):
            q_embed = discord.Embed(
                title=f"🎯【第 {idx + 1} / {num_questions} 題】 請搶答！",
                description=f"# {card['front']}\n\n請在聊天室打出【平假名讀音】或【中文意思】：",
                color=0x5865F2
            )
            await ctx.send(embed=q_embed)

            start_t = asyncio.get_event_loop().time()
            answered = False

            while asyncio.get_event_loop().time() - start_t < 20.0:
                remaining = 20.0 - (asyncio.get_event_loop().time() - start_t)
                if remaining <= 0:
                    break
                try:
                    msg = await bot.wait_for(
                        "message",
                        check=lambda m: m.channel.id == cid and not m.author.bot,
                        timeout=remaining
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

        # 結算排行榜
        if not scores:
            await ctx.send("🌸 **測驗結束！** 本次測驗無人得分，下次再接再厲！💪")
        else:
            sorted_scores = sorted(scores.values(), key=lambda x: x["points"], reverse=True)
            medals = ["🥇", "🥈", "🥉", "🎖️", "🎖️"]
            ranking_lines = []
            for r_idx, s in enumerate(sorted_scores):
                medal = medals[r_idx] if r_idx < len(medals) else "▫️"
                ranking_lines.append(f"{medal} **{s['name']}**：{s['points']} 分")

            end_embed = discord.Embed(
                title="🏆 Kotoba 測驗排行榜結算！",
                description="\n".join(ranking_lines) + "\n\n大家的表現都太厲害了！繼續保持喔～🌸",
                color=0xF1C40F
            )
            await ctx.send(embed=end_embed)

    finally:
        active_quiz_channels.discard(cid)


@bot.command(name="help", aliases=["指令", "說明"])
async def help_command(ctx: commands.Context):
    """查看常用指令說明"""
    if not is_target_channel(ctx.channel):
        return

    embed = discord.Embed(
        title="📖 GeminiBot 生活管家、文字冒險與共學指令手冊",
        description="除了直接用自然口語跟機器人聊天（支援看圖、網址摘要、自然呼叫管家），你也可以使用下列快捷指令：",
        color=0x48CAE4
    )
    embed.add_field(
        name="📋 待辦事項 (!todo)",
        value=(
            "• `!todo`：列出所有未完成事項\n"
            "• `!todo add <內容>`：新增待辦事項\n"
            "• `!todo done <編號/關鍵字>`：標記為已完成\n"
            "• `!todo del <編號>`：刪除待辦事項\n"
            "• `!todo all`：查看歷史紀錄"
        ),
        inline=False
    )
    embed.add_field(
        name="⏰ 定時提醒 (!remind)",
        value=(
            "• `!remind 10m 關火`：10 分鐘後提醒\n"
            "• `!remind 18:30 倒垃圾`：今天 18:30 提醒\n"
            "• `!reminds`：查看預約中的提醒清單\n"
            "• `!remind del <編號>`：取消提醒"
        ),
        inline=False
    )
    embed.add_field(
        name="🗡️ RPG 文字冒險與存檔 (!rpg / !party / !save / !load)",
        value=(
            "• `!rpg start [主題]`：自動建立專屬冒險討論串\n"
            "• `!rpg`：查看個人冒險狀態卡 (HP、背包裝備、地點、目標)\n"
            "• `!party`：查看冒險小隊全員狀態與血量（多人/雙人組隊）\n"
            "• `!save <存檔名>`：保存冒險進度與全隊快照\n"
            "• `!load <存檔名>`：載入存檔（小隊角色與手記一併還原）\n"
            "• `!saves`：查看所有冒險存檔清單\n"
            "• `!note <線索>`：記錄重要線索至本趟冒險手記\n"
            "• `!journal`：查看本趟冒險的所有線索與手記\n"
            "• `!roll [表達式]`：TRPG 擲骰 (如 `!roll`、`!roll 2d6`)\n"
            "• `!lore`：查看世界觀設定 (!lore import 批次匯入)\n"
            "• `!rpg reset`：重置當前頻道冒險開新局"
        ),
        inline=False
    )
    if ENABLE_FLASHCARD_MODE:
        embed.add_field(
            name="🌸 日語單字卡與 Kotoba 搶答 (!card / !quiz)",
            value=(
                "• `!card`：開啟互動式翻面單字卡（支援語音朗讀、AI 家教解說、一鍵換牌組）\n"
                "• `!card add <單字>`：新增單字（AI 自動補完讀音與例句）\n"
                "• `!card import [牌組名]`：夾帶 `.apkg` (Anki) 或 `.txt/.csv` 匯入牌組\n"
                "• `!card decks` (或 `!decks`)：查看所有牌組與卡片數量\n"
                "• `!card deck <牌組名>`：切換個人預設收集牌組\n"
                "• `!card generate <主題> [數量]`：由 AI 打造專屬主題單字庫\n"
                "• `!quiz [牌組名] [題數]` (或 `!搶答` / `!kotoba`)：Kotoba 風格多人限時搶答測驗"
            ),
            inline=False
        )
    embed.add_field(
        name="🧠 長期記憶庫 (!memory)",
        value=(
            "• `!memory`：查看所有保存的事實與喜好\n"
            "• `!remember <事實>`：直接存入長期記憶\n"
            "• `!forget <編號>`：刪除某一條記憶"
        ),
        inline=False
    )
    embed.add_field(
        name="🧹 話題重設 (!reset)",
        value="• `!reset`：封存當前頻道的聊天上下文，乾淨開啟新話題",
        inline=False
    )
    embed.set_footer(text="💡 口語直呼：也可以直接說「我想玩文字冒險！」或「15分鐘後提醒我收衣服」！")
    await ctx.send(embed=embed)


async def handle_general_chat(message: discord.Message):
    """處理日常三人閒聊（不刻意教日文、不標假名、自然繁體中文好友哈拉）"""
    author_name = message.author.display_name
    logger.info(f"💬 收到來自 [{author_name}] 的日常閒聊: {message.clean_content}")

    async with message.channel.typing():
        try:
            contents = await build_general_contents(message.channel, message)
            if not contents:
                return

            config = types.GenerateContentConfig(
                system_instruction=get_current_general_prompt(message),
                thinking_config=types.ThinkingConfig(thinking_level="medium"),
                temperature=0.8,
                tools=CURRENT_TOOLS,
                safety_settings=DEFAULT_SAFETY_SETTINGS,
            )

            models_to_try = [PRIMARY_MODEL] + FALLBACK_MODELS
            response = None
            last_error = None
            used_model = None

            token = assistant_tools.current_discord_msg.set(message)
            try:
                for m in models_to_try:
                    try:
                        logger.info(f"🤖 (日常群聊) 正在向 {m} 請求回答...")
                        response = await asyncio.wait_for(
                            gemini_client.aio.models.generate_content(
                                model=m,
                                contents=contents,
                                config=config,
                            ),
                            timeout=30.0,
                        )
                        if response and response.text:
                            used_model = m
                            break
                    except Exception as err:
                        last_error = err
                        logger.warning(f"⚠️ 模型 {m} 無法回應 ({err})，自動嘗試下一個備用模型...")
                        continue
            finally:
                assistant_tools.current_discord_msg.reset(token)

            if response and response.text:
                thoughts_count = getattr(response.usage_metadata, "thoughts_token_count", 0) if response.usage_metadata else 0
                if thoughts_count:
                    logger.info(f"💭 {used_model} 深度思考完成，思考消耗了 {thoughts_count} 個 tokens！")
                await send_split_messages(message.channel, response.text)
            else:
                raise last_error or RuntimeError("無法回應日常群聊訊息。")

        except Exception as e:
            logger.error(f"❌ 日常群聊呼叫 Gemini 失敗: {e}", exc_info=True)
            await message.channel.send("抱歉，我剛才斷線了一下，請再說一次。")


async def handle_japanese_learning_chat(message: discord.Message):
    """處理日語共學雙頻道對話（音字分離、小卡鏡像同步、HornBot朗讀）"""
    author_name = message.author.display_name
    logger.info(f"🌸 收到來自 [{author_name}] 的日語學習訊息: {message.clean_content}")

    main_channel = bot.get_channel(TARGET_CHANNEL_ID)
    tts_channel = bot.get_channel(TTS_CHANNEL_ID) if TTS_CHANNEL_ID else None

    # 雙頻道鏡像同步：在對向頻道以「小卡 (Embed)」顯示用戶發言 (HornBot 絕不朗讀 Embed)
    if tts_channel and main_channel:
        author_avatar = message.author.display_avatar.url if message.author.display_avatar else None
        embed_desc = message.clean_content.strip() or "🖼️ [分享了一張圖片]"

        if message.channel.id == TARGET_CHANNEL_ID:
            mirror_embed = discord.Embed(
                description=embed_desc,
                color=0xA0C4FF  # 柔和淡藍
            )
            mirror_embed.set_author(name=f"{author_name} (來自綜合頻道)", icon_url=author_avatar)
            if message.attachments and message.attachments[0].content_type and message.attachments[0].content_type.startswith("image/"):
                mirror_embed.set_image(url=message.attachments[0].url)
            try:
                await tts_channel.send(embed=mirror_embed)
            except Exception as e:
                logger.error(f"❌ 鏡像小卡發送至日文頻道失敗: {e}")

        elif message.channel.id == TTS_CHANNEL_ID:
            mirror_embed = discord.Embed(
                description=embed_desc,
                color=0xBDB2FF  # 柔和淺紫
            )
            mirror_embed.set_author(name=f"{author_name} (來自日文頻道)", icon_url=author_avatar)
            if message.attachments and message.attachments[0].content_type and message.attachments[0].content_type.startswith("image/"):
                mirror_embed.set_image(url=message.attachments[0].url)
            try:
                await main_channel.send(embed=mirror_embed)
            except Exception as e:
                logger.error(f"❌ 鏡像小卡發送至綜合頻道失敗: {e}")

    async with message.channel.typing():
        try:
            contents = await build_gemini_contents(message.channel, message)
            if not contents:
                return

            config = types.GenerateContentConfig(
                system_instruction=get_current_system_prompt(message),
                thinking_config=types.ThinkingConfig(thinking_level="medium"),
                temperature=0.7,
                tools=CURRENT_TOOLS,
                safety_settings=DEFAULT_SAFETY_SETTINGS,
            )

            models_to_try = [PRIMARY_MODEL] + FALLBACK_MODELS
            response = None
            last_error = None
            used_model = None

            token = assistant_tools.current_discord_msg.set(message)
            try:
                for m in models_to_try:
                    try:
                        logger.info(f"🤖 (日語共學) 正在向 {m} 請求回答...")
                        response = await asyncio.wait_for(
                            gemini_client.aio.models.generate_content(
                                model=m,
                                contents=contents,
                                config=config,
                            ),
                            timeout=30.0,
                        )
                        if response and response.text:
                            used_model = m
                            break
                    except Exception as model_err:
                        last_error = model_err
                        logger.warning(f"⚠️ 模型 {m} 無法回應 ({model_err})，自動嘗試下一個備用模型...")
                        continue
            finally:
                assistant_tools.current_discord_msg.reset(token)

            if response and response.text:
                thoughts_count = getattr(response.usage_metadata, "thoughts_token_count", 0) if response.usage_metadata else 0
                if thoughts_count:
                    logger.info(f"💭 {used_model} 深度思考完成，思考消耗了 {thoughts_count} 個 tokens！")
                reply_text = response.text
            else:
                raise last_error or RuntimeError("所有模型皆無法回應用戶訊息。")

            await send_synchronized_response(message.channel, reply_text, used_model=used_model or PRIMARY_MODEL)

        except Exception as e:
            logger.error(f"❌ 日語共學呼叫 Gemini 失敗: {e}", exc_info=True)
            await message.channel.send("すみません、剛才稍微斷線了一下，請再傳一次試試看。")


@bot.event
async def on_message(message: discord.Message):
    # 忽略來自自己或其他 Bot 的訊息
    if message.author.bot:
        return

    # 若為指令開頭 (如 !reset)，由指令系統處理
    if message.content.startswith("!"):
        await bot.process_commands(message)
        return

    # 只在指定的頻道或其旗下的討論串中觸發
    if not is_target_channel(message.channel):
        return

    # 忽略既無文字又無附件的純空白訊息
    if not message.content.strip() and not message.attachments:
        return

    # 判斷是否開啟日語學習模式 (社群版若 ENABLE_JAPANESE_MODE=false 則全頻繁中聊天與冒險)
    if not ENABLE_JAPANESE_MODE:
        await handle_general_chat(message)
        return

    # 判斷是否屬於「一般日常群聊頻道」
    is_general = False
    if GENERAL_CHANNEL_ID:
        if message.channel.id == GENERAL_CHANNEL_ID:
            is_general = True
        elif hasattr(message.channel, "parent_id") and message.channel.parent_id == GENERAL_CHANNEL_ID:
            is_general = True

    if is_general:
        await handle_general_chat(message)
    else:
        await handle_japanese_learning_chat(message)


if __name__ == "__main__":
    try:
        bot.run(DISCORD_BOT_TOKEN)
    except discord.errors.PrivilegedIntentsRequired:
        logger.error(
            "\n" + "=" * 65 + "\n"
            "❌ 啟動失敗：尚未在 Discord 開發者後台開啟「Message Content Intent」！\n"
            "👉 請前往：https://discord.com/developers/applications/\n"
            "👉 點選你的 Bot 應用程式 -> 左側點擊「Bot」頁籤\n"
            "👉 往下滑找到「Privileged Gateway Intents」區塊\n"
            "👉 將【Message Content Intent】切換為開啟 (ON)，並按下「Save Changes」！\n"
            "👉 儲存後重新執行 start_bot.bat 即可順利啟動。\n"
            + "=" * 65
        )
        sys.exit(1)
