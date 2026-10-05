"""Expectimax-based 2048 solver.

Board representation: 4x4 tuple-of-tuples of ints, where each cell holds the
actual tile value (0 = empty, 2, 4, 8, ...). Internally we work with a numpy
array for speed.
"""
from __future__ import annotations

import math
import time

import numpy as np

SIZE = 4
MOVES = ("up", "down", "left", "right")

# Heuristic weights, tuned for the classic corner-snake strategy.
WEIGHT_EMPTY = 2.7
WEIGHT_MONO = 1.0
WEIGHT_SMOOTH = 0.1
WEIGHT_MAX_CORNER = 1.0
WEIGHT_MERGE = 1.5

# The four corner cells and, for each, the snake weight matrix that treats it
# as the anchor. best_move() picks whichever corner currently holds the max
# tile and locks onto it (the classic "never let the biggest tile leave the
# corner" strategy), rather than only hard-coding top-left.
_CORNERS = ((0, 0), (0, 3), (3, 0), (3, 3))

_BASE_SNAKE = np.array(
    [
        [2 ** 15, 2 ** 14, 2 ** 13, 2 ** 12],
        [2 ** 8, 2 ** 9, 2 ** 10, 2 ** 11],
        [2 ** 7, 2 ** 6, 2 ** 5, 2 ** 4],
        [2 ** 0, 2 ** 1, 2 ** 2, 2 ** 3],
    ],
    dtype=np.float64,
)
_SNAKE_VARIANTS = {
    (0, 0): _BASE_SNAKE,
    (0, 3): _BASE_SNAKE[:, ::-1],
    (3, 0): _BASE_SNAKE[::-1, :],
    (3, 3): _BASE_SNAKE[::-1, ::-1],
}
_SNAKE = _SNAKE_VARIANTS[(0, 0)]  # default/legacy export used by evaluate()

# Extra penalty (scaled to the current max tile, not a flat constant) applied
# when a move knocks the max tile out of the corner it was already anchored
# in. This is deliberately a *soft* nudge on top of the search's own score,
# not an absolute veto: an earlier version made it an effectively-infinite
# override, which turned out to actively hurt play (self-play regression
# tests scored lower with it than without) by forcing moves the search had
# already correctly identified as worse. The properly corner-oriented snake
# heuristic in evaluate() already does most of the real work here.
CORNER_LOCK_PENALTY_MULT = 40
# Only start nudging once there's an actual big tile worth protecting —
# early/mid game the board is too fluid for this to matter or help.
CORNER_LOCK_MIN_VALUE = 128

# Default thinking time per move; best_move() uses iterative deepening and
# will return the deepest fully-completed search within this budget.
DEFAULT_TIME_BUDGET = 0.25


def _compress_and_merge(row: np.ndarray) -> tuple[np.ndarray, int, bool]:
    """Slide a single row left, merging equal tiles. Returns (new_row, gained_score, moved)."""
    nonzero = row[row != 0]
    merged = []
    score = 0
    i = 0
    n = len(nonzero)
    while i < n:
        if i + 1 < n and nonzero[i] == nonzero[i + 1]:
            v = int(nonzero[i]) * 2
            merged.append(v)
            score += v
            i += 2
        else:
            merged.append(int(nonzero[i]))
            i += 1
    new_row = np.zeros(SIZE, dtype=row.dtype)
    new_row[: len(merged)] = merged
    moved = not np.array_equal(new_row, row)
    return new_row, score, moved


def apply_move(board: np.ndarray, move: str) -> tuple[np.ndarray, int, bool]:
    """Apply a move to the board. Returns (new_board, score_gained, moved)."""
    b = board.copy()
    total_score = 0
    any_moved = False

    if move == "left":
        rows = range(SIZE)
        transform = lambda r: r
        inverse = lambda r: r
    elif move == "right":
        transform = lambda r: r[::-1]
        inverse = lambda r: r[::-1]
    elif move == "up":
        b = b.T
        transform = lambda r: r
        inverse = lambda r: r
    elif move == "down":
        b = b.T
        transform = lambda r: r[::-1]
        inverse = lambda r: r[::-1]
    else:
        raise ValueError(move)

    for i in range(SIZE):
        row = transform(b[i])
        new_row, score, moved = _compress_and_merge(row)
        b[i] = inverse(new_row)
        total_score += score
        any_moved = any_moved or moved

    if move in ("up", "down"):
        b = b.T

    return b, total_score, any_moved


def empty_cells(board: np.ndarray) -> list[tuple[int, int]]:
    ys, xs = np.where(board == 0)
    return list(zip(ys.tolist(), xs.tolist()))


def _monotonicity(board: np.ndarray) -> float:
    """Reward rows/cols that are monotonic (increasing or decreasing)."""
    score = 0.0
    logs = np.where(board > 0, np.log2(np.maximum(board, 1)), 0)
    for i in range(SIZE):
        row = logs[i]
        col = logs[:, i]
        for line in (row, col):
            inc = sum(max(0.0, line[j + 1] - line[j]) for j in range(SIZE - 1))
            dec = sum(max(0.0, line[j] - line[j + 1]) for j in range(SIZE - 1))
            score -= min(inc, dec)
    return score


def _smoothness(board: np.ndarray) -> float:
    logs = np.where(board > 0, np.log2(np.maximum(board, 1)), 0)
    score = 0.0
    for r in range(SIZE):
        for c in range(SIZE):
            if logs[r, c] == 0:
                continue
            if c + 1 < SIZE and logs[r, c + 1] != 0:
                score -= abs(logs[r, c] - logs[r, c + 1])
            if r + 1 < SIZE and logs[r + 1, c] != 0:
                score -= abs(logs[r, c] - logs[r + 1, c])
    return score


def _merge_potential(board: np.ndarray) -> float:
    """Count adjacent equal-tile pairs — more of these means more merges are
    imminently available, a standard 2048 heuristic tip (keep tiles that can
    combine next to each other rather than scattered)."""
    count = 0
    for r in range(SIZE):
        for c in range(SIZE):
            v = board[r, c]
            if v == 0:
                continue
            if c + 1 < SIZE and board[r, c + 1] == v:
                count += 1
            if r + 1 < SIZE and board[r + 1, c] == v:
                count += 1
    return float(count)


def max_tile_corner(board: np.ndarray) -> tuple[int, int] | None:
    """Which corner (if any) currently holds the board's max tile."""
    r, c = np.unravel_index(np.argmax(board), board.shape)
    cell = (int(r), int(c))
    return cell if cell in _CORNERS else None


def evaluate(board: np.ndarray, snake: np.ndarray = _SNAKE) -> float:
    empty = len(empty_cells(board))
    mono = _monotonicity(board)
    smooth = _smoothness(board)
    merge = _merge_potential(board)
    corner = float(np.sum(board * snake))
    return (
        WEIGHT_EMPTY * empty
        + WEIGHT_MONO * mono
        + WEIGHT_SMOOTH * smooth
        + WEIGHT_MERGE * merge
        + WEIGHT_MAX_CORNER * corner
    )


def _search(board: np.ndarray, depth: int, is_player_turn: bool, snake: np.ndarray) -> float:
    if depth <= 0:
        return evaluate(board, snake)

    if is_player_turn:
        best = -math.inf
        any_move = False
        for move in MOVES:
            new_board, _, moved = apply_move(board, move)
            if not moved:
                continue
            any_move = True
            val = _search(new_board, depth - 1, False, snake)
            if val > best:
                best = val
        if not any_move:
            return evaluate(board, snake)
        return best
    else:
        cells = empty_cells(board)
        if not cells:
            return evaluate(board, snake)
        # Sample at most 6 empty cells for speed when the board is sparse.
        if len(cells) > 6:
            step = len(cells) / 6
            cells = [cells[int(i * step)] for i in range(6)]
        total = 0.0
        for (r, c) in cells:
            for value, prob in ((2, 0.9), (4, 0.1)):
                b2 = board.copy()
                b2[r, c] = value
                total += prob * _search(b2, depth - 1, True, snake)
        return total / len(cells)


def _base_depth(board: np.ndarray) -> int:
    empties = len(empty_cells(board))
    if empties >= 8:
        return 3
    elif empties >= 4:
        return 4
    elif empties >= 2:
        return 5
    return 6


def best_move(board: np.ndarray, time_budget: float = DEFAULT_TIME_BUDGET) -> str | None:
    """Return the best move ('up'/'down'/'left'/'right') or None if game over.

    Two well-known 2048 tips are baked in on top of the raw expectimax score:
    1) once there's a sizeable max tile, moves that knock it out of its
       current corner get a scaled penalty (nudging the search rather than
       overriding it — see CORNER_LOCK_PENALTY_MULT); and
    2) the search's own heuristic (evaluate) is oriented around whichever
       corner currently holds the max tile the whole way down, not just at
       the top level, so it's never fighting against the wrong anchor.
    Search depth adapts via iterative deepening: it keeps going deeper until
    `time_budget` seconds have elapsed, returning the deepest fully-completed
    result — this plays noticeably stronger than a fixed shallow depth while
    still staying responsive for live play.
    """
    legal_moves = [m for m in MOVES if apply_move(board, m)[2]]
    if not legal_moves:
        return None

    locked_corner = max_tile_corner(board)
    max_val = int(board.max())
    lock_active = locked_corner is not None and max_val >= CORNER_LOCK_MIN_VALUE
    snake = _SNAKE_VARIANTS[locked_corner] if locked_corner is not None else _SNAKE

    start = time.perf_counter()
    base_depth = _base_depth(board)
    max_depth = min(8, base_depth + 2)
    depth = base_depth
    best_mv = legal_moves[0]

    while True:
        iter_start = time.perf_counter()
        best_val = -math.inf
        depth_best_mv = None
        for move in legal_moves:
            new_board, score, _ = apply_move(board, move)
            val = score * 0.1 + _search(new_board, depth - 1, False, snake)
            if lock_active and max_tile_corner(new_board) != locked_corner:
                val -= max_val * CORNER_LOCK_PENALTY_MULT
            if val > best_val:
                best_val = val
                depth_best_mv = move
        best_mv = depth_best_mv

        now = time.perf_counter()
        elapsed = now - start
        iter_duration = now - iter_start
        if depth >= max_depth or elapsed >= time_budget:
            break
        # Each extra ply of depth typically costs several times more than the
        # last (branching from both move choices and tile-spawn chance nodes)
        # — bail out before starting an iteration that would badly overshoot
        # the time budget rather than only detecting it after the fact.
        if elapsed + iter_duration * 8 > time_budget:
            break
        depth += 1

    return best_mv


def board_from_values(values: list[list[int]]) -> np.ndarray:
    return np.array(values, dtype=np.int64)
