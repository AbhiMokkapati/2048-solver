"""Screen capture + interactive region selection."""
from __future__ import annotations

import tkinter as tk

import mss
from PIL import Image

from board_reader import Region


def grab(region: Region) -> tuple[Image.Image, tuple[int, int]]:
    with mss.mss() as sct:
        shot = sct.grab(region.to_dict())
        img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
    return img, (region.left, region.top)


def select_region(parent: tk.Misc | None = None) -> Region | None:
    """Show a fullscreen transparent overlay; user drags a rectangle over the
    2048 board. Returns the selected Region, or None if cancelled (Esc)."""
    result: dict = {}

    win = tk.Toplevel(parent) if parent is not None else tk.Tk()
    win.attributes("-fullscreen", True)
    win.attributes("-alpha", 0.25)
    win.attributes("-topmost", True)
    win.configure(bg="black")
    win.config(cursor="crosshair")

    canvas = tk.Canvas(win, bg="gray12", highlightthickness=0)
    canvas.pack(fill="both", expand=True)
    canvas.create_text(
        20, 20, anchor="nw", fill="white",
        font=("Segoe UI", 14, "bold"),
        text="Drag a box around the 2048 board (the 4x4 tile grid only). Esc to cancel.",
    )

    state = {"x0": 0, "y0": 0, "rect": None}

    def on_press(event):
        state["x0"], state["y0"] = event.x_root, event.y_root
        state["rect"] = canvas.create_rectangle(
            event.x, event.y, event.x, event.y, outline="#4caf50", width=3
        )

    def on_drag(event):
        if state["rect"] is None:
            return
        x0 = state["x0"] - win.winfo_rootx()
        y0 = state["y0"] - win.winfo_rooty()
        canvas.coords(state["rect"], x0, y0, event.x, event.y)

    def on_release(event):
        x1, y1 = event.x_root, event.y_root
        left, top = min(state["x0"], x1), min(state["y0"], y1)
        width, height = abs(x1 - state["x0"]), abs(y1 - state["y0"])
        if width > 10 and height > 10:
            result["region"] = Region(left, top, width, height)
        win.destroy()

    def on_escape(_event):
        win.destroy()

    canvas.bind("<ButtonPress-1>", on_press)
    canvas.bind("<B1-Motion>", on_drag)
    canvas.bind("<ButtonRelease-1>", on_release)
    win.bind("<Escape>", on_escape)

    win.grab_set()
    win.wait_window()

    return result.get("region")
