"""2048 Solver Sidekick — a small always-on-top popup that watches a screen
region for a 2048 board, computes the best move with an expectimax AI, and
either shows you the move to make or plays it for you.

Run: python main.py
"""
from __future__ import annotations

import ctypes
import json
import os
import threading
import time
import tkinter as tk
from tkinter import ttk

import pyautogui

from board_reader import BoardReader, Region
from capture import grab, select_region
from solver import best_move, board_from_values

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "region.json")

ARROWS = {"up": "↑", "down": "↓", "left": "←", "right": "→"}
KEY_MAP = {"up": "up", "down": "down", "left": "left", "right": "right"}

user32 = ctypes.windll.user32 if os.name == "nt" else None
kernel32 = ctypes.windll.kernel32 if os.name == "nt" else None

GWL_STYLE = -16
GWL_EXSTYLE = -20
WS_CHILD = 0x40000000
WS_EX_NOACTIVATE = 0x08000000
WS_EX_TOOLWINDOW = 0x00000080
SW_SHOWNOACTIVATE = 4
HWND_TOPMOST = -1
SWP_NOMOVE = 0x0002
SWP_NOSIZE = 0x0001
SWP_NOACTIVATE = 0x0010

_OWN_PID = os.getpid() if os.name == "nt" else None


def get_foreground_window():
    return user32.GetForegroundWindow() if user32 else None


def set_foreground_window(hwnd):
    if user32 and hwnd:
        user32.SetForegroundWindow(hwnd)


def is_own_process_window(hwnd) -> bool:
    if not user32 or not hwnd:
        return False
    pid = ctypes.c_ulong()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value == _OWN_PID


def _resolve_toplevel_hwnd(tk_hwnd: int) -> int:
    style = user32.GetWindowLongW(tk_hwnd, GWL_STYLE)
    if style & WS_CHILD:
        return user32.GetParent(tk_hwnd)
    return tk_hwnd


def make_noactivate(root: tk.Tk) -> int | None:
    """Mark the popup as a non-activating tool window so clicking it never
    steals keyboard focus away from the game — that's what previously forced
    the user to re-click the game window before every manual move."""
    if not user32:
        return None
    root.update_idletasks()
    hwnd = _resolve_toplevel_hwnd(root.winfo_id())
    ex = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
    user32.SetWindowLongW(hwnd, GWL_EXSTYLE, ex | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW)
    return hwnd


def show_noactivate(hwnd: int | None):
    if not user32 or not hwnd:
        return
    user32.ShowWindow(hwnd, SW_SHOWNOACTIVATE)
    user32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)


class SolverApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("2048 Solver")
        self.root.attributes("-topmost", True)
        self.root.resizable(False, False)
        self._place_window()

        self.region: Region | None = self._load_region()
        self.reader: BoardReader | None = BoardReader(self.region) if self.region else None

        self.watching = False
        self.autoplay = tk.BooleanVar(value=False)
        self.speed_ms = tk.IntVar(value=350)
        self.game_hwnd = None
        self._worker: threading.Thread | None = None
        self._stop_event = threading.Event()

        self._build_ui()
        self._refresh_status()
        self._track_foreground_window()

    # ---------- persistence ----------
    def _load_region(self) -> Region | None:
        if os.path.exists(CONFIG_PATH):
            try:
                with open(CONFIG_PATH) as f:
                    return Region.from_dict(json.load(f))
            except Exception:
                return None
        return None

    def _save_region(self):
        if self.region:
            with open(CONFIG_PATH, "w") as f:
                json.dump(self.region.to_dict(), f)

    # ---------- window placement ----------
    def _place_window(self):
        w, h = 300, 430
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        x = sw - w - 10
        y = (sh - h) // 3
        self.root.geometry(f"{w}x{h}+{x}+{y}")

    # ---------- UI ----------
    def _build_ui(self):
        pad = {"padx": 10, "pady": 6}

        title = ttk.Label(self.root, text="2048 Solver", font=("Segoe UI", 16, "bold"))
        title.pack(**pad)

        self.region_label = ttk.Label(self.root, text="Region: not calibrated", wraplength=270)
        self.region_label.pack(**pad)

        ttk.Button(self.root, text="Calibrate board region", command=self.on_calibrate).pack(**pad)

        self.watch_btn = ttk.Button(self.root, text="▶  Start", command=self.on_toggle_watch)
        self.watch_btn.pack(**pad)

        autoplay_chk = ttk.Checkbutton(
            self.root, text="Auto-play (sends key presses)", variable=self.autoplay,
        )
        autoplay_chk.pack(**pad)

        speed_frame = ttk.Frame(self.root)
        speed_frame.pack(**pad, fill="x")
        ttk.Label(speed_frame, text="Move interval (ms)").pack(side="left")
        ttk.Scale(speed_frame, from_=100, to=1000, variable=self.speed_ms, orient="horizontal").pack(
            side="left", fill="x", expand=True, padx=8
        )

        self.move_label = ttk.Label(self.root, text="—", font=("Segoe UI", 48, "bold"))
        self.move_label.pack(pady=10)

        self.move_text = ttk.Label(self.root, text="Best move: -", font=("Segoe UI", 12))
        self.move_text.pack(**pad)

        self.status_label = ttk.Label(self.root, text="Idle", foreground="#666")
        self.status_label.pack(**pad)

        hint = ttk.Label(
            self.root,
            text="This popup never steals focus from your game — just watch "
                 "the arrow and press the key, no re-clicking needed.",
            wraplength=270, foreground="#888", font=("Segoe UI", 8),
        )
        hint.pack(**pad)

    def _refresh_status(self):
        if self.region:
            self.region_label.config(
                text=f"Region: {self.region.width}x{self.region.height} @ ({self.region.left},{self.region.top})"
            )
        else:
            self.region_label.config(text="Region: not calibrated")

    # ---------- foreground window tracking ----------
    def _track_foreground_window(self):
        """Continuously remember the last real (non-popup) foreground window,
        so Start/Auto-play always target the actual game — not whatever
        happened to have OS focus at the instant a button was clicked."""
        fg = get_foreground_window()
        if fg and not is_own_process_window(fg):
            self.game_hwnd = fg
        self.root.after(200, self._track_foreground_window)

    # ---------- calibration ----------
    def on_calibrate(self):
        region = select_region(self.root)
        if region:
            self.region = region
            self.reader = BoardReader(region)
            self._save_region()
            self._refresh_status()
            self.status_label.config(text="Calibrated.")

    # ---------- watch loop ----------
    def on_toggle_watch(self):
        if self.watching:
            self._stop_watch()
        else:
            self._start_watch()

    def _start_watch(self):
        if not self.region:
            self.status_label.config(text="Calibrate the board region first.")
            return
        self.watching = True
        self.watch_btn.config(text="■  Stop")
        self._stop_event.clear()
        self._worker = threading.Thread(target=self._watch_loop, daemon=True)
        self._worker.start()

    def _stop_watch(self):
        self.watching = False
        self._stop_event.set()
        self.watch_btn.config(text="▶  Start")
        self.status_label.config(text="Stopped.")

    def _read_stable_board(self, max_wait: float = 1.2, poll_interval: float = 0.05):
        """Poll the region until two consecutive reads agree, so we never hand
        the solver a frame captured mid tile-slide animation (those in-between
        colors don't match any tile in the palette and can misread values,
        which looks like "bad moves" even though the algorithm is fine)."""
        prev = None
        deadline = time.time() + max_wait
        while time.time() < deadline:
            img, origin = grab(self.region)
            values = self.reader.read(img, origin)
            board = board_from_values(values)
            if prev is not None and (board == prev).all():
                return board
            prev = board
            time.sleep(poll_interval)
        return prev

    def _watch_loop(self):
        last_board = None
        while not self._stop_event.is_set():
            try:
                board = self._read_stable_board()
                if board is None:
                    time.sleep(0.1)
                    continue

                if last_board is not None and (board == last_board).all():
                    self._set_status("Waiting for a move to register…")
                else:
                    move = best_move(board)
                    last_board = board
                    if move is None:
                        self._set_move(None, "Game over (no moves left).")
                    else:
                        self._set_move(move, f"Best move: {move.upper()}")
                        if self.autoplay.get():
                            self._send_key(move)
                            time.sleep(0.12)  # let the slide animation start before the next stable-read poll
            except Exception as e:  # keep the loop alive on transient read errors
                self._set_status(f"Error: {e}")

            time.sleep(max(self.speed_ms.get(), 50) / 1000)

    def _send_key(self, move: str):
        if self.game_hwnd:
            set_foreground_window(self.game_hwnd)
            time.sleep(0.03)
        pyautogui.press(KEY_MAP[move])

    # ---------- thread-safe UI updates ----------
    def _set_move(self, move: str | None, text: str):
        def apply():
            self.move_label.config(text=ARROWS.get(move, "—"))
            self.move_text.config(text=text)
            self.status_label.config(text="Watching…" if self.watching else "Idle")
        self.root.after(0, apply)

    def _set_status(self, text: str):
        self.root.after(0, lambda: self.status_label.config(text=text))


def main():
    prev_fg = get_foreground_window()  # whatever the user had focused before launching us

    root = tk.Tk()
    root.withdraw()  # build the UI off-screen first so it never activates on initial show
    app = SolverApp(root)

    hwnd = make_noactivate(root)
    show_noactivate(hwnd)

    # Windows can still grant a brand-new process's first window a one-time
    # foreground steal on startup regardless of WS_EX_NOACTIVATE. Hand focus
    # straight back so the game never loses it, even for a moment.
    if prev_fg and not is_own_process_window(prev_fg):
        root.after(150, lambda: set_foreground_window(prev_fg))

    def on_close():
        app._stop_event.set()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)
    root.mainloop()


if __name__ == "__main__":
    main()
