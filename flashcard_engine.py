import sqlite3
import zipfile
import tempfile
import json
import re
import os
import random
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import Optional

from assistant_db import get_conn, TZ_TAIPEI


def init_flashcard_tables():
    """初始化單字卡 (Flashcard)、牌組 (Decks) 與學習進度資料表"""
    with get_conn() as conn:
        cursor = conn.cursor()
        # 1. 牌組資料表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS flashcard_decks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL,
                description TEXT DEFAULT '',
                created_by TEXT DEFAULT 'System',
                created_at TEXT NOT NULL
            )
        """)
        # 2. 單字卡片資料表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS flashcard_cards (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                deck_id INTEGER NOT NULL,
                front TEXT NOT NULL,
                reading TEXT DEFAULT '',
                back TEXT NOT NULL,
                example_jp TEXT DEFAULT '',
                example_zh TEXT DEFAULT '',
                notes TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                FOREIGN KEY (deck_id) REFERENCES flashcard_decks(id) ON DELETE CASCADE
            )
        """)
        # 3. 個人學習進度表 (SRS / 間隔記憶)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS flashcard_progress (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                card_id INTEGER NOT NULL,
                box INTEGER DEFAULT 1,
                review_count INTEGER DEFAULT 0,
                correct_count INTEGER DEFAULT 0,
                last_reviewed TEXT,
                next_review_at TEXT,
                UNIQUE(user_id, card_id)
            )
        """)
        # 4. 使用者當前偏好牌組設定
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS flashcard_user_settings (
                user_id INTEGER PRIMARY KEY,
                active_deck_id INTEGER,
                updated_at TEXT NOT NULL
            )
        """)
        conn.commit()

    # 自動建立預設收集箱與內建基礎牌組
    _seed_default_and_starter_decks()


def _seed_default_and_starter_decks():
    """建立預設收集箱與初學者 JLPT N5 基礎生活單字庫"""
    now_str = datetime.now(TZ_TAIPEI).strftime("%Y-%m-%d %H:%M:%S")
    with get_conn() as conn:
        cursor = conn.cursor()
        
        # 1. 預設收集箱
        cursor.execute("SELECT id FROM flashcard_decks WHERE name = '⭐ 兩人的日常收藏'")
        if not cursor.fetchone():
            cursor.execute(
                "INSERT INTO flashcard_decks (name, description, created_by, created_at) VALUES (?, ?, ?, ?)",
                ("⭐ 兩人的日常收藏", "平時聊天隨手記下的單字、片語與佳句收集箱", "System", now_str)
            )
            conn.commit()

        # 2. 內建 starter 牌組：🌸 JLPT N5 基礎高頻生活單字
        starter_name = "🌸 JLPT N5 基礎高頻生活單字"
        cursor.execute("SELECT id FROM flashcard_decks WHERE name = ?", (starter_name,))
        row = cursor.fetchone()
        if not row:
            cursor.execute(
                "INSERT INTO flashcard_decks (name, description, created_by, created_at) VALUES (?, ?, ?, ?)",
                (starter_name, "精選 25 個最實用、高頻率的日語生活核心詞彙", "System", now_str)
            )
            deck_id = cursor.lastrowid
            
            starter_words = [
                ("桜", "さくら", "櫻花", "桜が満開でとても綺麗です。", "櫻花盛開，非常漂亮。", "春天的代表象徵"),
                ("猫", "ねこ", "貓咪", "公園で可愛い猫を見かけました。", "在公園看見了可愛的貓咪。", "常用名詞"),
                ("友達", "ともだち", "朋友", "週末に友達と映画を見に行きます。", "週末和朋友去看電影。", "基礎名詞"),
                ("勉強", "べんきょう", "學習、讀書", "毎日日本語を少しずつ勉強しています。", "每天一點一滴地學習日文。", "する動詞 / 名詞"),
                ("美味しい", "おいしい", "好吃的、美味的", "この抹茶ケーキはとても美味しいです。", "這個抹茶蛋糕非常好吃。", "い形容詞"),
                ("ありがとう", "ありがとう", "謝謝", "いつも手伝ってくれてありがとう。", "謝謝你總是幫助我。", "日常寒暄"),
                ("乾杯", "かんぱい", "乾杯", "今日の仕事が終わったら乾杯しましょう！", "今天工作結束後一起乾杯吧！", "社交聚會用語"),
                ("散歩", "さんぽ", "散步", "夕方に涼しい風を感じながら散歩します。", "傍晚一邊吹著涼風一邊散步。", "名詞 / する動詞"),
                ("約束", "やくそく", "約定", "明日の約束を忘れないでね。", "別忘了明天的約定喔。", "名詞 / する動詞"),
                ("笑顔", "えがお", "笑容", "君の笑顔を見ると元気がでます。", "看見你的笑容我就充滿精神。", "常用名詞"),
                ("好き", "すき", "喜歡", "私は甘いものが大好きです。", "我非常喜歡甜食。", "な形容詞"),
                ("綺麗", "きれい", "美麗的、乾淨的", "夜空の星がとても綺麗ですね。", "夜空的星星真漂亮呢。", "な形容詞"),
                ("幸せ", "しあわせ", "幸福", "一緒に美味しいご飯を食べる時が一番幸せです。", "一起吃美味飯菜的時候最幸福了。", "名詞 / な形容詞"),
                ("天気", "てんき", "天氣", "今日はとてもいい天気ですね。", "今天天氣真好呢。", "常用生活名詞"),
                ("写真", "しゃしん", "照片", "旅行の思い出に写真をたくさん撮りました。", "為了旅行的回憶拍了許多照片。", "常用生活名詞"),
                ("朝ご飯", "あさごはん", "早餐", "毎朝しっかり朝ご飯を食べましょう。", "每天早上都要好好吃早餐喔。", "生活名詞"),
                ("居酒屋", "いざかや", "居酒屋", "今夜は居酒屋で焼き鳥を食べよう。", "今晚在居酒屋吃烤雞肉串吧。", "文化名詞"),
                ("木漏れ日", "こもれび", "樹葉隙縫透進來的陽光", "木漏れ日の中を歩くのが好きです。", "喜歡走在陽光穿透樹葉的林間。", "優美的自然日語"),
                ("音楽", "おんがく", "音樂", "リラックスしたい時は静かな音楽を聴きます。", "想要放鬆時會聽安靜的音樂。", "休閒名詞"),
                ("買い物", "かいもの", "購物、買東西", "明日はスーパーへ買い物に行きます。", "明天要去超市買東西。", "常用名詞"),
                ("時計", "とけい", "鐘錶、手錶", "この時計は祖父からもらった宝物です。", "這只手錶是爺爺送我的寶物。", "物品名詞"),
                ("旅行", "りょこう", "旅行", "今年の秋は京都へ旅行したいです。", "今年秋天想去京都旅行。", "名詞 / する動詞"),
                ("家族", "かぞく", "家人", "週末は家族と一緒に過ごします。", "週末和家人一起度過。", "基礎名詞"),
                ("電車", "でんしゃ", "電車", "電車に乗って海へ行きましょう。", "搭電車去海邊吧。", "交通名詞"),
                ("おやすみ", "おやすみ", "晚安", "今日もお疲れ様、おやすみなさい。", "今天也辛苦了，晚安。", "日常道別用語")
            ]
            
            for w in starter_words:
                cursor.execute(
                    """INSERT INTO flashcard_cards 
                       (deck_id, front, reading, back, example_jp, example_zh, notes, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (deck_id, w[0], w[1], w[2], w[3], w[4], w[5], now_str)
                )
            conn.commit()


# -------------------------------------------------------------
# 1. 牌組管理 (Deck Operations)
# -------------------------------------------------------------
def get_or_create_deck(name: str, description: str = "", created_by: str = "User") -> dict:
    """取得或建立指定名稱的牌組"""
    init_flashcard_tables()
    name_clean = name.strip()
    now_str = datetime.now(TZ_TAIPEI).strftime("%Y-%m-%d %H:%M:%S")
    with get_conn() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM flashcard_decks WHERE name = ?", (name_clean,))
        row = cursor.fetchone()
        if row:
            return dict(row)
        cursor.execute(
            "INSERT INTO flashcard_decks (name, description, created_by, created_at) VALUES (?, ?, ?, ?)",
            (name_clean, description.strip(), created_by, now_str)
        )
        conn.commit()
        return {
            "id": cursor.lastrowid,
            "name": name_clean,
            "description": description.strip(),
            "created_by": created_by,
            "created_at": now_str
        }


def list_decks() -> list[dict]:
    """列出所有牌組與各牌組的卡片總數"""
    init_flashcard_tables()
    with get_conn() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT d.*, COUNT(c.id) as card_count
            FROM flashcard_decks d
            LEFT JOIN flashcard_cards c ON d.id = c.deck_id
            GROUP BY d.id
            ORDER BY d.id ASC
        """)
        return [dict(r) for r in cursor.fetchall()]


def get_deck(deck_identifier: str) -> Optional[dict]:
    """依據牌組 ID 或名稱尋找牌組"""
    init_flashcard_tables()
    ident = str(deck_identifier).strip()
    with get_conn() as conn:
        cursor = conn.cursor()
        if ident.isdigit():
            cursor.execute("""
                SELECT d.*, COUNT(c.id) as card_count
                FROM flashcard_decks d
                LEFT JOIN flashcard_cards c ON d.id = c.deck_id
                WHERE d.id = ?
                GROUP BY d.id
            """, (int(ident),))
            row = cursor.fetchone()
            if row:
                return dict(row)
        
        # 名稱模糊或精確比對
        cursor.execute("""
            SELECT d.*, COUNT(c.id) as card_count
            FROM flashcard_decks d
            LEFT JOIN flashcard_cards c ON d.id = c.deck_id
            WHERE d.name = ?
            GROUP BY d.id
        """, (ident,))
        row = cursor.fetchone()
        if row:
            return dict(row)
        
        # 模糊搜尋包含
        cursor.execute("""
            SELECT d.*, COUNT(c.id) as card_count
            FROM flashcard_decks d
            LEFT JOIN flashcard_cards c ON d.id = c.deck_id
            WHERE d.name LIKE ?
            GROUP BY d.id
            ORDER BY d.id ASC LIMIT 1
        """, (f"%{ident}%",))
        row = cursor.fetchone()
        return dict(row) if row else None


def delete_deck(deck_id: int) -> bool:
    """刪除指定牌組與其底下的所有卡片"""
    init_flashcard_tables()
    with get_conn() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM flashcard_cards WHERE deck_id = ?", (deck_id,))
        cursor.execute("DELETE FROM flashcard_decks WHERE id = ?", (deck_id,))
        conn.commit()
        return cursor.rowcount > 0


def get_user_active_deck(user_id: int) -> dict:
    """取得玩家當前偏好/正在學習的牌組，預設為 ⭐ 兩人的日常收藏"""
    init_flashcard_tables()
    with get_conn() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT active_deck_id FROM flashcard_user_settings WHERE user_id = ?", (user_id,))
        row = cursor.fetchone()
        if row and row["active_deck_id"]:
            deck = get_deck(str(row["active_deck_id"]))
            if deck:
                return deck
        # 回傳預設收集箱
        default_deck = get_deck("⭐ 兩人的日常收藏")
        if default_deck:
            return default_deck
        # 若找不到則回傳第一個牌組
        decks = list_decks()
        return decks[0] if decks else {"id": 1, "name": "預設牌組"}


def set_user_active_deck(user_id: int, deck_id: int):
    """設定玩家當前焦點牌組"""
    init_flashcard_tables()
    now_str = datetime.now(TZ_TAIPEI).strftime("%Y-%m-%d %H:%M:%S")
    with get_conn() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO flashcard_user_settings (user_id, active_deck_id, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
            active_deck_id=excluded.active_deck_id,
            updated_at=excluded.updated_at
        """, (user_id, deck_id, now_str))
        conn.commit()


# -------------------------------------------------------------
# 2. 卡片管理 (Card Operations)
# -------------------------------------------------------------
def add_card(
    deck_id: int,
    front: str,
    reading: str = "",
    back: str = "",
    example_jp: str = "",
    example_zh: str = "",
    notes: str = ""
) -> dict:
    """在指定牌組中新增一張單字卡"""
    init_flashcard_tables()
    now_str = datetime.now(TZ_TAIPEI).strftime("%Y-%m-%d %H:%M:%S")
    front_clean = front.strip()
    reading_clean = reading.strip()
    back_clean = back.strip()

    # 如果沒有平假名，嘗試自 front 提取（例如：桜[さくら] 或 桜(さくら)）
    if not reading_clean:
        m_kana = re.search(r"[\(\[（]([ぁ-んァ-ヶー]+)[\)\]）]", front_clean)
        if m_kana:
            reading_clean = m_kana.group(1).strip()
            front_clean = re.sub(r"[\(\[（][ぁ-んァ-ヶー]+[\)\]）]", "", front_clean).strip()

    with get_conn() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """INSERT INTO flashcard_cards
               (deck_id, front, reading, back, example_jp, example_zh, notes, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (deck_id, front_clean, reading_clean, back_clean, example_jp.strip(), example_zh.strip(), notes.strip(), now_str)
        )
        conn.commit()
        return {
            "id": cursor.lastrowid,
            "deck_id": deck_id,
            "front": front_clean,
            "reading": reading_clean,
            "back": back_clean,
            "example_jp": example_jp.strip(),
            "example_zh": example_zh.strip(),
            "notes": notes.strip(),
            "created_at": now_str
        }


def get_cards_in_deck(deck_id: int, limit: int = 500) -> list[dict]:
    """取得牌組內的所有卡片"""
    init_flashcard_tables()
    with get_conn() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM flashcard_cards WHERE deck_id = ? ORDER BY id ASC LIMIT ?", (deck_id, limit))
        return [dict(r) for r in cursor.fetchall()]


def get_card_by_id(card_id: int) -> Optional[dict]:
    """依卡片 ID 取得單張卡片資訊與所屬牌組"""
    init_flashcard_tables()
    with get_conn() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT c.*, d.name as deck_name
            FROM flashcard_cards c
            LEFT JOIN flashcard_decks d ON c.deck_id = d.id
            WHERE c.id = ?
        """, (card_id,))
        row = cursor.fetchone()
        return dict(row) if row else None


def delete_card(card_id: int) -> bool:
    """刪除指定卡片"""
    init_flashcard_tables()
    with get_conn() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM flashcard_cards WHERE id = ?", (card_id,))
        cursor.execute("DELETE FROM flashcard_progress WHERE card_id = ?", (card_id,))
        conn.commit()
        return cursor.rowcount > 0


def move_card(card_id: int, new_deck_id: int) -> bool:
    """將卡片移動到另一個牌組"""
    init_flashcard_tables()
    with get_conn() as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE flashcard_cards SET deck_id = ? WHERE id = ?", (new_deck_id, card_id))
        conn.commit()
        return cursor.rowcount > 0


def get_random_card_from_deck(deck_id: Optional[int] = None) -> Optional[dict]:
    """從指定牌組（或所有卡片）中隨機抽出一張卡片"""
    init_flashcard_tables()
    with get_conn() as conn:
        cursor = conn.cursor()
        if deck_id:
            cursor.execute("""
                SELECT c.*, d.name as deck_name
                FROM flashcard_cards c
                LEFT JOIN flashcard_decks d ON c.deck_id = d.id
                WHERE c.deck_id = ?
                ORDER BY RANDOM() LIMIT 1
            """, (deck_id,))
        else:
            cursor.execute("""
                SELECT c.*, d.name as deck_name
                FROM flashcard_cards c
                LEFT JOIN flashcard_decks d ON c.deck_id = d.id
                ORDER BY RANDOM() LIMIT 1
            """)
        row = cursor.fetchone()
        return dict(row) if row else None


def record_review_result(user_id: int, card_id: int, is_good: bool) -> dict:
    """更新玩家對該卡片的記憶進度 (SRS Leitner 簡易箱體法)"""
    init_flashcard_tables()
    now_dt = datetime.now(TZ_TAIPEI)
    now_str = now_dt.strftime("%Y-%m-%d %H:%M:%S")

    with get_conn() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM flashcard_progress WHERE user_id = ? AND card_id = ?", (user_id, card_id))
        row = cursor.fetchone()

        if row:
            curr_box = row["box"]
            review_count = row["review_count"] + 1
            correct_count = row["correct_count"] + (1 if is_good else 0)
            if is_good:
                new_box = min(5, curr_box + 1)
            else:
                new_box = max(1, curr_box - 1)
        else:
            curr_box = 1
            review_count = 1
            correct_count = 1 if is_good else 0
            new_box = 2 if is_good else 1

        # 根據 Box 估算下次複習時間 (Box 1: 1天, Box 2: 3天, Box 3: 7天, Box 4: 14天, Box 5: 30天)
        intervals = {1: 1, 2: 3, 3: 7, 4: 14, 5: 30}
        next_dt = now_dt + timedelta(days=intervals.get(new_box, 1))
        next_str = next_dt.strftime("%Y-%m-%d %H:%M:%S")

        cursor.execute("""
            INSERT INTO flashcard_progress 
            (user_id, card_id, box, review_count, correct_count, last_reviewed, next_review_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id, card_id) DO UPDATE SET
            box=excluded.box,
            review_count=excluded.review_count,
            correct_count=excluded.correct_count,
            last_reviewed=excluded.last_reviewed,
            next_review_at=excluded.next_review_at
        """, (user_id, card_id, new_box, review_count, correct_count, now_str, next_str))
        conn.commit()

        return {
            "card_id": card_id,
            "user_id": user_id,
            "new_box": new_box,
            "is_good": is_good,
            "review_count": review_count,
            "next_review_at": next_str
        }


# -------------------------------------------------------------
# 3. Anki (.apkg) 與純文字批次匯入引擎 (Import Engine)
# -------------------------------------------------------------
def import_anki_apkg_bytes(apkg_bytes: bytes, target_deck_name: Optional[str] = None, user_name: str = "User") -> dict:
    """
    從 Anki .apkg 檔案二進位資料中直接解壓縮並提取卡片，存入本地 SQLite。
    回傳：{"deck_name": str, "deck_id": int, "imported_count": int}
    """
    init_flashcard_tables()
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_zip = os.path.join(tmpdir, "deck.apkg")
        with open(tmp_zip, "wb") as f:
            f.write(apkg_bytes)

        with zipfile.ZipFile(tmp_zip, "r") as zf:
            namelist = zf.namelist()
            db_name = None
            for cand in ["collection.anki2", "collection.anki21"]:
                if cand in namelist:
                    db_name = cand
                    break
            if not db_name:
                raise ValueError("未在該 .apkg 檔案中找到 collection.anki2 資料庫。")

            zf.extract(db_name, tmpdir)
            db_path = os.path.join(tmpdir, db_name)

            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()

            resolved_deck_name = target_deck_name.strip() if target_deck_name else None
            if not resolved_deck_name:
                try:
                    cur.execute("SELECT decks FROM col LIMIT 1")
                    row = cur.fetchone()
                    if row and row["decks"]:
                        decks_dict = json.loads(row["decks"])
                        names = [d["name"] for d in decks_dict.values() if d.get("name") and d.get("name") != "Default"]
                        if names:
                            resolved_deck_name = names[0]
                except Exception:
                    pass

            if not resolved_deck_name:
                resolved_deck_name = f"Anki 匯入牌組_{datetime.now(TZ_TAIPEI).strftime('%m%d')}"

            # 建立或取得牌組
            deck = get_or_create_deck(resolved_deck_name, description=f"由 {user_name} 匯入之 Anki 牌組", created_by=user_name)
            deck_id = deck["id"]

            imported_cards = []
            try:
                cur.execute("SELECT flds FROM notes")
                for r in cur.fetchall():
                    flds = r["flds"].split("\x1f")
                    # 清除 HTML 與 sound 標籤
                    clean_flds = [re.sub(r"<[^>]+>|\[sound:[^\]]+\]", "", f).replace("&nbsp;", " ").strip() for f in flds]
                    if not clean_flds or not clean_flds[0]:
                        continue

                    front = clean_flds[0]
                    reading = clean_flds[1] if len(clean_flds) > 1 else ""
                    back = clean_flds[2] if len(clean_flds) > 2 else ""
                    ex_jp = clean_flds[3] if len(clean_flds) > 3 else ""
                    ex_zh = clean_flds[4] if len(clean_flds) > 4 else ""

                    if len(clean_flds) == 2:
                        back = clean_flds[1]
                        reading = ""

                    # 解析 front 內的假名注音（例如 桜[さくら]）
                    m_furi = re.match(r"^([^\[（]+)[\[（]([^\]）]+)[\]）]$", front)
                    if m_furi and not reading:
                        front = m_furi.group(1).strip()
                        reading = m_furi.group(2).strip()

                    if front and back:
                        card = add_card(deck_id, front=front, reading=reading, back=back, example_jp=ex_jp, example_zh=ex_zh)
                        imported_cards.append(card)
            finally:
                conn.close()

            return {
                "deck_name": resolved_deck_name,
                "deck_id": deck_id,
                "imported_count": len(imported_cards)
            }


def import_text_cards(text_content: str, target_deck_name: str, user_name: str = "User") -> dict:
    """
    批次解析純文字列表（支援 TSV、CSV 或以 '-'、'/'、':' 分隔的文字行）。
    例如：
      桜 \t さくら \t 櫻花
      猫 - ねこ - 貓咪
      美味しい: 好吃的
    """
    init_flashcard_tables()
    deck = get_or_create_deck(target_deck_name, description=f"由 {user_name} 匯入之自訂牌組", created_by=user_name)
    deck_id = deck["id"]

    lines = text_content.strip().splitlines()
    count = 0
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue

        # 優先以 Tab 分隔 (TSV)
        if "\t" in line:
            parts = [p.strip() for p in line.split("\t") if p.strip()]
        elif "," in line and line.count(",") >= 1:
            parts = [p.strip() for p in line.split(",") if p.strip()]
        elif " - " in line:
            parts = [p.strip() for p in line.split(" - ") if p.strip()]
        elif "/" in line:
            parts = [p.strip() for p in line.split("/") if p.strip()]
        elif ":" in line or "：" in line:
            parts = [p.strip() for p in re.split(r"[:：]", line) if p.strip()]
        else:
            # 只有一個單字
            parts = [line]

        if not parts:
            continue

        front = parts[0]
        reading = ""
        back = ""
        ex_jp = ""
        ex_zh = ""

        if len(parts) >= 3:
            reading = parts[1]
            back = parts[2]
            if len(parts) >= 4:
                ex_jp = parts[3]
            if len(parts) >= 5:
                ex_zh = parts[4]
        elif len(parts) == 2:
            back = parts[1]

        add_card(deck_id, front=front, reading=reading, back=back, example_jp=ex_jp, example_zh=ex_zh)
        count += 1

    return {
        "deck_name": target_deck_name,
        "deck_id": deck_id,
        "imported_count": count
    }
