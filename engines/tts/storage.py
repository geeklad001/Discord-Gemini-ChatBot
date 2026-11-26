"""SQLite persistence for TTS preferences, guild settings, clone requests, user clones, and bans."""

from __future__ import annotations

import json
import threading
from datetime import datetime
from typing import Any, Optional

from engines import assistant_db
from engines.tts.catalog import (
    DEFAULT_EDGE_VOICE,
    DEFAULT_JP_EDGE_VOICE,
    clamp_pitch_hz,
    clamp_rate_pct,
)

_initialized_tts_dbs: set[str] = set()
_tts_db_init_lock = threading.Lock()


def init_tts_tables() -> None:
    """Initialize TTS user preference and guild settings tables once per DB instance."""
    db_path = assistant_db.get_db_path()
    db_key = str(db_path)
    if db_key in _initialized_tts_dbs and db_path.is_file():
        return
    with _tts_db_init_lock:
        db_path = assistant_db.get_db_path()
        db_key = str(db_path)
        if db_key in _initialized_tts_dbs and db_path.is_file():
            return
        with assistant_db.get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS tts_user_prefs (
                    user_id INTEGER PRIMARY KEY,
                    voice_id TEXT NOT NULL,
                    rate_pct INTEGER NOT NULL DEFAULT 0,
                    pitch_hz INTEGER NOT NULL DEFAULT 0,
                    proxy_muted INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL
                )
                """
            )
            cursor.execute("PRAGMA table_info(tts_user_prefs)")
            existing_user_cols = {r[1] for r in cursor.fetchall()}
            if "proxy_muted" not in existing_user_cols:
                cursor.execute("ALTER TABLE tts_user_prefs ADD COLUMN proxy_muted INTEGER NOT NULL DEFAULT 0")
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS tts_guild_settings (
                    guild_id INTEGER PRIMARY KEY,
                    proxy_enabled INTEGER NOT NULL DEFAULT 1,
                    ai_read_jp INTEGER NOT NULL DEFAULT 1,
                    ai_read_general INTEGER NOT NULL DEFAULT 0,
                    require_in_vc INTEGER NOT NULL DEFAULT 0,
                    allowed_channels TEXT NOT NULL DEFAULT '[]',
                    ignored_channels TEXT NOT NULL DEFAULT '[]',
                    ai_voice_id TEXT NOT NULL DEFAULT '',
                    ai_rate_pct INTEGER NOT NULL DEFAULT 0,
                    ai_pitch_hz INTEGER NOT NULL DEFAULT 0,
                    ai_jp_voice_id TEXT NOT NULL DEFAULT '',
                    ai_jp_rate_pct INTEGER NOT NULL DEFAULT 0,
                    ai_jp_pitch_hz INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL
                )
                """
            )
            # 舊版資料庫平滑遷移：補齊同語音房開關與機器人自身 AI 聲線欄位
            cursor.execute("PRAGMA table_info(tts_guild_settings)")
            existing_cols = {r[1] for r in cursor.fetchall()}
            for col_name, col_def in (
                ("require_in_vc", "INTEGER NOT NULL DEFAULT 0"),
                ("ai_voice_id", "TEXT NOT NULL DEFAULT ''"),
                ("ai_rate_pct", "INTEGER NOT NULL DEFAULT 0"),
                ("ai_pitch_hz", "INTEGER NOT NULL DEFAULT 0"),
                ("ai_jp_voice_id", "TEXT NOT NULL DEFAULT ''"),
                ("ai_jp_rate_pct", "INTEGER NOT NULL DEFAULT 0"),
                ("ai_jp_pitch_hz", "INTEGER NOT NULL DEFAULT 0"),
            ):
                if col_name not in existing_cols:
                    cursor.execute(f"ALTER TABLE tts_guild_settings ADD COLUMN {col_name} {col_def}")

            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS tts_clone_requests (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER,
                    channel_id INTEGER,
                    user_id INTEGER NOT NULL,
                    user_name TEXT NOT NULL,
                    slot INTEGER NOT NULL DEFAULT 1,
                    voice_name TEXT NOT NULL,
                    prompt_text TEXT NOT NULL,
                    sanitized_audio_path TEXT NOT NULL,
                    duration_sec REAL NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    reviewer_id INTEGER,
                    review_note TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS tts_user_clones (
                    voice_id TEXT PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    slot INTEGER NOT NULL,
                    voice_name TEXT NOT NULL,
                    prompt_text TEXT NOT NULL,
                    is_public INTEGER NOT NULL DEFAULT 0,
                    approved_by INTEGER,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS tts_clone_bans (
                    user_id INTEGER PRIMARY KEY,
                    reason TEXT NOT NULL DEFAULT '',
                    banned_by INTEGER NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.commit()
        _initialized_tts_dbs.add(db_key)


def _now_str() -> str:
    return datetime.now(assistant_db.TZ_TAIPEI).strftime("%Y-%m-%d %H:%M:%S")


def get_user_voice_pref(user_id: int, default_voice: str = DEFAULT_EDGE_VOICE) -> dict[str, Any]:
    init_tts_tables()
    with assistant_db.get_conn() as conn:
        row = conn.execute(
            "SELECT user_id, voice_id, rate_pct, pitch_hz, proxy_muted FROM tts_user_prefs WHERE user_id = ?",
            (int(user_id),),
        ).fetchone()
    if row is None:
        return {
            "user_id": int(user_id),
            "voice_id": default_voice,
            "rate_pct": 0,
            "pitch_hz": 0,
            "proxy_muted": False,
            "is_custom": False,
        }
    return {
        "user_id": int(row["user_id"]),
        "voice_id": str(row["voice_id"] or default_voice),
        "rate_pct": clamp_rate_pct(int(row["rate_pct"] or 0)),
        "pitch_hz": clamp_pitch_hz(int(row["pitch_hz"] or 0)),
        "proxy_muted": bool(row["proxy_muted"]),
        "is_custom": True,
    }


def set_user_voice_pref(
    user_id: int,
    *,
    voice_id: Optional[str] = None,
    rate_pct: Optional[int] = None,
    pitch_hz: Optional[int] = None,
    proxy_muted: Optional[bool] = None,
    default_voice: str = DEFAULT_EDGE_VOICE,
) -> dict[str, Any]:
    init_tts_tables()
    now = _now_str()
    with assistant_db.get_conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT voice_id, rate_pct, pitch_hz, proxy_muted FROM tts_user_prefs WHERE user_id = ?",
            (int(user_id),),
        ).fetchone()
        if row is None:
            cur_voice = default_voice
            cur_rate = 0
            cur_pitch = 0
            cur_muted = False
        else:
            cur_voice = str(row["voice_id"] or default_voice)
            cur_rate = clamp_rate_pct(int(row["rate_pct"] or 0))
            cur_pitch = clamp_pitch_hz(int(row["pitch_hz"] or 0))
            cur_muted = bool(row["proxy_muted"])

        new_voice = cur_voice if voice_id is None else (voice_id.strip() or default_voice)
        new_rate = cur_rate if rate_pct is None else clamp_rate_pct(rate_pct)
        new_pitch = cur_pitch if pitch_hz is None else clamp_pitch_hz(pitch_hz)
        new_muted = cur_muted if proxy_muted is None else bool(proxy_muted)
        conn.execute(
            """
            INSERT INTO tts_user_prefs (user_id, voice_id, rate_pct, pitch_hz, proxy_muted, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                voice_id = excluded.voice_id,
                rate_pct = excluded.rate_pct,
                pitch_hz = excluded.pitch_hz,
                proxy_muted = excluded.proxy_muted,
                updated_at = excluded.updated_at
            """,
            (int(user_id), new_voice, new_rate, new_pitch, 1 if new_muted else 0, now),
        )
    return {
        "user_id": int(user_id),
        "voice_id": new_voice,
        "rate_pct": new_rate,
        "pitch_hz": new_pitch,
        "proxy_muted": new_muted,
        "is_custom": True,
    }


def reset_user_voice_pref(user_id: int) -> None:
    init_tts_tables()
    with assistant_db.get_conn() as conn:
        conn.execute("DELETE FROM tts_user_prefs WHERE user_id = ?", (int(user_id),))


def _parse_channel_id_list(raw_json: Optional[str]) -> list[int]:
    if not raw_json:
        return []
    try:
        items = json.loads(raw_json)
    except (TypeError, ValueError):
        return []
    if not isinstance(items, list):
        return []
    dedup: list[int] = []
    for item in items:
        try:
            cid = int(item)
        except (TypeError, ValueError):
            continue
        if cid not in dedup:
            dedup.append(cid)
    return dedup


def _dedup_channel_ids(channel_ids: list[int]) -> list[int]:
    dedup: list[int] = []
    for item in channel_ids:
        cid = int(item)
        if cid not in dedup:
            dedup.append(cid)
    return dedup


def _format_guild_tts_settings(
    guild_id: int,
    row: Any,
    *,
    default_ai_jp: bool = True,
    default_ai_general: bool = False,
    default_ai_voice: str = DEFAULT_EDGE_VOICE,
    default_ai_jp_voice: str = DEFAULT_JP_EDGE_VOICE,
) -> dict[str, Any]:
    if row is None:
        return {
            "guild_id": int(guild_id),
            "proxy_enabled": True,
            "ai_read_jp": bool(default_ai_jp),
            "ai_read_general": bool(default_ai_general),
            "require_in_vc": False,
            "allowed_channels": [],
            "ignored_channels": [],
            "ai_voice_id": default_ai_voice,
            "ai_rate_pct": 0,
            "ai_pitch_hz": 0,
            "ai_voice_is_custom": False,
            "ai_jp_voice_id": default_ai_jp_voice,
            "ai_jp_rate_pct": 0,
            "ai_jp_pitch_hz": 0,
            "ai_jp_voice_is_custom": False,
        }
    raw_ai_voice = str(row["ai_voice_id"] or "").strip()
    ai_rate = clamp_rate_pct(int(row["ai_rate_pct"] or 0))
    ai_pitch = clamp_pitch_hz(int(row["ai_pitch_hz"] or 0))
    raw_ai_jp_voice = str(row["ai_jp_voice_id"] or "").strip()
    ai_jp_rate = clamp_rate_pct(int(row["ai_jp_rate_pct"] or 0))
    ai_jp_pitch = clamp_pitch_hz(int(row["ai_jp_pitch_hz"] or 0))
    return {
        "guild_id": int(row["guild_id"]),
        "proxy_enabled": bool(row["proxy_enabled"]),
        "ai_read_jp": bool(row["ai_read_jp"]),
        "ai_read_general": bool(row["ai_read_general"]),
        "require_in_vc": bool(row["require_in_vc"]),
        "allowed_channels": _parse_channel_id_list(row["allowed_channels"]),
        "ignored_channels": _parse_channel_id_list(row["ignored_channels"]),
        "ai_voice_id": raw_ai_voice or default_ai_voice,
        "ai_rate_pct": ai_rate,
        "ai_pitch_hz": ai_pitch,
        "ai_voice_is_custom": bool(raw_ai_voice) or ai_rate != 0 or ai_pitch != 0,
        "ai_jp_voice_id": raw_ai_jp_voice or default_ai_jp_voice,
        "ai_jp_rate_pct": ai_jp_rate,
        "ai_jp_pitch_hz": ai_jp_pitch,
        "ai_jp_voice_is_custom": bool(raw_ai_jp_voice) or ai_jp_rate != 0 or ai_jp_pitch != 0,
    }


def _fetch_guild_tts_row(conn: Any, guild_id: int) -> Any:
    return conn.execute(
        """
        SELECT guild_id, proxy_enabled, ai_read_jp, ai_read_general, require_in_vc,
               allowed_channels, ignored_channels,
               ai_voice_id, ai_rate_pct, ai_pitch_hz, ai_jp_voice_id, ai_jp_rate_pct, ai_jp_pitch_hz
        FROM tts_guild_settings
        WHERE guild_id = ?
        """,
        (int(guild_id),),
    ).fetchone()


def get_guild_tts_settings(
    guild_id: int,
    *,
    default_ai_jp: bool = True,
    default_ai_general: bool = False,
    default_ai_voice: str = DEFAULT_EDGE_VOICE,
    default_ai_jp_voice: str = DEFAULT_JP_EDGE_VOICE,
) -> dict[str, Any]:
    init_tts_tables()
    with assistant_db.get_conn() as conn:
        row = _fetch_guild_tts_row(conn, guild_id)
    return _format_guild_tts_settings(
        guild_id,
        row,
        default_ai_jp=default_ai_jp,
        default_ai_general=default_ai_general,
        default_ai_voice=default_ai_voice,
        default_ai_jp_voice=default_ai_jp_voice,
    )


def update_guild_tts_settings(
    guild_id: int,
    *,
    proxy_enabled: Optional[bool] = None,
    ai_read_jp: Optional[bool] = None,
    ai_read_general: Optional[bool] = None,
    require_in_vc: Optional[bool] = None,
    allowed_channels: Optional[list[int]] = None,
    ignored_channels: Optional[list[int]] = None,
    ai_voice_id: Optional[str] = None,
    ai_rate_pct: Optional[int] = None,
    ai_pitch_hz: Optional[int] = None,
    ai_jp_voice_id: Optional[str] = None,
    ai_jp_rate_pct: Optional[int] = None,
    ai_jp_pitch_hz: Optional[int] = None,
    default_ai_jp: bool = True,
    default_ai_general: bool = False,
    default_ai_voice: str = DEFAULT_EDGE_VOICE,
    default_ai_jp_voice: str = DEFAULT_JP_EDGE_VOICE,
) -> dict[str, Any]:
    init_tts_tables()
    now = _now_str()
    with assistant_db.get_conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = _fetch_guild_tts_row(conn, guild_id)
        if row is None:
            cur_proxy = True
            cur_ai_jp = bool(default_ai_jp)
            cur_ai_gen = bool(default_ai_general)
            cur_req_vc = False
            cur_allowed: list[int] = []
            cur_ignored: list[int] = []
            cur_ai_voice = ""
            cur_ai_rate = 0
            cur_ai_pitch = 0
            cur_ai_jp_voice = ""
            cur_ai_jp_rate = 0
            cur_ai_jp_pitch = 0
        else:
            cur_proxy = bool(row["proxy_enabled"])
            cur_ai_jp = bool(row["ai_read_jp"])
            cur_ai_gen = bool(row["ai_read_general"])
            cur_req_vc = bool(row["require_in_vc"])
            cur_allowed = _parse_channel_id_list(row["allowed_channels"])
            cur_ignored = _parse_channel_id_list(row["ignored_channels"])
            cur_ai_voice = str(row["ai_voice_id"] or "").strip()
            cur_ai_rate = clamp_rate_pct(int(row["ai_rate_pct"] or 0))
            cur_ai_pitch = clamp_pitch_hz(int(row["ai_pitch_hz"] or 0))
            cur_ai_jp_voice = str(row["ai_jp_voice_id"] or "").strip()
            cur_ai_jp_rate = clamp_rate_pct(int(row["ai_jp_rate_pct"] or 0))
            cur_ai_jp_pitch = clamp_pitch_hz(int(row["ai_jp_pitch_hz"] or 0))

        new_proxy = cur_proxy if proxy_enabled is None else bool(proxy_enabled)
        new_ai_jp = cur_ai_jp if ai_read_jp is None else bool(ai_read_jp)
        new_ai_general = cur_ai_gen if ai_read_general is None else bool(ai_read_general)
        new_req_vc = cur_req_vc if require_in_vc is None else bool(require_in_vc)
        new_allowed = cur_allowed if allowed_channels is None else _dedup_channel_ids(allowed_channels)
        new_ignored = cur_ignored if ignored_channels is None else _dedup_channel_ids(ignored_channels)
        new_ai_voice = cur_ai_voice if ai_voice_id is None else ai_voice_id.strip()
        new_ai_rate = cur_ai_rate if ai_rate_pct is None else clamp_rate_pct(ai_rate_pct)
        new_ai_pitch = cur_ai_pitch if ai_pitch_hz is None else clamp_pitch_hz(ai_pitch_hz)
        new_ai_jp_voice = cur_ai_jp_voice if ai_jp_voice_id is None else ai_jp_voice_id.strip()
        new_ai_jp_rate = cur_ai_jp_rate if ai_jp_rate_pct is None else clamp_rate_pct(ai_jp_rate_pct)
        new_ai_jp_pitch = cur_ai_jp_pitch if ai_jp_pitch_hz is None else clamp_pitch_hz(ai_jp_pitch_hz)

        conn.execute(
            """
            INSERT INTO tts_guild_settings (
                guild_id, proxy_enabled, ai_read_jp, ai_read_general, require_in_vc,
                allowed_channels, ignored_channels,
                ai_voice_id, ai_rate_pct, ai_pitch_hz, ai_jp_voice_id, ai_jp_rate_pct, ai_jp_pitch_hz, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(guild_id) DO UPDATE SET
                proxy_enabled = excluded.proxy_enabled,
                ai_read_jp = excluded.ai_read_jp,
                ai_read_general = excluded.ai_read_general,
                require_in_vc = excluded.require_in_vc,
                allowed_channels = excluded.allowed_channels,
                ignored_channels = excluded.ignored_channels,
                ai_voice_id = excluded.ai_voice_id,
                ai_rate_pct = excluded.ai_rate_pct,
                ai_pitch_hz = excluded.ai_pitch_hz,
                ai_jp_voice_id = excluded.ai_jp_voice_id,
                ai_jp_rate_pct = excluded.ai_jp_rate_pct,
                ai_jp_pitch_hz = excluded.ai_jp_pitch_hz,
                updated_at = excluded.updated_at
            """,
            (
                int(guild_id),
                int(new_proxy),
                int(new_ai_jp),
                int(new_ai_general),
                int(new_req_vc),
                json.dumps(new_allowed),
                json.dumps(new_ignored),
                new_ai_voice,
                new_ai_rate,
                new_ai_pitch,
                new_ai_jp_voice,
                new_ai_jp_rate,
                new_ai_jp_pitch,
                now,
            ),
        )
        updated_row = _fetch_guild_tts_row(conn, guild_id)
    return _format_guild_tts_settings(
        guild_id,
        updated_row,
        default_ai_jp=default_ai_jp,
        default_ai_general=default_ai_general,
        default_ai_voice=default_ai_voice,
        default_ai_jp_voice=default_ai_jp_voice,
    )


def reset_guild_ai_voice(
    guild_id: int,
    *,
    target: str = "general",
    default_ai_jp: bool = True,
    default_ai_general: bool = False,
    default_ai_voice: str = DEFAULT_EDGE_VOICE,
    default_ai_jp_voice: str = DEFAULT_JP_EDGE_VOICE,
) -> dict[str, Any]:
    """Reset the guild's Bot AI voice override ('general', 'jp', or 'all') back to defaults."""
    tgt = (target or "general").strip().lower()
    kwargs: dict[str, Any] = {
        "default_ai_jp": default_ai_jp,
        "default_ai_general": default_ai_general,
        "default_ai_voice": default_ai_voice,
        "default_ai_jp_voice": default_ai_jp_voice,
    }
    if tgt in ("jp", "all"):
        kwargs.update(ai_jp_voice_id="", ai_jp_rate_pct=0, ai_jp_pitch_hz=0)
    if tgt in ("general", "all"):
        kwargs.update(ai_voice_id="", ai_rate_pct=0, ai_pitch_hz=0)
    return update_guild_tts_settings(guild_id, **kwargs)


def _row_to_clone_request(row: Any) -> dict[str, Any]:
    return {
        "id": int(row["id"]),
        "guild_id": row["guild_id"],
        "channel_id": row["channel_id"],
        "user_id": int(row["user_id"]),
        "user_name": str(row["user_name"]),
        "slot": int(row["slot"]),
        "voice_name": str(row["voice_name"]),
        "prompt_text": str(row["prompt_text"]),
        "sanitized_audio_path": str(row["sanitized_audio_path"]),
        "duration_sec": float(row["duration_sec"]),
        "status": str(row["status"]),
        "reviewer_id": row["reviewer_id"],
        "review_note": str(row["review_note"] or ""),
        "created_at": str(row["created_at"]),
        "updated_at": str(row["updated_at"]),
    }


def create_clone_request(
    *,
    guild_id: Optional[int],
    channel_id: Optional[int],
    user_id: int,
    user_name: str,
    slot: int,
    voice_name: str,
    prompt_text: str,
    sanitized_audio_path: str,
    duration_sec: float,
    status: str = "pending",
) -> dict[str, Any]:
    init_tts_tables()
    now = _now_str()
    with assistant_db.get_conn() as conn:
        cur = conn.execute(
            """
            INSERT INTO tts_clone_requests (
                guild_id, channel_id, user_id, user_name, slot, voice_name,
                prompt_text, sanitized_audio_path, duration_sec, status,
                reviewer_id, review_note, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, '', ?, ?)
            """,
            (
                int(guild_id) if guild_id is not None else None,
                int(channel_id) if channel_id is not None else None,
                int(user_id),
                str(user_name or user_id)[:64],
                int(slot),
                str(voice_name)[:32],
                str(prompt_text)[:120],
                str(sanitized_audio_path),
                float(duration_sec),
                str(status or "pending"),
                now,
                now,
            ),
        )
        req_id = int(cur.lastrowid)
        row = conn.execute("SELECT * FROM tts_clone_requests WHERE id = ?", (req_id,)).fetchone()
    return _row_to_clone_request(row)


def get_clone_request(req_id: int) -> Optional[dict[str, Any]]:
    init_tts_tables()
    with assistant_db.get_conn() as conn:
        row = conn.execute("SELECT * FROM tts_clone_requests WHERE id = ?", (int(req_id),)).fetchone()
    return _row_to_clone_request(row) if row is not None else None


def list_pending_clone_requests(user_id: Optional[int] = None) -> list[dict[str, Any]]:
    init_tts_tables()
    with assistant_db.get_conn() as conn:
        if user_id is not None:
            rows = conn.execute(
                "SELECT * FROM tts_clone_requests WHERE status = 'pending' AND user_id = ? ORDER BY id ASC",
                (int(user_id),),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM tts_clone_requests WHERE status = 'pending' ORDER BY id ASC"
            ).fetchall()
    return [_row_to_clone_request(r) for r in rows]


def update_clone_request_status(
    req_id: int,
    status: str,
    reviewer_id: Optional[int] = None,
    review_note: str = "",
) -> Optional[dict[str, Any]]:
    init_tts_tables()
    now = _now_str()
    with assistant_db.get_conn() as conn:
        conn.execute(
            """
            UPDATE tts_clone_requests
            SET status = ?, reviewer_id = ?, review_note = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                str(status),
                int(reviewer_id) if reviewer_id is not None else None,
                str(review_note or "")[:200],
                now,
                int(req_id),
            ),
        )
        row = conn.execute("SELECT * FROM tts_clone_requests WHERE id = ?", (int(req_id),)).fetchone()
    return _row_to_clone_request(row) if row is not None else None


def get_user_last_clone_request_ts(user_id: int) -> Optional[datetime]:
    init_tts_tables()
    with assistant_db.get_conn() as conn:
        row = conn.execute(
            "SELECT created_at FROM tts_clone_requests WHERE user_id = ? ORDER BY id DESC LIMIT 1",
            (int(user_id),),
        ).fetchone()
    if row is None or not row["created_at"]:
        return None
    try:
        return datetime.strptime(str(row["created_at"]), "%Y-%m-%d %H:%M:%S").replace(
            tzinfo=assistant_db.TZ_TAIPEI
        )
    except ValueError:
        return None


def _row_to_user_clone(row: Any) -> dict[str, Any]:
    return {
        "voice_id": str(row["voice_id"]),
        "user_id": int(row["user_id"]),
        "slot": int(row["slot"]),
        "voice_name": str(row["voice_name"]),
        "prompt_text": str(row["prompt_text"]),
        "is_public": bool(row["is_public"]),
        "approved_by": row["approved_by"],
        "created_at": str(row["created_at"]),
        "updated_at": str(row["updated_at"]),
    }


def upsert_user_clone(
    *,
    voice_id: str,
    user_id: int,
    slot: int,
    voice_name: str,
    prompt_text: str,
    is_public: bool,
    approved_by: Optional[int],
) -> dict[str, Any]:
    init_tts_tables()
    now = _now_str()
    with assistant_db.get_conn() as conn:
        conn.execute(
            """
            INSERT INTO tts_user_clones (
                voice_id, user_id, slot, voice_name, prompt_text, is_public, approved_by, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(voice_id) DO UPDATE SET
                user_id = excluded.user_id,
                slot = excluded.slot,
                voice_name = excluded.voice_name,
                prompt_text = excluded.prompt_text,
                is_public = excluded.is_public,
                approved_by = excluded.approved_by,
                updated_at = excluded.updated_at
            """,
            (
                str(voice_id),
                int(user_id),
                int(slot),
                str(voice_name)[:32],
                str(prompt_text)[:120],
                int(bool(is_public)),
                int(approved_by) if approved_by is not None else None,
                now,
                now,
            ),
        )
        row = conn.execute("SELECT * FROM tts_user_clones WHERE voice_id = ?", (str(voice_id),)).fetchone()
    return _row_to_user_clone(row)


def list_user_clones(user_id: Optional[int] = None) -> list[dict[str, Any]]:
    init_tts_tables()
    with assistant_db.get_conn() as conn:
        if user_id is not None:
            rows = conn.execute(
                "SELECT * FROM tts_user_clones WHERE user_id = ? ORDER BY slot ASC",
                (int(user_id),),
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM tts_user_clones ORDER BY user_id ASC, slot ASC").fetchall()
    return [_row_to_user_clone(r) for r in rows]


def count_total_user_clones() -> int:
    init_tts_tables()
    with assistant_db.get_conn() as conn:
        row = conn.execute("SELECT COUNT(*) AS cnt FROM tts_user_clones").fetchone()
    return int(row["cnt"]) if row else 0


def delete_user_clone_record(voice_id: str) -> bool:
    init_tts_tables()
    with assistant_db.get_conn() as conn:
        cur = conn.execute("DELETE FROM tts_user_clones WHERE voice_id = ?", (str(voice_id),))
    return cur.rowcount > 0


def get_user_clone(voice_id: str) -> Optional[dict[str, Any]]:
    init_tts_tables()
    with assistant_db.get_conn() as conn:
        row = conn.execute("SELECT * FROM tts_user_clones WHERE voice_id = ?", (str(voice_id),)).fetchone()
    return _row_to_user_clone(row) if row is not None else None


def update_user_clone_visibility(voice_id: str, is_public: bool) -> Optional[dict[str, Any]]:
    init_tts_tables()
    now = _now_str()
    with assistant_db.get_conn() as conn:
        conn.execute(
            "UPDATE tts_user_clones SET is_public = ?, updated_at = ? WHERE voice_id = ?",
            (int(bool(is_public)), now, str(voice_id)),
        )
        row = conn.execute("SELECT * FROM tts_user_clones WHERE voice_id = ?", (str(voice_id),)).fetchone()
    return _row_to_user_clone(row) if row is not None else None


def reset_prefs_using_voice(voice_id_full: str) -> int:
    init_tts_tables()
    vid = str(voice_id_full or "").strip()
    if not vid:
        return 0
    with assistant_db.get_conn() as conn:
        cur1 = conn.execute("DELETE FROM tts_user_prefs WHERE voice_id = ?", (vid,))
        conn.execute(
            "UPDATE tts_guild_settings SET ai_voice_id = '' WHERE ai_voice_id = ?",
            (vid,),
        )
        conn.execute(
            "UPDATE tts_guild_settings SET ai_jp_voice_id = '' WHERE ai_jp_voice_id = ?",
            (vid,),
        )
    return cur1.rowcount


def _row_to_clone_ban(row: Any) -> dict[str, Any]:
    return {
        "user_id": int(row["user_id"]),
        "reason": str(row["reason"] or ""),
        "banned_by": int(row["banned_by"]),
        "created_at": str(row["created_at"]),
    }


def ban_clone_user(user_id: int, banned_by: int, reason: str = "") -> dict[str, Any]:
    init_tts_tables()
    now = _now_str()
    clean_reason = (reason or "").strip()[:200]
    with assistant_db.get_conn() as conn:
        conn.execute(
            """
            INSERT INTO tts_clone_bans (user_id, reason, banned_by, created_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                reason = excluded.reason,
                banned_by = excluded.banned_by,
                created_at = excluded.created_at
            """,
            (int(user_id), clean_reason, int(banned_by), now),
        )
        row = conn.execute("SELECT * FROM tts_clone_bans WHERE user_id = ?", (int(user_id),)).fetchone()
    return _row_to_clone_ban(row)


def unban_clone_user(user_id: int) -> bool:
    init_tts_tables()
    with assistant_db.get_conn() as conn:
        cur = conn.execute("DELETE FROM tts_clone_bans WHERE user_id = ?", (int(user_id),))
    return cur.rowcount > 0


def get_clone_ban(user_id: int) -> Optional[dict[str, Any]]:
    init_tts_tables()
    with assistant_db.get_conn() as conn:
        row = conn.execute("SELECT * FROM tts_clone_bans WHERE user_id = ?", (int(user_id),)).fetchone()
    return _row_to_clone_ban(row) if row is not None else None
