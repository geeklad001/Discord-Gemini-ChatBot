import contextvars
import re
from datetime import datetime, timezone, timedelta
from typing import Optional
import discord

import assistant_db
import rpg_engine
import flashcard_engine

# 透過 ContextVar 傳遞當前觸發的 Discord 訊息上下文 (執行緒與非同步安全)
current_discord_msg = assistant_db.current_discord_msg


def record_memory(content: str) -> str:
    """
    記錄重要事情、個人喜好、習慣、紀念日或約定到情侶長期記憶庫中。即使聊天記憶重設，此記憶仍會永久保存。
    參數:
      content: 欲記錄的事實、喜好或備忘內容（例如：「妖貓喜歡抹茶」、「交往紀念日是 10 月 15 日」）。
    """
    msg = current_discord_msg.get()
    user_name = msg.author.display_name if msg else "共同"
    mid = assistant_db.add_memory(content=content, user_name=user_name)
    return f"已成功寫入長期記憶庫（編號 #{mid}）：{content}"


def create_todo(task: str) -> str:
    """
    新增一項待辦事項到兩人的共同待辦清單中。
    參數:
      task: 待辦事項內容（例如：「訂週六晚上餐廳」、「買衛生紙」）。
    """
    msg = current_discord_msg.get()
    user_name = msg.author.display_name if msg else "使用者"
    tid = assistant_db.add_todo(content=task, user_name=user_name)
    return f"已成功新增待辦事項（編號 #{tid}）：{task}（由 {user_name} 新增）"


def finish_todo(task_keyword_or_id: str) -> str:
    """
    將待辦事項標記為已完成。
    參數:
      task_keyword_or_id: 待辦編號（如 '1'）或關鍵字（如 '餐廳'）。
    """
    kw = str(task_keyword_or_id).strip()
    completed = None
    if kw.isdigit():
        completed = assistant_db.complete_todo_by_id(int(kw))
    if not completed:
        completed = assistant_db.complete_todo_by_keyword(kw)

    if completed:
        return f"已完成待辦事項 #{completed['id']}「{completed['content']}」！"
    return f"找不到符合「{kw}」的未完成待辦事項。"


def query_todos() -> str:
    """
    查詢目前兩人尚未完成的待辦事項清單。
    """
    todos = assistant_db.list_todos(only_pending=True)
    if not todos:
        return "目前沒有任何未完成的待辦事項！太棒了～"
    lines = ["目前尚未完成的待辦清單："]
    for t in todos:
        lines.append(f"- #{t['id']} {t['content']} (由 {t['user_name']} 於 {t['created_at']} 新增)")
    return "\n".join(lines)


def set_reminder(target_time_iso: str, reminder_message: str) -> str:
    """
    設定定時提醒。
    參數:
      target_time_iso: 目標提醒時間，必須為 ISO 8601 格式字串（例如 '2026-10-31T15:30:00+08:00'）。必須根據當前台北時間 (UTC+8) 計算。
      reminder_message: 時間到達時要提醒的內容（例如：「出門倒垃圾」、「關火」、「搶票」）。
    """
    msg = current_discord_msg.get()
    channel_id = msg.channel.id if msg else 0
    user_id = msg.author.id if msg else 0
    user_name = msg.author.display_name if msg else "使用者"

    dt = assistant_db.parse_iso_datetime(target_time_iso)
    if not dt:
        return f"時間格式解析失敗（{target_time_iso}），請確認使用正確的時間格式。"

    now = datetime.now(assistant_db.TZ_TAIPEI)
    if dt <= now:
        return f"設定的時間 {target_time_iso} 已經過去了，請設定未來的時間。"

    rid = assistant_db.add_reminder(
        channel_id=channel_id,
        user_id=user_id,
        user_name=user_name,
        remind_at_iso=dt.isoformat(),
        content=reminder_message
    )
    formatted_time = dt.strftime("%Y-%m-%d %H:%M")
    return f"已設定提醒（編號 #{rid}）！我會在 {formatted_time} 提醒 {user_name}：「{reminder_message}」。"


def parse_time_argument(arg: str) -> Optional[datetime]:
    """彈性解析命令列傳入的時間參數（如 10m、2h、30s、15:30 或完整日期）"""
    now = datetime.now(assistant_db.TZ_TAIPEI)
    arg = arg.strip().lower()

    # 相對時間：數字 + 單位 (s, m, h, d)
    m = re.match(r"^(\d+)\s*(s|sec|seconds?|m|min|minutes?|h|hr|hours?|d|days?)$", arg)
    if m:
        num = int(m.group(1))
        unit = m.group(2)
        if unit.startswith("s"):
            return now + timedelta(seconds=num)
        elif unit.startswith("m"):
            return now + timedelta(minutes=num)
        elif unit.startswith("h"):
            return now + timedelta(hours=num)
        elif unit.startswith("d"):
            return now + timedelta(days=num)

    # 鐘點時間：HH:MM (例如 15:30)
    m = re.match(r"^(\d{1,2}):(\d{2})$", arg)
    if m:
        hour = int(m.group(1))
        minute = int(m.group(2))
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if target <= now:
                target += timedelta(days=1)
            return target

    # 完整 ISO 或日期時間
    return assistant_db.parse_iso_datetime(arg)


def add_flashcard_card(
    word_or_phrase: str,
    reading: str = "",
    meaning: str = "",
    example_sentence_jp: str = "",
    example_sentence_zh: str = "",
    deck_name: Optional[str] = None
) -> str:
    """
    將欲學習的日語單字、片語、文法或佳句整理成單字卡 (Flashcard) 儲存至資料庫中。
    若未指定牌組名稱 (deck_name)，系統會自動放入預設的「⭐ 兩人的日常收藏」牌組中。
    參數:
      word_or_phrase: 日語漢字或單字/片語（例如：「木漏れ日」、「乾杯」、「美味しい」）。
      reading: 平假名讀音（例如：「こもれび」、「かんぱい」、「おいしい」）。
      meaning: 繁體中文釋義（例如：「從樹葉縫隙透進來的陽光」、「乾杯」、「好吃的」）。
      example_sentence_jp: 實用日語生活例句（若無可由 AI 自然造句）。
      example_sentence_zh: 繁體中文例句翻譯。
      deck_name: 目標牌組名稱（例如：「居酒屋日文」、「動漫常用語」）。若無指定可留空，自動放入預設收集箱。
    """
    msg = current_discord_msg.get()
    user_name = msg.author.display_name if msg else "使用者"
    user_id = msg.author.id if msg else 0

    if deck_name and deck_name.strip():
        deck = flashcard_engine.get_or_create_deck(deck_name.strip(), created_by=user_name)
    else:
        deck = flashcard_engine.get_user_active_deck(user_id) if user_id else flashcard_engine.get_or_create_deck("⭐ 兩人的日常收藏")

    card = flashcard_engine.add_card(
        deck_id=deck["id"],
        front=word_or_phrase,
        reading=reading,
        back=meaning,
        example_jp=example_sentence_jp,
        example_zh=example_sentence_zh
    )

    rd_str = f" ({reading})" if reading else ""
    ex_str = f"\n💬 例句：{example_sentence_jp}（{example_sentence_zh}）" if example_sentence_jp else ""
    return (
        f"🎴 已成功收錄單字卡 #{card['id']}！\n"
        f"單字：【{word_or_phrase}{rd_str}】➔ 釋義：{meaning}\n"
        f"📁 已歸入牌組：【{deck['name']}】{ex_str}"
    )


# 工具函式清單建立函式，支援依模式切換 (例如社群版可停用單字卡)
def get_assistant_tools(enable_flashcards: bool = True) -> list:
    tools = [
        record_memory,
        create_todo,
        finish_todo,
        query_todos,
        set_reminder,
        rpg_engine.rpg_roll_check,
        rpg_engine.rpg_update_state,
        rpg_engine.rpg_record_lore,
        rpg_engine.rpg_batch_record_lore,
        rpg_engine.rpg_record_story_clue,
        rpg_engine.rpg_import_character_sheet
    ]
    if enable_flashcards:
        tools.append(add_flashcard_card)
    return tools


# 預設工具函式列表，供 google-genai SDK 註冊 (向下相容)
ASSISTANT_TOOLS = get_assistant_tools(enable_flashcards=True)
