import contextvars
import re
from datetime import datetime, timezone, timedelta
from typing import Optional
import discord

import assistant_db
import rpg_engine

# 透過 ContextVar 傳遞當前觸發的 Discord 訊息上下文 (執行緒與非同步安全)
current_discord_msg = assistant_db.current_discord_msg


def record_memory(content: str) -> str:
    """
    記錄重要事情、個人喜好、習慣、重要日期、群組共識或約定到長期記憶庫中。即使聊天記憶重設，此記憶仍會永久保存。
    參數:
      content: 欲記錄的事實、喜好、設定或備忘內容（例如：「某成員喜歡抹茶」、「重要紀念日或活動是 10 月 15 日」）。
    """
    msg = current_discord_msg.get()
    user_name = msg.author.display_name if msg else "共同"
    mid = assistant_db.add_memory(content=content, user_name=user_name)
    return f"已成功寫入長期記憶庫（編號 #{mid}）：{content}"


def create_todo(task: str) -> str:
    """
    新增一項待辦事項到共同待辦清單中。
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
    查詢目前尚未完成的待辦事項清單。
    """
    todos = assistant_db.get_pending_todos()
    if not todos:
        return "目前沒有待辦事項，太棒了！"
    lines = ["📋 目前的待辦清單："]
    for t in todos:
        lines.append(f"- [#{t['id']}] {t['content']}（由 {t['user_name']} 於 {t['created_at']} 新增）")
    return "\n".join(lines)


def parse_time_expression(expr: str) -> Optional[datetime]:
    """解析簡單的相對時間（如 '10m', '2h', '1d', '30s'）或絕對時間（如 '18:30', '2026-10-31 18:30'）"""
    now = datetime.now(assistant_db.TZ_TAIPEI)
    expr = expr.strip()

    # 1. 相對時間：數字 + 單位
    rel_match = re.match(r"^(\d+)\s*(s|sec|m|min|h|hr|d|day)s?$", expr, re.IGNORECASE)
    if rel_match:
        val = int(rel_match.group(1))
        unit = rel_match.group(2).lower()
        if unit in ("s", "sec"):
            return now + timedelta(seconds=val)
        elif unit in ("m", "min"):
            return now + timedelta(minutes=val)
        elif unit in ("h", "hr"):
            return now + timedelta(hours=val)
        elif unit in ("d", "day"):
            return now + timedelta(days=val)

    # 2. 絕對時間：HH:MM
    time_match = re.match(r"^(\d{1,2}):(\d{2})$", expr)
    if time_match:
        h, m = int(time_match.group(1)), int(time_match.group(2))
        target = now.replace(hour=h, minute=m, second=0, microsecond=0)
        if target <= now:
            # 如果時間已過，視為明天的這個時間
            target += timedelta(days=1)
        return target

    # 3. 完整日期時間：YYYY-MM-DD HH:MM
    try:
        dt = datetime.strptime(expr, "%Y-%m-%d %H:%M")
        return dt.replace(tzinfo=assistant_db.TZ_TAIPEI)
    except ValueError:
        pass

    return None


def set_reminder(time_expr: str, content: str) -> str:
    """
    設定一個定時提醒。機器人將在指定時間於目前頻道標註成員發送提醒。
    參數:
      time_expr: 時間表達式。可以是相對時間（例如：'10m' 代表 10 分鐘後、'2h' 代表 2 小時後、'30s' 代表 30 秒後）或今天/明天的時間（例如：'18:30'）。
      content: 欲提醒的內容（例如：「記得收衣服」、「吃藥」、「開會」）。
    """
    msg = current_discord_msg.get()
    if not msg:
        return "無法取得目前的頻道資訊，無法設定提醒。"

    target_dt = parse_time_expression(time_expr)
    if not target_dt:
        return f"無法解析時間表達式「{time_expr}」。請使用如 '10m'（10分鐘後）、'2h'（2小時後）或 '18:30' 格式。"

    remind_at_str = target_dt.strftime("%Y-%m-%d %H:%M:%S")
    rid = assistant_db.add_reminder(
        channel_id=msg.channel.id,
        user_id=msg.author.id,
        user_name=msg.author.display_name,
        remind_at=remind_at_str,
        content=content,
    )
    return f"已成功設定提醒（編號 #{rid}）：將於 {remind_at_str} 提醒「{content}」！"


def get_scoped_tools(include_rpg: bool = True) -> list:
    """
    取得 Gemini 工具清單，包含生活記憶待辦與完整的 TRPG 冒險工具。
    """
    tools = [
        record_memory,
        create_todo,
        finish_todo,
        query_todos,
        set_reminder,
    ]
    if include_rpg:
        tools.extend([
            rpg_engine.rpg_roll_check,
            rpg_engine.rpg_update_state,
            rpg_engine.rpg_record_lore,
            rpg_engine.rpg_batch_record_lore,
            rpg_engine.rpg_record_story_clue,
            rpg_engine.rpg_import_character_sheet,
        ])
    return tools


def get_assistant_tools() -> list:
    return get_scoped_tools(include_rpg=True)


ASSISTANT_TOOLS = get_assistant_tools()
