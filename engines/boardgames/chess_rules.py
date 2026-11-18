"""FIDE Chess rules (castling, en passant, promotion, check/stalemate, 3-fold) and alpha-beta AI."""

import copy
import math
import random
import re
from typing import Optional

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


