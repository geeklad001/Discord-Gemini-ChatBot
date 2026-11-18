"""Authoritative Board Game Engine: Othello, Chess, Connect4, Gomoku + Local AI + PIL Card Renderer."""

from __future__ import annotations

import contextvars
import copy
import io
import json
import math
import random
import re
from datetime import datetime
from typing import Optional

from PIL import Image, ImageDraw, ImageFont

from assistant_db import TZ_TAIPEI, get_conn, get_db_path

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


# =============================================================
# 1. 黑白棋 (Othello / Reversi) 規則與 AI
# =============================================================
OTHELLO_DIRS = [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)]
OTHELLO_WEIGHTS = [
    [120, -20, 20,  5,  5, 20, -20, 120],
    [-20, -40, -5, -5, -5, -5, -40, -20],
    [ 20,  -5, 15,  3,  3, 15,  -5,  20],
    [  5,  -5,  3,  3,  3,  3,  -5,   5],
    [  5,  -5,  3,  3,  3,  3,  -5,   5],
    [ 20,  -5, 15,  3,  3, 15,  -5,  20],
    [-20, -40, -5, -5, -5, -5, -40, -20],
    [120, -20, 20,  5,  5, 20, -20, 120],
]


def _init_othello_state() -> dict:
    board = [[0] * 8 for _ in range(8)]
    board[3][3] = 2
    board[3][4] = 1
    board[4][3] = 1
    board[4][4] = 2
    return {"board": board, "moves": [], "last_move": None}


def _othello_flips(board: list[list[int]], r: int, c: int, player: int) -> list[tuple[int, int]]:
    if not (0 <= r < 8 and 0 <= c < 8) or board[r][c] != 0:
        return []
    opp = 3 - player
    flips: list[tuple[int, int]] = []
    for dr, dc in OTHELLO_DIRS:
        nr, nc = r + dr, c + dc
        line: list[tuple[int, int]] = []
        while 0 <= nr < 8 and 0 <= nc < 8 and board[nr][nc] == opp:
            line.append((nr, nc))
            nr += dr
            nc += dc
        if line and 0 <= nr < 8 and 0 <= nc < 8 and board[nr][nc] == player:
            flips.extend(line)
    return flips


def get_othello_legal_moves(board: list[list[int]], player: int) -> list[tuple[int, int]]:
    moves = []
    for r in range(8):
        for c in range(8):
            if _othello_flips(board, r, c, player):
                moves.append((r, c))
    return moves


def _othello_counts(board: list[list[int]]) -> tuple[int, int]:
    b = sum(cell == 1 for row in board for cell in row)
    w = sum(cell == 2 for row in board for cell in row)
    return b, w


def _parse_grid_coord(move_str: str, size: int) -> Optional[tuple[int, int]]:
    """解析 A1~H8 或 A1~M13（亦支援 3D / d3）座標"""
    s = move_str.strip().upper()
    m = re.match(r"^([A-Z])\s*[-,]?\s*(\d{1,2})$", s)
    if m:
        col = ord(m.group(1)) - ord("A")
        row = int(m.group(2)) - 1
    else:
        m2 = re.match(r"^(\d{1,2})\s*[-,]?\s*([A-Z])$", s)
        if not m2:
            return None
        row = int(m2.group(1)) - 1
        col = ord(m2.group(2)) - ord("A")
    if 0 <= row < size and 0 <= col < size:
        return row, col
    return None


def _coord_label(r: int, c: int) -> str:
    return f"{chr(ord('A') + c)}{r + 1}"


def _othello_eval(board: list[list[int]], player: int) -> float:
    opp = 3 - player
    b_cnt, w_cnt = _othello_counts(board)
    total = b_cnt + w_cnt
    my_cnt = b_cnt if player == 1 else w_cnt
    opp_cnt = w_cnt if player == 1 else b_cnt

    if total == 64:
        return (my_cnt - opp_cnt) * 1000.0

    pos_score = 0.0
    for r in range(8):
        for c in range(8):
            if board[r][c] == player:
                pos_score += OTHELLO_WEIGHTS[r][c]
            elif board[r][c] == opp:
                pos_score -= OTHELLO_WEIGHTS[r][c]

    my_mob = len(get_othello_legal_moves(board, player))
    opp_mob = len(get_othello_legal_moves(board, opp))
    mob_score = (my_mob - opp_mob) * 12.0
    disc_weight = 6.0 if total >= 50 else 0.5
    return pos_score + mob_score + (my_cnt - opp_cnt) * disc_weight


def _othello_apply_on_board(board: list[list[int]], r: int, c: int, player: int) -> list[list[int]]:
    nb = [row[:] for row in board]
    flips = _othello_flips(nb, r, c, player)
    nb[r][c] = player
    for fr, fc in flips:
        nb[fr][fc] = player
    return nb


def _othello_alphabeta(board: list[list[int]], player: int, root_player: int, depth: int, alpha: float, beta: float) -> float:
    moves = get_othello_legal_moves(board, player)
    if depth == 0:
        return _othello_eval(board, root_player)
    if not moves:
        opp = 3 - player
        if not get_othello_legal_moves(board, opp):
            b, w = _othello_counts(board)
            diff = (b - w) if root_player == 1 else (w - b)
            return diff * 1000.0
        return _othello_alphabeta(board, opp, root_player, depth - 1, alpha, beta)

    maximizing = (player == root_player)
    if maximizing:
        val = -math.inf
        for r, c in moves:
            nb = _othello_apply_on_board(board, r, c, player)
            val = max(val, _othello_alphabeta(nb, 3 - player, root_player, depth - 1, alpha, beta))
            alpha = max(alpha, val)
            if beta <= alpha:
                break
        return val
    val = math.inf
    for r, c in moves:
        nb = _othello_apply_on_board(board, r, c, player)
        val = min(val, _othello_alphabeta(nb, 3 - player, root_player, depth - 1, alpha, beta))
        beta = min(beta, val)
        if beta <= alpha:
            break
    return val


def _pick_othello_ai_move(board: list[list[int]], player: int, difficulty: str) -> Optional[str]:
    moves = get_othello_legal_moves(board, player)
    if not moves:
        return None
    if difficulty == "easy":
        r, c = random.choice(moves)
        return _coord_label(r, c)
    depth = 1 if difficulty == "normal" else 3
    best_val = -math.inf
    best_moves: list[tuple[int, int]] = []
    for r, c in moves:
        nb = _othello_apply_on_board(board, r, c, player)
        val = _othello_alphabeta(nb, 3 - player, player, depth - 1, -math.inf, math.inf)
        if val > best_val + 1e-6:
            best_val = val
            best_moves = [(r, c)]
        elif abs(val - best_val) <= 1e-6:
            best_moves.append((r, c))
    r, c = random.choice(best_moves)
    return _coord_label(r, c)


# =============================================================
# 2. 四子棋 (Connect Four) 規則與 AI
# =============================================================
C4_ROWS = 6
C4_COLS = 7


def _init_connect4_state() -> dict:
    return {"board": [[0] * C4_COLS for _ in range(C4_ROWS)], "moves": [], "last_move": None, "win_line": None}


def get_connect4_legal_cols(board: list[list[int]]) -> list[int]:
    return [c for c in range(C4_COLS) if board[0][c] == 0]


def _c4_find_win(board: list[list[int]], player: int) -> Optional[list[list[int]]]:
    for r in range(C4_ROWS):
        for c in range(C4_COLS):
            if board[r][c] != player:
                continue
            for dr, dc in ((0, 1), (1, 0), (1, 1), (1, -1)):
                line = [[r + i * dr, c + i * dc] for i in range(4)]
                if all(0 <= rr < C4_ROWS and 0 <= cc < C4_COLS and board[rr][cc] == player for rr, cc in line):
                    return line
    return None


def _c4_drop(board: list[list[int]], col: int, player: int) -> Optional[int]:
    for r in range(C4_ROWS - 1, -1, -1):
        if board[r][col] == 0:
            board[r][col] = player
            return r
    return None


def _c4_eval_window(win: list[int], player: int) -> float:
    opp = 3 - player
    me_cnt = win.count(player)
    opp_cnt = win.count(opp)
    empty_cnt = win.count(0)
    if me_cnt == 4:
        return 10000.0
    if opp_cnt == 4:
        return -10000.0
    if me_cnt == 3 and empty_cnt == 1:
        return 12.0
    if me_cnt == 2 and empty_cnt == 2:
        return 2.5
    if opp_cnt == 3 and empty_cnt == 1:
        return -15.0
    if opp_cnt == 2 and empty_cnt == 2:
        return -2.0
    return 0.0


def _c4_eval(board: list[list[int]], player: int) -> float:
    score = 0.0
    center_col = [board[r][3] for r in range(C4_ROWS)]
    score += center_col.count(player) * 4.0
    for r in range(C4_ROWS):
        for c in range(C4_COLS - 3):
            score += _c4_eval_window([board[r][c + i] for i in range(4)], player)
    for c in range(C4_COLS):
        for r in range(C4_ROWS - 3):
            score += _c4_eval_window([board[r + i][c] for i in range(4)], player)
    for r in range(C4_ROWS - 3):
        for c in range(C4_COLS - 3):
            score += _c4_eval_window([board[r + i][c + i] for i in range(4)], player)
            score += _c4_eval_window([board[r + 3 - i][c + i] for i in range(4)], player)
    return score


def _c4_alphabeta(board: list[list[int]], player: int, root_player: int, depth: int, alpha: float, beta: float) -> float:
    opp = 3 - player
    if _c4_find_win(board, root_player):
        return 100000.0 + depth
    if _c4_find_win(board, 3 - root_player):
        return -100000.0 - depth
    cols = get_connect4_legal_cols(board)
    if not cols or depth == 0:
        return _c4_eval(board, root_player)

    cols_ordered = sorted(cols, key=lambda c: abs(c - 3))
    if player == root_player:
        val = -math.inf
        for c in cols_ordered:
            nb = [row[:] for row in board]
            _c4_drop(nb, c, player)
            val = max(val, _c4_alphabeta(nb, opp, root_player, depth - 1, alpha, beta))
            alpha = max(alpha, val)
            if beta <= alpha:
                break
        return val
    val = math.inf
    for c in cols_ordered:
        nb = [row[:] for row in board]
        _c4_drop(nb, c, player)
        val = min(val, _c4_alphabeta(nb, opp, root_player, depth - 1, alpha, beta))
        beta = min(beta, val)
        if beta <= alpha:
            break
    return val


def _pick_connect4_ai_move(board: list[list[int]], player: int, difficulty: str) -> Optional[str]:
    cols = get_connect4_legal_cols(board)
    if not cols:
        return None
    if difficulty == "easy":
        # 50% smart block/win, 50% random center-biased
        for c in cols:
            nb = [row[:] for row in board]
            _c4_drop(nb, c, player)
            if _c4_find_win(nb, player):
                return str(c + 1)
        return str(random.choice(cols) + 1)

    depth = 2 if difficulty == "normal" else 4
    cols_ordered = sorted(cols, key=lambda c: abs(c - 3))
    best_val = -math.inf
    best_cols: list[int] = []
    for c in cols_ordered:
        nb = [row[:] for row in board]
        _c4_drop(nb, c, player)
        if _c4_find_win(nb, player):
            return str(c + 1)
        val = _c4_alphabeta(nb, 3 - player, player, depth - 1, -math.inf, math.inf)
        if val > best_val + 1e-6:
            best_val = val
            best_cols = [c]
        elif abs(val - best_val) <= 1e-6:
            best_cols.append(c)
    return str(random.choice(best_cols) + 1)


# =============================================================
# 3. 五子棋 (Gomoku 13x13) 規則與 AI
# =============================================================
GOMOKU_SIZE = 13


def _init_gomoku_state() -> dict:
    return {"board": [[0] * GOMOKU_SIZE for _ in range(GOMOKU_SIZE)], "moves": [], "last_move": None, "win_line": None}


def _gomoku_find_win(board: list[list[int]], player: int) -> Optional[list[list[int]]]:
    for r in range(GOMOKU_SIZE):
        for c in range(GOMOKU_SIZE):
            if board[r][c] != player:
                continue
            for dr, dc in ((0, 1), (1, 0), (1, 1), (1, -1)):
                line = [[r + i * dr, c + i * dc] for i in range(5)]
                if all(0 <= rr < GOMOKU_SIZE and 0 <= cc < GOMOKU_SIZE and board[rr][cc] == player for rr, cc in line):
                    return line
    return None


def _gomoku_line_score(board: list[list[int]], r: int, c: int, player: int) -> float:
    """評估若在 (r, c) 落子對 player 產生的連線攻擊強度"""
    total = 0.0
    for dr, dc in ((0, 1), (1, 0), (1, 1), (1, -1)):
        cnt = 1
        open_ends = 0
        for sign in (1, -1):
            step = 1
            while True:
                nr, nc = r + sign * step * dr, c + sign * step * dc
                if 0 <= nr < GOMOKU_SIZE and 0 <= nc < GOMOKU_SIZE:
                    if board[nr][nc] == player:
                        cnt += 1
                        step += 1
                    elif board[nr][nc] == 0:
                        open_ends += 1
                        break
                    else:
                        break
                else:
                    break
        if cnt >= 5:
            total += 1000000.0
        elif cnt == 4:
            total += 80000.0 if open_ends == 2 else (12000.0 if open_ends == 1 else 0.0)
        elif cnt == 3:
            total += 8000.0 if open_ends == 2 else (900.0 if open_ends == 1 else 0.0)
        elif cnt == 2:
            total += 400.0 if open_ends == 2 else (60.0 if open_ends == 1 else 0.0)
        else:
            total += 15.0 if open_ends == 2 else 2.0
    return total


def get_gomoku_candidates(board: list[list[int]], limit: int = 20, player_hint: Optional[int] = None) -> list[str]:
    has_stones = any(cell != 0 for row in board for cell in row)
    if not has_stones:
        return ["G7", "F6", "H8", "G6", "F7"]

    candidates: set[tuple[int, int]] = set()
    for r in range(GOMOKU_SIZE):
        for c in range(GOMOKU_SIZE):
            if board[r][c] != 0:
                for dr in range(-2, 3):
                    for dc in range(-2, 3):
                        nr, nc = r + dr, c + dc
                        if 0 <= nr < GOMOKU_SIZE and 0 <= nc < GOMOKU_SIZE and board[nr][nc] == 0:
                            candidates.add((nr, nc))

    scored: list[tuple[float, int, int]] = []
    p = player_hint or 1
    opp = 3 - p
    for r, c in candidates:
        atk = _gomoku_line_score(board, r, c, p)
        dfn = _gomoku_line_score(board, r, c, opp)
        center_bonus = -0.1 * (abs(r - 6) + abs(c - 6))
        scored.append((atk + dfn * 0.95 + center_bonus, r, c))
    scored.sort(reverse=True)
    return [_coord_label(r, c) for _, r, c in scored[:limit]]


def _pick_gomoku_ai_move(board: list[list[int]], player: int, difficulty: str) -> Optional[str]:
    opp = 3 - player
    has_stones = any(cell != 0 for row in board for cell in row)
    if not has_stones:
        return "G7"

    cands = get_gomoku_candidates(board, limit=60, player_hint=player)
    if not cands:
        return None
    if difficulty == "easy":
        pool = cands[:min(8, len(cands))]
        return random.choice(pool)

    def_weight = 0.92 if difficulty == "normal" else 1.05
    best_score = -math.inf
    best_coords: list[str] = []
    for coord in cands:
        rc = _parse_grid_coord(coord, GOMOKU_SIZE)
        if not rc:
            continue
        r, c = rc
        atk = _gomoku_line_score(board, r, c, player)
        dfn = _gomoku_line_score(board, r, c, opp)
        center_bias = -0.2 * (abs(r - 6) + abs(c - 6))
        score = atk + dfn * def_weight + center_bias
        if score > best_score + 1e-6:
            best_score = score
            best_coords = [coord]
        elif abs(score - best_score) <= 1e-6:
            best_coords.append(coord)
    return random.choice(best_coords) if best_coords else cands[0]


# =============================================================
# 4. 西洋棋 (Chess) 完整 FIDE 規則引擎與 AI
# =============================================================
CHESS_START_ROWS = [
    list("rnbqkbnr"),
    list("pppppppp"),
    [""] * 8,
    [""] * 8,
    [""] * 8,
    [""] * 8,
    list("PPPPPPPP"),
    list("RNBQKBNR"),
]

CHESS_PIECE_NAMES = {
    "P": "白兵", "N": "白馬", "B": "白象", "R": "白車", "Q": "白后", "K": "白王",
    "p": "黑兵", "n": "黑馬", "b": "黑象", "r": "黑車", "q": "黑后", "k": "黑王",
}

CHESS_PIECE_VALUES = {
    "P": 100, "N": 320, "B": 330, "R": 500, "Q": 900, "K": 20000,
    "p": 100, "n": 320, "b": 330, "r": 500, "q": 900, "k": 20000,
}

# Rank 8 -> Rank 1 tables for White (Black mirrors vertically)
PST_PAWN = [
    [0,  0,  0,  0,  0,  0,  0,  0],
    [50, 50, 50, 50, 50, 50, 50, 50],
    [10, 10, 20, 30, 30, 20, 10, 10],
    [5,  5, 10, 25, 25, 10,  5,  5],
    [0,  0,  0, 20, 20,  0,  0,  0],
    [5, -5,-10,  0,  0,-10, -5,  5],
    [5, 10, 10,-20,-20, 10, 10,  5],
    [0,  0,  0,  0,  0,  0,  0,  0],
]
PST_KNIGHT = [
    [-50,-40,-30,-30,-30,-30,-40,-50],
    [-40,-20,  0,  0,  0,  0,-20,-40],
    [-30,  0, 10, 15, 15, 10,  0,-30],
    [-30,  5, 15, 20, 20, 15,  5,-30],
    [-30,  0, 15, 20, 20, 15,  0,-30],
    [-30,  5, 10, 15, 15, 10,  5,-30],
    [-40,-20,  0,  5,  5,  0,-20,-40],
    [-50,-40,-30,-30,-30,-30,-40,-50],
]
PST_BISHOP = [
    [-20,-10,-10,-10,-10,-10,-10,-20],
    [-10,  0,  0,  0,  0,  0,  0,-10],
    [-10,  0,  5, 10, 10,  5,  0,-10],
    [-10,  5,  5, 10, 10,  5,  5,-10],
    [-10,  0, 10, 10, 10, 10,  0,-10],
    [-10, 10, 10, 10, 10, 10, 10,-10],
    [-10,  5,  0,  0,  0,  0,  5,-10],
    [-20,-10,-10,-10,-10,-10,-10,-20],
]
PST_ROOK = [
    [0,  0,  0,  0,  0,  0,  0,  0],
    [5, 10, 10, 10, 10, 10, 10,  5],
    [-5,  0,  0,  0,  0,  0,  0, -5],
    [-5,  0,  0,  0,  0,  0,  0, -5],
    [-5,  0,  0,  0,  0,  0,  0, -5],
    [-5,  0,  0,  0,  0,  0,  0, -5],
    [-5,  0,  0,  0,  0,  0,  0, -5],
    [0,  0,  0,  5,  5,  0,  0,  0],
]
PST_QUEEN = [
    [-20,-10,-10, -5, -5,-10,-10,-20],
    [-10,  0,  0,  0,  0,  0,  0,-10],
    [-10,  0,  5,  5,  5,  5,  0,-10],
    [-5,   0,  5,  5,  5,  5,  0, -5],
    [0,    0,  5,  5,  5,  5,  0, -5],
    [-10,  5,  5,  5,  5,  5,  0,-10],
    [-10,  0,  5,  0,  0,  0,  0,-10],
    [-20,-10,-10, -5, -5,-10,-10,-20],
]
PST_KING = [
    [-30,-40,-40,-50,-50,-40,-40,-30],
    [-30,-40,-40,-50,-50,-40,-40,-30],
    [-30,-40,-40,-50,-50,-40,-40,-30],
    [-30,-40,-40,-50,-50,-40,-40,-30],
    [-20,-30,-30,-40,-40,-30,-30,-20],
    [-10,-20,-20,-20,-20,-20,-20,-10],
    [20,  20,  0,  0,  0,  0, 20, 20],
    [20,  30, 10,  0,  0, 10, 30, 20],
]
PST_MAP = {"P": PST_PAWN, "N": PST_KNIGHT, "B": PST_BISHOP, "R": PST_ROOK, "Q": PST_QUEEN, "K": PST_KING}


def _chess_sq_name(r: int, c: int) -> str:
    return f"{chr(ord('a') + c)}{8 - r}"


def _chess_parse_sq(sq: str) -> Optional[tuple[int, int]]:
    s = sq.strip().lower()
    if len(s) != 2 or not ("a" <= s[0] <= "h") or not ("1" <= s[1] <= "8"):
        return None
    return 8 - int(s[1]), ord(s[0]) - ord("a")


def _chess_pos_key(state: dict, white_turn: bool) -> str:
    rows = "/".join("".join(cell or "." for cell in row) for row in state["board"])
    cast = "".join(k for k in ("K", "Q", "k", "q") if state["castling"].get(k))
    ep = _chess_sq_name(*state["ep"]) if state.get("ep") else "-"
    return f"{rows}|{'w' if white_turn else 'b'}|{cast or '-'}|{ep}"


def _init_chess_state() -> dict:
    st = {
        "board": [r[:] for r in CHESS_START_ROWS],
        "castling": {"K": True, "Q": True, "k": True, "q": True},
        "ep": None,
        "halfmove": 0,
        "fullmove": 1,
        "moves": [],
        "last_move": None,
        "in_check": False,
        "rep_counts": {},
    }
    key = _chess_pos_key(st, True)
    st["rep_counts"][key] = 1
    return st


def _is_white_piece(p: str) -> bool:
    return bool(p) and p.isupper()


def _chess_find_king(board: list[list[str]], is_white: bool) -> Optional[tuple[int, int]]:
    target = "K" if is_white else "k"
    for r in range(8):
        for c in range(8):
            if board[r][c] == target:
                return r, c
    return None


def _chess_is_attacked(board: list[list[str]], r: int, c: int, by_white: bool) -> bool:
    # Pawn attacks
    pawn = "P" if by_white else "p"
    p_row = r + 1 if by_white else r - 1
    if 0 <= p_row < 8:
        for dc in (-1, 1):
            nc = c + dc
            if 0 <= nc < 8 and board[p_row][nc] == pawn:
                return True
    # Knight attacks
    knight = "N" if by_white else "n"
    for dr, dc in ((-2, -1), (-2, 1), (-1, -2), (-1, 2), (1, -2), (1, 2), (2, -1), (2, 1)):
        nr, nc = r + dr, c + dc
        if 0 <= nr < 8 and 0 <= nc < 8 and board[nr][nc] == knight:
            return True
    # King attacks
    king = "K" if by_white else "k"
    for dr in (-1, 0, 1):
        for dc in (-1, 0, 1):
            if dr == 0 and dc == 0:
                continue
            nr, nc = r + dr, c + dc
            if 0 <= nr < 8 and 0 <= nc < 8 and board[nr][nc] == king:
                return True
    # Orthogonal (Rook / Queen)
    rook, queen = ("R", "Q") if by_white else ("r", "q")
    for dr, dc in ((-1, 0), (1, 0), (0, -1), (0, 1)):
        nr, nc = r + dr, c + dc
        while 0 <= nr < 8 and 0 <= nc < 8:
            p = board[nr][nc]
            if p:
                if p in (rook, queen):
                    return True
                break
            nr += dr
            nc += dc
    # Diagonal (Bishop / Queen)
    bishop = "B" if by_white else "b"
    for dr, dc in ((-1, -1), (-1, 1), (1, -1), (1, 1)):
        nr, nc = r + dr, c + dc
        while 0 <= nr < 8 and 0 <= nc < 8:
            p = board[nr][nc]
            if p:
                if p in (bishop, queen):
                    return True
                break
            nr += dr
            nc += dc
    return False


def _chess_in_check(board: list[list[str]], is_white: bool) -> bool:
    ksq = _chess_find_king(board, is_white)
    if not ksq:
        return False
    return _chess_is_attacked(board, ksq[0], ksq[1], not is_white)


def _chess_apply_move_raw(state: dict, move: tuple[int, int, int, int, str], white_turn: bool) -> dict:
    r1, c1, r2, c2, promo = move
    board = [row[:] for row in state["board"]]
    castling = dict(state["castling"])
    ep_old = state.get("ep")
    piece = board[r1][c1]
    captured = board[r2][c2]

    new_ep = None
    is_capture = bool(captured)

    # En passant capture
    if piece.upper() == "P" and ep_old and [r2, c2] == list(ep_old) and not captured:
        cap_row = r1
        board[cap_row][c2] = ""
        is_capture = True

    # Double pawn push -> set ep
    if piece.upper() == "P" and abs(r2 - r1) == 2:
        new_ep = [(r1 + r2) // 2, c1]

    # Move piece & promotion
    moved_piece = piece
    if piece.upper() == "P" and r2 in (0, 7):
        pr = (promo or "q").upper()
        moved_piece = pr if white_turn else pr.lower()

    board[r2][c2] = moved_piece
    board[r1][c1] = ""

    # Castling rook move
    if piece.upper() == "K" and abs(c2 - c1) == 2:
        if c2 == 6:  # King-side
            board[r1][5] = board[r1][7]
            board[r1][7] = ""
        elif c2 == 2:  # Queen-side
            board[r1][3] = board[r1][0]
            board[r1][0] = ""

    # Update castling rights
    if piece == "K":
        castling["K"] = False
        castling["Q"] = False
    elif piece == "k":
        castling["k"] = False
        castling["q"] = False
    elif piece == "R":
        if (r1, c1) == (7, 7):
            castling["K"] = False
        elif (r1, c1) == (7, 0):
            castling["Q"] = False
    elif piece == "r":
        if (r1, c1) == (0, 7):
            castling["k"] = False
        elif (r1, c1) == (0, 0):
            castling["q"] = False

    if (r2, c2) == (7, 7):
        castling["K"] = False
    elif (r2, c2) == (7, 0):
        castling["Q"] = False
    elif (r2, c2) == (0, 7):
        castling["k"] = False
    elif (r2, c2) == (0, 0):
        castling["q"] = False

    halfmove = 0 if (piece.upper() == "P" or is_capture) else state.get("halfmove", 0) + 1
    fullmove = state.get("fullmove", 1) + (0 if white_turn else 1)

    return {
        "board": board,
        "castling": castling,
        "ep": new_ep,
        "halfmove": halfmove,
        "fullmove": fullmove,
        "moves": state.get("moves", []),
        "last_move": [r1, c1, r2, c2],
        "in_check": False,
        "rep_counts": state.get("rep_counts", {}),
    }


def get_chess_legal_moves(state: dict, white_turn: bool) -> list[tuple[int, int, int, int, str]]:
    """產生當前輪次全部合法西洋棋著法：(r1, c1, r2, c2, promo)"""
    board = state["board"]
    castling = state["castling"]
    ep = state.get("ep")
    pseudo: list[tuple[int, int, int, int, str]] = []

    for r in range(8):
        for c in range(8):
            p = board[r][c]
            if not p or _is_white_piece(p) != white_turn:
                continue
            pt = p.upper()
            if pt == "P":
                step = -1 if white_turn else 1
                start_row = 6 if white_turn else 1
                promo_row = 0 if white_turn else 7
                r1 = r + step
                if 0 <= r1 < 8 and not board[r1][c]:
                    if r1 == promo_row:
                        for pr in ("q", "r", "b", "n"):
                            pseudo.append((r, c, r1, c, pr))
                    else:
                        pseudo.append((r, c, r1, c, ""))
                        r2 = r + 2 * step
                        if r == start_row and not board[r2][c]:
                            pseudo.append((r, c, r2, c, ""))
                for dc in (-1, 1):
                    nc = c + dc
                    if 0 <= r1 < 8 and 0 <= nc < 8:
                        target = board[r1][nc]
                        if target and _is_white_piece(target) != white_turn:
                            if r1 == promo_row:
                                for pr in ("q", "r", "b", "n"):
                                    pseudo.append((r, c, r1, nc, pr))
                            else:
                                pseudo.append((r, c, r1, nc, ""))
                        elif ep and [r1, nc] == list(ep):
                            pseudo.append((r, c, r1, nc, ""))
            elif pt == "N":
                for dr, dc in ((-2, -1), (-2, 1), (-1, -2), (-1, 2), (1, -2), (1, 2), (2, -1), (2, 1)):
                    nr, nc = r + dr, c + dc
                    if 0 <= nr < 8 and 0 <= nc < 8:
                        t = board[nr][nc]
                        if not t or _is_white_piece(t) != white_turn:
                            pseudo.append((r, c, nr, nc, ""))
            elif pt in ("B", "R", "Q"):
                dirs = []
                if pt in ("B", "Q"):
                    dirs.extend([(-1, -1), (-1, 1), (1, -1), (1, 1)])
                if pt in ("R", "Q"):
                    dirs.extend([(-1, 0), (1, 0), (0, -1), (0, 1)])
                for dr, dc in dirs:
                    nr, nc = r + dr, c + dc
                    while 0 <= nr < 8 and 0 <= nc < 8:
                        t = board[nr][nc]
                        if not t:
                            pseudo.append((r, c, nr, nc, ""))
                        else:
                            if _is_white_piece(t) != white_turn:
                                pseudo.append((r, c, nr, nc, ""))
                            break
                        nr += dr
                        nc += dc
            elif pt == "K":
                for dr in (-1, 0, 1):
                    for dc in (-1, 0, 1):
                        if dr == 0 and dc == 0:
                            continue
                        nr, nc = r + dr, c + dc
                        if 0 <= nr < 8 and 0 <= nc < 8:
                            t = board[nr][nc]
                            if not t or _is_white_piece(t) != white_turn:
                                pseudo.append((r, c, nr, nc, ""))
                # Castling
                home_r = 7 if white_turn else 0
                if (r, c) == (home_r, 4) and not _chess_is_attacked(board, home_r, 4, not white_turn):
                    k_right = "K" if white_turn else "k"
                    q_right = "Q" if white_turn else "q"
                    expected_rook = "R" if white_turn else "r"
                    if castling.get(k_right) and board[home_r][7] == expected_rook:
                        if not board[home_r][5] and not board[home_r][6]:
                            if not _chess_is_attacked(board, home_r, 5, not white_turn) and not _chess_is_attacked(board, home_r, 6, not white_turn):
                                pseudo.append((home_r, 4, home_r, 6, ""))
                    if castling.get(q_right) and board[home_r][0] == expected_rook:
                        if not board[home_r][3] and not board[home_r][2] and not board[home_r][1]:
                            if not _chess_is_attacked(board, home_r, 3, not white_turn) and not _chess_is_attacked(board, home_r, 2, not white_turn):
                                pseudo.append((home_r, 4, home_r, 2, ""))

    legal: list[tuple[int, int, int, int, str]] = []
    for mv in pseudo:
        nxt = _chess_apply_move_raw(state, mv, white_turn)
        if not _chess_in_check(nxt["board"], white_turn):
            legal.append(mv)
    return legal


def _chess_insufficient_material(board: list[list[str]]) -> bool:
    pieces = []
    for r in range(8):
        for c in range(8):
            p = board[r][c]
            if p and p.upper() != "K":
                pieces.append((p, (r + c) % 2))
    if not pieces:
        return True
    if len(pieces) == 1 and pieces[0][0].upper() in ("B", "N"):
        return True
    if len(pieces) == 2 and all(p[0].upper() == "B" for p in pieces) and pieces[0][1] == pieces[1][1]:
        return True
    return False


def _chess_format_lan(mv: tuple[int, int, int, int, str]) -> str:
    r1, c1, r2, c2, promo = mv
    return f"{_chess_sq_name(r1, c1)}{_chess_sq_name(r2, c2)}{promo}"


def _parse_chess_move(move_str: str, state: dict, white_turn: bool) -> Optional[tuple[int, int, int, int, str]]:
    """支援 LAN (e2e4 / e7e8q)、王車易位 (O-O / O-O-O) 與常見 SAN (Nf3 / e4 / Qxd5)"""
    raw = move_str.strip()
    legal = get_chess_legal_moves(state, white_turn)
    if not legal:
        return None

    clean = raw.replace("-", "").replace(" ", "").lower()
    # Castling
    if clean in ("oo", "00"):
        home_r = 7 if white_turn else 0
        for mv in legal:
            if mv[:4] == (home_r, 4, home_r, 6):
                return mv
    if clean in ("ooo", "000"):
        home_r = 7 if white_turn else 0
        for mv in legal:
            if mv[:4] == (home_r, 4, home_r, 2):
                return mv

    # LAN: e2e4 or e7e8q
    m_lan = re.match(r"^([a-h][1-8])([a-h][1-8])([qrbn])?$", clean)
    if m_lan:
        sq1 = _chess_parse_sq(m_lan.group(1))
        sq2 = _chess_parse_sq(m_lan.group(2))
        pr = m_lan.group(3) or ""
        if sq1 and sq2:
            for mv in legal:
                if mv[:4] == (sq1[0], sq1[1], sq2[0], sq2[1]):
                    if not mv[4] or mv[4] == (pr or "q"):
                        return mv

    # SAN fallback: e4, Nf3, exd5, Nbd2, e8=Q, Qxf7+
    san = raw.replace("+", "").replace("#", "").replace("x", "").strip()
    promo_match = re.search(r"=([QRBNqrbn])$", san)
    promo_req = promo_match.group(1).lower() if promo_match else ""
    if promo_match:
        san = san[:promo_match.start()]

    if san and san[0] in "NBRQK":
        pt = san[0]
        rest = san[1:]
    else:
        pt = "P"
        rest = san

    if len(rest) < 2:
        return None
    dest = _chess_parse_sq(rest[-2:])
    if not dest:
        return None
    disambig = rest[:-2].lower()

    matches = []
    board = state["board"]
    for mv in legal:
        r1, c1, r2, c2, pr = mv
        if (r2, c2) != dest:
            continue
        if board[r1][c1].upper() != pt:
            continue
        if promo_req and pr != promo_req:
            continue
        if not promo_req and pr and pr != "q":
            continue
        src_sq = _chess_sq_name(r1, c1)
        if disambig:
            if len(disambig) == 1:
                if disambig not in src_sq:
                    continue
            elif disambig != src_sq:
                continue
        matches.append(mv)

    return matches[0] if len(matches) == 1 else (matches[0] if matches else None)


def _chess_eval_board(board: list[list[str]]) -> float:
    """從白方視角評估棋盤分數（正分白優，負分黑優）"""
    score = 0.0
    for r in range(8):
        for c in range(8):
            p = board[r][c]
            if not p:
                continue
            pt = p.upper()
            val = CHESS_PIECE_VALUES.get(p, 0)
            pst = PST_MAP.get(pt)
            if p.isupper():
                pst_val = pst[r][c] if pst else 0
                score += val + pst_val
            else:
                pst_val = pst[7 - r][c] if pst else 0
                score -= val + pst_val
    return score


def _chess_order_moves(state: dict, moves: list[tuple[int, int, int, int, str]]) -> list[tuple[int, int, int, int, str]]:
    board = state["board"]
    def key_fn(mv):
        r1, c1, r2, c2, pr = mv
        cap = board[r2][c2]
        cap_val = CHESS_PIECE_VALUES.get(cap, 0) if cap else 0
        atk_val = CHESS_PIECE_VALUES.get(board[r1][c1], 100)
        promo_val = 800 if pr == "q" else (300 if pr else 0)
        return -(cap_val * 10 - atk_val + promo_val)
    return sorted(moves, key=key_fn)


def _chess_alphabeta(state: dict, white_turn: bool, depth: int, alpha: float, beta: float) -> float:
    if depth == 0:
        return _chess_eval_board(state["board"])
    moves = get_chess_legal_moves(state, white_turn)
    if not moves:
        if _chess_in_check(state["board"], white_turn):
            return (-99999.0 - depth) if white_turn else (99999.0 + depth)
        return 0.0

    ordered = _chess_order_moves(state, moves)
    if white_turn:
        val = -math.inf
        for mv in ordered:
            nxt = _chess_apply_move_raw(state, mv, True)
            val = max(val, _chess_alphabeta(nxt, False, depth - 1, alpha, beta))
            alpha = max(alpha, val)
            if beta <= alpha:
                break
        return val
    val = math.inf
    for mv in ordered:
        nxt = _chess_apply_move_raw(state, mv, False)
        val = min(val, _chess_alphabeta(nxt, True, depth - 1, alpha, beta))
        beta = min(beta, val)
        if beta <= alpha:
            break
    return val


def _pick_chess_ai_move(state: dict, white_turn: bool, difficulty: str) -> Optional[str]:
    moves = get_chess_legal_moves(state, white_turn)
    if not moves:
        return None
    if difficulty == "easy":
        # Prefer captures sometimes, otherwise random
        caps = [mv for mv in moves if state["board"][mv[2]][mv[3]]]
        chosen = random.choice(caps) if (caps and random.random() < 0.6) else random.choice(moves)
        return _chess_format_lan(chosen)

    depth = 1 if difficulty == "normal" else 2
    ordered = _chess_order_moves(state, moves)
    best_val = -math.inf if white_turn else math.inf
    best_moves: list[tuple[int, int, int, int, str]] = []
    for mv in ordered:
        nxt = _chess_apply_move_raw(state, mv, white_turn)
        val = _chess_alphabeta(nxt, not white_turn, depth, -math.inf, math.inf)
        if white_turn:
            if val > best_val + 1e-6:
                best_val = val
                best_moves = [mv]
            elif abs(val - best_val) <= 1e-6:
                best_moves.append(mv)
        else:
            if val < best_val - 1e-6:
                best_val = val
                best_moves = [mv]
            elif abs(val - best_val) <= 1e-6:
                best_moves.append(mv)
    return _chess_format_lan(random.choice(best_moves))


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
        nxt = _chess_apply_move_raw(state, mv, white_turn)
        opp_white = not white_turn
        opp_in_check = _chess_in_check(nxt["board"], opp_white)
        nxt["in_check"] = opp_in_check

        lan = _chess_format_lan(mv)
        tag = "+" if opp_in_check else ""
        cap_txt = f" 吃 {CHESS_PIECE_NAMES.get(cap_piece, cap_piece)}" if cap_piece else ""
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
        f"你正在與人類玩家進行【{meta['name']}】對弈。\n"
        f"你執：{my_role}\n"
        f"最近著法：{recent}\n\n"
        f"當前真實棋盤：\n{ascii_board}\n\n"
        f"{legal_block}{retry_note}\n\n"
        f"請以策略思維挑選一步最有利的**合法著法**，嚴格只回傳 JSON（不要加 markdown code fence 或額外廢話）：\n"
        f'{{"move": "<從上方合法清單選擇的座標或棋步>", "taunt": "<一句簡短自然的繁體中文對局感想或俏皮話，限25字內>"}}'
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


# =============================================================
# 6. PIL 高清向量棋盤圖像渲染器 (帶四邊明確座標與合法步提示)
# =============================================================
def _load_font(size: int) -> ImageFont.ImageFont:
    candidates = [
        "C:/Windows/Fonts/segoeuib.ttf",
        "C:/Windows/Fonts/arialbd.ttf",
        "C:/Windows/Fonts/arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ]
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            continue
    return ImageFont.load_default()


def _draw_centered_text(draw: ImageDraw.ImageDraw, cx: float, cy: float, text: str, font, fill) -> None:
    try:
        bbox = draw.textbbox((0, 0), text, font=font)
        tw = bbox[2] - bbox[0]
        th = bbox[3] - bbox[1]
        draw.text((cx - tw / 2 - bbox[0], cy - th / 2 - bbox[1]), text, font=font, fill=fill)
    except Exception:
        draw.text((cx - len(text) * 4, cy - 6), text, font=font, fill=fill)


def _draw_chess_vector_piece(draw: ImageDraw.ImageDraw, cx: float, cy: float, cell: int, piece: str) -> None:
    """用向量幾何繪製西洋棋棋子，確保跨系統零缺字且極高辨識度。"""
    is_w = piece.isupper()
    fill = (248, 245, 236) if is_w else (32, 34, 42)
    outline = (30, 30, 35) if is_w else (228, 224, 212)
    pt = piece.upper()
    s = cell * 0.40  # half-span

    # Base pedestal shared by all pieces
    base_y = cy + s * 0.72
    draw.rounded_rectangle(
        [cx - s * 0.65, base_y - s * 0.14, cx + s * 0.65, base_y + s * 0.14],
        radius=3, fill=fill, outline=outline, width=2,
    )

    if pt == "P":  # Pawn
        draw.polygon(
            [(cx - s * 0.42, base_y - s * 0.12), (cx + s * 0.42, base_y - s * 0.12), (cx + s * 0.16, cy - s * 0.15), (cx - s * 0.16, cy - s * 0.15)],
            fill=fill, outline=outline, width=2,
        )
        r = s * 0.30
        draw.ellipse([cx - r, cy - s * 0.56 - r * 0.3, cx + r, cy - s * 0.56 + r * 1.7], fill=fill, outline=outline, width=2)
    elif pt == "R":  # Rook
        draw.rectangle([cx - s * 0.40, cy - s * 0.28, cx + s * 0.40, base_y - s * 0.12], fill=fill, outline=outline, width=2)
        draw.rectangle([cx - s * 0.50, cy - s * 0.58, cx + s * 0.50, cy - s * 0.28], fill=fill, outline=outline, width=2)
        for bx in (-0.50, -0.14, 0.22):
            draw.rectangle([cx + s * bx, cy - s * 0.76, cx + s * (bx + 0.28), cy - s * 0.56], fill=fill, outline=outline, width=2)
    elif pt == "B":  # Bishop
        draw.polygon(
            [(cx - s * 0.44, base_y - s * 0.12), (cx + s * 0.44, base_y - s * 0.12), (cx + s * 0.26, cy - s * 0.10), (cx, cy - s * 0.62), (cx - s * 0.26, cy - s * 0.10)],
            fill=fill, outline=outline, width=2,
        )
        r = s * 0.13
        draw.ellipse([cx - r, cy - s * 0.74 - r, cx + r, cy - s * 0.74 + r], fill=fill, outline=outline, width=2)
        draw.line([(cx - s * 0.14, cy - s * 0.28), (cx + s * 0.14, cy - s * 0.02)], fill=outline, width=2)
    elif pt == "N":  # Knight (horse silhouette)
        pts = [
            (cx - s * 0.46, base_y - s * 0.12),
            (cx + s * 0.48, base_y - s * 0.12),
            (cx + s * 0.36, cy - s * 0.22),
            (cx + s * 0.15, cy - s * 0.66),
            (cx - s * 0.10, cy - s * 0.76),
            (cx - s * 0.54, cy - s * 0.28),
            (cx - s * 0.34, cy - s * 0.08),
            (cx - s * 0.08, cy - s * 0.16),
            (cx - s * 0.32, cy + s * 0.28),
        ]
        draw.polygon(pts, fill=fill, outline=outline, width=2)
        er = max(2, int(s * 0.07))
        draw.ellipse([cx - s * 0.20 - er, cy - s * 0.38 - er, cx - s * 0.20 + er, cy - s * 0.38 + er], fill=outline)
    elif pt == "Q":  # Queen (5-point crown)
        pts = [
            (cx - s * 0.44, base_y - s * 0.12),
            (cx + s * 0.44, base_y - s * 0.12),
            (cx + s * 0.60, cy - s * 0.44),
            (cx + s * 0.26, cy - s * 0.16),
            (cx, cy - s * 0.66),
            (cx - s * 0.26, cy - s * 0.16),
            (cx - s * 0.60, cy - s * 0.44),
        ]
        draw.polygon(pts, fill=fill, outline=outline, width=2)
        for px, py in ((cx - s * 0.60, cy - s * 0.48), (cx, cy - s * 0.70), (cx + s * 0.60, cy - s * 0.48)):
            pr = max(2, int(s * 0.09))
            draw.ellipse([px - pr, py - pr, px + pr, py + pr], fill=fill, outline=outline, width=2)
    elif pt == "K":  # King (dome + cross)
        draw.polygon(
            [(cx - s * 0.46, base_y - s * 0.12), (cx + s * 0.46, base_y - s * 0.12), (cx + s * 0.52, cy - s * 0.30), (cx, cy - s * 0.48), (cx - s * 0.52, cy - s * 0.30)],
            fill=fill, outline=outline, width=2,
        )
        # Cross on top
        draw.line([(cx, cy - s * 0.86), (cx, cy - s * 0.46)], fill=outline, width=3)
        draw.line([(cx - s * 0.20, cy - s * 0.68), (cx + s * 0.20, cy - s * 0.68)], fill=outline, width=3)


def render_board_image(game: dict, highlight_from: Optional[str] = None) -> bytes:
    """渲染帶四邊座標、合法步提示與最後一手標記的 PNG 棋盤卡圖片。"""
    gtype = game["game_type"]
    state = game["state"]
    board = state["board"]
    show_coords = game.get("show_coords", True)
    last_move = state.get("last_move")

    if gtype in ("othello", "chess"):
        rows, cols = 8, 8
        cell = 64
        margin = 38
    elif gtype == "connect4":
        rows, cols = C4_ROWS, C4_COLS
        cell = 68
        margin = 38
    else:  # gomoku 13x13
        rows, cols = GOMOKU_SIZE, GOMOKU_SIZE
        cell = 44
        margin = 36

    w = cols * cell + margin * 2
    h = rows * cell + margin * 2
    bg_color = (28, 32, 42)
    img = Image.new("RGB", (w, h), bg_color)
    draw = ImageDraw.Draw(img)

    font_axis = _load_font(17 if cell >= 60 else 14)
    font_small = _load_font(12 if cell >= 60 else 10)
    axis_text = (230, 234, 244)

    # Outer 4-side coordinate labels
    for c in range(cols):
        cx = margin + c * cell + cell / 2
        label = str(c + 1) if gtype == "connect4" else chr(ord("A") + c)
        _draw_centered_text(draw, cx, margin / 2, label, font_axis, axis_text)
        _draw_centered_text(draw, cx, h - margin / 2, label, font_axis, axis_text)

    if gtype != "connect4":
        for r in range(rows):
            cy = margin + r * cell + cell / 2
            label = str(8 - r) if gtype == "chess" else str(r + 1)
            _draw_centered_text(draw, margin / 2, cy, label, font_axis, axis_text)
            _draw_centered_text(draw, w - margin / 2, cy, label, font_axis, axis_text)

    if gtype == "othello":
        legal_set = set(get_othello_legal_moves(board, game["turn"])) if game["status"] == "active" else set()
        for r in range(8):
            for c in range(8):
                x0 = margin + c * cell
                y0 = margin + r * cell
                x1, y1 = x0 + cell, y0 + cell
                is_last = last_move and last_move == [r, c]
                sq_col = (40, 134, 84) if (r + c) % 2 == 0 else (33, 118, 72)
                if is_last:
                    sq_col = (72, 152, 92)
                draw.rectangle([x0, y0, x1, y1], fill=sq_col, outline=(20, 74, 44), width=1)
                if is_last:
                    draw.rectangle([x0 + 2, y0 + 2, x1 - 2, y1 - 2], outline=(245, 200, 70), width=3)

                v = board[r][c]
                pad = 8
                if v == 1:
                    draw.ellipse([x0 + pad, y0 + pad, x1 - pad, y1 - pad], fill=(22, 24, 30), outline=(85, 90, 105), width=2)
                elif v == 2:
                    draw.ellipse([x0 + pad, y0 + pad, x1 - pad, y1 - pad], fill=(244, 244, 248), outline=(170, 175, 185), width=2)
                elif (r, c) in legal_set:
                    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
                    lr = 14 if show_coords else 8
                    draw.ellipse([cx - lr, cy - lr, cx + lr, cy + lr], fill=(116, 224, 150), outline=(210, 255, 225), width=1)
                    if show_coords:
                        _draw_centered_text(draw, cx, cy, _coord_label(r, c), font_small, (16, 56, 32))
                elif show_coords:
                    _draw_centered_text(draw, (x0 + x1) / 2, (y0 + y1) / 2, _coord_label(r, c), font_small, (58, 150, 98))

    elif gtype == "chess":
        white_turn = (game["turn"] == 1)
        king_in_check_sq = _chess_find_king(board, white_turn) if (game["status"] == "active" and state.get("in_check")) else None
        sel_targets: set[tuple[int, int]] = set()
        sel_sq = _chess_parse_sq(highlight_from) if highlight_from else None
        if sel_sq and game["status"] == "active":
            for mv in get_chess_legal_moves(state, white_turn):
                if (mv[0], mv[1]) == sel_sq:
                    sel_targets.add((mv[2], mv[3]))

        last_src = tuple(last_move[:2]) if last_move and len(last_move) == 4 else None
        last_dst = tuple(last_move[2:]) if last_move and len(last_move) == 4 else None

        for r in range(8):
            for c in range(8):
                x0 = margin + c * cell
                y0 = margin + r * cell
                x1, y1 = x0 + cell, y0 + cell
                light = ((r + c) % 2 == 0)
                sq_col = (232, 214, 184) if light else (164, 118, 82)
                if (r, c) == last_src or (r, c) == last_dst:
                    sq_col = (240, 218, 128) if light else (188, 158, 72)
                if sel_sq and (r, c) == sel_sq:
                    sq_col = (116, 178, 232)
                if king_in_check_sq and (r, c) == king_in_check_sq:
                    sq_col = (212, 68, 76)
                draw.rectangle([x0, y0, x1, y1], fill=sq_col)

                p = board[r][c]
                if not p and show_coords and (r, c) not in sel_targets:
                    wm_col = (188, 166, 134) if light else (136, 94, 62)
                    _draw_centered_text(draw, x0 + cell * 0.25, y0 + cell * 0.22, _chess_sq_name(r, c), font_small, wm_col)

                if (r, c) in sel_targets:
                    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
                    if p:
                        draw.ellipse([x0 + 4, y0 + 4, x1 - 4, y1 - 4], outline=(46, 204, 113), width=4)
                    else:
                        tr = 15 if show_coords else 10
                        draw.ellipse([cx - tr, cy - tr, cx + tr, cy + tr], fill=(46, 204, 113), outline=(225, 255, 235), width=2)
                        if show_coords:
                            _draw_centered_text(draw, cx, cy, _chess_sq_name(r, c), font_small, (12, 54, 28))

                if p:
                    _draw_chess_vector_piece(draw, (x0 + x1) / 2, (y0 + y1) / 2, cell, p)

    elif gtype == "connect4":
        win_set = {tuple(pt) for pt in (state.get("win_line") or [])}
        draw.rectangle([margin, margin, w - margin, h - margin], fill=(32, 92, 198), outline=(18, 56, 132), width=3)
        for r in range(C4_ROWS):
            for c in range(C4_COLS):
                x0 = margin + c * cell
                y0 = margin + r * cell
                cx, cy = x0 + cell / 2, y0 + cell / 2
                rad = cell * 0.37
                v = board[r][c]
                if v == 1:
                    col_fill, col_out = (232, 65, 65), (150, 28, 28)
                elif v == 2:
                    col_fill, col_out = (248, 210, 54), (172, 138, 16)
                else:
                    col_fill, col_out = (28, 32, 42), (20, 48, 112)
                draw.ellipse([cx - rad, cy - rad, cx + rad, cy + rad], fill=col_fill, outline=col_out, width=2)
                if (r, c) in win_set:
                    draw.ellipse([cx - rad - 3, cy - rad - 3, cx + rad + 3, cy + rad + 3], outline=(255, 255, 255), width=4)
                elif last_move and last_move == [r, c]:
                    draw.ellipse([cx - rad * 0.32, cy - rad * 0.32, cx + rad * 0.32, cy + rad * 0.32], outline=(255, 255, 255), width=2)

    else:  # gomoku 13x13
        draw.rectangle([margin, margin, w - margin, h - margin], fill=(216, 174, 114), outline=(138, 96, 48), width=2)
        for i in range(GOMOKU_SIZE):
            xy_x = margin + i * cell + cell / 2
            xy_y = margin + i * cell + cell / 2
            x_start, x_end = margin + cell / 2, margin + (GOMOKU_SIZE - 1) * cell + cell / 2
            y_start, y_end = margin + cell / 2, margin + (GOMOKU_SIZE - 1) * cell + cell / 2
            draw.line([(xy_x, y_start), (xy_x, y_end)], fill=(92, 64, 34), width=1)
            draw.line([(x_start, xy_y), (x_end, xy_y)], fill=(92, 64, 34), width=1)

        # Star points (3,3), (3,9), (9,3), (9,9), (6,6)
        for sr, sc in ((3, 3), (3, 9), (9, 3), (9, 9), (6, 6)):
            sx, sy = margin + sc * cell + cell / 2, margin + sr * cell + cell / 2
            draw.ellipse([sx - 4, sy - 4, sx + 4, sy + 4], fill=(64, 44, 22))

        win_set = {tuple(pt) for pt in (state.get("win_line") or [])}
        for r in range(GOMOKU_SIZE):
            for c in range(GOMOKU_SIZE):
                cx = margin + c * cell + cell / 2
                cy = margin + r * cell + cell / 2
                v = board[r][c]
                rad = cell * 0.40
                if v == 1:
                    draw.ellipse([cx - rad, cy - rad, cx + rad, cy + rad], fill=(24, 24, 28), outline=(72, 72, 80), width=1)
                elif v == 2:
                    draw.ellipse([cx - rad, cy - rad, cx + rad, cy + rad], fill=(246, 246, 248), outline=(145, 145, 152), width=1)
                elif show_coords and (r + c) % 2 == 0:
                    _draw_centered_text(draw, cx, cy, _coord_label(r, c), font_small, (162, 122, 72))

                if (r, c) in win_set:
                    draw.ellipse([cx - rad - 2, cy - rad - 2, cx + rad + 2, cy + rad + 2], outline=(245, 196, 48), width=3)
                elif last_move and last_move == [r, c]:
                    dot_col = (245, 196, 48)
                    draw.ellipse([cx - 4, cy - 4, cx + 4, cy + 4], fill=dot_col)

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


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
