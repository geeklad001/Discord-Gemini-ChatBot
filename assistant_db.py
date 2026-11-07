import sqlite3
import contextvars
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import Optional
import discord

# 台灣時區 (UTC+8)
TZ_TAIPEI = timezone(timedelta(hours=8))
DB_DIR = Path(__file__).parent / "data"
DB_PATH = DB_DIR / "assistant.db"

# 透過 ContextVar 傳遞當前觸發的 Discord 訊息上下文 (執行緒與非同步安全)
current_discord_msg: contextvars.ContextVar[Optional[discord.Message]] = contextvars.ContextVar("current_discord_msg", default=None)


def get_conn() -> sqlite3.Connection:
    DB_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """初始化助理資料庫表結構"""
    with get_conn() as conn:
        cursor = conn.cursor()
        # 1. 長期記憶庫
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_name TEXT,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)
        # 2. 待辦清單
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS todos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_name TEXT,
                content TEXT NOT NULL,
                is_done INTEGER DEFAULT 0,
                created_at TEXT NOT NULL,
                completed_at TEXT
            )
        """)
        # 3. 定時提醒
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS reminders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                channel_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                user_name TEXT,
                remind_at TEXT NOT NULL,
                content TEXT NOT NULL,
                is_sent INTEGER DEFAULT 0,
                created_at TEXT NOT NULL
            )
        """)
        conn.commit()


# -------------------------------------------------------------
# 長期記憶 (Long-term Memories)
# -------------------------------------------------------------
def add_memory(content: str, user_name: str = "共同") -> int:
    now_str = datetime.now(TZ_TAIPEI).strftime("%Y-%m-%d %H:%M:%S")
    with get_conn() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO memories (user_name, content, created_at) VALUES (?, ?, ?)",
            (user_name, content.strip(), now_str)
        )
        conn.commit()
        return cursor.lastrowid


def get_all_memories() -> list[dict]:
    with get_conn() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id, user_name, content, created_at FROM memories ORDER BY id ASC")
        return [dict(row) for row in cursor.fetchall()]


def delete_memory(memory_id: int) -> bool:
    with get_conn() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
        conn.commit()
        return cursor.rowcount > 0


def format_memories_for_prompt() -> str:
    """將所有長期記憶格式化為適合注入 System Instruction 的清單"""
    memories = get_all_memories()
    if not memories:
        return ""
    lines = ["【情侶長期記憶庫（由使用者設定，請隨時牢記於心並自然融入對話）】"]
    for m in memories:
        user_prefix = f"[{m['user_name']}]: " if m['user_name'] else ""
        lines.append(f"{m['id']}. {user_prefix}{m['content']}")
    return "\n".join(lines)


# -------------------------------------------------------------
# 待辦清單 (To-do List)
# -------------------------------------------------------------
def add_todo(content: str, user_name: str = "使用者") -> int:
    now_str = datetime.now(TZ_TAIPEI).strftime("%Y-%m-%d %H:%M:%S")
    with get_conn() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO todos (user_name, content, is_done, created_at) VALUES (?, ?, 0, ?)",
            (user_name, content.strip(), now_str)
        )
        conn.commit()
        return cursor.lastrowid


def list_todos(only_pending: bool = True) -> list[dict]:
    with get_conn() as conn:
        cursor = conn.cursor()
        if only_pending:
            cursor.execute("SELECT id, user_name, content, is_done, created_at FROM todos WHERE is_done = 0 ORDER BY id ASC")
        else:
            cursor.execute("SELECT id, user_name, content, is_done, created_at, completed_at FROM todos ORDER BY is_done ASC, id DESC")
        return [dict(row) for row in cursor.fetchall()]


def complete_todo_by_id(todo_id: int) -> Optional[dict]:
    now_str = datetime.now(TZ_TAIPEI).strftime("%Y-%m-%d %H:%M:%S")
    with get_conn() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id, user_name, content FROM todos WHERE id = ? AND is_done = 0", (todo_id,))
        row = cursor.fetchone()
        if not row:
            return None
        cursor.execute("UPDATE todos SET is_done = 1, completed_at = ? WHERE id = ?", (now_str, todo_id))
        conn.commit()
        return dict(row)


def complete_todo_by_keyword(keyword: str) -> Optional[dict]:
    now_str = datetime.now(TZ_TAIPEI).strftime("%Y-%m-%d %H:%M:%S")
    with get_conn() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id, user_name, content FROM todos WHERE is_done = 0 AND content LIKE ? ORDER BY id ASC LIMIT 1", (f"%{keyword}%",))
        row = cursor.fetchone()
        if not row:
            return None
        todo_id = row["id"]
        cursor.execute("UPDATE todos SET is_done = 1, completed_at = ? WHERE id = ?", (now_str, todo_id))
        conn.commit()
        return dict(row)


def delete_todo(todo_id: int) -> bool:
    with get_conn() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM todos WHERE id = ?", (todo_id,))
        conn.commit()
        return cursor.rowcount > 0


# -------------------------------------------------------------
# 定時提醒 (Scheduled Reminders)
# -------------------------------------------------------------
def add_reminder(channel_id: int, user_id: int, user_name: str, remind_at_iso: str, content: str) -> int:
    """新增定時提醒。remind_at_iso 應為 ISO 8601 或 YYYY-MM-DD HH:MM:SS 格式"""
    now_str = datetime.now(TZ_TAIPEI).strftime("%Y-%m-%d %H:%M:%S")
    with get_conn() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """INSERT INTO reminders (channel_id, user_id, user_name, remind_at, content, is_sent, created_at)
               VALUES (?, ?, ?, ?, ?, 0, ?)""",
            (channel_id, user_id, user_name, remind_at_iso.strip(), content.strip(), now_str)
        )
        conn.commit()
        return cursor.lastrowid


def parse_iso_datetime(dt_str: str) -> Optional[datetime]:
    """彈性解析時間字串為帶台北時區的 datetime"""
    dt_str = dt_str.strip()
    # 嘗試從 ISO 格式解析
    try:
        dt = datetime.fromisoformat(dt_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=TZ_TAIPEI)
        return dt
    except Exception:
        pass

    # 嘗試標準時間格式
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M:%S", "%Y/%m/%d %H:%M"):
        try:
            dt = datetime.strptime(dt_str, fmt)
            return dt.replace(tzinfo=TZ_TAIPEI)
        except Exception:
            pass
    return None


def get_pending_due_reminders() -> list[dict]:
    """取得所有已到達提醒時間且尚未發送的項目"""
    now = datetime.now(TZ_TAIPEI)
    due_items = []
    with get_conn() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id, channel_id, user_id, user_name, remind_at, content FROM reminders WHERE is_sent = 0")
        for row in cursor.fetchall():
            item = dict(row)
            remind_dt = parse_iso_datetime(item["remind_at"])
            if remind_dt and remind_dt <= now:
                due_items.append(item)
    return due_items


def mark_reminder_sent(reminder_id: int):
    with get_conn() as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE reminders SET is_sent = 1 WHERE id = ?", (reminder_id,))
        conn.commit()


def list_upcoming_reminders() -> list[dict]:
    """列出未來尚未觸發的提醒事項"""
    now = datetime.now(TZ_TAIPEI)
    upcoming = []
    with get_conn() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id, channel_id, user_id, user_name, remind_at, content FROM reminders WHERE is_sent = 0 ORDER BY remind_at ASC")
        for row in cursor.fetchall():
            item = dict(row)
            remind_dt = parse_iso_datetime(item["remind_at"])
            if remind_dt and remind_dt > now:
                upcoming.append(item)
    return upcoming
