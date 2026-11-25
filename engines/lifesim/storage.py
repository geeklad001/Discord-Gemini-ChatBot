"""SQLite persistence, schema migration, country dex, and leaderboards for 1in8B."""

from __future__ import annotations

import contextvars
import json
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional

from engines.assistant_db import DATA_DIR, TZ_TAIPEI, get_conn, get_db_path

_initialized_ls_dbs: set[str] = set()
_ls_init_lock = threading.Lock()
_world_cache: Optional[list[dict]] = None

pending_lifesim_card_updates: contextvars.ContextVar[Optional[list[dict]]] = contextvars.ContextVar(
    "pending_lifesim_card_updates", default=None
)

_LIFE_RUN_MIGRATIONS: tuple[tuple[str, str], ...] = (
    ("urban_rural", "TEXT DEFAULT 'urban'"),
    ("traits_json", "TEXT DEFAULT '[]'"),
    ("relationships_json", "TEXT DEFAULT '{}'"),
    ("current_country_code", "TEXT DEFAULT ''"),
    ("current_country_name", "TEXT DEFAULT ''"),
    ("current_city", "TEXT DEFAULT ''"),
    ("stat_history_json", "TEXT DEFAULT '[]'"),
    ("seen_events_json", "TEXT DEFAULT '[]'"),
    ("side_action_age", "INTEGER DEFAULT -1"),
    ("titles_json", "TEXT DEFAULT '[]'"),
)


def load_world_demographics() -> list[dict]:
    """載入 197 國真實聯合國/世界銀行統計數據庫。"""
    global _world_cache
    if _world_cache is not None:
        return _world_cache
    path = DATA_DIR / "world_demographics.json"
    if not path.is_file():
        path = Path(__file__).resolve().parent.parent.parent / "data" / "world_demographics.json"
    if not path.is_file():
        raise FileNotFoundError(f"找不到全球人口統計資料檔：{path}")
    _world_cache = json.loads(path.read_text(encoding="utf-8"))
    return _world_cache


load_demographics = load_world_demographics


def _safe_json_loads(raw: Optional[str], default):
    if not raw:
        return default() if callable(default) else default
    try:
        parsed = json.loads(raw)
        expected = dict if isinstance(default, dict) or default is dict else list
        if isinstance(parsed, expected):
            return parsed
    except Exception:
        pass
    return default() if callable(default) else default


def init_lifesim_tables() -> None:
    """初始化《80億分之1》資料表並安全遷移舊版資料庫欄位。"""
    db_key = str(get_db_path())
    with _ls_init_lock:
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
                    urban_rural TEXT DEFAULT 'urban',
                    traits_json TEXT DEFAULT '[]',
                    relationships_json TEXT DEFAULT '{}',
                    current_country_code TEXT DEFAULT '',
                    current_country_name TEXT DEFAULT '',
                    current_city TEXT DEFAULT '',
                    stat_history_json TEXT DEFAULT '[]',
                    seen_events_json TEXT DEFAULT '[]',
                    side_action_age INTEGER DEFAULT -1,
                    titles_json TEXT DEFAULT '[]',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)
            cursor.execute("PRAGMA table_info(life_runs)")
            existing_cols = {row[1] for row in cursor.fetchall()}
            for col_name, col_def in _LIFE_RUN_MIGRATIONS:
                if col_name not in existing_cols:
                    cursor.execute(f"ALTER TABLE life_runs ADD COLUMN {col_name} {col_def}")

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
                    user_name TEXT DEFAULT '',
                    PRIMARY KEY (user_id, country_code)
                )
            """)
            cursor.execute("PRAGMA table_info(life_country_dex)")
            dex_cols = {row[1] for row in cursor.fetchall()}
            if "user_name" not in dex_cols:
                cursor.execute("ALTER TABLE life_country_dex ADD COLUMN user_name TEXT DEFAULT ''")
            conn.commit()
        _initialized_ls_dbs.add(db_key)


def _deserialize_life_run(row) -> dict:
    d = dict(row)
    d["pending_event"] = _safe_json_loads(d.get("pending_event_json"), dict)
    d["timeline"] = _safe_json_loads(d.get("timeline_json"), list)
    d["traits"] = _safe_json_loads(d.get("traits_json"), list)

    rel_raw = _safe_json_loads(d.get("relationships_json"), dict)
    d["relationships"] = {
        "partner": rel_raw.get("partner"),
        "married": bool(rel_raw.get("married", False)),
        "children": int(rel_raw.get("children", 0) or 0),
    }

    d["stat_history"] = _safe_json_loads(d.get("stat_history_json"), list)
    d["seen_events"] = [str(x) for x in _safe_json_loads(d.get("seen_events_json"), list)]
    d["titles"] = [str(x) for x in _safe_json_loads(d.get("titles_json"), list)]
    d["urban_rural"] = (d.get("urban_rural") or "urban").strip() or "urban"
    d["current_country_code"] = (d.get("current_country_code") or "").strip() or d["country_code"]
    d["current_country_name"] = (d.get("current_country_name") or "").strip() or d["country_name"]
    d["current_city"] = (d.get("current_city") or "").strip() or d["city"]
    if d.get("side_action_age") is None:
        d["side_action_age"] = -1
    return d


_deserialize_run = _deserialize_life_run


def unlock_country_in_dex(
    conn_or_none: Optional[sqlite3.Connection],
    user_id: int,
    user_name: str,
    country_code: str,
    country_name: str,
    age: int,
    luck_pr: int,
    *,
    increment_runs: bool = True,
) -> None:
    """將出生或移居解鎖的國家寫入玩家世界國家圖鑑。"""
    if not country_code:
        return
    now_str = datetime.now(TZ_TAIPEI).strftime("%Y-%m-%d %H:%M:%S")
    inc_expr = "runs_count = runs_count + 1," if increment_runs else ""
    sql = f"""
        INSERT INTO life_country_dex
            (user_id, country_code, country_name, runs_count, best_age, best_luck_pr, first_seen_at, user_name)
        VALUES (?, ?, ?, 1, ?, ?, ?, ?)
        ON CONFLICT(user_id, country_code) DO UPDATE SET
            {inc_expr}
            country_name = CASE WHEN excluded.country_name != '' THEN excluded.country_name ELSE life_country_dex.country_name END,
            best_age = MAX(best_age, excluded.best_age),
            best_luck_pr = MAX(best_luck_pr, excluded.best_luck_pr),
            user_name = CASE WHEN excluded.user_name != '' THEN excluded.user_name ELSE life_country_dex.user_name END
    """
    params = (user_id, country_code, country_name, int(age), int(luck_pr), now_str, user_name or "")
    if conn_or_none is not None:
        conn_or_none.execute(sql, params)
        return
    init_lifesim_tables()
    with get_conn() as conn:
        conn.execute(sql, params)
        conn.commit()


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
        return _deserialize_life_run(row) if row else None


def get_life_run_by_id(run_id: int) -> Optional[dict]:
    init_lifesim_tables()
    with get_conn() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM life_runs WHERE id = ?", (run_id,))
        row = cursor.fetchone()
        return _deserialize_life_run(row) if row else None


get_life_run = get_life_run_by_id


def get_user_country_dex(user_id: int) -> list[dict]:
    init_lifesim_tables()
    with get_conn() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM life_country_dex WHERE user_id = ? ORDER BY best_age DESC, country_code ASC",
            (user_id,),
        )
        return [dict(r) for r in cursor.fetchall()]


def save_life_run(run: dict) -> dict:
    """將人生模擬最新狀態寫回 SQLite，並在終局時同步圖鑑最高紀錄。"""
    now_str = datetime.now(TZ_TAIPEI).strftime("%Y-%m-%d %H:%M:%S")
    run["updated_at"] = now_str
    with get_conn() as conn:
        conn.execute(
            """
            UPDATE life_runs
            SET status = ?, age = ?, max_age = ?, focus = ?, health = ?, happiness = ?,
                knowledge = ?, wealth = ?, social = ?, occupation = ?, education = ?,
                pending_event_json = ?, timeline_json = ?, death_cause = ?,
                urban_rural = ?, traits_json = ?, relationships_json = ?,
                current_country_code = ?, current_country_name = ?, current_city = ?,
                stat_history_json = ?, seen_events_json = ?, side_action_age = ?,
                titles_json = ?, updated_at = ?
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
                run.get("urban_rural", "urban"),
                json.dumps(run.get("traits") or [], ensure_ascii=False),
                json.dumps(run.get("relationships") or {}, ensure_ascii=False),
                run.get("current_country_code") or run["country_code"],
                run.get("current_country_name") or run["country_name"],
                run.get("current_city") or run["city"],
                json.dumps(run.get("stat_history") or [], ensure_ascii=False),
                json.dumps(run.get("seen_events") or [], ensure_ascii=False),
                int(run.get("side_action_age", -1)),
                json.dumps(run.get("titles") or [], ensure_ascii=False),
                now_str,
                run["id"],
            ),
        )
        if run["status"] == "finished":
            unlock_country_in_dex(
                conn,
                run["user_id"],
                run.get("user_name", ""),
                run["country_code"],
                run["country_name"],
                run["age"],
                run["luck_pr"],
                increment_runs=False,
            )
            cur_code = run.get("current_country_code") or run["country_code"]
            if cur_code != run["country_code"]:
                unlock_country_in_dex(
                    conn,
                    run["user_id"],
                    run.get("user_name", ""),
                    cur_code,
                    run.get("current_country_name") or run["country_name"],
                    run["age"],
                    run["luck_pr"],
                    increment_runs=False,
                )
        conn.commit()
    return run


_save_run = save_life_run


def get_lifesim_leaderboard(metric: str = "age", limit: int = 10) -> list[dict]:
    """取得伺服器《80億分之1》三大排行榜：長壽榜 (age)、寒門逆襲榜 (comeback)、世界圖鑑榜 (dex)。"""
    init_lifesim_tables()
    safe_limit = max(1, min(50, int(limit or 10)))
    key = (metric or "age").strip().lower()

    with get_conn() as conn:
        cursor = conn.cursor()
        if key == "dex":
            cursor.execute(
                """
                SELECT
                    d.user_id,
                    COALESCE(
                        NULLIF(MAX(d.user_name), ''),
                        (SELECT r.user_name FROM life_runs r WHERE r.user_id = d.user_id ORDER BY r.id DESC LIMIT 1),
                        '玩家 ' || d.user_id
                    ) AS user_name,
                    COUNT(DISTINCT d.country_code) AS unlocked_count,
                    MAX(d.best_age) AS best_age,
                    SUM(d.runs_count) AS total_runs
                FROM life_country_dex d
                GROUP BY d.user_id
                ORDER BY unlocked_count DESC, best_age DESC, total_runs DESC
                LIMIT ?
                """,
                (safe_limit,),
            )
            return [dict(r) for r in cursor.fetchall()]

        cursor.execute("SELECT * FROM life_runs WHERE status = 'finished'")
        finished_runs = [_deserialize_life_run(r) for r in cursor.fetchall()]

    enriched: list[dict] = []
    for r in finished_runs:
        avg_stat = round(
            (r["health"] + r["happiness"] + r["knowledge"] + r["wealth"] + r["social"]) / 5.0,
            1,
        )
        comeback_score = round(avg_stat - r["luck_pr"] * 0.45, 1)
        enriched.append({
            "run_id": r["id"],
            "user_id": r["user_id"],
            "user_name": r["user_name"],
            "char_name": r["char_name"],
            "country_code": r["country_code"],
            "country_name": r["country_name"],
            "current_country_name": r["current_country_name"],
            "age": r["age"],
            "luck_pr": r["luck_pr"],
            "avg_stat": avg_stat,
            "comeback_score": comeback_score,
            "occupation": r["occupation"],
            "titles": r["titles"],
            "story_mode": r["story_mode"],
        })

    if key == "comeback":
        enriched.sort(key=lambda x: (x["comeback_score"], x["avg_stat"], -x["luck_pr"], x["age"]), reverse=True)
    else:
        enriched.sort(key=lambda x: (x["age"], x["avg_stat"], -x["run_id"]), reverse=True)
    return enriched[:safe_limit]
