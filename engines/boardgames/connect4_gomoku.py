"""Connect 4 & Gomoku (13x13) rules, heuristics, and AI."""

import math
import random
from typing import Optional

from engines.boardgames.othello import _coord_label, _parse_grid_coord

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
        val = _c4_alphabeta(nb, 3 - player, player, depth - 1, best_val, math.inf)
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


