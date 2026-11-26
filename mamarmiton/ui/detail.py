"""Recipe screen: ingredients with a servings calculator, numbered steps, photos."""

from __future__ import annotations

from fractions import Fraction
from typing import TYPE_CHECKING

import customtkinter as ctk

from mamarmiton.models import Recipe, format_minutes
from mamarmiton.quantities import scale_quantity
from mamarmiton.ui import theme
from mamarmiton.ui.widgets import Modal, load_image

if TYPE_CHECKING:
    from mamarmiton.ui.app import App

THUMB = (150, 112)
PHOTO_COLUMNS = 8


class DetailView(ctk.CTkFrame):
    def __init__(self, master, app: "App"):
        super().__init__(master, fg_color="transparent")
        self.app = app
        self.recipe: Recipe | None = None
        self.servings = 1
        self._step_labels: list[ctk.CTkLabel] = []

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        # --- toolbar
        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.grid(row=0, column=0, sticky="ew", padx=24, pady=(18, 4))
        theme.ghost_button(bar, text="←  Toutes les recettes", width=0, command=app.go_home).pack(side="left")
        self.btn_delete = theme.danger_button(bar, text="Supprimer", width=110,
                                              command=lambda: app.delete_recipe(self.recipe))
        self.btn_delete.pack(side="right", padx=(8, 0))
        self.btn_edit = theme.primary_button(bar, text="Modifier", width=110,
                                             command=lambda: app.edit_recipe(self.recipe))
        self.btn_edit.pack(side="right", padx=(8, 0))
        self.btn_fav = theme.secondary_button(bar, text="", width=0,
                                              command=lambda: app.toggle_favorite(self.recipe))
        self.btn_fav.pack(side="right")

        self.body = ctk.CTkScrollableFrame(self, fg_color="transparent", corner_radius=0)
        self.body.grid(row=1, column=0, sticky="nsew", padx=(12, 6), pady=(0, 10))
        self.body.grid_columnconfigure(0, weight=2, uniform="col")
        self.body.grid_columnconfigure(1, weight=3, uniform="col")

    # ------------------------------------------------------------------ api
    def show(self, recipe: Recipe) -> None:
        same = self.recipe is not None and self.recipe.id == recipe.id
        self.recipe = recipe
        if not same:
            self.servings = recipe.pour
        for child in self.body.winfo_children():
            child.destroy()
        self._step_labels = []
        self._update_fav_button()

        self._build_header(recipe)
        self._build_ingredients(recipe)
        self._build_steps(recipe)
        self._build_photos(recipe)
        if not same:
            self.body._parent_canvas.yview_moveto(0)

    def _update_fav_button(self) -> None:
        fav = bool(self.recipe and self.recipe.favori)
        self.btn_fav.configure(text="★  Favori" if fav else "☆  Ajouter aux favoris",
                               text_color=theme.STAR if fav else theme.ACCENT)

    # -------------------------------------------------------------- header
    def _build_header(self, r: Recipe) -> None:
        head = ctk.CTkFrame(self.body, fg_color="transparent")
        head.grid(row=0, column=0, columnspan=2, sticky="ew", padx=12, pady=(4, 14))
        title = ctk.CTkLabel(head, text=r.nom, font=theme.font(32, "bold"), text_color=theme.TEXT,
                             justify="left", anchor="w", wraplength=900)
        title.pack(anchor="w", fill="x")
        head.bind("<Configure>", lambda e: title.configure(wraplength=max(200, e.width - 10)))

        chips = ctk.CTkFrame(head, fg_color="transparent")
        chips.pack(anchor="w", pady=(10, 0))
        theme.chip(chips, r.type_plat, fg=theme.DISH_COLORS.get(r.type_plat)).pack(side="left", padx=(0, 6))
        theme.chip(chips, r.type_cuisson).pack(side="left", padx=(0, 6))
        if r.temps:
            theme.chip(chips, f"Préparation {format_minutes(r.temps)}").pack(side="left", padx=(0, 6))
        if r.temps_cuisson:
            theme.chip(chips, f"Cuisson {format_minutes(r.temps_cuisson)}").pack(side="left", padx=(0, 6))
        if r.temps_total:
            theme.chip(chips, f"Total {format_minutes(r.temps_total)}", fg=theme.ACCENT_SOFT).pack(side="left")
        if r.source:
            ctk.CTkLabel(head, text=f"Importée depuis « {r.source} »", font=theme.font(12, slant="italic"),
                         text_color=theme.MUTED).pack(anchor="w", pady=(8, 0))

    # ---------------------------------------------------------- ingredients
    def _build_ingredients(self, r: Recipe) -> None:
        box = theme.card(self.body)
        box.grid(row=1, column=0, sticky="nsew", padx=(12, 8), pady=(0, 14))
        ctk.CTkLabel(box, text="Ingrédients", font=theme.font(20, "bold"), text_color=theme.TEXT).pack(
            anchor="w", padx=20, pady=(18, 10))

        stepper = ctk.CTkFrame(box, fg_color=theme.SURFACE_ALT, corner_radius=12)
        stepper.pack(fill="x", padx=16, pady=(0, 6))
        btn = dict(width=40, height=40, corner_radius=20, font=theme.font(20, "bold"),
                   fg_color=theme.ACCENT, hover_color=theme.ACCENT_HOVER, text_color=theme.ON_ACCENT)
        ctk.CTkButton(stepper, text="-", command=lambda: self._change_servings(-1), **btn).pack(side="left", padx=8, pady=8)
        self.lbl_servings = ctk.CTkLabel(stepper, text="", font=theme.font(16, "bold"), text_color=theme.TEXT)
        self.lbl_servings.pack(side="left", expand=True)
        ctk.CTkButton(stepper, text="+", command=lambda: self._change_servings(+1), **btn).pack(side="right", padx=8, pady=8)

        self.lbl_reset = ctk.CTkButton(box, text="", height=24, fg_color="transparent", hover_color=theme.ACCENT_SOFT,
                                       text_color=theme.ACCENT, font=theme.font(12, "bold"),
                                       command=lambda: self._change_servings(r.pour - self.servings))
        self.ingredient_list = ctk.CTkFrame(box, fg_color="transparent")
        self.ingredient_list.pack(fill="both", expand=True, padx=16, pady=(4, 16))
        self._render_ingredients()

    def _change_servings(self, delta: int) -> None:
        self.servings = max(1, min(99, self.servings + delta))
        self._render_ingredients()

    def _render_ingredients(self) -> None:
        r = self.recipe
        if r is None:
            return
        self.lbl_servings.configure(text=f"{self.servings} personne{'s' if self.servings > 1 else ''}")
        if self.servings != r.pour:
            self.lbl_reset.configure(text=f"↺ Revenir à {r.pour} personnes (recette d'origine)")
            self.lbl_reset.pack(after=self.lbl_servings.master, anchor="w", padx=16)
        else:
            self.lbl_reset.pack_forget()

        for child in self.ingredient_list.winfo_children():
            child.destroy()
        if not r.ingredients:
            ctk.CTkLabel(self.ingredient_list, text="Aucun ingrédient pour l'instant.", font=theme.font(14),
                         text_color=theme.MUTED).pack(anchor="w", pady=6)
            return
        factor = Fraction(self.servings, max(1, r.pour))
        for ing in r.ingredients:
            qty = scale_quantity(ing.quantite, factor)
            text = f"{qty}  ·  {ing.nom}" if qty else ing.nom
            # tick boxes help to follow along while cooking (not saved)
            ctk.CTkCheckBox(self.ingredient_list, text=text, font=theme.font(15), text_color=theme.TEXT,
                            fg_color=theme.ACCENT, hover_color=theme.ACCENT_HOVER, border_color=theme.BORDER,
                            checkbox_width=22, checkbox_height=22, corner_radius=6).pack(anchor="w", pady=5)

    # ---------------------------------------------------------------- steps
    def _build_steps(self, r: Recipe) -> None:
        box = theme.card(self.body)
        box.grid(row=1, column=1, sticky="nsew", padx=(8, 12), pady=(0, 14))
        box.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(box, text="Préparation", font=theme.font(20, "bold"), text_color=theme.TEXT).grid(
            row=0, column=0, columnspan=2, sticky="w", padx=20, pady=(18, 10))

        if not r.etapes:
            ctk.CTkLabel(box, text="Aucune étape pour l'instant.", font=theme.font(14), text_color=theme.MUTED).grid(
                row=1, column=0, columnspan=2, sticky="w", padx=20)
            theme.secondary_button(box, text="Ajouter les étapes", width=180,
                                   command=lambda: self.app.edit_recipe(r)).grid(
                row=2, column=0, columnspan=2, sticky="w", padx=20, pady=(10, 18))
        for i, step in enumerate(r.etapes, 1):
            badge = ctk.CTkLabel(box, text=str(i), width=32, height=32, corner_radius=16, fg_color=theme.ACCENT_SOFT,
                                 text_color=theme.ACCENT, font=theme.font(14, "bold"))
            badge.grid(row=i, column=0, sticky="n", padx=(20, 12), pady=8)
            label = ctk.CTkLabel(box, text=step, font=theme.font(15), text_color=theme.TEXT, justify="left",
                                 anchor="w", wraplength=400)
            label.grid(row=i, column=1, sticky="ew", padx=(0, 20), pady=(12, 8))
            self._step_labels.append(label)
        tail = len(r.etapes) + 1
        ctk.CTkFrame(box, fg_color="transparent", width=1, height=10).grid(row=tail + 2, column=0, columnspan=2)

        if r.notes:
            note = ctk.CTkFrame(box, fg_color=theme.SURFACE_ALT, corner_radius=12)
            note.grid(row=tail + 1, column=0, columnspan=2, sticky="ew", padx=16, pady=(8, 16))
            ctk.CTkLabel(note, text="Notes", font=theme.font(14, "bold"), text_color=theme.TEXT).pack(
                anchor="w", padx=14, pady=(10, 0))
            note_label = ctk.CTkLabel(note, text=r.notes, font=theme.font(14), text_color=theme.TEXT,
                                      justify="left", anchor="w", wraplength=400)
            note_label.pack(anchor="w", fill="x", padx=14, pady=(2, 12))
            self._step_labels.append(note_label)

        box.bind("<Configure>", self._rewrap)

    def _rewrap(self, event) -> None:
        wrap = max(150, event.width - 110)
        for label in self._step_labels:
            label.configure(wraplength=wrap)

    # --------------------------------------------------------------- photos
    def _build_photos(self, r: Recipe) -> None:
        box = theme.card(self.body)
        box.grid(row=3, column=0, columnspan=2, sticky="ew", padx=12, pady=(0, 14))
        head = ctk.CTkFrame(box, fg_color="transparent")
        head.pack(fill="x", padx=20, pady=(16, 8))
        ctk.CTkLabel(head, text="Photos", font=theme.font(20, "bold"), text_color=theme.TEXT).pack(side="left")
        theme.ghost_button(head, text="+ Ajouter", width=0, text_color=theme.ACCENT,
                           command=lambda: self.app.add_photo(r)).pack(side="right")

        paths = self.app.store.image_paths(r)
        if not paths:
            ctk.CTkLabel(box, text="Pas encore de photo.", font=theme.font(14), text_color=theme.MUTED).pack(
                anchor="w", padx=20, pady=(0, 16))
            return
        grid = ctk.CTkFrame(box, fg_color="transparent")
        grid.pack(fill="x", padx=14, pady=(0, 14))
        thumbs = []
        for path in paths:
            img = load_image(path, THUMB, cover=True)
            if img is None:
                continue
            thumb = ctk.CTkLabel(grid, text="", image=img, cursor="hand2", width=0, corner_radius=0)
            thumb.bind("<Button-1>", lambda _e, p=path: PhotoViewer(self, self.app, r, p))
            thumbs.append(thumb)

        state = {"cols": 0}

        def reflow(event=None) -> None:
            if not thumbs:
                return
            width = event.width if event else grid.winfo_width()
            cell = max(thumbs[0].winfo_reqwidth(), 1) + 12  # real size, after DPI scaling
            cols = max(1, min(PHOTO_COLUMNS, width // cell))
            if cols == state["cols"]:
                return
            state["cols"] = cols
            for i, thumb in enumerate(thumbs):
                thumb.grid(row=i // cols, column=i % cols, padx=6, pady=6)

        grid.bind("<Configure>", reflow)
        reflow()


class PhotoViewer(Modal):
    def __init__(self, master, app: "App", recipe: Recipe, path):
        super().__init__(master, "Photo", 820, 680)
        img = load_image(path, (780, 560))
        ctk.CTkLabel(self, text="", image=img).pack(padx=20, pady=(20, 10), expand=True)
        row = ctk.CTkFrame(self, fg_color="transparent")
        row.pack(fill="x", padx=20, pady=(0, 16))
        theme.danger_button(row, text="Supprimer cette photo", width=190,
                            command=lambda: (app.remove_photo(recipe, path.name), self.close())).pack(side="left")
        theme.primary_button(row, text="Fermer", width=110, command=self.close).pack(side="right")
