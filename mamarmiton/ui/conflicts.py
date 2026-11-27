"""The "two versions of one recipe" dialog."""

from __future__ import annotations

from typing import Callable

import customtkinter as ctk

from mamarmiton.ui import theme
from mamarmiton.ui.remote_logic import conflict_rows, format_date
from mamarmiton.ui.widgets import Modal, ScrollFrame


def _date(recipe) -> str:
    return format_date(recipe.modifie_le) if recipe.modifie_le else ""


class ConflictDialog(Modal):
    """Shows my version and the other one side by side; ``on_choice("mine" | "theirs" | "both" | "later")``."""

    def __init__(self, master, conflict, on_choice: Callable[[str], None]):
        super().__init__(master, "Deux versions de la recette", 900, 760)
        self.on_choice = on_choice
        self._decided = False
        self._stacked: bool | None = None
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        head = self.header("Cette recette a été modifiée sur un autre appareil",
                           f"« {conflict.mine.nom} » a été changée ici et ailleurs en même temps. "
                           "Choisissez la version à garder.")
        self._title = head.winfo_children()[0]
        self._sub = head.winfo_children()[1]

        self.body = ScrollFrame(self)
        self.body.grid(row=1, column=0, sticky="nsew", padx=(22, 8), pady=(0, 6))
        self.body.grid_columnconfigure(0, weight=1)
        self.body.grid_columnconfigure(1, weight=1)
        rows = conflict_rows(conflict.mine, conflict.theirs)
        self.cards = [
            self._column("Ma version", "Modifiée ici", _date(conflict.mine), rows, 1),
            self._column("L'autre version", "Modifiée ailleurs", _date(conflict.theirs), rows, 2),
        ]
        self._texts: list[ctk.CTkLabel] = []
        for card in self.cards:
            self._texts += [w for w in card.winfo_children() if getattr(w, "_wrap_me", False)]
        self._job = None
        self._last: tuple | None = None
        self.body.bind("<Configure>", self._schedule, add="+")

        foot = ctk.CTkFrame(self, fg_color="transparent")
        foot.grid(row=2, column=0, sticky="ew", padx=28, pady=(8, 18))
        self.foot = foot
        self.buttons = [
            theme.primary_button(foot, text="Garder ma version", width=0, command=lambda: self._choose("mine")),
            theme.secondary_button(foot, text="Garder l'autre version", width=0,
                                   command=lambda: self._choose("theirs")),
            theme.secondary_button(foot, text="Garder les deux", width=0, command=lambda: self._choose("both")),
            theme.link_button(foot, text="Décider plus tard", height=38, command=self.close),
        ]
        self._place_buttons(False)
        self.after(150, self._schedule)

    def _column(self, title: str, subtitle: str, date: str, rows, col: int) -> ctk.CTkFrame:
        card = theme.card(self.body)
        card.grid(row=0, column=col - 1, sticky="new", padx=(0 if col == 1 else 8, 8 if col == 1 else 0), pady=(0, 14))
        theme.heading(card, title, size=20).pack(anchor="w", padx=18, pady=(16, 0))
        ctk.CTkLabel(card, text=subtitle + (f" le {date}" if date else ""), font=theme.font(13), text_color=theme.MUTED,
                     anchor="w", justify="left").pack(anchor="w", padx=18, pady=(0, 6))
        for title_, mine, theirs, differs in rows:
            text = mine if col == 1 else theirs
            label = theme.eyebrow(card, title_ + ("  ·  différent" if differs else ""),
                                  text_color=theme.ACCENT_TEXT if differs else theme.MUTED)
            label.pack(anchor="w", padx=18, pady=(10, 2))
            body = ctk.CTkLabel(card, text=text, font=theme.font(14, "bold" if differs else "normal"),
                                text_color=theme.TEXT, anchor="w", justify="left", wraplength=340)
            body._wrap_me = True  # noqa: SLF001 - marker for _reflow
            body.pack(anchor="w", padx=18)
        ctk.CTkFrame(card, fg_color="transparent", height=14).pack()
        # the labels are re-wrapped with the card (pack order is fine: _wrap_me labels are direct children)
        return card

    def _schedule(self, _event=None) -> None:
        """Re-flow shortly after the last resize (re-wrapping inside <Configure> can loop with the scrollbar)."""
        if self._job is not None:
            self.after_cancel(self._job)
        self._job = self.after(80, self._reflow)

    def _reflow(self, _event=None) -> None:
        self._job = None
        if not self.winfo_exists():
            return
        scaling = ctk.ScalingTracker.get_widget_scaling(self)
        width = self.body.winfo_width() / scaling
        if width <= 1:
            width = 800
        stacked = width < 640
        if stacked != self._stacked:
            self._stacked = stacked
            first, second = self.cards
            if stacked:
                first.grid(row=0, column=0, columnspan=2, padx=(0, 8))
                second.grid(row=1, column=0, columnspan=2, padx=(0, 8))
            else:
                first.grid(row=0, column=0, columnspan=1, padx=(0, 8))
                second.grid(row=0, column=1, columnspan=1, padx=(8, 0))
            self._place_buttons(stacked)
        wrap = int(width - 40 if stacked else width / 2 - 70) // 20 * 20  # coarse steps: no feedback loop
        head = int(self.winfo_width() / scaling - 80) // 20 * 20
        if (wrap, head) == self._last:
            return
        self._last = (wrap, head)
        for label in self._texts:
            label.configure(wraplength=max(180, wrap))
        for label in (self._title, self._sub):
            label.configure(wraplength=max(260, head))

    def _place_buttons(self, stacked: bool) -> None:
        for i, button in enumerate(self.buttons):
            if stacked:
                button.grid(row=i, column=0, columnspan=3, sticky="ew", pady=(0 if i == 0 else 6, 0))
            elif i < 3:
                button.grid(row=0, column=i, columnspan=1, sticky="w", padx=(0 if i == 0 else 8, 0))
            else:  # "later" link under the three buttons
                button.grid(row=1, column=0, columnspan=3, sticky="w", pady=(6, 0))
        self.foot.grid_columnconfigure(0, weight=1 if stacked else 0)

    def _choose(self, choice: str) -> None:
        self._decided = True
        self.close()
        self.on_choice(choice)

    def close(self) -> None:
        was_decided = self._decided
        self._decided = True
        super().close()
        if not was_decided:
            self.on_choice("later")
