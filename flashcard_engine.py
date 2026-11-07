import sqlite3
import zipfile
import tempfile
import json
import re
import os
import random
import urllib.parse
import io
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import Optional

from assistant_db import get_conn, get_db_path, TZ_TAIPEI

IMPORTS_DIR = Path(__file__).parent / "data" / "imports"
MEDIA_DIR = Path(__file__).parent / "data" / "media"
_initialized_dbs: set[str] = set()


def init_flashcard_tables():
    """初始化單字卡 (Flashcard)、牌組 (Decks) 與學習進度資料表（每個資料庫實例僅執行一次）"""
    db_key = str(get_db_path())
    if db_key in _initialized_dbs:
        return
    IMPORTS_DIR.mkdir(parents=True, exist_ok=True)
    MEDIA_DIR.mkdir(parents=True, exist_ok=True)
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
        # 2. 單字卡片資料表（含圖檔支援）
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
                image_filename TEXT DEFAULT '',
                image_side TEXT DEFAULT 'both',
                audio_filename TEXT DEFAULT '',
                pos TEXT DEFAULT '',
                pitch TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                FOREIGN KEY (deck_id) REFERENCES flashcard_decks(id) ON DELETE CASCADE
            )
        """)
        # 舊版資料庫欄位遷移
        try:
            cursor.execute("ALTER TABLE flashcard_cards ADD COLUMN image_filename TEXT DEFAULT ''")
        except sqlite3.OperationalError:
            pass
        try:
            cursor.execute("ALTER TABLE flashcard_cards ADD COLUMN image_side TEXT DEFAULT 'both'")
        except sqlite3.OperationalError:
            pass
        try:
            cursor.execute("ALTER TABLE flashcard_cards ADD COLUMN audio_filename TEXT DEFAULT ''")
        except sqlite3.OperationalError:
            pass
        try:
            cursor.execute("ALTER TABLE flashcard_cards ADD COLUMN pos TEXT DEFAULT ''")
        except sqlite3.OperationalError:
            pass
        try:
            cursor.execute("ALTER TABLE flashcard_cards ADD COLUMN pitch TEXT DEFAULT ''")
        except sqlite3.OperationalError:
            pass
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
    _initialized_dbs.add(db_key)


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
    """刪除指定牌組與其底下的所有卡片（連帶清理關聯媒體圖檔）"""
    init_flashcard_tables()
    with get_conn() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT image_filename FROM flashcard_cards WHERE deck_id = ? AND image_filename != ''", (deck_id,))
        for row in cursor.fetchall():
            fn = row["image_filename"]
            if fn:
                p = MEDIA_DIR / fn
                if p.is_file():
                    try:
                        p.unlink()
                    except Exception:
                        pass
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
    notes: str = "",
    image_filename: str = "",
    image_side: str = "both",
    audio_filename: str = "",
    pos: str = "",
    pitch: str = ""
) -> dict:
    """在指定牌組中新增一張單字卡（支援圖檔、真人語音檔、詞性與音調）"""
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
               (deck_id, front, reading, back, example_jp, example_zh, notes, image_filename, image_side, audio_filename, pos, pitch, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                deck_id,
                front_clean,
                reading_clean,
                back_clean,
                example_jp.strip(),
                example_zh.strip(),
                notes.strip(),
                image_filename.strip(),
                image_side.strip(),
                audio_filename.strip(),
                pos.strip(),
                pitch.strip(),
                now_str
            )
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
            "image_filename": image_filename.strip(),
            "image_side": image_side.strip(),
            "audio_filename": audio_filename.strip(),
            "pos": pos.strip(),
            "pitch": pitch.strip(),
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
MAX_APKG_BYTES = 25 * 1024 * 1024  # 25 MB 上限
MAX_UNCOMPRESSED_DB_BYTES = 50 * 1024 * 1024  # 解壓後 SQLite 上限 50 MB


def list_local_import_files() -> list[Path]:
    """列出本機 data/imports/ 資料夾下可供匯入的單字檔 (.apkg, .txt, .tsv, .csv)"""
    IMPORTS_DIR.mkdir(parents=True, exist_ok=True)
    valid_exts = {".apkg", ".txt", ".tsv", ".csv"}
    return sorted([p for p in IMPORTS_DIR.iterdir() if p.is_file() and p.suffix.lower() in valid_exts])


def clean_anki_html(html_text: str) -> str:
    """將 Anki 卡片欄位內的 HTML 樣式轉換為 Discord Markdown 粗體、斜體與換行，並清理雜訊"""
    if not html_text:
        return ""
    text = html_text
    # 換行轉換
    text = re.sub(r"<\s*br\s*/?>", "\n", text, flags=re.IGNORECASE)
    # 粗體轉換 <b> / <strong> -> **...**
    text = re.sub(r"<\s*(b|strong)\s*>(.*?)<\s*/\s*\1\s*>", r"**\2**", text, flags=re.IGNORECASE | re.DOTALL)
    # 斜體轉換 <i> / <em> -> *...*
    text = re.sub(r"<\s*(i|em)\s*>(.*?)<\s*/\s*\1\s*>", r"*\2*", text, flags=re.IGNORECASE | re.DOTALL)
    # 底線轉換 <u> -> __...__
    text = re.sub(r"<\s*u\s*>(.*?)<\s*/\s*u\s*>", r"__\1__", text, flags=re.IGNORECASE | re.DOTALL)
    # 高亮 class 轉換 <span class="highlight"> -> **...**
    text = re.sub(r'<\s*span\s+[^>]*class=["\']?[^"\'>]*highlight[^"\'>]*["\']?[^>]*>(.*?)<\s*/\s*span\s*>', r"**\1**", text, flags=re.IGNORECASE | re.DOTALL)
    # 移除 [sound:...] 標籤
    text = re.sub(r"\[sound:[^\]]+\]", "", text)
    # 清理所有剩餘 HTML 標籤
    text = re.sub(r"<[^>]+>", "", text)
    # 實體符號轉換
    text = text.replace("&nbsp;", " ").replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"')
    return re.sub(r"[ \t]+", " ", text).strip()


def normalize_subdeck_query(s: str) -> str:
    """正規化子牌組查詢字串（簡繁轉換與去標點）"""
    s = s.lower()
    s = s.replace("高頻", "高频").replace("中頻", "中频").replace("低頻", "低频")
    return s


def match_subdeck(query: str, deck_name: str) -> bool:
    """比對查詢字詞是否符合 Anki 子牌組名稱"""
    q_norm = normalize_subdeck_query(query)
    d_norm = normalize_subdeck_query(deck_name)
    if q_norm in d_norm:
        return True
    tokens = re.findall(r"[a-z0-9]+|[\u4e00-\u9fa5]+", q_norm)
    if tokens and all(t in d_norm for t in tokens):
        return True
    return False


def clean_deck_title(raw_name: str) -> str:
    """美化 Anki 子牌組名稱為易讀之牌組標題"""
    name = raw_name
    m = re.search(r"(\d?-?N[1-5])(?:::(\d-?[高空中低]+频?))?", name, re.IGNORECASE)
    if m:
        lvl = m.group(1)
        lvl_clean = re.sub(r"^\d-?", "", lvl).upper()
        freq = m.group(2) or ""
        freq_clean = re.sub(r"^\d-?", "", freq).replace("高频", "高頻").replace("中频", "中頻").replace("低频", "低頻")
        if freq_clean:
            return f"🌸 JLPT {lvl_clean} {freq_clean}單字"
        return f"🌸 JLPT {lvl_clean} 核心單字"
    parts = [p for p in name.split("::") if p and p != "Default"]
    return f"🌸 {parts[-1]}" if parts else name


def get_anki_subdecks(apkg_path: Path) -> list[dict]:
    """快速掃描本機 .apkg 檔案內的可用子牌組與卡片數"""
    if not apkg_path.is_file():
        return []
    try:
        with zipfile.ZipFile(apkg_path, "r") as zf:
            namelist = set(zf.namelist())
            db_name = "collection.anki21" if "collection.anki21" in namelist else "collection.anki2"
            if db_name not in namelist:
                return []
            with tempfile.TemporaryDirectory() as tmpdir:
                zf.extract(db_name, tmpdir)
                db_path = os.path.join(tmpdir, db_name)
                conn = sqlite3.connect(db_path)
                conn.row_factory = sqlite3.Row
                cur = conn.cursor()
                try:
                    cur.execute("SELECT decks FROM col LIMIT 1")
                    row = cur.fetchone()
                    if not row or not row["decks"]:
                        return []
                    decks = json.loads(row["decks"])
                    cur.execute("SELECT did, count(*) as cnt FROM cards GROUP BY did")
                    card_counts = {r["did"]: r["cnt"] for r in cur.fetchall()}
                    results = []
                    for did_str, d in decks.items():
                        did = int(did_str)
                        dname = d.get("name", "")
                        if dname == "Default" or did not in card_counts:
                            continue
                        results.append({
                            "did": did,
                            "raw_name": dname,
                            "title": clean_deck_title(dname),
                            "card_count": card_counts.get(did, 0)
                        })
                    return sorted(results, key=lambda x: x["raw_name"])
                finally:
                    cur.close()
                    conn.close()
    except Exception:
        return []


def import_anki_apkg(
    file_path: Optional[Path] = None,
    apkg_bytes: Optional[bytes] = None,
    subdeck_filter: Optional[str] = None,
    target_deck_name: Optional[str] = None,
    user_name: str = "User",
    extract_media: bool = True
) -> dict:
    """
    從 Anki .apkg 檔案（本機檔案路徑或二進位資料）中解析卡片，支援子牌組/等級篩選與真人音訊/圖檔提取。
    回傳：{"success": bool, "deck_name": str, "deck_id": int, "imported_count": int, "imported_cards": int, "media_count": int}
    """
    init_flashcard_tables()
    # 若指定為 all / 全部 / 全自動，則轉由 import_all_anki_subdecks 進行全牌組一鍵分類匯入
    if file_path and subdeck_filter and subdeck_filter.lower() in ("all", "全部", "全自動", "一鍵分類", "*"):
        return import_all_anki_subdecks(file_path=file_path, user_name=user_name, extract_media=False)

    if apkg_bytes and len(apkg_bytes) > MAX_APKG_BYTES:
        return {"success": False, "error": f"檔案過大（超過 {MAX_APKG_BYTES // (1024 * 1024)} MB 上限）。建議放入本機 data/imports/ 目錄。"}

    try:
        if file_path:
            zf = zipfile.ZipFile(file_path, "r")
        elif apkg_bytes:
            zf = zipfile.ZipFile(io.BytesIO(apkg_bytes), "r")
        else:
            return {"success": False, "error": "未提供有效之 .apkg 檔案路徑或資料。"}
    except Exception as e:
        return {"success": False, "error": f"無法開啟 .apkg 壓縮檔：{e}"}

    with zf:
        namelist = set(zf.namelist())
        db_name = None
        for cand in ["collection.anki21", "collection.anki2"]:
            if cand in namelist:
                db_name = cand
                break
        if not db_name:
            return {"success": False, "error": "未在該 .apkg 檔案中找到 collection.anki2 / anki21 資料庫。"}

        # 讀取 media 映射表
        name_to_entry = {}
        if "media" in namelist:
            try:
                media_dict = json.loads(zf.read("media").decode("utf-8"))
                name_to_entry = {v: k for k, v in media_dict.items()}
            except Exception:
                pass

        with tempfile.TemporaryDirectory() as tmpdir:
            zf.extract(db_name, tmpdir)
            db_path = os.path.join(tmpdir, db_name)

            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            try:
                cur.execute("SELECT decks, models FROM col LIMIT 1")
                col_row = cur.fetchone()
                if not col_row:
                    return {"success": False, "error": "Anki 資料庫格式異常（遺失 col 資料表）。"}

                decks = json.loads(col_row["decks"]) if col_row["decks"] else {}
                models = json.loads(col_row["models"]) if col_row["models"] else {}

                # 判定子牌組篩選
                matched_dids = []
                matched_deck_names = []
                if subdeck_filter:
                    for did_str, d in decks.items():
                        dname = d.get("name", "")
                        if dname == "Default":
                            continue
                        if match_subdeck(subdeck_filter, dname):
                            matched_dids.append(int(did_str))
                            matched_deck_names.append(dname)

                # 決定牌組名稱與欲抓取之卡片
                if matched_dids:
                    cur.execute(f"SELECT DISTINCT nid FROM cards WHERE did IN ({','.join('?'*len(matched_dids))})", matched_dids)
                    nids = [r[0] for r in cur.fetchall()]
                    if target_deck_name:
                        resolved_deck_name = target_deck_name.strip()
                    elif len(matched_deck_names) == 1:
                        resolved_deck_name = clean_deck_title(matched_deck_names[0])
                    else:
                        resolved_deck_name = f"🌸 JLPT {subdeck_filter.strip()} 單字"
                else:
                    cur.execute("SELECT DISTINCT nid FROM cards")
                    nids = [r[0] for r in cur.fetchall()]
                    if target_deck_name:
                        resolved_deck_name = target_deck_name.strip()
                    else:
                        names = [d["name"] for d in decks.values() if d.get("name") and d.get("name") != "Default"]
                        resolved_deck_name = clean_deck_title(names[0]) if names else f"Anki 匯入牌組_{datetime.now(TZ_TAIPEI).strftime('%m%d')}"

                if not nids:
                    return {"success": False, "error": f"未在牌組中找到符合「{subdeck_filter or '所有'}」條件的卡片。"}

                # 建立目標牌組
                deck = get_or_create_deck(resolved_deck_name, description=f"由 {user_name} 匯入之 Anki 牌組", created_by=user_name)
                deck_id = deck["id"]

                # 讀取該牌組已存在之單字，避免重複匯入 (Deduplication)
                with get_conn() as local_conn:
                    existing_fronts = {row[0] for row in local_conn.execute(
                        "SELECT front FROM flashcard_cards WHERE deck_id = ?", (deck_id,)
                    ).fetchall()}

                imported_cards = []
                media_count = 0

                # 分批讀取 notes，避免 SQLite 參數上限
                all_notes = []
                chunk_size = 500
                for i in range(0, len(nids), chunk_size):
                    chunk = nids[i:i + chunk_size]
                    cur.execute(f"SELECT id, mid, flds, tags FROM notes WHERE id IN ({','.join('?' * len(chunk))})", chunk)
                    all_notes.extend(cur.fetchall())

                for r in all_notes:
                    flds = r["flds"].split("\x1f")
                    mid = str(r["mid"])
                    m = models.get(mid, {})
                    fld_names = [f["name"] for f in m.get("flds", [])]
                    card_dict = dict(zip(fld_names, flds)) if fld_names else {}

                    # 針對 eggrolls-JLPT10k 豐富欄位之專屬解析
                    if "VocabKanji" in card_dict and "VocabFurigana" in card_dict:
                        front = card_dict.get("VocabKanji") or card_dict.get("VocabFurigana", "")
                        reading = card_dict.get("VocabFurigana", "")
                        pitch = card_dict.get("VocabPitch", "")
                        pos = card_dict.get("VocabPoS", "")
                        back = card_dict.get("VocabDefTC") or card_dict.get("VocabDefSC", "")
                        ex_jp = clean_anki_html(card_dict.get("SentFurigana1") or card_dict.get("SentKanji1", ""))
                        ex_zh = card_dict.get("SentDefTC1") or card_dict.get("SentDefSC1", "")
                        notes = clean_anki_html(card_dict.get("VocabPlus", ""))
                        audio_raw = card_dict.get("VocabAudio", "")
                    else:
                        # 通用型 Anki 筆記解析
                        clean_flds = [clean_anki_html(f) for f in flds]
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
                        pitch = ""
                        pos = ""
                        notes = ""
                        audio_raw = r["flds"]

                    # 提取真人發音音訊檔 (.mp3)
                    audio_filename = ""
                    if extract_media and name_to_entry:
                        m_sound = re.search(r"\[sound:([^\]]+)\]", audio_raw)
                        if m_sound:
                            sound_ref = m_sound.group(1).strip()
                            entry_name = name_to_entry.get(sound_ref)
                            if entry_name and entry_name in namelist:
                                safe_audio = f"audio_{deck_id}_{r['id']}.mp3"
                                out_audio_path = MEDIA_DIR / safe_audio
                                if not out_audio_path.is_file():
                                    try:
                                        out_audio_path.write_bytes(zf.read(entry_name))
                                        media_count += 1
                                    except Exception:
                                        pass
                                audio_filename = safe_audio

                    # 提取圖片檔 (.png, .jpg, .webp)
                    image_filename = ""
                    if extract_media and name_to_entry:
                        m_img = re.search(r'<img[^>]+src=["\']?([^"\'>]+)["\']?', r["flds"], re.IGNORECASE)
                        if m_img:
                            img_ref = urllib.parse.unquote(m_img.group(1).strip())
                            entry_name = name_to_entry.get(img_ref)
                            if entry_name and entry_name in namelist:
                                ext = Path(img_ref).suffix.lower() or ".png"
                                safe_img = f"img_{deck_id}_{r['id']}{ext}"
                                out_img_path = MEDIA_DIR / safe_img
                                if not out_img_path.is_file():
                                    try:
                                        out_img_path.write_bytes(zf.read(entry_name))
                                        media_count += 1
                                    except Exception:
                                        pass
                                image_filename = safe_img

                    if front and (back or reading):
                        if front in existing_fronts:
                            continue
                        existing_fronts.add(front)
                        card = add_card(
                            deck_id,
                            front=front,
                            reading=reading,
                            back=back,
                            example_jp=ex_jp,
                            example_zh=ex_zh,
                            notes=notes,
                            image_filename=image_filename,
                            audio_filename=audio_filename,
                            pos=pos,
                            pitch=pitch
                        )
                        imported_cards.append(card)
            finally:
                cur.close()
                conn.close()

    count = len(imported_cards)
    return {
        "success": True,
        "deck_name": resolved_deck_name,
        "deck_id": deck_id,
        "imported_count": count,
        "imported_cards": count,
        "media_count": media_count
    }


def get_card_audio_path(card: dict) -> Optional[Path]:
    """取得單字卡的音訊檔路徑，若本地尚未解壓則從 data/imports/ 中的 .apkg 自動隨選提取"""
    audio_fn = card.get("audio_filename")
    if not audio_fn:
        return None
    local_path = MEDIA_DIR / audio_fn
    if local_path.is_file():
        return local_path

    # audio_fn 格式通常為 audio_{deck_id}_{nid}.mp3
    m = re.match(r"audio_\d+_(\d+)\.mp3", audio_fn)
    target_nid = int(m.group(1)) if m else None

    for apkg_file in IMPORTS_DIR.glob("*.apkg"):
        try:
            with zipfile.ZipFile(apkg_file, "r") as zf:
                namelist = set(zf.namelist())
                if "media" not in namelist:
                    continue
                media_dict = json.loads(zf.read("media").decode("utf-8"))
                name_to_entry = {v: k for k, v in media_dict.items()}

                db_name = "collection.anki21" if "collection.anki21" in namelist else "collection.anki2"
                if db_name not in namelist:
                    continue
                with tempfile.TemporaryDirectory() as tmpdir:
                    zf.extract(db_name, tmpdir)
                    conn = sqlite3.connect(os.path.join(tmpdir, db_name))
                    cur = conn.cursor()
                    if target_nid:
                        cur.execute("SELECT flds FROM notes WHERE id = ?", (target_nid,))
                    else:
                        cur.execute("SELECT flds FROM notes WHERE flds LIKE ?", (f"%{card['front']}%",))
                    row = cur.fetchone()
                    cur.close()
                    conn.close()

                    if row:
                        m_sound = re.search(r"\[sound:([^\]]+)\]", row[0])
                        if m_sound:
                            entry_name = name_to_entry.get(m_sound.group(1).strip())
                            if entry_name and entry_name in namelist:
                                local_path.write_bytes(zf.read(entry_name))
                                return local_path
        except Exception:
            continue

    return None


def import_all_anki_subdecks(
    file_path: Path,
    user_name: str = "User",
    extract_media: bool = False
) -> dict:
    """
    一鍵掃描並全自動匯入 Anki .apkg 內的所有等級子牌組，依子牌組自動拆分並建置各自的獨立牌組。
    回傳：{
        "success": bool,
        "is_batch_all": True,
        "total_decks": int,
        "total_cards": int,
        "decks_summary": list[dict],
        "media_count": int
    }
    """
    init_flashcard_tables()
    if not file_path.is_file():
        return {"success": False, "error": f"找不到本機檔案：{file_path.name}"}

    try:
        zf = zipfile.ZipFile(file_path, "r")
    except Exception as e:
        return {"success": False, "error": f"無法開啟 .apkg 壓縮檔：{e}"}

    with zf:
        namelist = set(zf.namelist())
        db_name = None
        for cand in ["collection.anki21", "collection.anki2"]:
            if cand in namelist:
                db_name = cand
                break
        if not db_name:
            return {"success": False, "error": "未在該 .apkg 檔案中找到 collection.anki2 / anki21 資料庫。"}

        media_json = {}
        name_to_entry = {}
        if "media" in namelist:
            try:
                media_dict = json.loads(zf.read("media").decode("utf-8"))
                name_to_entry = {v: k for k, v in media_dict.items()}
            except Exception:
                pass

        with tempfile.TemporaryDirectory() as tmpdir:
            zf.extract(db_name, tmpdir)
            db_path = os.path.join(tmpdir, db_name)
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            try:
                cur.execute("SELECT decks, models FROM col LIMIT 1")
                col_row = cur.fetchone()
                if not col_row:
                    return {"success": False, "error": "Anki 資料庫格式異常。"}
                decks = json.loads(col_row["decks"]) if col_row["decks"] else {}
                models = json.loads(col_row["models"]) if col_row["models"] else {}

                cur.execute("SELECT did, nid FROM cards")
                deck_to_nids = {}
                for r in cur.fetchall():
                    deck_to_nids.setdefault(r["did"], []).append(r["nid"])

                cur.execute("SELECT id, mid, flds, tags FROM notes")
                notes_dict = {r["id"]: r for r in cur.fetchall()}
            finally:
                cur.close()
                conn.close()

    now_str = datetime.now(TZ_TAIPEI).strftime("%Y-%m-%d %H:%M:%S")
    decks_summary = []
    total_cards = 0
    media_count = 0

    with get_conn() as local_conn:
        local_cur = local_conn.cursor()
        for did, nids in sorted(deck_to_nids.items(), key=lambda x: decks.get(str(x[0]), {}).get("name", "")):
            dname = decks.get(str(did), {}).get("name", "")
            if dname == "Default" or not nids:
                continue
            clean_title = clean_deck_title(dname)

            local_cur.execute(
                "INSERT OR IGNORE INTO flashcard_decks (name, description, created_by, created_at) VALUES (?, ?, ?, ?)",
                (clean_title, f"由 {user_name} 自 {file_path.name} 一鍵分類匯入", user_name, now_str)
            )
            local_cur.execute("SELECT id FROM flashcard_decks WHERE name = ?", (clean_title,))
            deck_id = local_cur.fetchone()[0]

            # 讀取該牌組已存在之單字，避免重複匯入 (Deduplication)
            local_cur.execute("SELECT front FROM flashcard_cards WHERE deck_id = ?", (deck_id,))
            existing_fronts = {row[0] for row in local_cur.fetchall()}

            cards_batch = []
            for nid in nids:
                n = notes_dict.get(nid)
                if not n:
                    continue
                mid = str(n["mid"])
                m = models.get(mid, {})
                fld_names = [f["name"] for f in m.get("flds", [])]
                flds = n["flds"].split("\x1f")
                card_dict = dict(zip(fld_names, flds)) if fld_names else {}

                if "VocabKanji" in card_dict and "VocabFurigana" in card_dict:
                    front = card_dict.get("VocabKanji") or card_dict.get("VocabFurigana", "")
                    reading = card_dict.get("VocabFurigana", "")
                    pitch = card_dict.get("VocabPitch", "")
                    pos = card_dict.get("VocabPoS", "")
                    back = card_dict.get("VocabDefTC") or card_dict.get("VocabDefSC", "")
                    ex_jp = clean_anki_html(card_dict.get("SentFurigana1") or card_dict.get("SentKanji1", ""))
                    ex_zh = card_dict.get("SentDefTC1") or card_dict.get("SentDefSC1", "")
                    notes = clean_anki_html(card_dict.get("VocabPlus", ""))
                    audio_raw = card_dict.get("VocabAudio", "")
                else:
                    clean_flds = [clean_anki_html(f) for f in flds]
                    if not clean_flds or not clean_flds[0]:
                        continue
                    front = clean_flds[0]
                    reading = clean_flds[1] if len(clean_flds) > 1 else ""
                    back = clean_flds[2] if len(clean_flds) > 2 else ""
                    ex_jp = clean_flds[3] if len(clean_flds) > 3 else ""
                    ex_zh = clean_flds[4] if len(clean_flds) > 4 else ""
                    pitch = ""
                    pos = ""
                    notes = ""
                    audio_raw = n["flds"]

                if not front:
                    continue
                if front in existing_fronts:
                    continue
                existing_fronts.add(front)

                audio_fn = ""
                m_sound = re.search(r"\[sound:([^\]]+)\]", audio_raw)
                if m_sound:
                    sound_ref = m_sound.group(1).strip()
                    if sound_ref in name_to_entry:
                        audio_fn = f"audio_{deck_id}_{nid}.mp3"

                cards_batch.append((
                    deck_id, front, reading, back, ex_jp, ex_zh, notes,
                    "", "both", audio_fn, pos, pitch, now_str
                ))

            if cards_batch:
                local_cur.executemany("""
                    INSERT INTO flashcard_cards
                    (deck_id, front, reading, back, example_jp, example_zh, notes, image_filename, image_side, audio_filename, pos, pitch, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, cards_batch)

            local_cur.execute("SELECT count(*) FROM flashcard_cards WHERE deck_id = ?", (deck_id,))
            current_total = local_cur.fetchone()[0]

            decks_summary.append({
                "id": deck_id,
                "name": clean_title,
                "new_count": len(cards_batch),
                "card_count": current_total
            })
            total_cards += len(cards_batch)

        local_conn.commit()

    return {
        "success": True,
        "is_batch_all": True,
        "total_decks": len(decks_summary),
        "total_cards": total_cards,
        "new_cards": total_cards,
        "decks_summary": decks_summary,
        "media_count": media_count
    }


def import_anki_apkg_bytes(apkg_bytes: bytes, target_deck_name: Optional[str] = None, user_name: str = "User") -> dict:
    """從 Anki .apkg 檔案二進位資料中直接解壓縮並提取卡片（向下相容介面）"""
    return import_anki_apkg(apkg_bytes=apkg_bytes, target_deck_name=target_deck_name, user_name=user_name)


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

    # 讀取該牌組已存在之單字，避免重複匯入 (Deduplication)
    with get_conn() as local_conn:
        existing_fronts = {row[0] for row in local_conn.execute(
            "SELECT front FROM flashcard_cards WHERE deck_id = ?", (deck_id,)
        ).fetchall()}

    lines = text_content.strip().splitlines()
    valid_lines = 0
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
        if not front:
            continue
        valid_lines += 1

        if front in existing_fronts:
            continue
        existing_fronts.add(front)

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
        "success": valid_lines > 0,
        "deck_name": target_deck_name,
        "deck_id": deck_id,
        "imported_count": count,
        "imported_cards": count,
        "already_existed_count": valid_lines - count,
        "error": None if valid_lines > 0 else "未解析出任何有效的單字行。"
    }
