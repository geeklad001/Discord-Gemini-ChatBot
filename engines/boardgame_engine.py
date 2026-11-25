"""Authoritative Board Game Engine: Othello, Chess, Connect4, Gomoku + Local AI + PIL Card Renderer."""

from __future__ import annotations

import contextvars
import copy
import io
import json
import re
from datetime import datetime
from typing import Optional

from engines.assistant_db import TZ_TAIPEI, get_conn, get_db_path

_initialized_bg_dbs: set[str] = set()
pending_board_card_updates: contextvars.ContextVar[Optional[list[tuple[int, str]]]] = contextvars.ContextVar(
    "pending_board_card_updates", default=None
)

BOARDGAME_INTENT_KEYWORDS = (
    "黑白棋", "翻轉棋", "奧賽羅", "othello", "reversi",
    "西洋棋", "國際象棋", "chess", "王車易位", "將軍", "將死", "入堡", "升變",
    "四子棋", "connect4", "connect 4", "c4",
    "五子棋", "gomoku", "gobang",
    "桌遊", "棋盤", "下棋", "玩棋", "落子", "走棋", "對弈", "棋局", "開一局", "開局",
    "投降", "認輸", "悔棋", "上一步", "撤銷", "退回", "戰績", "棋盤面板", "重發棋盤",
    "這步", "那步", "哪一步", "剛才那盤", "這盤", "盤面", "棋子", "黑棋", "白棋", "黑子", "白子", "先手", "後手",
    "兵", "馬", "象", "騎士", "主教", "城堡", "皇后", "國王", "綠點",
)


def is_boardgame_intent(text: str) -> bool:
    t = (text or "").lower()
    if any(kw in t for kw in BOARDGAME_INTENT_KEYWORDS):
        return True
    if re.search(r"\b([a-h][1-8][a-h][1-8][qrbn]?|[a-m]\d{1,2}|[kqrbn][a-h]?[1-8]?x?[a-h][1-8]|o-o(-o)?)\b", t):
        return True
    return False

GAME_CATALOG = {
    "othello": {
        "name": "黑白棋 (Othello)",
        "short": "黑白棋",
        "emoji": "⚫",
        "size": "8×8",
        "p1_label": "⚫ 黑棋",
        "p2_label": "⚪ 白棋",
        "desc": "夾住對方棋子並翻轉，棋盤填滿後多子者勝！附合法步座標提示與一鍵下拉選單。",
        "aliases": ("othello", "reversi", "黑白棋", "翻轉棋", "奧賽羅"),
    },
    "chess": {
        "name": "西洋棋 (Chess)",
        "short": "西洋棋",
        "emoji": "♟️",
        "size": "8×8",
        "p1_label": "♔ 白方",
        "p2_label": "♚ 黑方",
        "desc": "完整國際象棋規則（王車易位、吃過路兵、兵升變、將死與和局），支援選子點擊或 `!m e2e4`。",
        "aliases": ("chess", "西洋棋", "國際象棋"),
    },
    "connect4": {
        "name": "四子棋 (Connect 4)",
        "short": "四子棋",
        "emoji": "🔴",
        "size": "7×6",
        "p1_label": "🔴 紅方",
        "p2_label": "🟡 黃方",
        "desc": "重力落子連成四顆即勝！直接點擊下方 1️⃣~7️⃣ 按鈕即可秒速落子。",
        "aliases": ("connect4", "c4", "four", "四子棋", "重力四子棋"),
    },
    "gomoku": {
        "name": "五子棋 (Gomoku)",
        "short": "五子棋",
        "emoji": "⭕",
        "size": "13×13",
        "p1_label": "⚫ 黑棋",
        "p2_label": "⚪ 白棋",
        "desc": "先連成五子者獲勝！四邊明確標示 A~M / 1~13 座標，支援 `!m g7` 或推薦落點選單。",
        "aliases": ("gomoku", "gobang", "five", "五子棋"),
    },
}

DIFFICULTY_LABELS = {
    "easy": "🌱 簡單",
    "normal": "⚔️ 普通",
    "hard": "🔥 困難",
    "llm": "🧠 Gemini LLM",
}


def resolve_game_type(raw: Optional[str]) -> Optional[str]:
    if not raw:
        return None
    q = raw.strip().lower()
    for gtype, meta in GAME_CATALOG.items():
        if q == gtype or q in meta["aliases"]:
            return gtype
    return None


def resolve_difficulty(raw: Optional[str]) -> str:
    if not raw:
        return "normal"
    q = raw.strip().lower()
    if q in ("llm", "gemini", "model", "ai", "gpt", "模型", "大型語言模型"):
        return "llm"
    if q in ("easy", "e", "1", "簡單", "新手", "初級"):
        return "easy"
    if q in ("hard", "h", "3", "困難", "高手", "地獄"):
        return "hard"
    return "normal"


def init_boardgame_tables() -> None:
    """初始化桌遊棋盤狀態表與戰績統計表（每個資料庫實例僅執行一次）"""
    db_key = str(get_db_path())
    if db_key in _initialized_bg_dbs:
        return
    with get_conn() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS board_games (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                channel_id INTEGER NOT NULL,
                game_type TEXT NOT NULL,
                mode TEXT NOT NULL,
                difficulty TEXT DEFAULT 'normal',
                status TEXT NOT NULL,
                p1_id INTEGER NOT NULL,
                p1_name TEXT NOT NULL,
                p2_id INTEGER DEFAULT 0,
                p2_name TEXT DEFAULT 'Bot AI',
                invited_id INTEGER DEFAULT 0,
                turn INTEGER DEFAULT 1,
                winner INTEGER DEFAULT 0,
                end_reason TEXT DEFAULT '',
                draw_offer_by INTEGER DEFAULT 0,
                undo_approvals TEXT DEFAULT '[]',
                state_json TEXT NOT NULL,
                move_count INTEGER DEFAULT 0,
                show_coords INTEGER DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        cursor.execute("PRAGMA table_info(board_games)")
        bg_cols = {r[1] for r in cursor.fetchall()}
        if "undo_approvals" not in bg_cols:
            cursor.execute("ALTER TABLE board_games ADD COLUMN undo_approvals TEXT DEFAULT '[]'")
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_board_games_channel_status
            ON board_games(channel_id, status)
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS board_game_stats (
                user_id INTEGER NOT NULL,
                user_name TEXT NOT NULL,
                game_type TEXT NOT NULL,
                mode TEXT NOT NULL,
                wins INTEGER DEFAULT 0,
                losses INTEGER DEFAULT 0,
                draws INTEGER DEFAULT 0,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (user_id, game_type, mode)
            )
        """)
        conn.commit()
    _initialized_bg_dbs.add(db_key)


def _deserialize_game(row) -> dict:
    g = dict(row)
    try:
        g["state"] = json.loads(g["state_json"])
    except Exception:
        g["state"] = {}
    try:
        g["undo_approvals"] = json.loads(g.get("undo_approvals") or "[]")
    except Exception:
        g["undo_approvals"] = []
    g["show_coords"] = bool(g.get("show_coords", 1))
    return g


def get_active_game(channel_id: int) -> Optional[dict]:
    """取得指定頻道目前進行中或等待加入的棋局"""
    init_boardgame_tables()
    with get_conn() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM board_games WHERE channel_id = ? AND status IN ('waiting', 'active') ORDER BY id DESC LIMIT 1",
            (channel_id,),
        )
        row = cursor.fetchone()
        return _deserialize_game(row) if row else None


def get_latest_game_in_channel(channel_id: int, include_finished_hours: int = 4) -> Optional[dict]:
    """取得當前進行中的棋局；若無，回傳數小時內最近一局已結束（非取消）的棋局供對話回顧討論"""
    active = get_active_game(channel_id)
    if active:
        return active
    init_boardgame_tables()
    with get_conn() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM board_games WHERE channel_id = ? AND status = 'finished' AND COALESCE(end_reason, '') != 'aborted' ORDER BY id DESC LIMIT 1",
            (channel_id,),
        )
        row = cursor.fetchone()
        if not row:
            return None
        g = _deserialize_game(row)
        try:
            updated_dt = datetime.strptime(g["updated_at"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=TZ_TAIPEI)
            age_hours = (datetime.now(TZ_TAIPEI) - updated_dt).total_seconds() / 3600.0
            if age_hours > include_finished_hours:
                return None
        except Exception:
            pass
        return g


def get_game_by_id(game_id: int) -> Optional[dict]:
    init_boardgame_tables()
    with get_conn() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM board_games WHERE id = ?", (game_id,))
        row = cursor.fetchone()
        return _deserialize_game(row) if row else None


def _record_game_stats(game: dict) -> None:
    """在對局結束時更新 SQLite 勝敗和戰績"""
    if game.get("winner") == 0 or game.get("end_reason") == "aborted":
        return
    now_str = datetime.now(TZ_TAIPEI).strftime("%Y-%m-%d %H:%M:%S")
    gtype = game["game_type"]
    mode = game["mode"]
    winner = game["winner"]

    participants = [(game["p1_id"], game["p1_name"], 1)]
    if mode == "pvp" and game.get("p2_id"):
        participants.append((game["p2_id"], game["p2_name"], 2))

    with get_conn() as conn:
        cursor = conn.cursor()
        for uid, uname, slot in participants:
            if not uid:
                continue
            w = 1 if winner == slot else 0
            l = 1 if (winner in (1, 2) and winner != slot) else 0
            d = 1 if winner == -1 else 0
            cursor.execute(
                """
                INSERT INTO board_game_stats (user_id, user_name, game_type, mode, wins, losses, draws, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id, game_type, mode) DO UPDATE SET
                    user_name = excluded.user_name,
                    wins = wins + excluded.wins,
                    losses = losses + excluded.losses,
                    draws = draws + excluded.draws,
                    updated_at = excluded.updated_at
                """,
                (uid, uname, gtype, mode, w, l, d, now_str),
            )
        conn.commit()


def get_user_stats(user_id: Optional[int] = None, limit: int = 15) -> list[dict]:
    init_boardgame_tables()
    with get_conn() as conn:
        cursor = conn.cursor()
        if user_id is not None:
            cursor.execute(
                "SELECT * FROM board_game_stats WHERE user_id = ? ORDER BY wins DESC, draws DESC",
                (user_id,),
            )
        else:
            cursor.execute(
                "SELECT * FROM board_game_stats ORDER BY wins DESC, draws DESC LIMIT ?",
                (limit,),
            )
        return [dict(r) for r in cursor.fetchall()]


def _save_game_state(game: dict) -> dict:
    now_str = datetime.now(TZ_TAIPEI).strftime("%Y-%m-%d %H:%M:%S")
    game["updated_at"] = now_str
    state_json = json.dumps(game["state"], ensure_ascii=False)
    undo_json = json.dumps(game.get("undo_approvals", []), ensure_ascii=False)
    with get_conn() as conn:
        conn.execute(
            """
            UPDATE board_games
            SET status = ?, p2_id = ?, p2_name = ?, turn = ?, winner = ?, end_reason = ?,
                draw_offer_by = ?, undo_approvals = ?, state_json = ?, move_count = ?, show_coords = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                game["status"],
                game.get("p2_id", 0),
                game.get("p2_name", "Bot AI"),
                game["turn"],
                game["winner"],
                game.get("end_reason", ""),
                game.get("draw_offer_by", 0),
                undo_json,
                state_json,
                game["move_count"],
                1 if game.get("show_coords", True) else 0,
                now_str,
                game["id"],
            ),
        )
        conn.commit()
    return game


from engines.boardgames.othello import (
    OTHELLO_DIRS, OTHELLO_WEIGHTS, _init_othello_state, _othello_flips,
    get_othello_legal_moves, _othello_counts, _parse_grid_coord, _coord_label,
    _othello_eval, _othello_apply_on_board, _othello_alphabeta, _pick_othello_ai_move,
)
from engines.boardgames.connect4_gomoku import (
    C4_ROWS, C4_COLS, _init_connect4_state, get_connect4_legal_cols,
    _c4_find_win, _c4_drop, _c4_eval_window, _c4_eval, _c4_alphabeta, _pick_connect4_ai_move,
    GOMOKU_SIZE, _init_gomoku_state, _gomoku_find_win, _gomoku_line_score,
    get_gomoku_candidates, _pick_gomoku_ai_move,
)
from engines.boardgames.chess_rules import (
    CHESS_START_ROWS, CHESS_PIECE_NAMES, CHESS_PIECE_VALUES, PST_MAP,
    _chess_sq_name, _chess_parse_sq, _chess_pos_key, _init_chess_state, _is_white_piece,
    _chess_find_king, _chess_is_attacked, _chess_in_check, _chess_apply_move_raw,
    get_chess_legal_moves, _chess_insufficient_material, _chess_format_lan,
    _parse_chess_move, _chess_eval_board, _chess_order_moves, _chess_alphabeta, _pick_chess_ai_move,
)
from engines.boardgames.renderer import (
    _load_font, _draw_centered_text, _draw_chess_vector_piece, render_board_image,
)

# =============================================================
# 5. 統一對局生命週期介面 (Create / Join / Move / Resign / Draw / Hint)
# =============================================================
def create_game(
    channel_id: int,
    game_type: str,
    mode: str,
    p1_id: int,
    p1_name: str,
    difficulty: str = "normal",
    invited_id: int = 0,
    invited_name: str = "",
) -> dict:
    """在指定頻道建立一局新桌遊（若有尚未結束的舊對局則回傳錯誤）。"""
    init_boardgame_tables()
    gtype = resolve_game_type(game_type)
    if not gtype:
        raise ValueError(f"不支援的遊戲類型：{game_type}")

    existing = get_active_game(channel_id)
    if existing:
        raise ValueError("此頻道目前已有進行中的棋局！請先完成對局或由當局玩家投降／取消。")

    diff = resolve_difficulty(difficulty)
    if gtype == "othello":
        state = _init_othello_state()
    elif gtype == "chess":
        state = _init_chess_state()
    elif gtype == "connect4":
        state = _init_connect4_state()
    else:
        state = _init_gomoku_state()

    now_str = datetime.now(TZ_TAIPEI).strftime("%Y-%m-%d %H:%M:%S")
    if mode == "pve":
        status = "active"
        p2_id = 0
        p2_name = f"Bot AI ({DIFFICULTY_LABELS[diff]})"
    else:
        status = "waiting"
        p2_id = 0
        p2_name = invited_name if invited_name else "等待對手加入..."

    state_json = json.dumps(state, ensure_ascii=False)
    with get_conn() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO board_games
            (channel_id, game_type, mode, difficulty, status, p1_id, p1_name, p2_id, p2_name, invited_id,
             turn, winner, end_reason, draw_offer_by, state_json, move_count, show_coords, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 0, '', 0, ?, 0, 1, ?, ?)
            """,
            (channel_id, gtype, mode, diff, status, p1_id, p1_name, p2_id, p2_name, invited_id, state_json, now_str, now_str),
        )
        gid = cursor.lastrowid
        conn.commit()
    return get_game_by_id(gid)


def join_pvp_game(game_id: int, user_id: int, user_name: str) -> dict:
    game = get_game_by_id(game_id)
    if not game:
        raise ValueError("找不到該棋局。")
    if game["status"] != "waiting":
        raise ValueError("這場對局已經開始或結束了。")
    if game["p1_id"] == user_id:
        raise ValueError("不能自己跟自己進行 PvP 對戰喔（若要練習請開 PvE 或點「改與 Bot AI 對戰」）！")
    if game.get("invited_id") and game["invited_id"] != user_id:
        raise ValueError(f"這場對戰是專屬向 <@{game['invited_id']}> 發出的邀請！")
    game["p2_id"] = user_id
    game["p2_name"] = user_name
    game["status"] = "active"
    return _save_game_state(game)


def switch_waiting_to_pve(game_id: int, user_id: int) -> dict:
    game = get_game_by_id(game_id)
    if not game or game["status"] != "waiting":
        raise ValueError("目前沒有可切換模式的等待棋局。")
    if game["p1_id"] != user_id:
        raise ValueError("只有開局發起人才能切換模式。")
    with get_conn() as conn:
        conn.execute(
            "UPDATE board_games SET mode = 'pve', status = 'active', p2_id = 0, p2_name = ?, invited_id = 0 WHERE id = ?",
            (f"Bot AI ({DIFFICULTY_LABELS[game['difficulty']]})", game_id),
        )
        conn.commit()
    return get_game_by_id(game_id)


def cancel_waiting_game(game_id: int, user_id: int) -> dict:
    game = get_game_by_id(game_id)
    if not game:
        raise ValueError("找不到該棋局。")
    if game["status"] != "waiting":
        raise ValueError("對局已開始，無法直接取消（請使用投降）。")
    if game["p1_id"] != user_id:
        raise ValueError("只有發起人可以取消等待中的挑戰。")
    game["status"] = "finished"
    game["end_reason"] = "aborted"
    return _save_game_state(game)


def toggle_show_coords(game_id: int) -> dict:
    game = get_game_by_id(game_id)
    if not game:
        raise ValueError("找不到該棋局。")
    game["show_coords"] = not game.get("show_coords", True)
    return _save_game_state(game)


def _is_player_turn(game: dict, user_id: int) -> bool:
    if game["turn"] == 1:
        return user_id == game["p1_id"]
    if game["mode"] == "pve":
        return user_id == 0
    return user_id == game["p2_id"]


def _is_participant(game: dict, user_id: int) -> bool:
    return user_id == game["p1_id"] or (game["mode"] == "pvp" and user_id == game.get("p2_id"))


def apply_move(game_id: int, user_id: int, move_str: str) -> tuple[dict, str]:
    """
    對指定棋局執行一步走法（100% 規則校驗），回傳 (updated_game, move_summary)。
    user_id=0 代表 PvE 本地 AI。
    """
    game = get_game_by_id(game_id)
    if not game:
        raise ValueError("找不到該棋局。")
    if game["status"] != "active":
        raise ValueError("這場棋局尚未開始或已結束。")
    if user_id != 0 and not _is_participant(game, user_id):
        raise ValueError("你不是這場對局的參賽玩家喔！如果想玩，歡迎開一局新的 `!play`。")
    if not _is_player_turn(game, user_id):
        raise ValueError("還沒輪到你的回合喔！")

    gtype = game["game_type"]
    prev_state = copy.deepcopy(game["state"])
    prev_stack = list(prev_state.pop("undo_stack", []))
    prev_turn = game["turn"]
    prev_move_count = game["move_count"]

    state = copy.deepcopy(prev_state)
    turn = game["turn"]
    player_label = GAME_CATALOG[gtype]["p1_label"] if turn == 1 else GAME_CATALOG[gtype]["p2_label"]
    note = ""

    if gtype == "othello":
        coord = _parse_grid_coord(move_str, 8)
        if not coord:
            raise ValueError("座標格式錯誤！請輸入 `A1`~`H8`（例如 `!m d3`）。")
        r, c = coord
        board = state["board"]
        flips = _othello_flips(board, r, c, turn)
        if not flips:
            raise ValueError(f"無法落在 `{_coord_label(r, c)}`！黑白棋必須夾住至少一顆對手的棋子。")
        board[r][c] = turn
        for fr, fc in flips:
            board[fr][fc] = turn
        label = _coord_label(r, c)
        state["last_move"] = [r, c]
        state["moves"].append(f"{player_label} {label} (翻{len(flips)}子)")
        game["move_count"] += 1

        opp = 3 - turn
        opp_moves = get_othello_legal_moves(board, opp)
        my_moves = get_othello_legal_moves(board, turn)
        b_cnt, w_cnt = _othello_counts(board)
        if opp_moves:
            game["turn"] = opp
            note = f"{player_label} 落子 `{label}`（翻轉 {len(flips)} 子）"
        elif my_moves:
            game["turn"] = turn
            opp_label = GAME_CATALOG[gtype]["p2_label"] if turn == 1 else GAME_CATALOG[gtype]["p1_label"]
            state["moves"].append(f"{opp_label} 無合法步 Pass")
            note = f"{player_label} 落子 `{label}`；{opp_label} 無合法步自動讓手 (Pass)！"
        else:
            game["status"] = "finished"
            game["end_reason"] = "normal"
            if b_cnt > w_cnt:
                game["winner"] = 1
            elif w_cnt > b_cnt:
                game["winner"] = 2
            else:
                game["winner"] = -1
            note = f"{player_label} 落子 `{label}`，雙方無步可下，終局！比數 ⚫ {b_cnt} : {w_cnt} ⚪"

    elif gtype == "connect4":
        s = move_str.strip().upper()
        if len(s) == 1 and "A" <= s <= "G":
            col = ord(s) - ord("A")
        elif s.isdigit() and 1 <= int(s) <= C4_COLS:
            col = int(s) - 1
        else:
            raise ValueError("請輸入欄號 `1`~`7`（或 `A`~`G`），或直接點擊下方按鈕！")
        board = state["board"]
        row = _c4_drop(board, col, turn)
        if row is None:
            raise ValueError(f"第 `{col + 1}` 欄已經滿了，請選別欄！")
        state["last_move"] = [row, col]
        state["moves"].append(f"{player_label} 第{col + 1}欄")
        game["move_count"] += 1

        win_line = _c4_find_win(board, turn)
        if win_line:
            state["win_line"] = win_line
            game["status"] = "finished"
            game["winner"] = turn
            game["end_reason"] = "normal"
            note = f"{player_label} 投入第 `{col + 1}` 欄 — 四子連線獲勝！"
        elif not get_connect4_legal_cols(board):
            game["status"] = "finished"
            game["winner"] = -1
            game["end_reason"] = "draw"
            note = f"{player_label} 投入第 `{col + 1}` 欄 — 棋盤已滿，雙方和局！"
        else:
            game["turn"] = 3 - turn
            note = f"{player_label} 投入第 `{col + 1}` 欄"

    elif gtype == "gomoku":
        coord = _parse_grid_coord(move_str, GOMOKU_SIZE)
        if not coord:
            raise ValueError("座標格式錯誤！五子棋請輸入 `A1`~`M13`（例如 `!m g7`）。")
        r, c = coord
        board = state["board"]
        if board[r][c] != 0:
            raise ValueError(f"`{_coord_label(r, c)}` 已經有棋子了！")
        board[r][c] = turn
        label = _coord_label(r, c)
        state["last_move"] = [r, c]
        state["moves"].append(f"{player_label} {label}")
        game["move_count"] += 1

        win_line = _gomoku_find_win(board, turn)
        if win_line:
            state["win_line"] = win_line
            game["status"] = "finished"
            game["winner"] = turn
            game["end_reason"] = "normal"
            note = f"{player_label} 落子 `{label}` — 五子連珠獲勝！"
        elif all(cell != 0 for row in board for cell in row):
            game["status"] = "finished"
            game["winner"] = -1
            game["end_reason"] = "draw"
            note = f"{player_label} 落子 `{label}` — 棋盤已滿，和局！"
        else:
            game["turn"] = 3 - turn
            note = f"{player_label} 落子 `{label}`"

    elif gtype == "chess":
        white_turn = (turn == 1)
        mv = _parse_chess_move(move_str, state, white_turn)
        if not mv:
            raise ValueError(f"「`{move_str}`」不是合法西洋棋步！可用座標如 `e2e4`、`Nf3`、`O-O` 或使用下方選單挑選。")
        r1, c1, r2, c2, promo = mv
        moved_piece = state["board"][r1][c1]
        cap_piece = state["board"][r2][c2]
        is_ep_capture = (
            moved_piece.upper() == "P"
            and bool(state.get("ep"))
            and [r2, c2] == list(state["ep"])
            and not cap_piece
        )
        if is_ep_capture:
            cap_piece = state["board"][r1][c2]
        nxt = _chess_apply_move_raw(state, mv, white_turn)
        opp_white = not white_turn
        opp_in_check = _chess_in_check(nxt["board"], opp_white)
        nxt["in_check"] = opp_in_check

        lan = _chess_format_lan(mv)
        tag = "+" if opp_in_check else ""
        if is_ep_capture:
            cap_txt = f" 吃過路兵 {CHESS_PIECE_NAMES.get(cap_piece, cap_piece)}" if cap_piece else " 吃過路兵"
        elif cap_piece:
            cap_txt = f" 吃 {CHESS_PIECE_NAMES.get(cap_piece, cap_piece)}"
        else:
            cap_txt = ""
        promo_txt = f" (升變={promo.upper()})" if promo else ""
        nxt["moves"] = list(state.get("moves", [])) + [f"{player_label} {lan}{tag}"]
        rep_counts = dict(state.get("rep_counts", {}))
        pos_key = _chess_pos_key(nxt, opp_white)
        rep_counts[pos_key] = rep_counts.get(pos_key, 0) + 1
        nxt["rep_counts"] = rep_counts
        state = nxt
        game["move_count"] += 1

        opp_legal = get_chess_legal_moves(state, opp_white)
        if not opp_legal:
            game["status"] = "finished"
            if opp_in_check:
                game["winner"] = turn
                game["end_reason"] = "checkmate"
                note = f"{player_label} 走 `{lan}`{cap_txt}{promo_txt} — 👑 **將死 (Checkmate)！**"
            else:
                game["winner"] = -1
                game["end_reason"] = "stalemate"
                note = f"{player_label} 走 `{lan}` — 無子可動，**逼和 (Stalemate)！**"
        elif rep_counts.get(pos_key, 0) >= 3:
            game["status"] = "finished"
            game["winner"] = -1
            game["end_reason"] = "draw_rep"
            note = f"{player_label} 走 `{lan}` — 三次重複局面，和局！"
        elif state["halfmove"] >= 100:
            game["status"] = "finished"
            game["winner"] = -1
            game["end_reason"] = "draw_50"
            note = f"{player_label} 走 `{lan}` — 五十步無吃子與無進兵，和局！"
        elif _chess_insufficient_material(state["board"]):
            game["status"] = "finished"
            game["winner"] = -1
            game["end_reason"] = "draw_material"
            note = f"{player_label} 走 `{lan}` — 雙方子力不足以將死，和局！"
        else:
            game["turn"] = 3 - turn
            chk_msg = " ⚠️ **將軍 (Check!)**" if opp_in_check else ""
            p_name = CHESS_PIECE_NAMES.get(moved_piece, moved_piece)
            note = f"{player_label} {p_name} `{lan}`{cap_txt}{promo_txt}{chk_msg}"

    prev_stack.append({
        "turn": prev_turn,
        "move_count": prev_move_count,
        "by_user_id": user_id,
        "state": prev_state,
    })
    state["undo_stack"] = prev_stack[-15:]
    game["draw_offer_by"] = 0
    game["undo_approvals"] = []
    game["state"] = state
    _save_game_state(game)
    if game["status"] == "finished":
        _record_game_stats(game)
    return game, note


def compute_ai_move_for_game(game: dict, difficulty_override: Optional[str] = None) -> Optional[str]:
    """由本機確定性引擎為當前輪次計算一步棋（供 PvE AI 與 Hint 提示使用）"""
    gtype = game["game_type"]
    state = game["state"]
    turn = game["turn"]
    diff = difficulty_override or game.get("difficulty", "normal")
    if gtype == "othello":
        return _pick_othello_ai_move(state["board"], turn, diff)
    if gtype == "connect4":
        return _pick_connect4_ai_move(state["board"], turn, diff)
    if gtype == "gomoku":
        return _pick_gomoku_ai_move(state["board"], turn, diff)
    if gtype == "chess":
        return _pick_chess_ai_move(state, turn == 1, diff)
    return None


def resign_game(game_id: int, user_id: int) -> dict:
    game = get_game_by_id(game_id)
    if not game:
        raise ValueError("找不到該棋局。")
    if game["status"] not in ("waiting", "active"):
        raise ValueError("這場棋局已經結束了。")
    if not _is_participant(game, user_id):
        raise ValueError("你不是這場對局的參賽玩家。")
    if game["status"] == "waiting":
        return cancel_waiting_game(game_id, user_id)
    game["status"] = "finished"
    game["winner"] = 2 if user_id == game["p1_id"] else 1
    game["end_reason"] = "resign"
    game["draw_offer_by"] = 0
    _save_game_state(game)
    _record_game_stats(game)
    return game


def offer_or_accept_draw(game_id: int, user_id: int) -> tuple[dict, str]:
    game = get_game_by_id(game_id)
    if not game or game["status"] != "active":
        raise ValueError("目前沒有進行中的對局。")
    if not _is_participant(game, user_id):
        raise ValueError("只有參賽玩家才能提議或接受和局。")
    if game["mode"] == "pve":
        raise ValueError("PvE 人機對戰暫不支援協議和局（若想結束請點投降）。")

    if game.get("draw_offer_by") == user_id:
        return game, "你已經提出和局請求，請等待對手點擊「接受和局」。"
    if game.get("draw_offer_by") and game["draw_offer_by"] != user_id:
        game["status"] = "finished"
        game["winner"] = -1
        game["end_reason"] = "draw"
        game["draw_offer_by"] = 0
        _save_game_state(game)
        _record_game_stats(game)
        return game, "accepted"

    game["draw_offer_by"] = user_id
    _save_game_state(game)
    return game, "offered"


def required_human_players(game: dict) -> set[int]:
    """回傳此局所有必須同意悔棋的人類玩家 ID 集合。"""
    if game["mode"] == "pvp" and game.get("p2_id"):
        return {int(game["p1_id"]), int(game["p2_id"])}
    return {int(game["p1_id"])}


def request_or_approve_undo(game_id: int, user_id: int) -> tuple[dict, str]:
    """
    申請或同意退回上一步（悔棋）。
    嚴格限制：僅限參與此局的人類玩家呼叫（user_id != 0），且需該局全體參賽玩家皆同意方能執行回溯。
    回傳 (updated_game, status): 'applied' | 'pending' | 'already_approved'
    """
    game = get_game_by_id(game_id)
    if not game or game["status"] != "active":
        raise ValueError("僅有進行中的對局才能請求退回上一步。")
    if user_id == 0 or not _is_participant(game, user_id):
        raise ValueError("僅限實際參與本局的玩家本人可發起或同意退回上一步！")

    stack = list(game["state"].get("undo_stack") or [])
    if not stack:
        raise ValueError("目前尚無可退回的歷史步數。")

    approvals = list(game.get("undo_approvals") or [])
    req = required_human_players(game)

    if user_id in approvals:
        if not req.issubset(set(approvals)):
            return game, "already_approved"
    else:
        approvals.append(user_id)

    if not req.issubset(set(approvals)):
        game["undo_approvals"] = approvals
        _save_game_state(game)
        return game, "pending"

    # 全體參賽玩家同意！執行回溯
    if game["mode"] == "pve":
        # PvE 模式：連同 AI 的回應步一併退回至玩家最後一步之前（即 stack 中最近一筆 turn == 1）
        chosen_idx = None
        for idx in range(len(stack) - 1, -1, -1):
            if stack[idx].get("turn") == 1:
                chosen_idx = idx
                break
        if chosen_idx is None:
            chosen_idx = len(stack) - 1
        snap = stack[chosen_idx]
        remaining_stack = stack[:chosen_idx]
    else:
        snap = stack[-1]
        remaining_stack = stack[:-1]

    restored_state = copy.deepcopy(snap["state"])
    restored_state["undo_stack"] = remaining_stack
    game["state"] = restored_state
    game["turn"] = snap["turn"]
    game["move_count"] = snap["move_count"]
    game["undo_approvals"] = []
    game["draw_offer_by"] = 0
    _save_game_state(game)
    return game, "applied"


def reject_undo(game_id: int, user_id: int) -> dict:
    """任一參賽玩家拒絕或取消當前的悔棋請求。"""
    game = get_game_by_id(game_id)
    if not game or game["status"] != "active":
        raise ValueError("目前沒有進行中的對局。")
    if user_id == 0 or not _is_participant(game, user_id):
        raise ValueError("僅限本局參賽玩家可操作。")
    game["undo_approvals"] = []
    return _save_game_state(game)


def _format_ascii_board_for_llm(game: dict) -> str:
    gtype = game["game_type"]
    board = game["state"]["board"]
    if gtype == "othello":
        sym = {0: ".", 1: "B", 2: "W"}
        lines = ["  A B C D E F G H"]
        for r in range(8):
            lines.append(f"{r + 1} " + " ".join(sym[board[r][c]] for c in range(8)))
        return "\n".join(lines)
    if gtype == "connect4":
        sym = {0: ".", 1: "R", 2: "Y"}
        lines = ["1 2 3 4 5 6 7"]
        for r in range(C4_ROWS):
            lines.append(" ".join(sym[board[r][c]] for c in range(C4_COLS)))
        return "\n".join(lines)
    if gtype == "gomoku":
        sym = {0: ".", 1: "B", 2: "W"}
        header = "   " + " ".join(chr(ord("A") + c) for c in range(GOMOKU_SIZE))
        lines = [header]
        for r in range(GOMOKU_SIZE):
            lines.append(f"{r + 1:2d} " + " ".join(sym[board[r][c]] for c in range(GOMOKU_SIZE)))
        return "\n".join(lines)
    if gtype == "chess":
        lines = ["  a b c d e f g h"]
        for r in range(8):
            lines.append(f"{8 - r} " + " ".join(board[r][c] or "." for c in range(8)))
        return "\n".join(lines)
    return ""


def format_llm_opponent_prompt(game: dict, retry_error: str = "") -> str:
    """建構防作弊的 LLM 對弈決策 Prompt，強制從合法著法清單擇一輸出 JSON。"""
    gtype = game["game_type"]
    meta = GAME_CATALOG[gtype]
    turn = game["turn"]
    my_role = meta["p2_label"] if turn == 2 else meta["p1_label"]
    ascii_board = _format_ascii_board_for_llm(game)
    recent = " -> ".join((game["state"].get("moves") or [])[-8:]) or "無"

    if gtype == "othello":
        legal_list = [_coord_label(r, c) for r, c in get_othello_legal_moves(game["state"]["board"], turn)]
        legal_block = f"合法步座標清單：{', '.join(legal_list)}"
    elif gtype == "connect4":
        legal_list = [str(c + 1) for c in get_connect4_legal_cols(game["state"]["board"])]
        legal_block = f"合法欄號清單：{', '.join(legal_list)}"
    elif gtype == "gomoku":
        cands = get_gomoku_candidates(game["state"]["board"], limit=30, player_hint=turn)
        legal_block = f"推薦戰術候選空位：{', '.join(cands)}（13×13 任意空位 A1~M13 皆可，嚴禁下在已有 B/W 的位置）"
    else:
        legal_list = [_chess_format_lan(mv) for mv in get_chess_legal_moves(game["state"], turn == 1)]
        legal_block = f"合法棋步清單（LAN 格式）：{', '.join(legal_list)}"

    retry_note = f"\n⚠️ 你上一輪挑選的步法違例（{retry_error}），請務必從上方合法清單挑選！" if retry_error else ""
    return (
        f"你正在 Discord 聊天室裡，以**你自己的第一人稱身分（自稱「我」）**親自與玩家「{game.get('p1_name', '對方')}」進行【{meta['name']}】對弈。\n"
        f"你執：{my_role}\n"
        f"最近著法：{recent}\n\n"
        f"當前真實棋盤：\n{ascii_board}\n\n"
        f"{legal_block}{retry_note}\n\n"
        f"請以真實棋友對弈的思維挑選一步最有利的**合法著法**，嚴格只回傳 JSON（不要加 markdown code fence 或額外廢話）：\n"
        f'{{"move": "<從上方合法清單選擇的座標或棋步>", "taunt": "<用第一人稱對玩家說的一句自然對局台詞，限25字內；嚴禁出現 AI / LLM / 電腦 等旁觀第三人稱字眼>"}}'
    )


def parse_llm_move_response(raw_text: str) -> tuple[Optional[str], str]:
    """安全解析 LLM 回傳的走法 JSON 與感想。"""
    text = (raw_text or "").strip()
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        try:
            data = json.loads(m.group(0))
            mv = str(data.get("move") or "").strip()
            taunt = str(data.get("taunt") or "").strip()[:40]
            if mv:
                return mv, taunt
        except Exception:
            pass
    # Fallback regex for bare coordinate
    m_mv = re.search(r"\b([a-h][1-8][a-h][1-8][qrbn]?|[A-Ma-m]\d{1,2}|[1-7])\b", text)
    return (m_mv.group(1), "") if m_mv else (None, "")



def play_pve_ai_turn_sync(game: dict, note_prefix: str = "") -> tuple[dict, str]:
    """若為 PvE 且輪到 Bot AI，由本機確定性引擎計算 AI 落子並更新棋局。"""
    note = note_prefix
    safety_loops = 0
    while game["status"] == "active" and game["mode"] == "pve" and game["turn"] == 2 and safety_loops < 4:
        safety_loops += 1
        ai_mv = compute_ai_move_for_game(game)
        if not ai_mv:
            break
        game, ai_note = apply_move(game["id"], 0, ai_mv)
        note = f"{note}\n🤖 {ai_note}" if note else f"🤖 {ai_note}"
    return game, note


def _format_legal_moves_for_llm(game: dict) -> str:
    gtype = game["game_type"]
    state = game["state"]
    turn = game["turn"]
    if gtype == "othello":
        moves = get_othello_legal_moves(state["board"], turn)
        coords = [_coord_label(r, c) for r, c in moves]
        b_cnt, w_cnt = _othello_counts(state["board"])
        return f"目前子數：⚫黑 {b_cnt} : ⚪白 {w_cnt}\n合法落子座標（{len(coords)} 個）：{', '.join(coords) if coords else '無'}"
    if gtype == "connect4":
        cols = [str(c + 1) for c in get_connect4_legal_cols(state["board"])]
        return f"可投子欄號：{', '.join(cols)}"
    if gtype == "gomoku":
        board = state["board"]
        stones = []
        for r in range(GOMOKU_SIZE):
            for c in range(GOMOKU_SIZE):
                if board[r][c] == 1:
                    stones.append(f"⚫{_coord_label(r, c)}")
                elif board[r][c] == 2:
                    stones.append(f"⚪{_coord_label(r, c)}")
        cands = get_gomoku_candidates(board, limit=18, player_hint=turn)
        return (
            f"盤面已有棋子（共 {len(stones)} 顆）：{', '.join(stones[-24:]) if stones else '空棋盤'}\n"
            f"推薦熱點／鄰近空位：{', '.join(cands)}（13×13 支援 A1~M13 任意空位）"
        )
    if gtype == "chess":
        white_turn = (turn == 1)
        board = state["board"]
        pieces_desc = []
        for r in range(8):
            for c in range(8):
                p = board[r][c]
                if p:
                    pieces_desc.append(f"{CHESS_PIECE_NAMES.get(p, p)}@{_chess_sq_name(r, c)}")
        legal = get_chess_legal_moves(state, white_turn)
        legal_strs = []
        for mv in legal[:45]:
            lan = _chess_format_lan(mv)
            pn = CHESS_PIECE_NAMES.get(board[mv[0]][mv[1]], board[mv[0]][mv[1]])
            legal_strs.append(f"{pn}:{lan}")
        suffix = f"...(共 {len(legal)} 步)" if len(legal) > 45 else ""
        chk = " ⚠️目前正在被將軍！" if state.get("in_check") else ""
        return (
            f"目前盤面棋子：{' | '.join(pieces_desc)}{chk}\n"
            f"當前玩家全部合法棋步：{', '.join(legal_strs)}{suffix}"
        )
    return ""


def get_board_status_summary_for_llm(channel_id: int) -> str:
    """提供給 Gemini 上下文與工具查詢的權威棋盤圖、對局紀錄與完整合法步清單"""
    game = get_latest_game_in_channel(channel_id, include_finished_hours=4)
    if not game:
        return (
            "目前此頻道沒有進行中或近期結束的棋盤對局。\n"
            "👉 若玩家想用自然語言開始玩或直接報出第一步棋，【必須】主動呼叫 `boardgame_start(game_type, mode, difficulty, first_move)` "
            "建立正式棋局並貼出圖形棋盤卡！嚴禁自己在純文字裡模擬下棋。"
        )
    meta = GAME_CATALOG[game["game_type"]]
    ascii_board = _format_ascii_board_for_llm(game)
    recent = " -> ".join((game["state"].get("moves") or [])[-12:]) or "尚無落子"

    if game["status"] == "finished":
        w = game["winner"]
        winner_txt = "雙方和局" if w == -1 else (f"{meta['p1_label']} {game['p1_name']} 勝出" if w == 1 else f"{meta['p2_label']} {game['p2_name']} 勝出")
        extra_score = ""
        if game["game_type"] == "othello":
            b_cnt, w_cnt = _othello_counts(game["state"]["board"])
            extra_score = f"（比數 ⚫ {b_cnt} : {w_cnt} ⚪）"
        return (
            f"近期剛結束的棋局：#{game['id']} {meta['name']}（模式：{game['mode'].upper()} | 難度：{game['difficulty']}）\n"
            f"結果：{winner_txt}{extra_score} | 結束原因：{game.get('end_reason') or 'normal'} | 總手數：{game['move_count']}\n"
            f"完整近期著法：{recent}\n"
            f"終局真實棋盤：\n{ascii_board}\n"
            f"（以上為剛剛那盤的真實資料，可直接據此跟玩家討論或檢討；若玩家要開新局請呼叫 `boardgame_start`。）"
        )

    turn_label = meta["p1_label"] if game["turn"] == 1 else meta["p2_label"]
    turn_name = game["p1_name"] if game["turn"] == 1 else game["p2_name"]
    details = _format_legal_moves_for_llm(game) if game["status"] == "active" else ""
    diff_tag = f" | 對手類型：{DIFFICULTY_LABELS.get(game['difficulty'], game['difficulty'])}" if game["mode"] == "pve" else ""
    return (
        f"當前進行中：#{game['id']} {meta['name']}（模式：{game['mode'].upper()}{diff_tag} | 狀態：{game['status']}）\n"
        f"玩家 1 ({meta['p1_label']})：{game['p1_name']} vs 玩家 2 ({meta['p2_label']})：{game['p2_name']}\n"
        f"目前輪到：{turn_label} ({turn_name}) | 總手數：{game['move_count']}\n"
        f"近期著法紀錄：{recent}\n"
        f"當前真實棋盤：\n{ascii_board}\n"
        f"{details}\n"
        f"⚠️【強制規範】：若玩家用口語指明要下哪一步或悔棋/投降，你【必須】呼叫 `boardgame_play_move(move_str)` / `boardgame_undo()` / `boardgame_resign()` 寫入真實引擎；"
        f"系統會自動在你的同一則回覆下方附上圖形棋盤卡，請勿在文字中複述或手畫棋盤卡片欄位（「黑白棋 (Othello)」、「目前輪到」、「最新動態」、「目前子數」、「最近著法」）。"
    )
