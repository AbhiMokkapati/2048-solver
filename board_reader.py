"""Reads a 4x4 2048 board from a screen region using tile background color.

Works out of the box with the classic 2048 palette (play2048.co and most
clones/forks that reuse it). The board region must be calibrated once by the
user so we know the pixel rectangle of the playing grid; cell centers are
then computed assuming a uniform 4x4 grid with equal padding, which matches
how virtually every 2048 implementation lays out its board.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from PIL import Image

# value -> RGB, standard 2048 palette
PALETTE = {
    0: (205, 193, 180),
    2: (238, 228, 218),
    4: (237, 224, 200),
    8: (242, 177, 121),
    16: (245, 149, 99),
    32: (246, 124, 95),
    64: (246, 94, 59),
    128: (237, 207, 114),
    256: (237, 204, 97),
    512: (237, 200, 80),
    1024: (237, 197, 63),
    2048: (237, 194, 46),
}
# Tiles above 2048 all share this dark background in the reference palette.
HIGH_TILE_COLOR = (60, 58, 50)
HIGH_TILE_START = 4096


@dataclass
class Region:
    left: int
    top: int
    width: int
    height: int

    def to_dict(self):
        return {"left": self.left, "top": self.top, "width": self.width, "height": self.height}

    @staticmethod
    def from_dict(d):
        return Region(d["left"], d["top"], d["width"], d["height"])


def _nearest_value(rgb: tuple[int, int, int]) -> tuple[int, float]:
    best_val, best_dist = 0, float("inf")
    for val, color in PALETTE.items():
        d = sum((a - b) ** 2 for a, b in zip(rgb, color))
        if d < best_dist:
            best_dist, best_val = d, val
    d_high = sum((a - b) ** 2 for a, b in zip(rgb, HIGH_TILE_COLOR))
    if d_high < best_dist:
        return HIGH_TILE_START, d_high
    return best_val, best_dist


class BoardReader:
    def __init__(self, region: Region, grid_padding_frac: float = 0.08):
        self.region = region
        self.grid_padding_frac = grid_padding_frac
        # Sticky memory so ambiguous "high tile" cells (4096+) don't flicker;
        # we assume such a cell keeps doubling only when we can't tell values
        # apart, which is rare and only affects very late-game play.
        self._last_high_values: dict[tuple[int, int], int] = {}

    def cell_centers(self) -> list[list[tuple[int, int]]]:
        w, h = self.region.width, self.region.height
        cell_w, cell_h = w / 4, h / 4
        centers = []
        for r in range(4):
            row = []
            for c in range(4):
                cx = self.region.left + int((c + 0.5) * cell_w)
                cy = self.region.top + int((r + 0.5) * cell_h)
                row.append((cx, cy))
            centers.append(row)
        return centers

    def read(self, screenshot_img: Image.Image, img_origin: tuple[int, int]) -> list[list[int]]:
        """screenshot_img: a PIL image already captured (e.g. covering the region
        or the full screen); img_origin: the (left, top) the image's pixel (0,0)
        corresponds to in screen coordinates."""
        arr = np.asarray(screenshot_img.convert("RGB"))
        ox, oy = img_origin
        w, h = self.region.width, self.region.height
        cell_w, cell_h = w / 4, h / 4
        pad_w, pad_h = cell_w * self.grid_padding_frac, cell_h * self.grid_padding_frac

        board = [[0] * 4 for _ in range(4)]
        for r in range(4):
            for c in range(4):
                cx = self.region.left + (c + 0.5) * cell_w
                cy = self.region.top + (r + 0.5) * cell_h
                x0 = int(cx - (cell_w / 2 - pad_w) / 2 - ox)
                x1 = int(cx + (cell_w / 2 - pad_w) / 2 - ox)
                y0 = int(cy - (cell_h / 2 - pad_h) / 2 - oy)
                y1 = int(cy + (cell_h / 2 - pad_h) / 2 - oy)
                x0, x1 = max(0, x0), min(arr.shape[1], max(x0 + 1, x1))
                y0, y1 = max(0, y0), min(arr.shape[0], max(y0 + 1, y1))
                patch = arr[y0:y1, x0:x1].reshape(-1, 3)
                if patch.size == 0:
                    board[r][c] = 0
                    continue
                med = tuple(int(v) for v in np.median(patch, axis=0))
                val, dist = _nearest_value(med)

                if val == HIGH_TILE_START:
                    prev = self._last_high_values.get((r, c), HIGH_TILE_START)
                    val = prev
                    self._last_high_values[(r, c)] = prev
                else:
                    self._last_high_values.pop((r, c), None)

                board[r][c] = val
        return board

    def bump_high_tile(self, r: int, c: int):
        """Call when solver logic infers a high tile likely doubled (e.g. after
        a merge involving that cell) to keep sticky memory roughly accurate."""
        cur = self._last_high_values.get((r, c), HIGH_TILE_START)
        self._last_high_values[(r, c)] = cur * 2
