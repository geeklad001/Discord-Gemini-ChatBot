"""PIL vector renderer for Othello, Chess, Connect 4, and Gomoku boards."""

import io
from typing import Optional

from PIL import Image, ImageDraw

from core.rendering import draw_centered_text as _draw_centered_text
from core.rendering import load_font as _load_font
from engines.boardgames.othello import _coord_label, get_othello_legal_moves
from engines.boardgames.connect4_gomoku import C4_COLS, C4_ROWS, GOMOKU_SIZE, get_connect4_legal_cols
from engines.boardgames.chess_rules import _chess_parse_sq, _chess_sq_name, get_chess_legal_moves

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

