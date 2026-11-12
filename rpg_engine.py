import os
import sqlite3
import random
import re
import json
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import Optional

from assistant_db import get_conn, get_db_path, TZ_TAIPEI, current_discord_msg

_initialized_rpg_dbs: set[str] = set()


def init_rpg_tables():
    """初始化 RPG 狀態機、Lorebook 世界設定集、旅程手記與存檔資料表（每個資料庫實例僅執行一次）"""
    db_key = str(get_db_path())
    if db_key in _initialized_rpg_dbs:
        return
    with get_conn() as conn:
        cursor = conn.cursor()
        # 1. 冒險角色與狀態機 (權威狀態 Authoritative State)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS rpg_characters (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                channel_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                user_name TEXT NOT NULL,
                char_name TEXT DEFAULT '冒險者',
                char_class TEXT DEFAULT '初級冒險者',
                hp INTEGER DEFAULT 100,
                max_hp INTEGER DEFAULT 100,
                inventory TEXT DEFAULT '["隨身小刀", "火把x1", "水壺"]',
                location TEXT DEFAULT '冒險公會大廳',
                current_quest TEXT DEFAULT '挑選第一個委託任務',
                profile_json TEXT DEFAULT '{}',
                updated_at TEXT NOT NULL,
                UNIQUE(channel_id, user_id)
            )
        """)
        # 2. 動態世界觀設定集 (Lorebook / World Info)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS lorebook (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                keywords TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)
        # 3. 當前旅程獨立手記與關鍵線索 (Journey Journal / Extra Memory)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS rpg_journey_notes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                channel_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                user_name TEXT NOT NULL,
                note_type TEXT DEFAULT 'clue',
                content TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)
        # 4. 冒險進度存檔快照 (Saves / Snapshots)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS rpg_saves (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                user_name TEXT DEFAULT '',
                save_name TEXT NOT NULL,
                char_name TEXT NOT NULL,
                char_class TEXT NOT NULL,
                hp INTEGER NOT NULL,
                max_hp INTEGER NOT NULL,
                inventory TEXT NOT NULL,
                location TEXT NOT NULL,
                current_quest TEXT NOT NULL,
                journey_notes_json TEXT DEFAULT '[]',
                saved_at TEXT NOT NULL,
                UNIQUE(user_id, save_name)
            )
        """)
        try:
            cursor.execute("ALTER TABLE rpg_saves ADD COLUMN user_name TEXT DEFAULT ''")
        except sqlite3.OperationalError:
            pass
        try:
            cursor.execute("ALTER TABLE rpg_saves ADD COLUMN party_json TEXT DEFAULT '[]'")
        except sqlite3.OperationalError:
            pass
        try:
            cursor.execute("ALTER TABLE rpg_saves ADD COLUMN profile_json TEXT DEFAULT '{}'")
        except sqlite3.OperationalError:
            pass
        try:
            cursor.execute("ALTER TABLE rpg_characters ADD COLUMN profile_json TEXT DEFAULT '{}'")
        except sqlite3.OperationalError:
            pass
        # 5. 活躍冒險會話標記表 (Active RPG Sessions)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS rpg_active_sessions (
                channel_id INTEGER PRIMARY KEY,
                is_active INTEGER DEFAULT 1,
                started_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        conn.commit()
    _initialized_rpg_dbs.add(db_key)


def set_rpg_session_active(channel_id: int, is_active: bool = True):
    """標記指定頻道或討論串當前是否正處於活躍的 TRPG 冒險中"""
    init_rpg_tables()
    now_str = datetime.now(TZ_TAIPEI).strftime("%Y-%m-%d %H:%M:%S")
    with get_conn() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO rpg_active_sessions (channel_id, is_active, started_at, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(channel_id) DO UPDATE SET
                is_active = excluded.is_active,
                updated_at = excluded.updated_at
        """, (channel_id, 1 if is_active else 0, now_str, now_str))
        conn.commit()


def is_rpg_session_active(channel_id: int) -> bool:
    """查詢指定頻道或討論串是否為活躍的冒險會話"""
    init_rpg_tables()
    with get_conn() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT is_active FROM rpg_active_sessions WHERE channel_id = ?", (channel_id,))
        row = cursor.fetchone()
        if row:
            return bool(row[0])
    return False


# -------------------------------------------------------------
# 1. 權威角色狀態管理 (Authoritative RPG State)
# -------------------------------------------------------------
def get_or_create_rpg_character(channel_id: int, user_id: int, user_name: str) -> dict:
    """取得或建立指定玩家在該頻道的 RPG 角色狀態"""
    init_rpg_tables()
    now_str = datetime.now(TZ_TAIPEI).strftime("%Y-%m-%d %H:%M:%S")
    with get_conn() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM rpg_characters WHERE channel_id = ? AND user_id = ?",
            (channel_id, user_id)
        )
        row = cursor.fetchone()
        if row:
            char = dict(row)
            try:
                char["inventory"] = json.loads(char["inventory"])
            except Exception:
                char["inventory"] = ["隨身小刀", "火把x1"]
            try:
                char["profile"] = json.loads(char.get("profile_json") or "{}")
            except Exception:
                char["profile"] = {}
            return char

        # 預設建立新角色
        default_inv = ["隨身小刀", "火把x1", "乾糧x2"]
        cursor.execute(
            """INSERT INTO rpg_characters 
               (channel_id, user_id, user_name, char_name, char_class, hp, max_hp, inventory, location, current_quest, profile_json, updated_at)
               VALUES (?, ?, ?, '冒險者', '見習冒險者', 100, 100, ?, '冒險者酒館', '展開新冒險', '{}', ?)""",
            (channel_id, user_id, user_name, json.dumps(default_inv, ensure_ascii=False), now_str)
        )
        conn.commit()
        return {
            "channel_id": channel_id,
            "user_id": user_id,
            "user_name": user_name,
            "char_name": "冒險者",
            "char_class": "見習冒險者",
            "hp": 100,
            "max_hp": 100,
            "inventory": default_inv,
            "location": "冒險者酒館",
            "current_quest": "展開新冒險",
            "profile": {},
            "updated_at": now_str
        }


def update_rpg_character(
    channel_id: int,
    user_id: int,
    user_name: str,
    char_name: Optional[str] = None,
    char_class: Optional[str] = None,
    hp_delta: int = 0,
    max_hp: Optional[int] = None,
    set_hp: Optional[int] = None,
    add_items: Optional[list[str]] = None,
    remove_items: Optional[list[str]] = None,
    set_items: Optional[list[str]] = None,
    location: Optional[str] = None,
    current_quest: Optional[str] = None,
    profile: Optional[dict] = None
) -> dict:
    """更新玩家角色的血量、背包物品、地點、任務與詳細角色設定"""
    char = get_or_create_rpg_character(channel_id, user_id, user_name)
    now_str = datetime.now(TZ_TAIPEI).strftime("%Y-%m-%d %H:%M:%S")

    # 更新名稱與職業
    if char_name:
        char["char_name"] = char_name.strip()
    if char_class:
        char["char_class"] = char_class.strip()

    # 更新血量上限與當前血量
    if max_hp is not None and max_hp > 0:
        char["max_hp"] = max_hp
    if set_hp is not None:
        char["hp"] = max(0, min(char["max_hp"], set_hp))
    elif hp_delta != 0:
        char["hp"] = max(0, min(char["max_hp"], char["hp"] + hp_delta))

    # 更新背包 (去重、加減或整批覆寫)
    if set_items is not None:
        char["inventory"] = list(set_items)
    else:
        inv = list(char["inventory"])
        if add_items:
            for it in add_items:
                it_clean = it.strip()
                if it_clean and it_clean not in inv:
                    inv.append(it_clean)

        if remove_items:
            for rit in remove_items:
                rit_clean = rit.strip()
                # 支援部分匹配刪除
                matched = [x for x in inv if rit_clean in x]
                for m in matched:
                    inv.remove(m)
        char["inventory"] = inv

    # 更新地點與任務
    if location:
        char["location"] = location.strip()
    if current_quest:
        char["current_quest"] = current_quest.strip()

    # 更新詳細人設/屬性卡
    if profile is not None:
        if not isinstance(char.get("profile"), dict):
            char["profile"] = {}
        char["profile"].update(profile)

    with get_conn() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """UPDATE rpg_characters 
               SET char_name = ?, char_class = ?, hp = ?, max_hp = ?, inventory = ?, location = ?, current_quest = ?, profile_json = ?, updated_at = ?
               WHERE channel_id = ? AND user_id = ?""",
            (
                char["char_name"],
                char["char_class"],
                char["hp"],
                char["max_hp"],
                json.dumps(char["inventory"], ensure_ascii=False),
                char["location"],
                char["current_quest"],
                json.dumps(char.get("profile", {}), ensure_ascii=False),
                now_str,
                channel_id,
                user_id
            )
        )
        conn.commit()

    return char


def reset_rpg_character(channel_id: int, user_id: int):
    """重置冒險角色狀態並清空當前頻道旅程手記，回到初始開局"""
    with get_conn() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM rpg_characters WHERE channel_id = ? AND user_id = ?", (channel_id, user_id))
        conn.commit()
    clear_journey_notes(channel_id)


def get_all_characters_in_channel(channel_id: int) -> list[dict]:
    """取得指定頻道中所有玩家的角色狀態"""
    init_rpg_tables()
    with get_conn() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM rpg_characters WHERE channel_id = ? ORDER BY id ASC", (channel_id,))
        chars = []
        for row in cursor.fetchall():
            c = dict(row)
            try:
                c["inventory"] = json.loads(c["inventory"])
            except Exception:
                c["inventory"] = []
            try:
                c["profile"] = json.loads(c.get("profile_json") or "{}")
            except Exception:
                c["profile"] = {}
            chars.append(c)
        return chars


def format_char_rpg_summary(c: dict) -> str:
    """格式化單個角色的詳細屬性、專長、防禦與人設摘要（自適應修仙、賽博龐克、CoC、D&D 或任意自由設定）"""
    prof = c.get("profile") or {}
    race = prof.get("race", "")
    align = prof.get("alignment", "")
    ac = prof.get("ac")
    stats = prof.get("stats") or {}
    skills = prof.get("skills") or []
    backstory = prof.get("backstory", "")
    custom_traits = prof.get("custom_traits") or {}

    lines = []
    race_align_parts = []
    if race:
        race_align_parts.append(race)
    if align:
        race_align_parts.append(align)
    ra_str = f"（{' | '.join(race_align_parts)}）" if race_align_parts else ""

    lines.append(f"👤 角色：{c['char_name']}（身分/定位：{c['char_class']}）{ra_str}")
    
    # 戰鬥/生命/防禦
    ac_part = f" | 🛡️ 防禦等級 AC：{ac}" if ac is not None else ""
    lines.append(f"❤️ 狀態/體力值：{c['hp']}/{c['max_hp']}{ac_part}")

    # 自定義世界觀特質（靈根、宗門、義體、SAN值、超能力等）
    if custom_traits:
        ct_items = [f"{k}: {v}" for k, v in custom_traits.items() if k not in ("武器", "防具", "道具", "裝備", "隨身裝備", "隨身物品")]
        if ct_items:
            lines.append(f"🔮 世界觀專屬特質/設定：{' | '.join(ct_items)}")

    # D&D 核心屬性（僅在有定義時顯示）
    if stats:
        st_parts = [f"{k} {v}" for k, v in stats.items()]
        lines.append(f"📊 核心能力屬性：{' | '.join(st_parts)}")

    # 技能專長/功法/異能
    if skills:
        lines.append(f"🎭 熟練專長/技能/法術：{'、'.join(skills)}")

    inv_str = "、".join(c["inventory"]) if c["inventory"] else "（無任何持有物）"
    lines.append(f"🎒 隨身裝備/法寶/持有物：{inv_str}")
    lines.append(f"📍 當前地點：{c['location']} | 🎯 當前目標：{c['current_quest']}")

    if backstory:
        lines.append(f"📜 角色背景與人設：{backstory}")
        lines.append(f"👉 人設指引：AI 主持人請務必將此世界觀與人設融入對話描寫，尊重其特色、專長與性格！")

    return "\n".join(lines)


def format_rpg_context_for_prompt(channel_id: int, user_id: int, user_name: str) -> str:
    """產生用於注入 System Prompt 的權威遊戲狀態（支援單人或多人組隊冒險）與當前旅程手記"""
    current_char = get_or_create_rpg_character(channel_id, user_id, user_name)
    all_chars = get_all_characters_in_channel(channel_id)

    parts = []
    if len(all_chars) <= 1:
        # 單人模式
        char_summary = format_char_rpg_summary(current_char)
        parts.append(
            f"【本機權威 RPG 遊戲狀態（由本機系統維護，AI 描述時請嚴格以此為準，不得憑空捏造不存在之物品）】\n"
            f"👤 玩家：{current_char['user_name']}\n"
            f"{char_summary}\n"
            f"👉 敘事原則（極度重要）：嚴禁重複、總結或換句話說複述玩家剛剛說過的話或動作！玩家已經清楚自己的行動，請直接推進劇情，描寫世界環境變化、NPC 即時反應與動作帶來的直接後果！"
        )
    else:
        # 多人組隊合作模式 (Party Cooperative TRPG)
        party_lines = [
            f"【本機權威 RPG 冒險小隊狀態（當前隊伍共有 {len(all_chars)} 位冒險者共同組隊）】\n"
            f"（AI 扮演地下城主持者 DM，請描寫兩人/多人的默契配合、相互掩護或技能組合，傷害與掉落物可分配給不同隊友）：\n"
            f"👉 敘事原則（極度重要）：嚴禁重複、總結或換句話說複述玩家剛剛說過的話或動作！直接推進劇情，描寫世界環境變化、NPC 即時反應與新突發狀況！\n"
            f"👉 本輪發言行動者：【{current_char['user_name']}】（角色：{current_char['char_name']}）\n\n"
            f"👥 冒險隊伍成員名冊："
        ]
        for idx, c in enumerate(all_chars, 1):
            party_lines.append(
                f"{idx}. 玩家：{c['user_name']}\n{format_char_rpg_summary(c)}"
            )
        parts.append("\n\n".join(party_lines))

    notes_prompt = format_journey_notes_for_prompt(channel_id)
    if notes_prompt:
        parts.append(notes_prompt)

    return "\n\n".join(parts)


# -------------------------------------------------------------
# 2. 擲骰與判定檢定 (TRPG Dice Roll & DC Checks)
# -------------------------------------------------------------
def roll_dice(dice_str: str = "1d20") -> tuple[int, list[int], str]:
    """
    通用擲骰子引擎：支援例如 '1d20', '2d6', '1d100', '1d20+3'
    回傳：(總和, [各顆骰子點數], 格式化字串)
    """
    dice_str = dice_str.strip().lower()
    m = re.match(r"^(\d+)?d(\d+)([\+\-]\d+)?$", dice_str)
    if not m:
        # 預設 1d20
        count, sides, modifier = 1, 20, 0
    else:
        count = int(m.group(1)) if m.group(1) else 1
        sides = int(m.group(2))
        modifier = int(m.group(3)) if m.group(3) else 0

    count = max(1, min(10, count))  # 限制最多一次 10 顆
    sides = max(2, min(1000, sides))

    rolls = [random.randint(1, sides) for _ in range(count)]
    total = sum(rolls) + modifier

    mod_str = f"{modifier:+d}" if modifier != 0 else ""
    if count == 1:
        roll_desc = f"{rolls[0]}{mod_str} = {total}" if modifier != 0 else f"{rolls[0]}"
    else:
        roll_desc = f"{rolls}{mod_str} = {total}"

    return total, rolls, roll_desc


def perform_action_check(action_name: str, difficulty: str = "普通") -> tuple[int, int, str, str]:
    """
    進行難度檢定 (DC Check)
    難度分級：簡單(DC 8)、普通(DC 12)、困難(DC 15)、極難(DC 18)
    回傳：(擲骰點數, DC, 結果等級, 提示描述)
    """
    diff_map = {
        "簡單": 8, "easy": 8,
        "普通": 12, "normal": 12,
        "困難": 15, "hard": 15,
        "極難": 18, "extreme": 18
    }
    dc = diff_map.get(difficulty.lower(), 12)
    roll, _, roll_desc = roll_dice("1d20")

    if roll == 20:
        outcome = "大成功 (Critical Success)"
        guidance = "擲出 20 滿分大成功！請描寫極度幸運、超乎預期的華麗神操作或意想不到的驚喜收穫！"
    elif roll == 1:
        outcome = "大失敗 (Critical Fumble)"
        guidance = "擲出 1 點大失敗！請描寫嚴重失手、引來意外危險或尷尬搞笑的突發危機！"
    elif roll >= dc:
        outcome = "成功 (Success)"
        guidance = f"檢定成功 ({roll} >= DC {dc})！請生動描寫順利完成「{action_name}」的帥氣過程。"
    else:
        outcome = "失敗 (Failure)"
        guidance = f"檢定失敗 ({roll} < DC {dc})！請描寫未能成功「{action_name}」，並產生緊張的波折或需另尋出路。"

    return roll, dc, outcome, guidance


# -------------------------------------------------------------
# 3. 動態設定集 (Lorebook / World Info)
# -------------------------------------------------------------
def add_lorebook_entry(keywords: str, content: str) -> int:
    """新增一筆世界觀設定（可多個關鍵字，以逗號或空格分隔）"""
    init_rpg_tables()
    now_str = datetime.now(TZ_TAIPEI).strftime("%Y-%m-%d %H:%M:%S")
    with get_conn() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO lorebook (keywords, content, created_at) VALUES (?, ?, ?)",
            (keywords.strip(), content.strip(), now_str)
        )
        conn.commit()
        return cursor.lastrowid


def get_all_lorebook_entries() -> list[dict]:
    init_rpg_tables()
    with get_conn() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id, keywords, content, created_at FROM lorebook ORDER BY id ASC")
        return [dict(row) for row in cursor.fetchall()]


def delete_lorebook_entry(lore_id: int) -> bool:
    init_rpg_tables()
    with get_conn() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM lorebook WHERE id = ?", (lore_id,))
        conn.commit()
        return cursor.rowcount > 0


def clear_lorebook() -> int:
    """清空世界觀設定集，回傳刪除筆數"""
    init_rpg_tables()
    with get_conn() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM lorebook")
        count = cursor.rowcount
        conn.commit()
        return count


def batch_add_lorebook_entries(entries: list[tuple[str, str]]) -> int:
    """批次新增多筆世界觀設定，回傳成功新增數量"""
    init_rpg_tables()
    now_str = datetime.now(TZ_TAIPEI).strftime("%Y-%m-%d %H:%M:%S")
    count = 0
    with get_conn() as conn:
        cursor = conn.cursor()
        for kw, cnt in entries:
            kw_clean = kw.strip()
            cnt_clean = cnt.strip()
            if kw_clean and cnt_clean:
                cursor.execute(
                    "INSERT INTO lorebook (keywords, content, created_at) VALUES (?, ?, ?)",
                    (kw_clean, cnt_clean, now_str)
                )
                count += 1
        conn.commit()
    return count


def parse_lore_text_blocks(raw_text: str) -> list[tuple[str, str]]:
    """
    智能解析文字區塊中的多筆世界觀設定。
    支援多種常見格式：
    1. 【名詞/關鍵字】設定內容...
    2. 關鍵字: 設定內容 或 關鍵字：設定內容
    3. 1. 關鍵字 - 設定內容 或 1. 關鍵字：設定內容
    4. 關鍵字 | 設定內容
    """
    entries = []
    lines = raw_text.strip().split("\n")
    cur_kw = None
    cur_cnt = []

    for line in lines:
        sline = line.strip()
        if not sline:
            continue

        # 格式 1: 【關鍵字】內容
        m1 = re.match(r"^【(.*?)】\s*(.*)$", sline)
        if m1:
            if cur_kw and cur_cnt:
                entries.append((cur_kw, "\n".join(cur_cnt).strip()))
            cur_kw = m1.group(1).strip()
            cnt = m1.group(2).strip()
            cur_cnt = [cnt] if cnt else []
            continue

        # 格式 2: 數字標號 + 關鍵字: 內容 或 關鍵字 - 內容
        m2 = re.match(r"^(?:\d+[\.\、\)]\s*)?([^\:：\|—\-]{1,30})[\:：\|—\-]\s*(.+)$", sline)
        if m2 and not sline.startswith("http"):
            if cur_kw and cur_cnt:
                entries.append((cur_kw, "\n".join(cur_cnt).strip()))
            cur_kw = m2.group(1).strip()
            cur_cnt = [m2.group(2).strip()]
            continue

        # 若上一行有關鍵字，作為內容接續
        if cur_kw:
            cur_cnt.append(sline)

    if cur_kw and cur_cnt:
        entries.append((cur_kw, "\n".join(cur_cnt).strip()))

    return entries


def match_lorebook_for_text(text: str) -> list[str]:
    """掃描文字內容，若符合關鍵字則提取對應的世界觀條目（節省 Token，按需注入）"""
    entries = get_all_lorebook_entries()
    if not entries:
        return []

    matched = []
    text_lower = text.lower()
    for entry in entries:
        # 分割關鍵字
        kw_list = [k.strip().lower() for k in re.split(r'[,，、\s]+', entry["keywords"]) if k.strip()]
        for kw in kw_list:
            if kw and kw in text_lower:
                matched.append(f"【世界觀設定：{entry['keywords']}】{entry['content']}")
                break
    return matched[:5]  # 最多注入 5 條相關設定，避免爆量


# -------------------------------------------------------------
# 4. 當前旅程獨立手記與關鍵線索 (Journey Journal / Extra Memory)
# -------------------------------------------------------------
def add_journey_note(channel_id: int, user_id: int, user_name: str, content: str, note_type: str = "clue") -> int:
    """新增一筆當前頻道/討論串專屬的旅程筆記或故事線索"""
    init_rpg_tables()
    now_str = datetime.now(TZ_TAIPEI).strftime("%Y-%m-%d %H:%M:%S")
    with get_conn() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """INSERT INTO rpg_journey_notes (channel_id, user_id, user_name, note_type, content, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (channel_id, user_id, user_name, note_type, content.strip(), now_str)
        )
        conn.commit()
        return cursor.lastrowid


def get_journey_notes(channel_id: int) -> list[dict]:
    """取得指定頻道/討論串的所有旅程手記與線索"""
    init_rpg_tables()
    with get_conn() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM rpg_journey_notes WHERE channel_id = ? ORDER BY id ASC",
            (channel_id,)
        )
        return [dict(row) for row in cursor.fetchall()]


def delete_journey_note(channel_id: int, note_id: int) -> bool:
    """刪除指定頻道中的某一條手記"""
    init_rpg_tables()
    with get_conn() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM rpg_journey_notes WHERE channel_id = ? AND id = ?", (channel_id, note_id))
        conn.commit()
        return cursor.rowcount > 0


def clear_journey_notes(channel_id: int):
    """清空指定頻道的所有旅程手記"""
    init_rpg_tables()
    with get_conn() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM rpg_journey_notes WHERE channel_id = ?", (channel_id,))
        conn.commit()


def format_journey_notes_for_prompt(channel_id: int) -> str:
    """將當前頻道的旅程手記格式化為 Prompt 注入內容"""
    notes = get_journey_notes(channel_id)
    if not notes:
        return ""
    lines = [
        "【當前冒險旅程獨立手記與關鍵線索 (Journey Journal)】\n"
        "（以下為本趟冒險中已確立的重要情報與線索，AI 敘事時請務必融入並嚴格遵守這些事實）："
    ]
    for n in notes:
        tag = "重要線索" if n["note_type"] == "clue" else n["note_type"]
        lines.append(f"- #{n['id']} [{tag}] {n['content']} (由 {n['user_name']} 於 {n['created_at'][5:16]} 記下)")
    return "\n".join(lines)


# -------------------------------------------------------------
# 5. 冒險進度存檔與讀檔管理 (Save & Load Snapshot Engine)
# -------------------------------------------------------------
def save_game(channel_id: int, user_id: int, user_name: str, save_name: str) -> dict:
    """
    保存玩家在當前頻道的冒險進度（角色狀態機 + 隊伍全員快照 + 當前旅程所有獨立手記筆記快照）
    """
    init_rpg_tables()
    char = get_or_create_rpg_character(channel_id, user_id, user_name)
    party = get_all_characters_in_channel(channel_id)
    party_json = json.dumps(party, ensure_ascii=False)
    notes = get_journey_notes(channel_id)
    notes_json = json.dumps(notes, ensure_ascii=False)
    now_str = datetime.now(TZ_TAIPEI).strftime("%Y-%m-%d %H:%M:%S")

    with get_conn() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """INSERT INTO rpg_saves
               (user_id, user_name, save_name, char_name, char_class, hp, max_hp, inventory, location, current_quest, journey_notes_json, party_json, profile_json, saved_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(user_id, save_name) DO UPDATE SET
               user_name=excluded.user_name,
               char_name=excluded.char_name,
               char_class=excluded.char_class,
               hp=excluded.hp,
               max_hp=excluded.max_hp,
               inventory=excluded.inventory,
               location=excluded.location,
               current_quest=excluded.current_quest,
               journey_notes_json=excluded.journey_notes_json,
               party_json=excluded.party_json,
               profile_json=excluded.profile_json,
               saved_at=excluded.saved_at
            """,
            (
                user_id,
                user_name,
                save_name.strip(),
                char["char_name"],
                char["char_class"],
                char["hp"],
                char["max_hp"],
                json.dumps(char["inventory"], ensure_ascii=False),
                char["location"],
                char["current_quest"],
                notes_json,
                party_json,
                json.dumps(char.get("profile", {}), ensure_ascii=False),
                now_str
            )
        )
        conn.commit()

    return {
        "save_name": save_name.strip(),
        "char_name": char["char_name"],
        "char_class": char["char_class"],
        "hp": char["hp"],
        "max_hp": char["max_hp"],
        "inventory": char["inventory"],
        "party_count": len(party),
        "location": char["location"],
        "current_quest": char["current_quest"],
        "notes_count": len(notes),
        "saved_at": now_str
    }


def load_game(channel_id: int, user_id: int, user_name: str, save_name: str) -> Optional[dict]:
    """
    載入存檔進度至當前頻道/討論串（還原小隊全員角色狀態機與所有獨立手記筆記）
    """
    init_rpg_tables()
    with get_conn() as conn:
        cursor = conn.cursor()
        # 優先搜尋該使用者的存檔
        cursor.execute(
            "SELECT * FROM rpg_saves WHERE user_id = ? AND save_name = ?",
            (user_id, save_name.strip())
        )
        row = cursor.fetchone()
        if not row:
            # 支援同伺服器/伴侶共享存檔（搜尋同名存檔）
            cursor.execute(
                "SELECT * FROM rpg_saves WHERE save_name = ? ORDER BY saved_at DESC LIMIT 1",
                (save_name.strip(),)
            )
            row = cursor.fetchone()

        if not row:
            return None

        save_data = dict(row)
        now_str = datetime.now(TZ_TAIPEI).strftime("%Y-%m-%d %H:%M:%S")

        # 1. 還原/覆寫 rpg_characters (若有 party_json 則同步還原隊伍全員，否則還原單人)
        saved_party = []
        try:
            saved_party = json.loads(save_data.get("party_json", "[]"))
        except Exception:
            saved_party = []

        if saved_party:
            for p in saved_party:
                pinv = json.dumps(p["inventory"], ensure_ascii=False) if isinstance(p["inventory"], list) else p["inventory"]
                prof_json = json.dumps(p.get("profile", {}), ensure_ascii=False) if isinstance(p.get("profile"), dict) else p.get("profile_json", "{}")
                cursor.execute(
                    """INSERT INTO rpg_characters 
                       (channel_id, user_id, user_name, char_name, char_class, hp, max_hp, inventory, location, current_quest, profile_json, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(channel_id, user_id) DO UPDATE SET
                       char_name=excluded.char_name,
                       char_class=excluded.char_class,
                       hp=excluded.hp,
                       max_hp=excluded.max_hp,
                       inventory=excluded.inventory,
                       location=excluded.location,
                       current_quest=excluded.current_quest,
                       profile_json=excluded.profile_json,
                       updated_at=excluded.updated_at
                    """,
                    (
                        channel_id,
                        p["user_id"],
                        p["user_name"],
                        p["char_name"],
                        p["char_class"],
                        p["hp"],
                        p["max_hp"],
                        pinv,
                        p["location"],
                        p["current_quest"],
                        prof_json,
                        now_str
                    )
                )
        else:
            prof_json = save_data.get("profile_json", "{}")
            cursor.execute(
                """INSERT INTO rpg_characters 
                   (channel_id, user_id, user_name, char_name, char_class, hp, max_hp, inventory, location, current_quest, profile_json, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(channel_id, user_id) DO UPDATE SET
                   char_name=excluded.char_name,
                   char_class=excluded.char_class,
                   hp=excluded.hp,
                   max_hp=excluded.max_hp,
                   inventory=excluded.inventory,
                   location=excluded.location,
                   current_quest=excluded.current_quest,
                   profile_json=excluded.profile_json,
                   updated_at=excluded.updated_at
                """,
                (
                    channel_id,
                    user_id,
                    user_name,
                    save_data["char_name"],
                    save_data["char_class"],
                    save_data["hp"],
                    save_data["max_hp"],
                    save_data["inventory"],
                    save_data["location"],
                    save_data["current_quest"],
                    prof_json,
                    now_str
                )
            )

        # 2. 清除該頻道當前筆記並還原存檔中的筆記
        cursor.execute("DELETE FROM rpg_journey_notes WHERE channel_id = ?", (channel_id,))
        try:
            saved_notes = json.loads(save_data.get("journey_notes_json", "[]"))
        except Exception:
            saved_notes = []

        for n in saved_notes:
            cursor.execute(
                """INSERT INTO rpg_journey_notes (channel_id, user_id, user_name, note_type, content, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    channel_id,
                    user_id,
                    n.get("user_name", user_name),
                    n.get("note_type", "clue"),
                    n.get("content", ""),
                    n.get("created_at", now_str)
                )
            )

        conn.commit()

        try:
            inv = json.loads(save_data["inventory"])
        except Exception:
            inv = []

        party_count = len(saved_party) if saved_party else 1

        return {
            "save_name": save_data["save_name"],
            "saved_by": save_data.get("user_name", ""),
            "char_name": save_data["char_name"],
            "char_class": save_data["char_class"],
            "hp": save_data["hp"],
            "max_hp": save_data["max_hp"],
            "inventory": inv,
            "party_count": party_count,
            "location": save_data["location"],
            "current_quest": save_data["current_quest"],
            "notes_count": len(saved_notes),
            "saved_at": save_data["saved_at"]
        }


def list_saves(user_id: Optional[int] = None) -> list[dict]:
    """列出冒險存檔清單"""
    init_rpg_tables()
    with get_conn() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM rpg_saves ORDER BY saved_at DESC")
        rows = cursor.fetchall()
        saves = []
        for r in rows:
            d = dict(r)
            try:
                d["inventory"] = json.loads(d["inventory"])
            except Exception:
                d["inventory"] = []
            try:
                d["journey_notes"] = json.loads(d.get("journey_notes_json", "[]"))
            except Exception:
                d["journey_notes"] = []
            saves.append(d)
        return saves


def delete_save(save_name: str, user_id: Optional[int] = None) -> bool:
    """刪除指定存檔（社群模式下僅允許存檔建立者本人刪除，情侶模式下可共同管理）"""
    init_rpg_tables()
    is_jp_mode = os.getenv("ENABLE_JAPANESE_MODE", "true").strip().lower() not in ("false", "0", "no")
    with get_conn() as conn:
        cursor = conn.cursor()
        if user_id:
            cursor.execute("DELETE FROM rpg_saves WHERE user_id = ? AND save_name = ?", (user_id, save_name.strip()))
            if cursor.rowcount > 0:
                conn.commit()
                return True
            if not is_jp_mode:
                # 社群模式下禁止刪除他人建立的存檔
                return False
        cursor.execute("DELETE FROM rpg_saves WHERE save_name = ?", (save_name.strip(),))
        conn.commit()
        return cursor.rowcount > 0


# -------------------------------------------------------------
# 6. 提供給 Gemini 的 RPG 專屬 Function Calling 工具
# -------------------------------------------------------------
def rpg_roll_check(action_description: str, difficulty: str = "普通") -> str:
    """
    在文字冒險中進行關鍵動作檢定（如撬鎖、攀爬、說服NPC、發動攻擊、躲避陷阱）。
    由本機真隨機 D20 骰子判定勝負，確保遊戲具備真正的緊張感與隨機性。
    參數:
      action_description: 要嘗試進行的動作（例如：「撬開牢門」、「試圖說服老闆打折」、「閃避哥布林的飛斧」）。
      difficulty: 難度等級，可填：「簡單」、「普通」、「困難」、「極難」。預設為「普通」。
    """
    roll, dc, outcome, guidance = perform_action_check(action_description, difficulty)
    return (
        f"🎲【系統擲骰檢定結算】\n"
        f"動作：{action_description}（難度：{difficulty} / DC {dc}）\n"
        f"骰出點數：D20 = {roll} ➔ 結果：【{outcome}】\n"
        f"敘事指引：{guidance}"
    )


def rpg_update_state(
    target_player: Optional[str] = None,
    char_name: Optional[str] = None,
    char_class: Optional[str] = None,
    hp_change: int = 0,
    add_items: Optional[list[str]] = None,
    remove_items: Optional[list[str]] = None,
    new_location: Optional[str] = None,
    new_quest: Optional[str] = None
) -> str:
    """
    更新玩家或隊伍成員的冒險狀態（獲得或失去物品、扣減或恢復血量、轉移地點、更新任務目標）。
    這會直接永久寫入本機資料庫，使物品和數值絕對不會在未來的對話中憑空遺忘或消失。
    參數:
      target_player: 要更新的目標角色名或玩家名稱（例如：「莉莉亞」或「妖貓」）。若更新全隊地點或目標，可填「全隊」或「隊伍」。若留空，預設為當前發言玩家。
      char_name: 角色名稱（若無更改可留空）
      char_class: 職業稱號（若無更改可留空）
      hp_change: 血量增減（如受傷填 -15，喝藥水恢復填 +25）
      add_items: 獲得的道具列表（例如：["古代金幣x10", "地牢鑰匙"]）
      remove_items: 消耗或遺失的道具列表（例如：["生鏽鐵鑰匙", "火把x1"]）
      new_location: 抵達的新地點（例如：「古堡頂層露臺」）
      new_quest: 當前新目標（例如：「打敗石像鬼衛兵」）
    """
    msg = current_discord_msg.get()
    channel_id = msg.channel.id if msg else 0
    sender_id = msg.author.id if msg else 0
    sender_name = msg.author.display_name if msg else "玩家"

    all_chars = get_all_characters_in_channel(channel_id)
    target_id = sender_id
    target_name = sender_name

    # 判斷是否指定特定隊友
    if target_player and all_chars:
        tp_clean = target_player.strip().lower()
        if tp_clean not in ("全隊", "隊伍", "team", "party", "everyone", "all"):
            for c in all_chars:
                if tp_clean in c["char_name"].lower() or tp_clean in c["user_name"].lower():
                    target_id = c["user_id"]
                    target_name = c["user_name"]
                    break

    # 若為全隊更新地點或任務目標，同步更新全隊所有人
    if target_player and target_player.strip().lower() in ("全隊", "隊伍", "team", "party", "everyone", "all"):
        for c in all_chars:
            update_rpg_character(
                channel_id=channel_id,
                user_id=c["user_id"],
                user_name=c["user_name"],
                location=new_location,
                current_quest=new_quest
            )

    updated = update_rpg_character(
        channel_id=channel_id,
        user_id=target_id,
        user_name=target_name,
        char_name=char_name,
        char_class=char_class,
        hp_delta=hp_change,
        add_items=add_items,
        remove_items=remove_items,
        location=new_location,
        current_quest=new_quest
    )

    inv_display = "、".join(updated["inventory"]) if updated["inventory"] else "（背包空）"
    return (
        f"✅ 已同步更新【{updated['char_name']} ({updated['user_name']})】遊戲狀態：\n"
        f"❤️ 血量：{updated['hp']}/{updated['max_hp']} | 🎒 背包：{inv_display}\n"
        f"📍 地點：{updated['location']} | 🎯 目標：{updated['current_quest']}\n"
        f"⚡【權威狀態覆蓋提示】：本回合狀態已更新！請直接以此最新數值與物品覆蓋 System Prompt 中的舊狀態快照進行敘事，切勿引用舊數值！"
    )


def rpg_record_lore(keyword: str, lore_content: str) -> str:
    """
    在故事進行中記錄一筆世界觀知識或 NPC 關鍵情報到世界設定集（Lorebook）中。
    參數:
      keyword: 關鍵字或 NPC 姓名（例如：「聖劍艾克斯」、「黑魔法師雷文」）。
      lore_content: 該名詞或人物的設定或弱點。
    """
    lid = add_lorebook_entry(keyword, lore_content)
    return f"📖 已將【{keyword}】記錄至世界觀設定集 #{lid}。"


def rpg_record_story_clue(clue: str, clue_type: str = "clue") -> str:
    """
    在文字冒險中，當玩家發現重要劇情線索、解謎關鍵、NPC秘密或發生重大轉折時，自動記錄至該旅程專屬手記 (Journey Journal)。
    這會永遠保存在此冒險中，不會因為對話歷史滾動而被遺忘。
    參數:
      clue: 要記錄的線索或情報內容（例如：「在石棺底部發現了刻有王室家徽的古銅徽章」、「守衛老伯最害怕老鼠」）。
      clue_type: 類型，可選：'clue'(線索)、'event'(重要事件)、'memo'(備忘)。預設為 'clue'。
    """
    msg = current_discord_msg.get()
    channel_id = msg.channel.id if msg else 0
    user_id = msg.author.id if msg else 0
    user_name = msg.author.display_name if msg else "冒險者"

    nid = add_journey_note(channel_id, user_id, user_name, clue, note_type=clue_type)
    return f"📝 已將劇情線索 #{nid} 記錄至當前冒險手記：「{clue}」"


def rpg_batch_record_lore(entries_json: str) -> str:
    """
    當玩家提供一整篇或多筆世界觀設定、地理環境、勢力陣營、重要人物或魔法物品時，批次解析並一次性存入世界觀設定集 (Lorebook)。
    參數:
      entries_json: JSON 字串，格式為 [{"keywords": "關鍵字（可多個，以逗號分隔）", "content": "詳細設定說明"}, ...]
    """
    try:
        items = json.loads(entries_json)
        if not isinstance(items, list):
            return "❌ 格式錯誤，entries_json 必須為 JSON 陣列。"
        pairs = []
        for it in items:
            kw = it.get("keywords") or it.get("keyword") or ""
            cnt = it.get("content") or it.get("description") or ""
            if kw and cnt:
                pairs.append((kw, cnt))
        count = batch_add_lorebook_entries(pairs)
        return f"📖 成功批次將 {count} 筆世界觀設定存入 Lorebook 資料庫！"
    except Exception as e:
        return f"❌ 批次寫入世界觀失敗：{e}"


def extract_sheet_field(raw_text: str, patterns: list[str]) -> str:
    for pat in patterns:
        m = re.search(pat, raw_text, re.IGNORECASE)
        if m and m.group(1).strip():
            val = m.group(1).strip().strip("*-` \t")
            if val:
                return val
    return ""


def parse_character_sheet(raw_text: str, default_name: str = "冒險者") -> dict:
    """
    通用自適應角色卡解析器（Universal Multi-Genre Character Parser）
    支援 D&D、克蘇魯 (CoC)、修仙仙俠、賽博龐克、都市異能、現代校園或任意自定義自由劇本。
    """
    sheet = {
        "char_name": "",
        "race": "",
        "char_class": "冒險者",
        "alignment": "",
        "background": "",
        "backstory": "",
        "stats": {},
        "hp": 100,
        "max_hp": 100,
        "ac": None,
        "skills": [],
        "inventory": [],
        "custom_traits": {}
    }

    # 1. 角色名稱（支援：角色名稱、Name、姓名、道號、代號、稱號、名字）
    m_name = re.search(r"\*\*(?:角色名稱|Name|姓名|道號|代號|稱號|名字)[^\*]*\*\*[ \t\:\：]+([^\r\n]+)", raw_text, re.IGNORECASE)
    if not m_name:
        m_name = re.search(r"^(?:[\-\*][ \t]*)?(?:角色名稱|姓名|道號|代號|稱號)[ \t\:\：]+([^\r\n]+)", raw_text, re.MULTILINE)
    if m_name and m_name.group(1).strip():
        val = m_name.group(1).strip().strip("*-` \t")
        if val and not val.startswith("**") and not val.startswith("-"):
            sheet["char_name"] = val

    # 2. 職業 / 身分 / 門派 / 宗門 / 境界 / 定位
    class_val = extract_sheet_field(raw_text, [
        r"(?:[\-\*][ \t]*)?\*\*(?:職業|Class|身分|身份|宗門|門派|修為境界|修為|境界|定位)[^\*]*\*\*[ \t\:\：]+([^\r\n]+)",
        r"(?:職業|Class|身分|身份|宗門|門派|修為境界|修為|境界|定位)[ \t\:\：]+([^\r\n]+)"
    ])
    if class_val:
        sheet["char_class"] = class_val

    # 3. 種族 / 靈根 / 派系 / 陣營
    race_val = extract_sheet_field(raw_text, [
        r"(?:[\-\*][ \t]*)?\*\*(?:種族|Race|靈根)[^\*]*\*\*[ \t\:\：]+([^\r\n]+)",
        r"(?:種族|Race|靈根)[ \t\:\：]+([^\r\n]+)"
    ])
    if race_val:
        sheet["race"] = race_val

    align_val = extract_sheet_field(raw_text, [
        r"(?:[\-\*][ \t]*)?\*\*(?:陣營|Alignment)[^\*]*\*\*[ \t\:\：]+([^\r\n]+)",
        r"(?:陣營|Alignment)[ \t\:\：]+([^\r\n]+)"
    ])
    if align_val:
        sheet["alignment"] = align_val

    # 4. 背景
    bg_val = extract_sheet_field(raw_text, [
        r"(?:[\-\*][ \t]*)?\*\*(?:背景|Background)[^\*]*\*\*[ \t\:\：]+([^\r\n]+)",
        r"(?:背景|Background)[ \t\:\：]+([^\r\n]+)"
    ])
    if bg_val:
        sheet["background"] = bg_val

    # 5. 背景故事 / 外觀描述 / 性格描述 / 經歷
    m_story = re.search(r"(?:[\-\*][ \t]*)?\*\*(?:背景故事|外觀描述|故事|描述|性格描述|人設|背景經歷|經歷|Backstory)[^\*]*\*\*[ \t\:\：]+([^\r\n]+(?:\r?\n(?![ \t]*[\-\*#])[^\r\n]+)*)", raw_text, re.IGNORECASE)
    if m_story and m_story.group(1).strip():
        sheet["backstory"] = m_story.group(1).strip().strip("*-` ")

    # 6. 專長技能 / 擅長 / 異能 / 功法
    m_skills = re.search(r"(?:擅長[^\n\r\?\？]*[？\?\:\：]|(?:\*\*|#)[ \t]*(?:專長|技能|Skills|擅長|功法|異能|特技)[^\*\n\r]*[\*\:\：]+)[ \t]*([^\r\n]+)", raw_text, re.IGNORECASE)
    if m_skills:
        raw_skills = m_skills.group(1).strip().strip("*-` ")
        skills_list = [s.strip() for s in re.split(r"[,，、\s]+", raw_skills) if s.strip()]
        if skills_list:
            sheet["skills"] = skills_list

    # 7. 初始裝備 / 隨身道具 / 武器 / 防具 / 法寶 / 義體
    inv_items = []
    equip_matches = re.findall(r"(?:[\-\*][ \t]*)?\*\*(?:武器|防具|特殊道具|道具|裝備|隨身裝備|隨身物品|攜帶物品|本命法寶|法寶|義體|義體植入)[^\*]*\*\*[ \t\:\：]+([^\r\n]+)", raw_text)
    if equip_matches:
        for em in equip_matches:
            item_clean = em.strip().strip("*-` ")
            if item_clean:
                for sub_it in re.split(r"[,，、]+", item_clean):
                    sub_clean = sub_it.strip()
                    if sub_clean and sub_clean not in inv_items:
                        inv_items.append(sub_clean)

    if not inv_items:
        m_sec5 = re.search(r"(?:\*\*|#)\s*5[^\r\n]*\r?\n([\s\S]*?)(?=\r?\n(?:\*\*|#)|\Z)", raw_text)
        if m_sec5:
            lines = m_sec5.group(1).splitlines()
            for line in lines:
                line = line.strip().lstrip("-*•0123456789. \t")
                if line and not line.startswith("#"):
                    inv_items.append(line)

    if inv_items:
        sheet["inventory"] = inv_items
    else:
        sheet["inventory"] = ["隨身物品"]

    # 8. 掃描自定義世界觀專屬特質 (Custom Traits)
    for line in raw_text.splitlines():
        line = line.strip()
        m_kv = re.match(r"^(?:[\-\*][ \t]*)?\*\*([^\*\:\：]+)\*\*[\s\:\：]+([^\r\n]+)$", line)
        if m_kv:
            k = m_kv.group(1).strip()
            v = m_kv.group(2).strip().strip("*-` \t")
            ignore_keys = (
                "角色名稱", "name", "姓名", "道號", "代號", "稱號",
                "背景故事", "外觀描述", "性格描述", "背景經歷", "backstory",
                "種族", "race", "職業", "class", "陣營", "alignment", "背景", "background",
                "生命值", "hp", "防禦等級", "ac", "武器", "防具", "道具", "裝備", "特殊道具",
                "力量", "敏捷", "體質", "智力", "感知", "魅力", "str", "dex", "con", "int", "wis", "cha"
            )
            if not any(ik in k.lower() for ik in ignore_keys) and v:
                sheet["custom_traits"][k] = v

    # 9. 六大核心屬性 (若有填寫 D&D 屬性才解析)
    stat_patterns = {
        "STR": [r"\*\*(?:力量|STR)[^\*]*\*\*[ \t\:\：]+(\d+)", r"(?:力量|STR)[ \t\:\：]+(\d+)"],
        "DEX": [r"\*\*(?:敏捷|DEX)[^\*]*\*\*[ \t\:\：]+(\d+)", r"(?:敏捷|DEX)[ \t\:\：]+(\d+)"],
        "CON": [r"\*\*(?:體質|CON)[^\*]*\*\*[ \t\:\：]+(\d+)", r"(?:體質|CON)[ \t\:\：]+(\d+)"],
        "INT": [r"\*\*(?:智力|INT)[^\*]*\*\*[ \t\:\：]+(\d+)", r"(?:智力|INT)[ \t\:\：]+(\d+)"],
        "WIS": [r"\*\*(?:感知|WIS)[^\*]*\*\*[ \t\:\：]+(\d+)", r"(?:感知|WIS)[ \t\:\：]+(\d+)"],
        "CHA": [r"\*\*(?:魅力|CHA)[^\*]*\*\*[ \t\:\：]+(\d+)", r"(?:魅力|CHA)[ \t\:\：]+(\d+)"]
    }
    has_dnd_stats = False
    temp_stats = {}
    for stat_key, pats in stat_patterns.items():
        val_str = extract_sheet_field(raw_text, pats)
        if val_str and val_str.isdigit():
            temp_stats[stat_key] = int(val_str)
            has_dnd_stats = True

    if has_dnd_stats:
        sheet["stats"] = temp_stats
        dex = sheet["stats"].get("DEX", 2)
        con = sheet["stats"].get("CON", 1)
        ac_bonus = dex if dex <= 5 else (dex - 10) // 2
        sheet["ac"] = 10 + ac_bonus
        con_bonus = con if con <= 5 else (con - 10) // 2
        sheet["hp"] = max(50, 80 + (con_bonus * 10))
        sheet["max_hp"] = sheet["hp"]

    # 生命值與 AC 顯式覆寫
    hp_val = extract_sheet_field(raw_text, [r"\*\*(?:生命值|HP|理智值|SAN)[^\*]*\*\*[ \t\:\：]+(\d+)", r"(?:生命值|HP|理智值|SAN)[ \t\:\：]+(\d+)"])
    if hp_val and hp_val.isdigit():
        sheet["hp"] = int(hp_val)
        sheet["max_hp"] = int(hp_val)

    ac_val = extract_sheet_field(raw_text, [r"\*\*(?:防禦等級|AC)[^\*]*\*\*[ \t\:\：]+(\d+)", r"(?:防禦等級|AC)[ \t\:\：]+(\d+)"])
    if ac_val and ac_val.isdigit():
        sheet["ac"] = int(ac_val)

    # 10. 決定角色名稱
    if not sheet["char_name"]:
        if sheet["background"]:
            sheet["char_name"] = sheet["background"]
        else:
            sheet["char_name"] = default_name

    return sheet


def apply_character_sheet(channel_id: int, user_id: int, user_name: str, sheet_data: dict) -> dict:
    """將解析後的角色卡完整套用到該玩家的權威狀態機"""
    init_rpg_tables()
    profile = {
        "race": sheet_data.get("race", ""),
        "alignment": sheet_data.get("alignment", ""),
        "background": sheet_data.get("background", ""),
        "backstory": sheet_data.get("backstory", ""),
        "stats": sheet_data.get("stats", {}),
        "skills": sheet_data.get("skills", []),
        "ac": sheet_data.get("ac"),
        "custom_traits": sheet_data.get("custom_traits", {})
    }
    updated = update_rpg_character(
        channel_id=channel_id,
        user_id=user_id,
        user_name=user_name,
        char_name=sheet_data.get("char_name"),
        char_class=sheet_data.get("char_class"),
        set_hp=sheet_data.get("hp"),
        max_hp=sheet_data.get("max_hp"),
        set_items=sheet_data.get("inventory"),
        profile=profile
    )
    return updated


def rpg_import_character_sheet(sheet_text: str) -> str:
    """
    當玩家在對話中貼上「角色人設卡」或提供詳細的角色設定（支援修仙、賽博龐克、克蘇魯CoC、D&D、都市異能等任意世界觀）時，
    自動解析並匯入為該玩家的正式權威角色卡。這會永久儲存於本機資料庫中，使之後的所有冒險檢定、數值判定與主持引導完全遵循此設定。
    參數:
      sheet_text: 角色卡文字內容或設定描述。
    """
    msg = current_discord_msg.get()
    channel_id = msg.channel.id if msg else 0
    user_id = msg.author.id if msg else 0
    user_name = msg.author.display_name if msg else "冒險者"

    parsed = parse_character_sheet(sheet_text, default_name=user_name)
    applied = apply_character_sheet(channel_id, user_id, user_name, parsed)

    prof = applied.get("profile") or {}
    stats = prof.get("stats") or {}
    skills_str = "、".join(prof.get("skills", [])) if prof.get("skills") else "無特別指定"
    inv_str = "、".join(applied.get("inventory", [])) if applied.get("inventory") else "空"

    info_lines = [
        f"👑【角色卡匯入成功！】已為玩家【{user_name}】設定好專屬角色：",
        f"👤 角色：{applied['char_name']}（身分/定位：{applied['char_class']}）",
        f"❤️ 狀態/體力值：{applied['hp']}/{applied['max_hp']}"
    ]
    if prof.get("ac") is not None:
        info_lines[-1] += f" | 🛡️ 防禦 (AC)：{prof['ac']}"

    custom_traits = prof.get("custom_traits") or {}
    if custom_traits:
        ct_items = [f"{k}: {v}" for k, v in custom_traits.items() if k not in ("武器", "防具", "道具", "裝備", "隨身裝備", "隨身物品")]
        if ct_items:
            info_lines.append(f"🔮 專屬世界觀特質：{' | '.join(ct_items)}")

    if stats:
        stat_str = " | ".join([f"{k} {v}" for k, v in stats.items()])
        info_lines.append(f"📊 核心能力屬性：{stat_str}")

    info_lines.append(f"🎭 熟練技能/法術：{skills_str}")
    info_lines.append(f"🎒 隨身裝備/法寶：{inv_str}")
    if prof.get("backstory"):
        info_lines.append(f"📜 背景設定：{prof['backstory']}")
    info_lines.append("⚡【權威狀態覆蓋提示】：角色卡已寫入資料庫！請直接以此最新角色設定覆蓋 System Prompt 中的舊狀態，並以符合該世界觀風格的主持人口吻歡迎這位角色加入旅程！")

    return "\n".join(info_lines)


# RPG 工具清單，供註冊給 Gemini
RPG_TOOLS = [
    rpg_roll_check,
    rpg_update_state,
    rpg_record_lore,
    rpg_batch_record_lore,
    rpg_record_story_clue,
    rpg_import_character_sheet
]
