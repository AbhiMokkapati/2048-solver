# 2048 Solver Sidekick

A small always-on-top desktop popup that watches a region of your screen for
a 2048 board, computes the best move with an expectimax AI, and shows it to
you — or plays it for you.

## Setup

A virtual environment is already set up in `.venv/` with all dependencies
installed. To recreate it from scratch:

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

(Uses Tkinter, which ships with standard Python on Windows.)

## Run

**Desktop shortcut:** double-click **"2048 Solver"** on your Desktop — it
launches via the venv's `pythonw.exe`, so no console window pops up.

**Manually:**

```powershell
.venv\Scripts\python.exe main.py
```

1. Open your 2048 game (e.g. play2048.co, or any clone using the classic
   tile color palette) so it's visible on screen.
2. Click **Calibrate board region**, then drag a box tightly around just the
   4x4 tile grid (outer edge to outer edge, not the score/title above it).
3. Click **Start Watching**. The popup will show the recommended next move
   as a big arrow, refreshed continuously.
4. Optionally check **Auto-play** to have it send the arrow key presses for
   you. Make sure the game window is focused (click it once) right before
   turning this on — the app remembers the currently-focused window at that
   moment and refocuses it before every keystroke.

Calibration is saved to `region.json` next to the script, so you don't have
to redo it if the game window doesn't move between runs.

## How it works

- **capture.py** — grabs the calibrated screen region via `mss` and provides
  a click-drag overlay for selecting it.
- **board_reader.py** — classifies each of the 16 cells by matching its
  median color against the standard 2048 tile palette. This is robust and
  fast, and needs no OCR install. Tiles ≥4096 share one dark background
  color in the reference palette, so the reader remembers the last known
  value for those cells rather than guessing on every frame.
- **solver.py** — an expectimax search (4 player-move branches, chance nodes
  for 2/4 tile spawns) with a heuristic combining empty-cell count, row/column
  monotonicity, smoothness, and a "snake" corner-weighting that keeps the
  largest tiles corralled in one corner. Search depth adapts to how full the
  board is (deeper when few empty cells remain, since the game gets sharper).
- **main.py** — the Tkinter popup UI and the watch/autoplay loop (runs on a
  background thread so the UI stays responsive).

## Limitations

- Color-based reading assumes the classic 2048 palette. A visually
  restyled clone may need new colors added to `PALETTE` in `board_reader.py`.
- Tiles ≥4096 aren't color-distinguishable from each other in the reference
  palette (they're all rendered in dark grey), so very late-game accuracy
  can degrade slightly — this is a cosmetic limitation of the source game's
  own palette, not the solver.
- Auto-play relies on the game window staying the one that was focused at
  calibration/toggle time; if you switch windows while it's running, either
  turn autoplay off first or re-toggle it after refocusing the game.

## Test

`test_solver.py` runs a headless self-play simulation (no screen involved)
to sanity-check the search/heuristics:

```bash
python test_solver.py
```
