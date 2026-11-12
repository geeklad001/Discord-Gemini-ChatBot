import os
import sys
import logging
import asyncio
from pathlib import Path
from typing import List, Optional

import discord
from discord.ext import commands
from dotenv import load_dotenv
from google import genai
from google.genai import types

# -------------------------------------------------------------
# 1. 環境設定與日誌初始化
# -------------------------------------------------------------
if sys.platform.startswith("win"):
    try:
        if sys.stdout and sys.stdout.encoding.lower() != "utf-8":
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        if sys.stderr and sys.stderr.encoding.lower() != "utf-8":
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(dotenv_path=BASE_DIR / ".env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("DiscordGeminiBot")

DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN", "").strip()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
TARGET_CHANNEL_ID_RAW = os.getenv("TARGET_CHANNEL_ID", "").strip()
PRIMARY_MODEL = os.getenv("PRIMARY_MODEL", "gemini-3.8-flash").strip()
FALLBACK_MODELS = ["gemini-2.5-flash", "gemini-flash-lite"]

# 檢查關鍵環境變數
if not DISCORD_BOT_TOKEN or DISCORD_BOT_TOKEN == "YOUR_DISCORD_BOT_TOKEN_HERE":
    logger.error("❌ 找不到有效的 DISCORD_BOT_TOKEN！請確認已在 .env 中正確填寫機器人 Token。")
    sys.exit(1)

if not GEMINI_API_KEY or GEMINI_API_KEY == "YOUR_GEMINI_API_KEY_HERE":
    logger.error("❌ 找不到有效的 GEMINI_API_KEY！請確認已在 .env 中正確填寫 Gemini API Key。")
    sys.exit(1)

TARGET_CHANNEL_ID: Optional[int] = None
if TARGET_CHANNEL_ID_RAW and TARGET_CHANNEL_ID_RAW.isdigit():
    TARGET_CHANNEL_ID = int(TARGET_CHANNEL_ID_RAW)

# 初始化 Google GenAI 客戶端
gemini_client = genai.Client(api_key=GEMINI_API_KEY)

# -------------------------------------------------------------
# 2. 載入人設提示詞 (Prompt)
# -------------------------------------------------------------
def load_system_prompt() -> str:
    prompt_path = BASE_DIR / "prompt.txt"
    if prompt_path.exists():
        try:
            content = prompt_path.read_text(encoding="utf-8").strip()
            if content:
                logger.info("📄 已成功載入人設檔案: prompt.txt")
                return content
        except Exception as e:
            logger.warning(f"⚠️ 讀取 prompt.txt 失敗 ({e})，將採用預設人設。")
    return "你是這個 Discord 伺服器裡的隨性群友，講話自然、略帶冷幽默，不用客服口氣，像個真實的朋友一樣聊天。"

SYSTEM_INSTRUCTION = load_system_prompt()

# -------------------------------------------------------------
# 3. Discord Bot 初始化
# -------------------------------------------------------------
intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True

bot = commands.Bot(
    command_prefix="!",
    intents=intents,
    allowed_mentions=discord.AllowedMentions(everyone=False, roles=False, replied_user=True),
)

# -------------------------------------------------------------
# 4. 輔助工具函式
# -------------------------------------------------------------
async def send_split_messages(
    destination: discord.abc.Messageable,
    text: str,
    max_len: int = 1900,
    reply_to: Optional[discord.Message] = None,
):
    """將過長的文字切分成多則訊息依序發送，避免超過 Discord 2000 字元上限。"""
    if not text:
        return

    chunks = []
    current_chunk = ""
    for line in text.splitlines(keepends=True):
        if len(current_chunk) + len(line) > max_len:
            if current_chunk:
                chunks.append(current_chunk)
                current_chunk = ""
            while len(line) > max_len:
                chunks.append(line[:max_len])
                line = line[max_len:]
        current_chunk += line

    if current_chunk:
        chunks.append(current_chunk)

    first = True
    for chunk in chunks:
        chunk = chunk.strip()
        if not chunk:
            continue
        try:
            if first and reply_to:
                await reply_to.reply(chunk, mention_author=False)
                first = False
            else:
                await destination.send(chunk)
        except Exception as e:
            logger.error(f"發送訊息失敗: {e}")
            break

async def call_gemini_with_fallback(
    contents: List[types.Content],
    system_instruction: str,
) -> str:
    """呼叫 Gemini 生成回覆，支援自動降級 fallback 機制。"""
    candidate_models = [PRIMARY_MODEL] + [m for m in FALLBACK_MODELS if m != PRIMARY_MODEL]
    last_error = None

    for model_name in candidate_models:
        try:
            config = types.GenerateContentConfig(
                system_instruction=system_instruction,
                temperature=0.8,
            )
            response = await asyncio.wait_for(
                gemini_client.aio.models.generate_content(
                    model=model_name,
                    contents=contents,
                    config=config,
                ),
                timeout=30.0,
            )
            if response and response.text:
                return response.text.strip()
        except Exception as e:
            last_error = e
            logger.warning(f"⚠️ 模型 {model_name} 呼叫失敗: {e}，嘗試降級備用模型...")
            continue

    logger.error(f"❌ 所有 Gemini 模型呼叫皆失敗，最後錯誤: {last_error}")
    return "（摸了摸口袋）...抱歉，我剛才腦袋稍微斷線了一下，可以再說一次嗎？"

async def build_chat_context(
    channel: discord.TextChannel,
    current_message: discord.Message,
    history_limit: int = 10,
) -> List[types.Content]:
    """從頻道抓取近期歷史訊息，組合成連貫的對話上下文。"""
    raw_messages = []
    try:
        async for msg in channel.history(limit=history_limit, before=current_message):
            # 排除空的或是由其他機器人發送的指令訊息
            if msg.content or msg.attachments:
                raw_messages.append(msg)
    except Exception as e:
        logger.warning(f"抓取歷史訊息時出錯: {e}")

    raw_messages.reverse()  # 轉為時間正序

    contents: List[types.Content] = []

    for msg in raw_messages:
        role = "model" if msg.author.id == bot.user.id else "user"
        author_name = msg.author.display_name
        parts = []

        text_content = f"[{author_name}]: {msg.content}" if role == "user" else msg.content
        if text_content.strip():
            parts.append(types.Part.from_text(text=text_content))

        if parts:
            contents.append(types.Content(role=role, parts=parts))

    # 加入當前回應目標訊息
    current_author = current_message.author.display_name
    current_clean_text = current_message.clean_content.replace(f"@{bot.user.display_name}", "").strip()
    current_parts = []

    # 處理圖片附件
    for att in current_message.attachments:
        if att.content_type and att.content_type.startswith("image/"):
            try:
                img_bytes = await att.read()
                current_parts.append(types.Part.from_bytes(data=img_bytes, mime_type=att.content_type))
            except Exception as e:
                logger.warning(f"讀取圖片附件失敗: {e}")

    prompt_text = f"[{current_author}]: {current_clean_text}" if current_clean_text else f"[{current_author} 傳送了一張圖片]"
    current_parts.append(types.Part.from_text(text=prompt_text))

    contents.append(types.Content(role="user", parts=current_parts))
    return contents

# -------------------------------------------------------------
# 5. 事件監聽 (Events)
# -------------------------------------------------------------
@bot.event
async def on_ready():
    logger.info("=" * 60)
    logger.info(f"🚀 {bot.user.name} 已成功連線至 Discord！")
    logger.info(f"🆔 Bot ID: {bot.user.id}")
    if TARGET_CHANNEL_ID:
        target_ch = bot.get_channel(TARGET_CHANNEL_ID)
        ch_name = f"#{target_ch.name}" if target_ch else "找不到頻道(請確認ID)"
        logger.info(f"🎯 目標互動頻道: {ch_name} (ID: {TARGET_CHANNEL_ID})")
    else:
        logger.info("🎯 目標頻道未設定，將在被 @提及 時進行回覆。")
    logger.info(f"🧠 主要模型: {PRIMARY_MODEL}")
    logger.info("=" * 60)

    activity = discord.CustomActivity(name="在群裡摸魚打工中 | 找我直接說話")
    await bot.change_presence(status=discord.Status.online, activity=activity)

@bot.event
async def on_message(message: discord.Message):
    # 忽略自己或所有其他機器人的發言
    if message.author.bot or message.author.id == bot.user.id:
        return

    # 判斷是否需要回應
    is_target_channel = (TARGET_CHANNEL_ID is not None and message.channel.id == TARGET_CHANNEL_ID)
    is_mentioned = bot.user in message.mentions

    # 若非目標頻道且未被 mention，則不回應
    if not is_target_channel and not is_mentioned:
        return

    # 若是指令（例如 !help），交給指令處理器
    if message.content.startswith("!"):
        await bot.process_commands(message)
        return

    # 觸發 AI 回覆
    async with message.channel.typing():
        try:
            contents = await build_chat_context(message.channel, message)
            reply_text = await call_gemini_with_fallback(contents, SYSTEM_INSTRUCTION)
            await send_split_messages(message.channel, reply_text, reply_to=message)
        except Exception as e:
            logger.error(f"處理訊息時發生錯誤: {e}", exc_info=True)
            await message.reply("（揉了揉太陽穴）...後台出了點小狀況，等下再聊吧。", mention_author=False)

# -------------------------------------------------------------
# 6. 基本實用指令
# -------------------------------------------------------------
@bot.command(name="ping")
async def ping(ctx: commands.Context):
    """檢查機器人延遲與在線狀態"""
    latency_ms = round(bot.latency * 1000)
    await ctx.reply(f"🏓 延遲 {latency_ms} ms。在線呢，別催了。", mention_author=False)

@bot.command(name="reload")
async def reload_prompt(ctx: commands.Context):
    """熱重載人設提示詞 (修改 prompt.txt 後可直接生效)"""
    global SYSTEM_INSTRUCTION
    SYSTEM_INSTRUCTION = load_system_prompt()
    await ctx.reply("🔄 人設 prompt.txt 已重新載入！現在已經換上新設定囉。", mention_author=False)

# -------------------------------------------------------------
# 7. 主程式進入點
# -------------------------------------------------------------
if __name__ == "__main__":
    try:
        bot.run(DISCORD_BOT_TOKEN)
    except discord.LoginFailure:
        logger.error("❌ 登入失敗！請確認 .env 中的 DISCORD_BOT_TOKEN 是否正確複製。")
    except Exception as e:
        logger.error(f"❌ 機器人運行遭遇錯誤: {e}")
