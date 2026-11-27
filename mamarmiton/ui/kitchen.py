"""Kitchen mode: one big step at a time, readable from across the counter."""

from __future__ import annotations

import ctypes
import sys
from fractions import Fraction
from typing import TYPE_CHECKING

import customtkinter as ctk

from mamarmiton.models import Recipe
from mamarmiton.quantities import scale_quantity
from mamarmiton.timers import find_durations, format_short
from mamarmiton.ui import art, theme
from mamarmiton.ui.widgets import ScrollFrame

if TYPE_CHECKING:
    from mamarmiton.ui.app import App

ES_CONTINUOUS = 0x80000000
ES_DISPLAY_REQUIRED = 0x00000002


def keep_awake(on: bool) -> bool:
    """Ask Windows not to turn the screen off (``on``) or release that request. No-op elsewhere."""
    if not sys.platform.startswith("win"):
        return False
    try:
        flags = ES_CONTINUOUS | (ES_DISPLAY_REQUIRED if on else 0)
        ctypes.windll.kernel32.SetThreadExecutionState(flags)
        return True
    except Exception:
        return False


def step_font_size(text: str) -> int:
    n = len(text)
    return 36 if n < 140 else 30 if n < 280 else 25


class KitchenView(ctk.CTkFrame):
    """Covers the whole window. Created by :meth:`App.open_kitchen`, destroyed by :meth:`close`."""

    PANEL_W = 300

    def __init__(self, app: "App", recipe: Recipe, servings: int | None = None, start: int = 0):
        super().__init__(app, fg_color=theme.BG, corner_radius=0)
        self.app = app
        self.recipe = recipe
        self.steps = list(recipe.etapes)
        self.index = max(0, min(start, len(self.steps) - 1))
        self.servings = servings or recipe.pour
        self.show_ingredients = False

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)  # rows: top, progress, step, "ensuite", buttons

        top = ctk.CTkFrame(self, fg_color="transparent")
        top.grid(row=0, column=0, columnspan=2, sticky="ew", padx=28, pady=(20, 6))
        self.counter = ctk.CTkLabel(top, text="", font=theme.serif(26), text_color=theme.ACCENT_TEXT)
        self.counter.pack(side="left")
        self.btn_quit = theme.secondary_button(top, text="Quitter", width=0, height=46, compound="left",
                                               image=art.icon("close", theme.TEXT, 16), command=self.close)
        self.btn_quit.pack(side="right")
        self.btn_ing = theme.secondary_button(top, text="Ingrédients", width=0, height=46, compound="left",
                                              image=art.icon("pot", theme.TEXT, 18), command=self.toggle_ingredients)
        self.btn_ing.pack(side="right", padx=(0, 10))
        self.title = ctk.CTkLabel(top, text=recipe.nom, font=theme.font(15), text_color=theme.MUTED, anchor="w")
        self.title.pack(side="left", fill="x", expand=True, padx=(18, 12))

        # one segment per step: where you are in the recipe, readable from across the kitchen
        self.segments = []
        if len(self.steps) > 1:
            bar = ctk.CTkFrame(self, fg_color="transparent")
            bar.grid(row=1, column=0, columnspan=2, sticky="ew", padx=28, pady=(4, 4))
            for i in range(len(self.steps)):
                bar.grid_columnconfigure(i, weight=1, uniform="seg")
                seg = ctk.CTkFrame(bar, height=7, corner_radius=4, fg_color=theme.BORDER)
                seg.grid(row=0, column=i, sticky="ew", padx=(0 if i == 0 else 6, 0))
                self.segments.append(seg)
            self.progress_bar = bar

        self.stage = ScrollFrame(self)
        self.stage.grid(row=2, column=0, sticky="nsew", padx=(28, 12), pady=8)
        self.stage.grid_columnconfigure(0, weight=1)
        self.text = ctk.CTkLabel(self.stage, text="", font=theme.font(36), text_color=theme.TEXT, justify="left",
                                 anchor="nw", wraplength=600)
        self.text.grid(row=0, column=0, sticky="nw", pady=(10, 18))
        self.timer_row = ctk.CTkFrame(self.stage, fg_color="transparent")
        self.timer_row.grid(row=1, column=0, sticky="w")

        # "Ensuite": the next step, small, so you can get the next thing ready
        self.next_box = ctk.CTkFrame(self, fg_color="transparent")
        self.next_head = ctk.CTkLabel(self.next_box, text="Ensuite", font=theme.font(15, "bold"),
                                      text_color=theme.ACCENT_TEXT, anchor="w", height=20)
        self.next_head.pack(anchor="w")
        self.next_text = ctk.CTkLabel(self.next_box, text="", font=theme.font(17), text_color=theme.MUTED,
                                      anchor="w", justify="left", wraplength=600)
        self.next_text.pack(anchor="w", fill="x")

        self.panel = theme.card(self, width=self.PANEL_W)
        self._fill_panel()

        nav = ctk.CTkFrame(self, fg_color="transparent")
        nav.grid(row=4, column=0, columnspan=2, sticky="ew", padx=28, pady=(14, 24))
        nav.grid_columnconfigure((0, 1), weight=1, uniform="nav")
        big = dict(height=78, font=theme.font(24, "bold"), corner_radius=18, compound="left")
        self.btn_prev = theme.secondary_button(nav, text=" Précédent", image=art.icon("back", theme.TEXT, 24),
                                               command=self.previous, **big)
        self.btn_prev.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        self.btn_next = theme.primary_button(nav, text="Suivant ", image=art.icon("forward", theme.ON_ACCENT, 24),
                                             command=self.next, **big)
        self.btn_next.configure(compound="right")
        self.btn_next.grid(row=0, column=1, sticky="ew", padx=(8, 0))

        self.bind("<Configure>", self._rewrap)
        self.place(x=0, y=0, relwidth=1, relheight=1)
        self.lift()
        self._render()

    # ----------------------------------------------------------- navigation
    def previous(self) -> None:
        if self.index > 0:
            self.index -= 1
            self._render()

    def next(self) -> None:
        if self.index < len(self.steps) - 1:
            self.index += 1
            self._render()
        else:
            self.close()

    def toggle_ingredients(self) -> None:
        self.show_ingredients = not self.show_ingredients
        if self.show_ingredients:
            self.panel.grid(row=2, column=1, sticky="ns", padx=(0, 28), pady=8)
        else:
            self.panel.grid_forget()
        self.btn_ing.configure(fg_color=theme.ACCENT_SOFT if self.show_ingredients else theme.SURFACE)
        self._rewrap()

    def close(self) -> None:
        self.app.close_kitchen()

    # ------------------------------------------------------------- drawing
    def _fill_panel(self) -> None:
        self.panel.grid_propagate(False)
        self.panel.pack_propagate(False)
        theme.heading(self.panel, "Ingrédients", size=22).pack(anchor="w", padx=18, pady=(18, 4))
        sub = ctk.CTkLabel(self.panel, text=f"pour {self.servings} personne{'s' if self.servings > 1 else ''}",
                           font=theme.font(13), text_color=theme.MUTED, anchor="w")
        sub.pack(anchor="w", padx=18)
        scroll = ScrollFrame(self.panel)
        scroll.pack(fill="both", expand=True, padx=6, pady=(8, 10))
        factor = Fraction(self.servings, max(1, self.recipe.pour))
        for ing in self.recipe.ingredients:
            qty = scale_quantity(ing.quantite, factor)
            line = f"{qty} {ing.nom}".strip()
            ctk.CTkLabel(scroll, text=line, font=theme.font(18), text_color=theme.TEXT, anchor="w", justify="left",
                         wraplength=self.PANEL_W - 70).pack(anchor="w", padx=10, pady=5)
        if not self.recipe.ingredients:
            ctk.CTkLabel(scroll, text="Aucun ingrédient.", font=theme.font(16), text_color=theme.MUTED).pack(
                anchor="w", padx=10)

    def _render(self) -> None:
        n = len(self.steps)
        if not n:
            self.counter.configure(text="Aucune étape")
            self.text.configure(text="Cette recette n'a pas encore d'étapes.", font=theme.font(30))
            self.btn_prev.configure(state="disabled")
            self.btn_next.configure(state="disabled")
            return
        text = self.steps[self.index]
        self.counter.configure(text=f"Étape {self.index + 1} / {n}")
        self.text.configure(text=text, font=theme.font(step_font_size(text)))
        self.btn_prev.configure(state="normal" if self.index > 0 else "disabled")
        last = self.index == n - 1
        self.btn_next.configure(text="Terminer " if last else "Suivant ")
        for i, seg in enumerate(self.segments):
            seg.configure(fg_color=theme.ACCENT if i == self.index else theme.ACCENT_SOFT if i < self.index else theme.BORDER)
        if last:
            self.next_box.grid_forget()
        else:
            nxt = self.steps[self.index + 1]
            self.next_text.configure(text=nxt if len(nxt) <= 160 else nxt[:159].rsplit(" ", 1)[0] + "…")
            self.next_box.grid(row=3, column=0, columnspan=2, sticky="ew", padx=28, pady=(6, 0))
        for child in self.timer_row.winfo_children():
            child.destroy()
        for duration in find_durations(text):
            theme.secondary_button(
                self.timer_row, text=f"  {format_short(duration.seconds)}", width=0, height=56, corner_radius=16,
                font=theme.font(20, "bold"), compound="left", image=art.icon("play", theme.ACCENT_TEXT, 20),
                command=lambda s=duration.seconds: self.app.start_timer(f"Étape {self.index + 1}", s),
            ).pack(side="left", padx=(0, 12), pady=(0, 8))
        self.stage._parent_canvas.yview_moveto(0)
        self._rewrap()

    def _rewrap(self, _event=None) -> None:
        scaling = ctk.ScalingTracker.get_widget_scaling(self)
        width = int(self.winfo_width() / scaling) - 28 - 28 - 12 - 30
        if self.show_ingredients:
            width -= self.PANEL_W + 28
        try:
            self.next_text.configure(wraplength=max(240, int(self.winfo_width() / scaling) - 80))
            self.text.configure(wraplength=max(240, width))
        except Exception:
            pass
