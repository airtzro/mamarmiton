"""Floating bar with the running step timers. Owned by the App so timers outlive the screens."""

from __future__ import annotations

import sys
import time
from dataclasses import dataclass, field

import customtkinter as ctk

from mamarmiton.timers import format_clock
from mamarmiton.ui import art, theme

RING_TIMES = 4
RING_GAP_MS = 700
FLASH_MS = 500


def beep(widget) -> None:
    """One audible signal: the Windows system sound when available, the Tk bell otherwise."""
    if sys.platform.startswith("win"):
        try:
            import winsound

            winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
            return
        except Exception:
            pass
    try:
        widget.bell()
    except Exception:
        pass


@dataclass
class Timer:
    label: str
    end: float  # time.monotonic() value
    row: ctk.CTkFrame | None = None
    text: ctk.CTkLabel | None = None
    finished: bool = False
    flash: bool = False
    widgets: dict = field(default_factory=dict)


class TimerBar(ctk.CTkFrame):
    def __init__(self, master):
        super().__init__(master, fg_color=theme.SURFACE, border_width=1, border_color=theme.BORDER_STRONG,
                         corner_radius=theme.RADIUS)
        self.timers: list[Timer] = []
        self._job = None
        self._flash_job = None
        self._above_nav = False

    # ------------------------------------------------------------------ api
    def start(self, label: str, seconds: int) -> Timer:
        timer = Timer(label=label, end=time.monotonic() + seconds)
        row = ctk.CTkFrame(self, fg_color="transparent", corner_radius=8)
        row.pack(fill="x", padx=6, pady=3)
        timer.row = row
        timer.text = ctk.CTkLabel(row, text="", font=theme.font(15, "bold"), text_color=theme.TEXT, anchor="w",
                                  image=art.icon("clock", theme.ACCENT_TEXT, 16), compound="left")
        timer.text.pack(side="left", padx=(10, 6), pady=4)
        ctk.CTkButton(row, text="", width=30, height=30, corner_radius=15, fg_color="transparent",
                      hover_color=theme.SURFACE_ALT, image=art.icon("close", theme.MUTED, 14),
                      command=lambda t=timer: self.cancel(t)).pack(side="right", padx=(4, 4))
        self.timers.append(timer)
        self._show()
        self._tick()
        return timer

    def cancel(self, timer: Timer) -> None:
        if timer in self.timers:
            self.timers.remove(timer)
            timer.row.destroy()
        if not self.timers:
            self.place_forget()

    @property
    def running(self) -> list[Timer]:
        return [t for t in self.timers if not t.finished]

    def clear(self) -> None:
        for timer in list(self.timers):
            self.cancel(timer)

    # ------------------------------------------------------------- internals
    def dock(self, above_nav: bool) -> None:
        """Top-right normally; above the big buttons in kitchen mode, which uses the top-right itself."""
        self._above_nav = above_nav
        if self.timers:
            self._show()

    def _show(self) -> None:
        if self._above_nav:
            self.place(relx=0.5, rely=1.0, anchor="s", x=0, y=-120)
        else:
            self.place(relx=1.0, rely=0.0, anchor="ne", x=-20, y=16)
        self.lift()
        self._schedule()

    def _schedule(self) -> None:
        if self._job is None:
            self._job = self.after(250, self._on_tick)
        if self._flash_job is None and any(t.finished for t in self.timers):
            self._flash_job = self.after(FLASH_MS, self._on_flash)

    def _on_tick(self) -> None:
        self._job = None
        self._tick()
        if self.timers:
            self._schedule()

    def _tick(self) -> None:
        now = time.monotonic()
        for timer in list(self.timers):
            if timer.finished:
                continue
            left = timer.end - now
            if left <= 0:
                self._finish(timer)
            else:
                timer.text.configure(text=f"{timer.label} · {format_clock(int(left + 0.999))}")

    def _finish(self, timer: Timer) -> None:
        timer.finished = True
        timer.text.configure(text=f"{timer.label} · Terminé !", text_color=theme.ON_ACCENT)
        timer.row.configure(fg_color=theme.ACCENT)
        self.lift()
        self._ring(RING_TIMES)
        self._schedule()

    def _ring(self, left: int) -> None:
        if left <= 0:
            return
        beep(self)
        try:
            self.after(RING_GAP_MS, lambda: self._ring(left - 1))
        except Exception:
            pass

    def _on_flash(self) -> None:
        self._flash_job = None
        finished = [t for t in self.timers if t.finished]
        for timer in finished:
            timer.flash = not timer.flash
            timer.row.configure(fg_color=theme.ACCENT_HOVER if timer.flash else theme.ACCENT)
        if finished:
            self._flash_job = self.after(FLASH_MS, self._on_flash)
