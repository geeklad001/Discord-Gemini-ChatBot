"""1 in 8 Billion (80億分之1) Life Simulation Engine: UN demographics, SQLite, PIL Cards & Hybrid LLM."""

from __future__ import annotations

import contextvars
import io
import json
import random
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

from PIL import Image, ImageDraw, ImageFont

from engines.assistant_db import DATA_DIR, TZ_TAIPEI, get_conn, get_db_path

_initialized_ls_dbs: set[str] = set()
_world_cache: Optional[list[dict]] = None

pending_lifesim_card_updates: contextvars.ContextVar[Optional[list[dict]]] = contextvars.ContextVar(
    "pending_lifesim_card_updates", default=None
)

ERA_CONFIG = {
    "past_1950": {"label": "🕰️ 過去 (1950年代)", "year_range": (1950, 1965), "le_delta": -14.0, "gdp_mult": 0.28, "edu_delta": -4.5, "inf_pen": 0.045},
    "past_1980": {"label": "📼 過去 (1980年代)", "year_range": (1980, 1995), "le_delta": -6.5, "gdp_mult": 0.55, "edu_delta": -2.0, "inf_pen": 0.018},
    "present": {"label": "🌍 現代 (2026)", "year_range": (2024, 2026), "le_delta": 0.0, "gdp_mult": 1.0, "edu_delta": 0.0, "inf_pen": 0.0},
    "future_2050": {"label": "🚀 未來 (2050預測)", "year_range": (2048, 2055), "le_delta": 3.5, "gdp_mult": 1.35, "edu_delta": 1.5, "inf_pen": -0.008},
}

DRAW_METHOD_LABELS = {
    "births": "👶 依全球新生兒出生比例",
    "population": "👥 依全球現存人口比例",
}

FOCUS_LABELS = {
    "balance": "⚖️ 均衡發展",
    "study": "📚 專注學業進修",
    "career": "💼 全力衝刺事業",
    "health": "❤️ 重視健康養生",
    "social": "🤝 經營感情人脈",
}

REGION_COLORS = {
    "東亞": (235, 95, 130),
    "東南亞": (46, 196, 182),
    "南亞": (255, 159, 28),
    "西亞": (212, 163, 115),
    "中亞": (131, 197, 190),
    "撒哈拉以南非洲": (231, 111, 81),
    "北非": (233, 196, 106),
    "西歐": (88, 129, 242),
    "北歐": (96, 165, 250),
    "南歐": (244, 162, 97),
    "東歐": (155, 93, 229),
    "北美": (67, 170, 139),
    "拉美": (249, 199, 79),
    "大洋洲": (58, 134, 255),
}

REGIONAL_NAMES = {
    "TW": (["子軒", "宇辰", "冠宇", "承翰", "柏翰"], ["品妍", "詠晴", "羽彤", "子晴", "思穎"], ["陳", "林", "黃", "張", "李"]),
    "JP": (["蓮", "陽翔", "湊", "悠真", "蒼"], ["陽葵", "凜", "詩", "芽依", "結衣"], ["佐藤", "鈴木", "高橋", "田中", "渡邊"]),
    "KR": (["瑞俊", "道允", "夏俊", "時宇", "恩宇"], ["瑞妍", "夏允", "智宇", "書雅", "荷允"], ["金", "李", "朴", "崔", "鄭"]),
    "CN": (["浩宇", "子墨", "宇航", "梓軒", "澤宇"], ["欣怡", "梓涵", "詩涵", "語桐", "若曦"], ["王", "李", "張", "劉", "陳"]),
    "IN": (["Aarav", "Vihaan", "Aditya", "Arjun", "Reyansh"], ["Aadhya", "Ananya", "Diya", "Saanvi", "Meera"], ["Sharma", "Patel", "Kumar", "Singh", "Verma"]),
    "東亞": (["浩然", "明軒", "俊傑"], ["雅婷", "佳穎", "心怡"], ["陳", "林", "王", "李"]),
    "東南亞": (["Aditya", "Rizky", "Minh", "Somchai", "Mateo"], ["Siti", "Linh", "Ploy", "Maria", "Putri"], ["Nguyen", "Santos", "Tan", "Wijoyo"]),
    "南亞": (["Aryan", "Bilal", "Rohan", "Kamal"], ["Fatima", "Priya", "Nadia", "Sana"], ["Khan", "Ahmed", "Das", "Rai"]),
    "西亞": (["Omar", "Ali", "Yusuf", "Tariq", "Daniel"], ["Layla", "Noor", "Zainab", "Maya", "Noa"], ["Al-Mansoor", "Hassan", "Kaya", "Levi"]),
    "中亞": (["Timur", "Azamat", "Ruslan", "Nurlan"], ["Amina", "Madina", "Aigerim", "Dilnoza"], ["Alimov", "Nazarov", "Akhmetov"]),
    "撒哈拉以南非洲": (["Chidi", "Kwame", "Samuel", "Tariro", "Kofi", "Abebe"], ["Amara", "Zainab", "Grace", "Amina", "Nia", "Wanjiku"], ["Okafor", "Mensah", "Diallo", "Ndlovu", "Kamau"]),
    "北非": (["Youssef", "Karim", "Mehdi", "Amine"], ["Salma", "Ines", "Yasmine", "Nour"], ["Benali", "Mansouri", "El-Sayed"]),
    "西歐": (["Lucas", "Lukas", "Liam", "Hugo", "Noah"], ["Emma", "Léa", "Mia", "Hannah", "Sophie"], ["Müller", "Bernard", "De Vries", "Weber"]),
    "北歐": (["Oliver", "Oscar", "Elias", "William", "Soren"], ["Freja", "Astrid", "Olivia", "Amelia", "Maja"], ["Jensen", "Lindqvist", "Hansen", "Smith"]),
    "南歐": (["Mateo", "Leonardo", "Marco", "Tiago", "Nikos"], ["Sofia", "Lucia", "Giulia", "Maria", "Elena"], ["Rossi", "García", "Silva", "Papadopoulos"]),
    "東歐": (["Ivan", "Jakub", "Mateusz", "Dmitry", "Andrei"], ["Anastasia", "Zofia", "Elena", "Olena", "Katarina"], ["Nowak", "Kovalenko", "Petrov", "Popescu"]),
    "北美": (["Liam", "Noah", "Mateo", "Ethan", "Santiago"], ["Olivia", "Emma", "Sofia", "Mia", "Camila"], ["Johnson", "Hernández", "Tremblay", "Garcia"]),
    "拉美": (["Gabriel", "Mateo", "Lucas", "Sebastián", "Thiago"], ["Valentina", "Isabella", "Sofía", "Camila", "Mariana"], ["Silva", "Rodríguez", "González", "López"]),
    "大洋洲": (["Jack", "Oliver", "Noah", "Tama"], ["Charlotte", "Amelia", "Isla", "Aroha"], ["Wilson", "Taylor", "Brown", "Tui"]),
}


def load_world_demographics() -> list[dict]:
    """載入 190+ 國家與地區真實聯合國/世界銀行統計數據庫。"""
    global _world_cache
    if _world_cache is not None:
        return _world_cache
    path = DATA_DIR / "world_demographics.json"
    if not path.is_file():
        path = Path(__file__).resolve().parent.parent / "data" / "world_demographics.json"
    if not path.is_file():
        raise FileNotFoundError(f"找不到全球人口統計資料檔：{path}")
    _world_cache = json.loads(path.read_text(encoding="utf-8"))
    return _world_cache


def resolve_era(raw: Optional[str]) -> str:
    q = (raw or "").strip().lower()
    if q in ("past_1950", "1950", "過去1950", "1950年代"):
        return "past_1950"
    if q in ("past_1980", "1980", "past", "過去", "過去1980", "1980年代"):
        return "past_1980"
    if q in ("future_2050", "future", "2050", "未來"):
        return "future_2050"
    return "present"


def resolve_draw_method(raw: Optional[str]) -> str:
    q = (raw or "").strip().lower()
    if q in ("population", "pop", "總人口", "現存人口"):
        return "population"
    return "births"


def init_lifesim_tables() -> None:
    """初始化 80億分之1 人生模擬資料表與世界圖鑑收集表。"""
    db_key = str(get_db_path())
    if db_key in _initialized_ls_dbs:
        return
    with get_conn() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS life_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                channel_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                user_name TEXT NOT NULL,
                story_mode TEXT DEFAULT 'fast',
                era TEXT DEFAULT 'present',
                draw_method TEXT DEFAULT 'births',
                status TEXT NOT NULL,
                country_code TEXT NOT NULL,
                country_name TEXT NOT NULL,
                region TEXT NOT NULL,
                city TEXT NOT NULL,
                char_name TEXT NOT NULL,
                gender TEXT NOT NULL,
                birth_year INTEGER NOT NULL,
                age INTEGER DEFAULT 0,
                max_age INTEGER NOT NULL,
                family_class TEXT NOT NULL,
                focus TEXT DEFAULT 'balance',
                health INTEGER DEFAULT 70,
                happiness INTEGER DEFAULT 70,
                knowledge INTEGER DEFAULT 50,
                wealth INTEGER DEFAULT 50,
                social INTEGER DEFAULT 60,
                luck_pr INTEGER DEFAULT 50,
                birth_prob_pct REAL DEFAULT 0.0,
                occupation TEXT DEFAULT '新生兒',
                education TEXT DEFAULT '尚未入學',
                pending_event_json TEXT DEFAULT '{}',
                timeline_json TEXT DEFAULT '[]',
                death_cause TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_life_runs_channel_status
            ON life_runs(channel_id, status)
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS life_country_dex (
                user_id INTEGER NOT NULL,
                country_code TEXT NOT NULL,
                country_name TEXT NOT NULL,
                runs_count INTEGER DEFAULT 1,
                best_age INTEGER DEFAULT 0,
                best_luck_pr INTEGER DEFAULT 0,
                first_seen_at TEXT NOT NULL,
                PRIMARY KEY (user_id, country_code)
            )
        """)
        conn.commit()
    _initialized_ls_dbs.add(db_key)


def _deserialize_run(row) -> dict:
    d = dict(row)
    try:
        d["pending_event"] = json.loads(d.get("pending_event_json") or "{}")
    except Exception:
        d["pending_event"] = {}
    try:
        d["timeline"] = json.loads(d.get("timeline_json") or "[]")
    except Exception:
        d["timeline"] = []
    return d


def get_active_life_run(channel_id: int, user_id: Optional[int] = None) -> Optional[dict]:
    init_lifesim_tables()
    with get_conn() as conn:
        cursor = conn.cursor()
        if user_id is not None:
            cursor.execute(
                "SELECT * FROM life_runs WHERE channel_id = ? AND user_id = ? AND status = 'active' ORDER BY id DESC LIMIT 1",
                (channel_id, user_id),
            )
        else:
            cursor.execute(
                "SELECT * FROM life_runs WHERE channel_id = ? AND status = 'active' ORDER BY id DESC LIMIT 1",
                (channel_id,),
            )
        row = cursor.fetchone()
        return _deserialize_run(row) if row else None


def get_life_run_by_id(run_id: int) -> Optional[dict]:
    init_lifesim_tables()
    with get_conn() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM life_runs WHERE id = ?", (run_id,))
        row = cursor.fetchone()
        return _deserialize_run(row) if row else None


def get_user_country_dex(user_id: int) -> list[dict]:
    init_lifesim_tables()
    with get_conn() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM life_country_dex WHERE user_id = ? ORDER BY best_age DESC, country_code ASC",
            (user_id,),
        )
        return [dict(r) for r in cursor.fetchall()]


def _clamp_stat(val: int) -> int:
    return max(0, min(100, int(val)))


def _save_run(run: dict) -> dict:
    now_str = datetime.now(TZ_TAIPEI).strftime("%Y-%m-%d %H:%M:%S")
    run["updated_at"] = now_str
    with get_conn() as conn:
        conn.execute(
            """
            UPDATE life_runs
            SET status = ?, age = ?, max_age = ?, focus = ?, health = ?, happiness = ?,
                knowledge = ?, wealth = ?, social = ?, occupation = ?, education = ?,
                pending_event_json = ?, timeline_json = ?, death_cause = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                run["status"],
                run["age"],
                run["max_age"],
                run["focus"],
                run["health"],
                run["happiness"],
                run["knowledge"],
                run["wealth"],
                run["social"],
                run["occupation"],
                run["education"],
                json.dumps(run.get("pending_event") or {}, ensure_ascii=False),
                json.dumps(run.get("timeline") or [], ensure_ascii=False),
                run.get("death_cause", ""),
                now_str,
                run["id"],
            ),
        )
        if run["status"] == "finished":
            conn.execute(
                """
                UPDATE life_country_dex
                SET best_age = MAX(best_age, ?), best_luck_pr = MAX(best_luck_pr, ?)
                WHERE user_id = ? AND country_code = ?
                """,
                (run["age"], run["luck_pr"], run["user_id"], run["country_code"]),
            )
        conn.commit()
    return run


def draw_random_country(era: str = "present", draw_method: str = "births") -> tuple[dict, float]:
    """依全球真實出生人口或總人口統計加權抽出出生國家與該國機率 (%)。"""
    world = load_world_demographics()
    weights: list[float] = []
    for c in world:
        base = float(c["birth_weight"] if draw_method == "births" else c["pop_weight"])
        if era == "past_1950":
            if c["region"] in ("西歐", "北歐", "南歐", "東歐", "北美"):
                base *= 1.55
            elif c["region"] in ("東亞",):
                base *= 1.85
            elif c["region"] in ("撒哈拉以南非洲",):
                base *= 0.42
        elif era == "past_1980":
            if c["region"] in ("東亞",):
                base *= 1.65
            elif c["region"] in ("撒哈拉以南非洲",):
                base *= 0.65
        elif era == "future_2050":
            if c["region"] in ("撒哈拉以南非洲",):
                base *= 1.45
            elif c["region"] in ("東亞", "東歐"):
                base *= 0.68
        weights.append(max(0.05, base))

    total_w = sum(weights)
    chosen = random.choices(world, weights=weights, k=1)[0]
    idx = world.index(chosen)
    prob_pct = (weights[idx] / total_w) * 100.0
    return chosen, round(prob_pct, 3)


def _generate_local_name(country_code: str, region: str, gender: str) -> str:
    pool = REGIONAL_NAMES.get(country_code) or REGIONAL_NAMES.get(region) or REGIONAL_NAMES["西歐"]
    boys, girls, surnames = pool
    given = random.choice(boys if gender == "男" else girls)
    sur = random.choice(surnames)
    if country_code in ("TW", "CN", "JP", "KR") or region == "東亞":
        return f"{sur}{given}"
    return f"{given} {sur}"


def _build_local_event(run: dict) -> dict:
    """依年齡階段、國家貧富 tier、與年度重心產生真實寫實的人生轉折事件與選項。"""
    age = run["age"]
    country = run["country_name"]
    city = run["city"]
    tier = "high" if run["wealth"] >= 68 else ("low" if run["wealth"] <= 28 else "mid")
    focus = run.get("focus", "balance")

    if age <= 5:
        return {
            "stage": "幼年時期",
            "title": f"{age}歲・在{city}的童年光景",
            "story": (
                f"你在{country}{city}的{run['family_class']}家庭長大。"
                + ("家裡生活拮据，父母每天早出晚歸維持生計。" if tier == "low" else "家裡雖不算頂尖豪門，但還算能安穩照料你。")
            ),
            "choices": [
                {"label": "幫家裡分擔家務並聽故事", "deltas": {"happiness": 5, "social": 6, "knowledge": 3}},
                {"label": "翻找家裡的舊書或圖畫學認字", "deltas": {"knowledge": 9, "happiness": 2}},
                {"label": "整天在巷口和鄰居小孩跑跳", "deltas": {"health": 7, "happiness": 6, "social": 5}},
            ],
        }
    if age <= 12:
        return {
            "stage": "小學階段",
            "title": f"{age}歲・第一場人生起跑線",
            "story": (
                f"到了學齡，{country}當地的教育環境決定了你能接觸到的資源。"
                + ("學校教室擁擠，但你依然抓住機會坐在前排。" if tier == "low" else "你在學校裡開始展現出自己的偏好與天賦。")
            ),
            "choices": [
                {"label": "苦讀課本，爭取好成績", "deltas": {"knowledge": 11, "happiness": -3, "health": -2}},
                {"label": "參加社團或運動隊交朋友", "deltas": {"health": 6, "social": 10, "happiness": 5}},
                {"label": "幫父母做小工或學生活技能", "deltas": {"wealth": 7, "social": 4, "knowledge": -2}},
            ],
        }
    if age <= 18:
        study_bonus = 4 if focus == "study" else 0
        return {
            "stage": "升學與成年路口",
            "title": f"{age}歲・命運的分岔點",
            "story": (
                f"你即將在{country}{city}迎來成年。"
                "要繼續拚升學考試、還是提前踏入社會分擔家計，將大幅改寫未來十年的軌道。"
            ),
            "choices": [
                {"label": "全力衝刺升學／爭取獎學金", "deltas": {"knowledge": 12 + study_bonus, "wealth": -3, "happiness": -2}, "new_occ": "大學生"},
                {"label": "半工半讀或學一技之長", "deltas": {"wealth": 9, "knowledge": 5, "health": -3}, "new_occ": "學徒／服務業人員"},
                {"label": "把重心放在愛情與死黨身上", "deltas": {"social": 11, "happiness": 8, "knowledge": -2}},
            ],
        }
    if age <= 30:
        career_bonus = 4 if focus == "career" else 0
        return {
            "stage": "青年闖蕩期",
            "title": f"{age}歲・{city}的現實與夢想",
            "story": (
                f"作為{country}的「{run['occupation']}」，你面臨住房、物價與自我實現的壓力。"
                "一個跨城市發展或創業的機會突然出現在你眼前。"
            ),
            "choices": [
                {"label": "接下高薪但高壓的加班／遠調工作", "deltas": {"wealth": 13 + career_bonus, "health": -6, "happiness": -3}, "new_occ": "資深專員／工程師"},
                {"label": "穩穩工作並把下班時間留給生活與愛情", "deltas": {"happiness": 9, "social": 8, "wealth": 4}},
                {"label": "進修新技能、考證照或籌備副業", "deltas": {"knowledge": 10, "wealth": 5, "social": -2}, "new_occ": "獨立接案／技術顧問"},
            ],
        }
    if age <= 52:
        return {
            "stage": "中年中流砥柱",
            "title": f"{age}歲・責任與健康拉鋸戰",
            "story": (
                f"步入中年，你在{city}的生活逐漸定型，但經濟波動或身體警訊也開始敲門。"
                "你需要決定接下來十年的核心節奏。"
            ),
            "choices": [
                {"label": "定期體檢、改善作息與飲食", "deltas": {"health": 11, "wealth": -3, "happiness": 4}},
                {"label": "抓住升遷或投資機會再拚一把", "deltas": {"wealth": 12, "health": -6, "social": 2}, "new_occ": "部門主管／小企業主"},
                {"label": "多陪伴家人與老朋友，簡樸過日子", "deltas": {"happiness": 10, "social": 9, "wealth": 1}},
            ],
        }
    return {
        "stage": "熟齡與晚年",
        "title": f"{age}歲・歲月沉澱下來的風景",
        "story": (
            f"在{country}走過大半輩子，年輕時的執著漸漸化作平靜。"
            "身體機能雖然緩慢下降，但你仍能選擇如何度過黃金晚年。"
        ),
        "choices": [
            {"label": "每天散步運動、培養園藝或閱讀習慣", "deltas": {"health": 8, "happiness": 9, "knowledge": 4}, "new_occ": "退休長者"},
            {"label": "當社區志工、把人生經驗傳給後輩", "deltas": {"social": 11, "happiness": 8}},
            {"label": "動用積蓄去一直想看的城市旅行", "deltas": {"happiness": 13, "wealth": -8, "health": -2}},
        ],
    }


def _next_step_age(age: int) -> int:
    if age < 5:
        return 5
    if age < 11:
        return 11
    if age < 18:
        return 18
    if age < 25:
        return 25
    if age < 33:
        return 33
    if age < 43:
        return 43
    if age < 55:
        return 55
    if age < 65:
        return 65
    return age + random.randint(4, 7)


def create_life_run(
    channel_id: int,
    user_id: int,
    user_name: str,
    story_mode: str = "fast",
    era: str = "present",
    draw_method: str = "births",
) -> dict:
    """依全球真實統計抽出新生兒命運並建立人生模擬對局（結束任何同頻道舊局或同時只保留一局進行中）。"""
    init_lifesim_tables()
    existing = get_active_life_run(channel_id, user_id=user_id)
    if existing:
        raise ValueError("你在此頻道還有一場進行中的《80億分之1》人生！請先繼續該局或點「投胎重開」。")

    era_key = resolve_era(era)
    dm_key = resolve_draw_method(draw_method)
    era_cfg = ERA_CONFIG[era_key]
    country, prob_pct = draw_random_country(era=era_key, draw_method=dm_key)

    gender = random.choice(["男", "女"])
    birth_year = random.randint(*era_cfg["year_range"])
    city = random.choice(country["cities"]) if country.get("cities") else country["name_zh"]
    char_name = _generate_local_name(country["code"], country["region"], gender)

    eff_gdp = max(300.0, country["gdp_pc"] * era_cfg["gdp_mult"])
    eff_le = max(38.0, min(92.0, country["life_exp"] + era_cfg["le_delta"]))
    eff_inf = max(0.75, min(0.999, country["infant_surv"] - era_cfg["inf_pen"]))
    eff_edu = max(3.0, min(21.0, country["edu_years"] + era_cfg["edu_delta"]))

    # Socioeconomic class weighted by country GDP per capita
    if eff_gdp >= 30000:
        cls_weights = [0.06, 0.24, 0.50, 0.20]
    elif eff_gdp >= 8000:
        cls_weights = [0.16, 0.42, 0.34, 0.08]
    elif eff_gdp >= 2000:
        cls_weights = [0.34, 0.44, 0.19, 0.03]
    else:
        cls_weights = [0.58, 0.31, 0.10, 0.01]
    classes = ["貧困家庭", "勞工藍領", "小康中產", "富裕菁英"]
    family_class = random.choices(classes, weights=cls_weights, k=1)[0]
    cls_idx = classes.index(family_class)

    # Initial 5 stats (0-100) anchored to real-world country + class
    wealth_base = min(92, max(8, int(12 + (eff_gdp ** 0.36) * 1.15 + (cls_idx - 1) * 16 + random.randint(-6, 6))))
    health_base = min(95, max(25, int(45 + (eff_inf - 0.85) * 260 + cls_idx * 4 + random.randint(-7, 7))))
    know_base = min(85, max(12, int(eff_edu * 2.2 + cls_idx * 7 + random.randint(-5, 5))))
    happy_base = min(92, max(25, int(52 + cls_idx * 7 + random.randint(-10, 10))))
    social_base = min(90, max(30, int(55 + random.randint(-12, 12))))

    # Global luck PR (percentile combining country wealth, life_exp & family class)
    raw_luck = (min(eff_gdp, 75000) / 75000) * 45 + (max(0, eff_le - 50) / 36) * 30 + (cls_idx / 3) * 25
    luck_pr = max(1, min(99, int(round(raw_luck + random.randint(-3, 3)))))

    # Probabilistic lifespan (checks infant/child survival too)
    if random.random() > eff_inf:
        max_age = random.randint(0, 4)
    else:
        max_age = int(round(random.gauss(eff_le + (cls_idx - 1.5) * 2.5, 9.0)))
        max_age = max(16, min(104, max_age))

    now_str = datetime.now(TZ_TAIPEI).strftime("%Y-%m-%d %H:%M:%S")
    birth_note = f"{birth_year}年出生於{country['name_zh']}・{city}（{family_class} | 全球出生 PR {luck_pr}）"
    timeline = [{"age": 0, "title": "誕生", "detail": birth_note}]

    run_dict = {
        "channel_id": channel_id,
        "user_id": user_id,
        "user_name": user_name,
        "story_mode": "llm" if story_mode == "llm" else "fast",
        "era": era_key,
        "draw_method": dm_key,
        "status": "active",
        "country_code": country["code"],
        "country_name": country["name_zh"],
        "region": country["region"],
        "city": city,
        "char_name": char_name,
        "gender": gender,
        "birth_year": birth_year,
        "age": 0,
        "max_age": max_age,
        "family_class": family_class,
        "focus": "balance",
        "health": health_base,
        "happiness": happy_base,
        "knowledge": know_base,
        "wealth": wealth_base,
        "social": social_base,
        "luck_pr": luck_pr,
        "birth_prob_pct": prob_pct,
        "occupation": "新生兒",
        "education": "尚未入學",
        "pending_event": {},
        "timeline": timeline,
        "death_cause": "",
        "created_at": now_str,
        "updated_at": now_str,
    }

    if max_age <= 4:
        # Rare realistic infant/child mortality weighted by country's real infant survival
        run_dict["age"] = max_age
        run_dict["status"] = "finished"
        run_dict["health"] = 0
        run_dict["death_cause"] = f"因當地醫療衛生條件不足，在 {max_age} 歲時不幸染病夭折（符合該時代/地區真實嬰幼兒存活統計）。"
        run_dict["timeline"].append({"age": max_age, "title": "早夭", "detail": run_dict["death_cause"]})
    else:
        run_dict["age"] = 5
        run_dict["pending_event"] = _build_local_event(run_dict)

    with get_conn() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO life_runs
            (channel_id, user_id, user_name, story_mode, era, draw_method, status,
             country_code, country_name, region, city, char_name, gender, birth_year,
             age, max_age, family_class, focus, health, happiness, knowledge, wealth, social,
             luck_pr, birth_prob_pct, occupation, education, pending_event_json, timeline_json,
             death_cause, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_dict["channel_id"], run_dict["user_id"], run_dict["user_name"],
                run_dict["story_mode"], run_dict["era"], run_dict["draw_method"], run_dict["status"],
                run_dict["country_code"], run_dict["country_name"], run_dict["region"], run_dict["city"],
                run_dict["char_name"], run_dict["gender"], run_dict["birth_year"],
                run_dict["age"], run_dict["max_age"], run_dict["family_class"], run_dict["focus"],
                run_dict["health"], run_dict["happiness"], run_dict["knowledge"],
                run_dict["wealth"], run_dict["social"], run_dict["luck_pr"], run_dict["birth_prob_pct"],
                run_dict["occupation"], run_dict["education"],
                json.dumps(run_dict["pending_event"], ensure_ascii=False),
                json.dumps(run_dict["timeline"], ensure_ascii=False),
                run_dict["death_cause"], now_str, now_str,
            ),
        )
        run_id = cursor.lastrowid
        cursor.execute(
            """
            INSERT INTO life_country_dex (user_id, country_code, country_name, runs_count, best_age, best_luck_pr, first_seen_at)
            VALUES (?, ?, ?, 1, ?, ?, ?)
            ON CONFLICT(user_id, country_code) DO UPDATE SET
                runs_count = runs_count + 1,
                best_age = MAX(best_age, excluded.best_age),
                best_luck_pr = MAX(best_luck_pr, excluded.best_luck_pr)
            """,
            (user_id, country["code"], country["name_zh"], run_dict["age"], luck_pr, now_str),
        )
        conn.commit()
    return get_life_run_by_id(run_id)


def set_life_focus(run_id: int, user_id: int, focus: str) -> dict:
    run = get_life_run_by_id(run_id)
    if not run or run["status"] != "active":
        raise ValueError("目前沒有進行中的人生模擬。")
    if run["user_id"] != user_id:
        raise ValueError("只能調整你自己的人生重心喔！")
    if focus not in FOCUS_LABELS:
        raise ValueError("不支援的重心類型。")
    run["focus"] = focus
    return _save_run(run)


def _apply_focus_drift(run: dict, years_passed: int) -> None:
    f = run.get("focus", "balance")
    step = max(1, years_passed // 3)
    if f == "study":
        run["knowledge"] = _clamp_stat(run["knowledge"] + 2 * step)
    elif f == "career":
        run["wealth"] = _clamp_stat(run["wealth"] + 2 * step)
        run["health"] = _clamp_stat(run["health"] - step)
    elif f == "health":
        run["health"] = _clamp_stat(run["health"] + 2 * step)
    elif f == "social":
        run["social"] = _clamp_stat(run["social"] + 2 * step)
        run["happiness"] = _clamp_stat(run["happiness"] + step)
    if run["age"] >= 55:
        run["health"] = _clamp_stat(run["health"] - step)


def _finalize_death(run: dict, cause: str) -> dict:
    run["status"] = "finished"
    run["death_cause"] = cause
    run["pending_event"] = {}
    run["timeline"].append({"age": run["age"], "title": "謝幕", "detail": cause})
    return _save_run(run)


def resolve_life_step(
    run_id: int,
    user_id: int,
    choice_idx: Optional[int] = None,
    custom_deltas: Optional[dict] = None,
    custom_outcome_text: str = "",
    llm_next_event: Optional[dict] = None,
) -> tuple[dict, str]:
    """
    結算當前人生事件選擇，推動年齡與五維數值（由 Python 嚴格夾限與判定壽命終點）。
    回傳 (updated_run, summary_note)。
    """
    run = get_life_run_by_id(run_id)
    if not run or run["status"] != "active":
        raise ValueError("這場人生模擬尚未開始或已經結束了。")
    if run["user_id"] != user_id:
        raise ValueError("這段人生屬於原本的玩家，想體驗請點擊「🌍 開啟自己的人生」！")

    pe = run.get("pending_event") or {}
    choices = pe.get("choices") or []
    note_parts = []

    if custom_deltas is not None:
        # Natural-language custom choice evaluated & bounded strictly [-18, +18]
        for k in ("health", "happiness", "knowledge", "wealth", "social"):
            delta = max(-18, min(18, int(custom_deltas.get(k, 0))))
            if delta:
                run[k] = _clamp_stat(run[k] + delta)
        detail = custom_outcome_text.strip() or "你順從自己的心意做出了獨特選擇。"
        run["timeline"].append({"age": run["age"], "title": pe.get("stage", "轉折"), "detail": detail})
        note_parts.append(f"**{run['age']}歲**：{detail}")
    elif choice_idx is not None and choices:
        idx = max(0, min(len(choices) - 1, choice_idx))
        ch = choices[idx]
        deltas = ch.get("deltas") or {}
        for k in ("health", "happiness", "knowledge", "wealth", "social"):
            delta = int(deltas.get(k, 0))
            if delta:
                run[k] = _clamp_stat(run[k] + delta)
        if ch.get("new_occ"):
            run["occupation"] = ch["new_occ"]
        if run["age"] >= 18 and run["knowledge"] >= 65 and run["education"] in ("尚未入學", "義務教育"):
            run["education"] = "大學／高等專科"
        elif run["age"] >= 12 and run["education"] == "尚未入學":
            run["education"] = "中學教育"
        detail = f"選擇「{ch['label']}」"
        run["timeline"].append({"age": run["age"], "title": pe.get("stage", "里程碑"), "detail": detail})
        note_parts.append(f"**{run['age']}歲**：{detail}")

    # Check immediate mortality from health collapse
    if run["health"] <= 4:
        _finalize_death(run, f"在 {run['age']} 歲時因長期積勞與健康嚴重惡化而離世。")
        return run, "\n".join(note_parts)

    # Advance age
    prev_age = run["age"]
    next_age = _next_step_age(prev_age)
    if next_age >= run["max_age"]:
        run["age"] = run["max_age"]
        cause = (
            f"在 {run['city']} 安詳走完 {run['age']} 歲的人生旅程（{run['occupation']}）。"
            if run["age"] >= 65
            else f"在 {run['age']} 歲時因突發重病／意外於{run['city']}劃下生命句點。"
        )
        _finalize_death(run, cause)
        return run, "\n".join(note_parts)

    run["age"] = next_age
    _apply_focus_drift(run, next_age - prev_age)
    if run["occupation"] == "新生兒" and run["age"] >= 6:
        run["occupation"] = "小學生" if run["age"] < 12 else "中學生"

    if llm_next_event and isinstance(llm_next_event, dict) and llm_next_event.get("choices"):
        run["pending_event"] = llm_next_event
    else:
        run["pending_event"] = _build_local_event(run)
    _save_run(run)
    return run, "\n".join(note_parts)


def auto_finish_life(run_id: int, user_id: int) -> dict:
    """一鍵模擬過完剩餘人生（純本地演算法快速結算，0 Token）。"""
    run = get_life_run_by_id(run_id)
    if not run or run["status"] != "active":
        raise ValueError("沒有進行中的人生可模擬。")
    if run["user_id"] != user_id:
        raise ValueError("只能快轉你自己的模擬人生！")
    safety = 0
    while run["status"] == "active" and safety < 25:
        safety += 1
        choices = (run.get("pending_event") or {}).get("choices") or []
        pick = random.randrange(len(choices)) if choices else None
        run, _ = resolve_life_step(run["id"], user_id, choice_idx=pick)
    return run


def abandon_life_run(run_id: int, user_id: int) -> dict:
    run = get_life_run_by_id(run_id)
    if not run or run["status"] != "active":
        raise ValueError("沒有進行中的人生。")
    if run["user_id"] != user_id:
        raise ValueError("只能結束你自己的人生模擬。")
    return _finalize_death(run, f"玩家在 {run['age']} 歲主動結束本輪模擬（投胎重開）。")


# =============================================================
# LLM 故事編劇 Prompt 格式化 (Python 掌控真實機率與數值夾限)
# =============================================================
def format_lifesim_next_event_prompt(run: dict) -> str:
    era_lbl = ERA_CONFIG.get(run["era"], {}).get("label", run["era"])
    focus_lbl = FOCUS_LABELS.get(run.get("focus", "balance"), "均衡")
    return (
        f"你正在為《80億分之1》人生模擬遊戲擔任寫實文化編劇。\n"
        f"主角設定：{run['char_name']}（{run['gender']}），{era_lbl}出生於 **{run['country_name']}・{run['city']}**，家境：{run['family_class']}。\n"
        f"目前狀態：{run['age']}歲 | 身分：{run['occupation']} | 年度重心：{focus_lbl}\n"
        f"真實五維數值（0~100）：健康 {run['health']} / 快樂 {run['happiness']} / 知識 {run['knowledge']} / 財富 {run['wealth']} / 人際 {run['social']}\n\n"
        f"請結合【{run['country_name']}・{run['city']}】當地的真實社會文化、飲食/城市風景、經濟條件與主角目前年齡，撰寫一個這個歲數的寫實人生轉折事件，並給出 3 個合理選項。\n"
        f"⚠️ 嚴禁讓貧困角色憑空暴富或無視國情限制！deltas 每個屬性變動限於 -15 到 +15。\n"
        f"嚴格只輸出 JSON：\n"
        f'{{"title": "{run["age"]}歲・事件短標題", "story": "2~3句生動寫實的故事描述", '
        f'"choices": ['
        f'{{"label": "選項A(15字內)", "deltas": {{"knowledge": 8, "happiness": -2}}, "new_occ": ""}}, '
        f'{{"label": "選項B(15字內)", "deltas": {{"wealth": 8, "health": -3}}, "new_occ": ""}}, '
        f'{{"label": "選項C(15字內)", "deltas": {{"social": 7, "happiness": 6}}, "new_occ": ""}}'
        f']}}'
    )


def format_lifesim_custom_choice_prompt(run: dict, user_decision: str) -> str:
    pe = run.get("pending_event") or {}
    return (
        f"你正在主持《80億分之1》寫實人生模擬。\n"
        f"主角：{run['char_name']}（{run['age']}歲，{run['country_name']}・{run['city']}，{run['family_class']}，職業：{run['occupation']}）\n"
        f"數值：健康 {run['health']} / 快樂 {run['happiness']} / 知識 {run['knowledge']} / 財富 {run['wealth']} / 人際 {run['social']}\n"
        f"當前遭遇：{pe.get('story', '')}\n"
        f"玩家自訂決定：「{user_decision}」\n\n"
        f"請根據他真實的國情與目前數值評估這項決定的合理後果（不切實際的妄想如瞬間變總統或億萬富翁應該失敗並扣快樂/財富），嚴格只輸出 JSON：\n"
        f'{{"outcome": "2句以內的寫實結果描述", "deltas": {{"health": 0, "happiness": 0, "knowledge": 0, "wealth": 0, "social": 0}}, "new_occ": ""}}'
    )


def parse_lifesim_event_json(raw_text: str, age: int) -> Optional[dict]:
    text = (raw_text or "").strip()
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
        choices_raw = data.get("choices") or []
        if not isinstance(choices_raw, list) or not choices_raw:
            return None
        cleaned_choices = []
        for c in choices_raw[:3]:
            lbl = str(c.get("label") or "").strip()[:28]
            if not lbl:
                continue
            raw_d = c.get("deltas") or {}
            safe_d = {}
            for k in ("health", "happiness", "knowledge", "wealth", "social"):
                if k in raw_d:
                    safe_d[k] = max(-15, min(15, int(raw_d[k])))
            cleaned_choices.append({
                "label": lbl,
                "deltas": safe_d,
                "new_occ": str(c.get("new_occ") or "").strip()[:24],
            })
        if not cleaned_choices:
            return None
        return {
            "stage": f"{age}歲轉折",
            "title": str(data.get("title") or f"{age}歲・人生抉擇").strip()[:45],
            "story": str(data.get("story") or "").strip()[:350],
            "choices": cleaned_choices,
        }
    except Exception:
        return None


def parse_lifesim_custom_json(raw_text: str) -> tuple[str, dict]:
    text = (raw_text or "").strip()
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return "你依循直覺做出了這個選擇，命運的齒輪隨之轉動。", {"happiness": 3}
    try:
        data = json.loads(m.group(0))
        outcome = str(data.get("outcome") or "你做出了自己的選擇。").strip()[:220]
        raw_d = data.get("deltas") or {}
        safe_d = {
            k: max(-18, min(18, int(raw_d.get(k, 0))))
            for k in ("health", "happiness", "knowledge", "wealth", "social")
            if raw_d.get(k, 0) != 0
        }
        return outcome, safe_d
    except Exception:
        return "你做出了自己的選擇，人生繼續向前推進。", {"happiness": 2}


# =============================================================
# PIL 圖形渲染器：🌍 全球出生證明書 & 📜 人生總結紀念卡
# =============================================================
def _load_cjk_font(size: int, bold: bool = False) -> ImageFont.ImageFont:
    candidates = [
        "C:/Windows/Fonts/msjhbd.ttc" if bold else "C:/Windows/Fonts/msjh.ttc",
        "C:/Windows/Fonts/msjh.ttc",
        "C:/Windows/Fonts/NotoSansTC-VF.ttf",
        "C:/Windows/Fonts/segoeuib.ttf",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ]
    for p in candidates:
        try:
            return ImageFont.truetype(p, size)
        except Exception:
            continue
    return ImageFont.load_default()


def _draw_stat_bar(draw: ImageDraw.ImageDraw, x: int, y: int, w: int, label: str, val: int, color: tuple, f_lbl, f_val) -> None:
    draw.text((x, y), label, font=f_lbl, fill=(224, 228, 238))
    bar_x = x + 95
    bar_w = w - 150
    bar_h = 16
    draw.rounded_rectangle([bar_x, y + 3, bar_x + bar_w, y + 3 + bar_h], radius=7, fill=(45, 52, 68))
    fill_w = max(8, int(bar_w * max(0, min(100, val)) / 100.0)) if val > 0 else 0
    if fill_w > 0:
        draw.rounded_rectangle([bar_x, y + 3, bar_x + fill_w, y + 3 + bar_h], radius=7, fill=color)
    draw.text((bar_x + bar_w + 12, y), f"{val:>3}", font=f_val, fill=(245, 248, 255))


def render_birth_certificate_png(run: dict) -> bytes:
    """渲染高質感🌍全球出生證明書 PNG 圖片。"""
    w, h = 720, 440
    img = Image.new("RGB", (w, h), (22, 27, 38))
    draw = ImageDraw.Draw(img)

    accent = REGION_COLORS.get(run["region"], (88, 101, 242))
    draw.rounded_rectangle([14, 14, w - 14, h - 14], radius=18, fill=(28, 34, 48), outline=accent, width=3)
    draw.rounded_rectangle([14, 14, w - 14, 78], radius=18, fill=accent)
    draw.rectangle([14, 52, w - 14, 78], fill=accent)

    f_title = _load_cjk_font(26, bold=True)
    f_sub = _load_cjk_font(16, bold=False)
    f_big = _load_cjk_font(28, bold=True)
    f_body = _load_cjk_font(17, bold=False)
    f_small = _load_cjk_font(14, bold=False)

    draw.text((34, 26), "1 IN 8 BILLION // 全球出生證明書", font=f_title, fill=(255, 255, 255))
    draw.text((w - 175, 34), f"RUN #{run['id']} | {run['country_code']}", font=f_sub, fill=(240, 245, 255))

    # Left column: Identity
    draw.text((36, 98), f"{run['country_name']}・{run['city']}", font=f_big, fill=(255, 248, 220))
    draw.text((36, 138), f"地區：{run['region']}  |  出生確率：{run['birth_prob_pct']:.3f}%", font=f_small, fill=(168, 182, 208))

    info_lines = [
        f"姓名：{run['char_name']}（性別：{run['gender']}）",
        f"時代：{ERA_CONFIG.get(run['era'], {}).get('label', run['era'])}（{run['birth_year']}年生）",
        f"家境：{run['family_class']}",
        f"全球出生幸運度：PR {run['luck_pr']}",
    ]
    y_cursor = 175
    for line in info_lines:
        draw.text((36, y_cursor), line, font=f_body, fill=(232, 236, 246))
        y_cursor += 32

    # PR Badge box
    pr_col = (87, 242, 135) if run["luck_pr"] >= 70 else ((254, 231, 92) if run["luck_pr"] >= 40 else (237, 66, 69))
    draw.rounded_rectangle([36, 315, 320, 398], radius=12, fill=(20, 24, 34), outline=pr_col, width=2)
    draw.text((52, 328), f"GLOBAL LUCK PR {run['luck_pr']}", font=_load_cjk_font(22, bold=True), fill=pr_col)
    draw.text((52, 362), f"期望與當前年齡：{run['age']} 歲", font=f_small, fill=(190, 200, 218))

    # Right column: 5 Core Stat Bars
    draw.text((365, 102), "先天與初始五維指標", font=_load_cjk_font(20, bold=True), fill=(255, 255, 255))
    stats_spec = [
        ("健康 Health", run["health"], (235, 87, 87)),
        ("快樂 Happy", run["happiness"], (242, 201, 76)),
        ("知識 Know.", run["knowledge"], (86, 204, 242)),
        ("財富 Wealth", run["wealth"], (111, 207, 151)),
        ("人際 Social", run["social"], (187, 107, 217)),
    ]
    sy = 148
    for lbl, val, col in stats_spec:
        _draw_stat_bar(draw, 365, sy, 325, lbl, val, col, f_small, f_small)
        sy += 48

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def render_life_epitaph_png(run: dict, unlocked_count: int = 1, total_countries: int = 197) -> bytes:
    """渲染終局📜人生總結紀念卡 PNG 圖片。"""
    w, h = 760, 500
    img = Image.new("RGB", (w, h), (20, 22, 30))
    draw = ImageDraw.Draw(img)

    accent = REGION_COLORS.get(run["region"], (155, 93, 229))
    draw.rounded_rectangle([14, 14, w - 14, h - 14], radius=18, fill=(28, 31, 42), outline=(218, 178, 92), width=3)
    draw.rounded_rectangle([14, 14, w - 14, 76], radius=18, fill=(44, 48, 66))
    draw.rectangle([14, 52, w - 14, 76], fill=(44, 48, 66))

    f_title = _load_cjk_font(25, bold=True)
    f_big = _load_cjk_font(24, bold=True)
    f_body = _load_cjk_font(16, bold=False)
    f_small = _load_cjk_font(14, bold=False)

    draw.text((34, 26), f"人生謝幕紀念卡 // {run['char_name']}（享壽 {run['age']} 歲）", font=f_title, fill=(255, 232, 166))
    draw.text((36, 94), f"🌍 {run['country_name']}・{run['city']}  |  職業：{run['occupation']}  |  出身 PR {run['luck_pr']}", font=f_big if len(run["country_name"]) < 6 else _load_cjk_font(20, bold=True), fill=(242, 246, 255))

    cause = run.get("death_cause") or "走完了一生。"
    draw.text((36, 134), f"結局：{cause[:48]}", font=f_body, fill=(214, 192, 176))

    # Left: final 5 stats
    draw.text((36, 178), "終局五維總覽", font=_load_cjk_font(18, bold=True), fill=accent)
    stats_spec = [
        ("健康 Health", run["health"], (235, 87, 87)),
        ("快樂 Happy", run["happiness"], (242, 201, 76)),
        ("知識 Know.", run["knowledge"], (86, 204, 242)),
        ("財富 Wealth", run["wealth"], (111, 207, 151)),
        ("人際 Social", run["social"], (187, 107, 217)),
    ]
    sy = 216
    for lbl, val, col in stats_spec:
        _draw_stat_bar(draw, 36, sy, 315, lbl, val, col, f_small, f_small)
        sy += 42

    draw.rounded_rectangle([36, 434, 345, 470], radius=10, fill=(36, 42, 58), outline=accent, width=1)
    draw.text((50, 442), f"🗺️ 已解鎖世界國家圖鑑：{unlocked_count} / {total_countries}", font=f_small, fill=(224, 236, 255))

    # Right: timeline highlights
    draw.text((385, 178), "生平重大里程碑", font=_load_cjk_font(18, bold=True), fill=(255, 232, 166))
    tl = run.get("timeline") or []
    sample_tl = tl[:1] + tl[-5:] if len(tl) > 6 else tl
    ty = 214
    for item in sample_tl[:6]:
        line_txt = f"• {item.get('age', 0)}歲 {item.get('title', '')}：{item.get('detail', '')}"
        if len(line_txt) > 26:
            line_txt = line_txt[:25] + "…"
        draw.text((385, ty), line_txt, font=f_small, fill=(220, 226, 238))
        ty += 38

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def is_lifesim_intent(text: str) -> bool:
    """判斷玩家口語是否與《80億分之1》人生模擬相關，供動態工具與上下文注入使用。"""
    if not text:
        return False
    low = text.lower()
    keywords = (
        "80億", "八十億", "1in8b", "投胎", "重開人生", "轉生", "出生證明",
        "人生模擬", "人生進度", "國家圖鑑", "世界圖鑑", "這輩子", "下輩子",
        "自動過完", "選a", "選b", "選c", "選1", "選2", "選3", "模擬人生",
    )
    return any(k in low for k in keywords)


def set_pending_event(run_id: int, pending_event: dict) -> dict:
    """更新進行中人生的下一個待抉擇事件（供 LLM 沉浸劇情模式覆寫本地範本）。"""
    run = get_life_run_by_id(run_id)
    if not run or run["status"] != "active":
        return run or {}
    if isinstance(pending_event, dict) and pending_event.get("choices"):
        run["pending_event"] = pending_event
        return _save_run(run)
    return run


def format_lifesim_status_for_llm(channel_id: int, user_id: Optional[int] = None) -> str:
    """產出當前頻道/玩家進行中《80億分之1》人生的權威狀態摘要，供 Gemini 參考並防幻覺。"""
    run = get_active_life_run(channel_id, user_id=user_id)
    if not run:
        return ""
    pe = run.get("pending_event") or {}
    choices = pe.get("choices") or []
    choice_lines = []
    for idx, ch in enumerate(choices[:3], start=1):
        lbl = ch.get("label", f"選項{idx}")
        letter = chr(64 + idx)
        choice_lines.append(f"  - [{letter}] {lbl}")
    unlocked = len(get_user_country_dex(run["user_id"]))
    story_mode_lbl = "Gemini 沉浸劇情版" if run["story_mode"] == "llm" else "極速統計版"
    return (
        f"🌍【《80億分之1》進行中人生 #run_id={run['id']}（玩家：{run['user_name']}，{story_mode_lbl}）】\n"
        f"• 姓名/性別/出身：{run['char_name']}（{run['gender']}）｜{run['country_name']}・{run['city']}（{run['birth_year']}年生｜家境：{run['family_class']}｜幸運 PR {run['luck_pr']}）\n"
        f"• 年齡/職業/重心：{run['age']} 歲｜{run['occupation']}｜重心：{FOCUS_LABELS.get(run.get('focus', 'balance'), '⚖️ 均衡發展')}\n"
        f"• 五維權威數值：健康 {run['health']} / 快樂 {run['happiness']} / 知識 {run['knowledge']} / 財富 {run['wealth']} / 人際 {run['social']}\n"
        f"• 當前遭遇：《{pe.get('title', '')}》{pe.get('story', '')}\n"
        f"• 可選預設選項：\n" + ("\n".join(choice_lines) if choice_lines else "  （無）") + "\n"
        f"• 已解鎖國家圖鑑：{unlocked} / 197"
    )
