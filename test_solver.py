"""Quick self-play smoke test for the solver (not a screen-reading test)."""
import random

import numpy as np

from solver import apply_move, best_move, empty_cells


def spawn_tile(board):
    cells = empty_cells(board)
    if not cells:
        return board
    r, c = random.choice(cells)
    board[r, c] = 4 if random.random() < 0.1 else 2
    return board


def play_one_game(max_moves=4000):
    board = np.zeros((4, 4), dtype=np.int64)
    spawn_tile(board)
    spawn_tile(board)
    moves = 0
    while moves < max_moves:
        mv = best_move(board)
        if mv is None:
            break
        board, _, moved = apply_move(board, mv)
        if not moved:
            break
        spawn_tile(board)
        moves += 1
    return int(board.max()), moves


if __name__ == "__main__":
    random.seed(1)
    best_tile, moves = play_one_game()
    print(f"Max tile reached: {best_tile} in {moves} moves")
    assert best_tile >= 512, "solver seems broken — didn't reach 512"
    print("OK")
