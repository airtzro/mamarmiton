"""Home screen: a responsive grid of recipe cards."""

from __future__ import annotations

from typing import TYPE_CHECKING

import customtkinter as ctk

from mamarmiton.models import Recipe
from mamarmiton.ui import theme
from mamarmiton.ui.widgets import RecipeCard

if TYPE_CHECKING:
    from mamarmiton.ui.app import App

GAP = 18


class HomeView(ctk.CTkFrame):
    def __init__(self, master, app: "App"):
        super().__init__(master, fg_color="transparent")
        self.app = app
        self._cards: list[RecipeCard] = []
        self._columns = 0

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=28, pady=(24, 8))
        self.title = ctk.CTkLabel(header, text="Mes recettes", font=theme.font(28, "bold"), text_color=theme.TEXT)
        self.title.pack(side="left")
        self.count = ctk.CTkLabel(header, text="", font=theme.font(14), text_color=theme.MUTED)
        self.count.pack(side="left", padx=12, pady=(8, 0))

        self.grid_area = ctk.CTkScrollableFrame(self, fg_color="transparent", corner_radius=0)
        self.grid_area.grid(row=1, column=0, sticky="nsew", padx=(16, 8), pady=(0, 12))
        self.grid_area.bind("<Configure>", self._on_resize)

    # ------------------------------------------------------------------ api
    def show(self, recipes: list[Recipe], total: int, filtered: bool) -> None:
        for child in self.grid_area.winfo_children():
            child.destroy()
        self._cards = []
        self._columns = 0

        self.title.configure(text="Résultats" if filtered else "Mes recettes")
        if filtered:
            self.count.configure(text=f"{len(recipes)} sur {total}")
        else:
            self.count.configure(text=f"{total} recette{'s' if total > 1 else ''}")

        if not recipes:
            self._empty_state(filtered and total > 0)
            return

        store = self.app.store
        for recipe in recipes:
            photos = store.image_paths(recipe)
            card = RecipeCard(self.grid_area, recipe, photos[0] if photos else None,
                              on_open=self.app.open_recipe, on_favorite=self.app.toggle_favorite)
            self._cards.append(card)
        self._layout()
        self.grid_area._parent_canvas.yview_moveto(0)

    # -------------------------------------------------------------- layout
    def _on_resize(self, _event=None) -> None:
        if self._cards and self._wanted_columns() != self._columns:
            self._layout()

    def _wanted_columns(self) -> int:
        width = self.grid_area.winfo_width()
        if width <= 1:
            width = self.winfo_width() - 40
        return max(1, (width - GAP) // (RecipeCard.WIDTH + GAP))

    def _layout(self) -> None:
        cols = self._wanted_columns()
        self._columns = cols
        for c in range(12):
            self.grid_area.grid_columnconfigure(c, weight=1 if c < cols else 0, uniform="card" if c < cols else "")
        for i, card in enumerate(self._cards):
            card.grid(row=i // cols, column=i % cols, padx=GAP // 2, pady=GAP // 2, sticky="nsew")

    def _empty_state(self, filtered: bool) -> None:
        for c in range(12):
            self.grid_area.grid_columnconfigure(c, weight=0, uniform="")
        self.grid_area.grid_columnconfigure(0, weight=1)
        box = ctk.CTkFrame(self.grid_area, fg_color="transparent")
        box.grid(row=0, column=0, pady=80)
        if filtered:
            ctk.CTkLabel(box, text="Aucune recette ne correspond", font=theme.font(20, "bold"),
                         text_color=theme.TEXT).pack()
            ctk.CTkLabel(box, text="Essayez d'enlever un filtre.", font=theme.font(14),
                         text_color=theme.MUTED).pack(pady=(6, 16))
            theme.secondary_button(box, text="Effacer les filtres", command=self.app.sidebar.reset_filters).pack()
        else:
            ctk.CTkLabel(box, text="Votre carnet est vide", font=theme.font(22, "bold"),
                         text_color=theme.TEXT).pack()
            ctk.CTkLabel(box, text="Ajoutez votre première recette, à la main ou depuis un PDF.",
                         font=theme.font(14), text_color=theme.MUTED).pack(pady=(6, 18))
            row = ctk.CTkFrame(box, fg_color="transparent")
            row.pack()
            theme.primary_button(row, text="+  Écrire une recette", command=self.app.new_recipe).pack(side="left", padx=6)
            theme.secondary_button(row, text="Importer un PDF", command=self.app.import_pdf).pack(side="left", padx=6)
