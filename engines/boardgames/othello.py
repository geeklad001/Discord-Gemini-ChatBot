"""Othello / Reversi rules, coordinate parser, and alpha-beta AI."""

import math
import random
import re
from typing import Optional

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


