"""Recipe screen: cover + key facts, ingredients with a servings calculator,
numbered steps you can tick off while cooking, notes and photos."""

from __future__ import annotations

import json
from fractions import Fraction
from typing import TYPE_CHECKING

import customtkinter as ctk

from mamarmiton.models import Recipe, format_minutes
from mamarmiton.quantities import scale_quantity
from mamarmiton.ui import art, remote_logic as rl, theme
from mamarmiton.ui.widgets import Modal, ScrollFrame, bind_tree, pointer_inside, set_cursor_tree

if TYPE_CHECKING:
    from mamarmiton.ui.app import App

PAD = 36
COVER = (400, 290)
COVER_SMALL = (300, 220)
THUMB = (168, 124)
PHOTO_COLUMNS = 8


def hover_rows(row: ctk.CTkFrame, color) -> None:
    """Tint ``row`` while the mouse is over it (or over one of its children)."""
    def leave(_e=None):
        row.after(20, lambda: row.winfo_exists() and not pointer_inside(row) and row.configure(fg_color="transparent"))
    bind_tree(row, "<Enter>", lambda _e: row.configure(fg_color=color))
    bind_tree(row, "<Leave>", leave)


class IngredientRow(ctk.CTkFrame):
    """Quantity + name with a tick box; ticked rows are struck through (not saved)."""

    def __init__(self, master, qty: str, name: str):
        super().__init__(master, fg_color="transparent", corner_radius=10)
        self.var = ctk.BooleanVar(value=False)
        self.grid_columnconfigure(2, weight=1)
        self.box = ctk.CTkCheckBox(self, text="", variable=self.var, width=24, checkbox_width=22, checkbox_height=22,
                                   corner_radius=7, border_width=2, fg_color=theme.ACCENT,
                                   hover_color=theme.ACCENT_HOVER, border_color=theme.BORDER_STRONG,
                                   command=self._paint)
        self.box.grid(row=0, column=0, padx=(10, 8), pady=8)
        self.qty = ctk.CTkLabel(self, text=qty, font=theme.font(15, "bold"), text_color=theme.ACCENT_TEXT, anchor="w")
        if qty:
            self.qty.grid(row=0, column=1, sticky="w", padx=(0, 8))
        self.name = ctk.CTkLabel(self, text=name, font=theme.font(15), text_color=theme.TEXT, anchor="w",
                                 justify="left")
        self.name.grid(row=0, column=2, sticky="ew", padx=(0, 10))
        for w in (self, self.qty, self.name):
            w.bind("<Button-1>", self._toggle, add="+")
        hover_rows(self, theme.SURFACE_ALT)
        set_cursor_tree(self, "hand2")

    def _toggle(self, _event=None) -> None:
        self.var.set(not self.var.get())
        self._paint()

    def _paint(self) -> None:
        done = self.var.get()
        self.name.configure(font=theme.font(15, overstrike=done), text_color=theme.FAINT if done else theme.TEXT)
        self.qty.configure(font=theme.font(15, "bold", overstrike=done),
                           text_color=theme.FAINT if done else theme.ACCENT_TEXT)


class StepRow(ctk.CTkFrame):
    """A numbered step. Clicking it marks it as done (not saved)."""

    def __init__(self, master, number: int, text: str):
        super().__init__(master, fg_color="transparent", corner_radius=12)
        self.done = False
        self.number = number
        self.grid_columnconfigure(1, weight=1)
        self.badge = ctk.CTkLabel(self, text=str(number), width=38, height=38, corner_radius=19,
                                  fg_color=theme.ACCENT_SOFT, text_color=theme.ACCENT_TEXT, font=theme.serif(17))
        self.badge.grid(row=0, column=0, sticky="n", padx=(12, 16), pady=12)
        self.label = ctk.CTkLabel(self, text=text, font=theme.font(16), text_color=theme.TEXT, justify="left",
                                  anchor="w", wraplength=420)
        self.label.grid(row=0, column=1, sticky="ew", padx=(0, 16), pady=(19, 14))
        bind_tree(self, "<Button-1>", self._toggle)
        hover_rows(self, theme.SURFACE_HOVER)
        set_cursor_tree(self, "hand2")

    def _toggle(self, _event=None) -> None:
        self.done = not self.done
        if self.done:
            self.badge.configure(text="", image=art.icon("check", theme.ON_ACCENT, 18), fg_color=theme.ACCENT)
            self.label.configure(text_color=theme.FAINT)
        else:
            self.badge.configure(text=str(self.number), image=None, fg_color=theme.ACCENT_SOFT)
            self.label.configure(text_color=theme.TEXT)

    def set_wrap(self, width: int) -> None:
        self.label.configure(wraplength=width)


class DetailView(ctk.CTkFrame):
    def __init__(self, master, app: "App"):
        super().__init__(master, fg_color="transparent")
        self.app = app
        self.recipe: Recipe | None = None
        self.servings = 1
        self._wrapped: list = []

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        # --- toolbar
        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.grid(row=0, column=0, sticky="ew", padx=PAD - 10, pady=(22, 8))
        self.btn_back = theme.ghost_button(bar, text="Toutes les recettes", image=art.icon("back", theme.TEXT, 18),
                                           compound="left", width=0, command=app.go_home)
        self.btn_back.pack(side="left")
        self.btn_delete = theme.danger_button(bar, text="Supprimer", image=art.icon("trash", theme.DANGER, 17),
                                              compound="left", width=0, command=lambda: app.delete_recipe(self.recipe))
        self.btn_variant = theme.secondary_button(bar, text="Créer une variante", width=0, compound="left",
                                                  image=art.icon("copy", theme.TEXT, 17),
                                                  command=lambda: app.duplicate_recipe(self.recipe))
        self.btn_edit = theme.primary_button(bar, text="Modifier", image=art.icon("pencil", theme.ON_ACCENT, 17),
                                             compound="left", width=0, command=lambda: app.edit_recipe(self.recipe))
        self.btn_share = theme.secondary_button(bar, text="Partager", image=art.icon("share", theme.TEXT, 17),
                                                compound="left", width=0,
                                                command=lambda: app.share_recipe(self.recipe))
        self.btn_fav = theme.secondary_button(bar, text="", width=0, compound="left",
                                              command=lambda: app.toggle_favorite(self.recipe))
        self.signature: tuple | None = None

        self.body = ScrollFrame(self)
        self.body.grid(row=1, column=0, sticky="nsew", padx=(PAD - 14, 8), pady=(0, 8))
        self.body.bind("<Configure>", self._rewrap, add="+")
        self._mode = None
        self._built = False
        self._compact = False

    # ------------------------------------------------------------------ api
    def signature_of(self, recipe: Recipe) -> tuple:
        """What the page displays, to skip a redraw when a sync changed nothing here."""
        store = self.app.store
        return (json.dumps(recipe.to_dict(), sort_keys=True), recipe.favori,
                tuple(p.name for p in store.image_paths(recipe)), rl.can_edit(store, recipe.id),
                rl.can_delete(store, recipe.id), self.app.owner_name(recipe))

    def show(self, recipe: Recipe) -> None:
        same = self.recipe is not None and self.recipe.id == recipe.id
        self.recipe = recipe
        if not same:
            self.servings = recipe.pour
        for child in self.body.winfo_children():
            child.destroy()
        self._wrapped = []
        self._mode = None
        self._built = False
        self.signature = self.signature_of(recipe)
        self._update_toolbar()
        self._update_fav_button()

        self._build_hero(recipe)
        self._build_ingredients(recipe)
        self._build_steps(recipe)
        self._build_photos(recipe)
        self._built = True
        self._rewrap()
        if not same:
            self.body._parent_canvas.yview_moveto(0)

    def _update_toolbar(self) -> None:
        """Show only the buttons this recipe's permissions allow."""
        r, app = self.recipe, self.app
        if r is None:
            return
        for button in (self.btn_delete, self.btn_variant, self.btn_edit, self.btn_share, self.btn_fav):
            button.pack_forget()
        can_delete, can_edit = app.can_delete(r), app.can_edit(r)
        if can_delete:
            self.btn_delete.pack(side="right", padx=(8, 10))
        if can_edit:
            self.btn_edit.pack(side="right", padx=(8, 0 if can_delete else 10))
        self.btn_variant.pack(side="right", padx=(8, 0 if (can_edit or can_delete) else 10))
        if app.is_remote and app.is_owner(r):
            self.btn_share.pack(side="right", padx=(8, 0))
        self.btn_fav.pack(side="right", padx=(0, 0 if (can_edit or can_delete) else 10))

    def _update_fav_button(self) -> None:
        fav = bool(self.recipe and self.recipe.favori)
        label = "Dans mes favoris" if fav else "Ajouter aux favoris"
        self.btn_fav.configure(text="" if self._compact else label,
                               image=art.icon("star" if fav else "star_outline", theme.STAR if fav else theme.TEXT, 18))

    # ----------------------------------------------------------------- hero
    def _build_hero(self, r: Recipe) -> None:
        hero = ctk.CTkFrame(self.body, fg_color="transparent")
        hero.grid(row=0, column=0, columnspan=2, sticky="ew", padx=14, pady=(4, 22))
        hero.grid_columnconfigure(1, weight=1)

        photos = self.app.store.image_paths(r)
        self._cover_photo = photos[0] if photos else None
        self.cover_label = ctk.CTkLabel(hero, text="")
        self.cover_label.grid(row=0, column=0, sticky="nw")

        info = ctk.CTkFrame(hero, fg_color="transparent")
        self.hero_info = info
        tags = ctk.CTkFrame(info, fg_color="transparent")
        tags.pack(anchor="w", pady=(6, 0))
        pal = theme.dish(r.type_plat)
        theme.chip(tags, f"  {r.type_plat}", fg=pal["chip"], image=art.dot(pal["dot"], 9), compound="left").pack(
            side="left", padx=(0, 6))
        theme.chip(tags, f"  {r.type_cuisson}", image=art.icon("pot", theme.MUTED, 14), compound="left").pack(
            side="left", padx=(0, 6))
        if r.favori:
            theme.chip(tags, "  Favori", fg=theme.NOTE_BG, image=art.icon("star", theme.STAR, 14),
                       compound="left").pack(side="left", padx=(0, 6))
        if not self.app.can_edit(r):  # on its own line: the tag row cannot wrap
            theme.chip(info, "  Lecture seule", fg=theme.ACCENT_SOFT, text_color=theme.ACCENT_TEXT,
                       image=art.icon("eye", theme.ACCENT_TEXT, 14), compound="left").pack(anchor="w", pady=(8, 0))

        title = theme.heading(info, r.nom, size=36, wraplength=560)
        title.pack(anchor="w", fill="x", pady=(14, 0))
        self._wrapped.append((title, "title"))
        if self.app.is_remote and not self.app.is_owner(r):
            owner = ctk.CTkLabel(info, text=f" Recette de {self.app.owner_name(r)}", font=theme.font(14, "bold"),
                                 image=art.icon("share", theme.MUTED, 15), compound="left",
                                 text_color=theme.MUTED, anchor="w", justify="left")
            owner.pack(anchor="w", pady=(8, 0))
            self._wrapped.append((owner, "title"))
        if r.source:
            ctk.CTkLabel(info, text=f"Importée depuis « {r.source} »", font=theme.font(13, slant="italic"),
                         text_color=theme.MUTED, anchor="w").pack(anchor="w", pady=(6, 0))

        stats = ctk.CTkFrame(info, fg_color="transparent")
        stats.pack(anchor="w", fill="x", pady=(22, 0))
        self.tiles = []
        facts = [("clock", "Préparation", format_minutes(r.temps) if r.temps else "—"),
                 ("pot", "Cuisson", format_minutes(r.temps_cuisson) if r.temps_cuisson else "—"),
                 ("clock", "Total", format_minutes(r.temps_total) if r.temps_total else "—"),
                 ("users", "Pour", f"{r.pour} pers.")]
        for i, (icon, label, value) in enumerate(facts):
            tile = theme.card(stats, corner_radius=16)
            self.tiles.append(tile)
            ctk.CTkLabel(tile, text=f"  {label.upper()}", image=art.icon(icon, theme.MUTED, 14), compound="left",
                         font=theme.font(11, "bold"), text_color=theme.MUTED, anchor="w").pack(
                anchor="w", padx=16, pady=(14, 0))
            ctk.CTkLabel(tile, text=value, font=theme.serif(21), text_color=theme.TEXT, anchor="w").pack(
                anchor="w", padx=16, pady=(2, 14))

    # ---------------------------------------------------------- ingredients
    def _build_ingredients(self, r: Recipe) -> None:
        box = theme.card(self.body)
        self.ing_box = box

        head = ctk.CTkFrame(box, fg_color="transparent")
        head.pack(fill="x", padx=22, pady=(22, 4))
        theme.heading(head, "Ingrédients", size=22).pack(side="left")

        stepper = ctk.CTkFrame(box, fg_color=theme.SURFACE_ALT, corner_radius=26, height=52)
        stepper.pack(fill="x", padx=18, pady=(10, 4))
        btn = dict(width=40, height=40, corner_radius=20, fg_color=theme.SURFACE, hover_color=theme.ACCENT_SOFT,
                   border_width=1, border_color=theme.BORDER_STRONG, text="")
        ctk.CTkButton(stepper, image=art.icon("minus", theme.TEXT, 16), command=lambda: self._change_servings(-1),
                      **btn).pack(side="left", padx=6, pady=6)
        self.lbl_servings = ctk.CTkLabel(stepper, text="", font=theme.font(15, "bold"), text_color=theme.TEXT,
                                         image=art.icon("users", theme.MUTED, 16), compound="left")
        self.lbl_servings.pack(side="left", expand=True)
        ctk.CTkButton(stepper, image=art.icon("plus", theme.TEXT, 16), command=lambda: self._change_servings(+1),
                      **btn).pack(side="right", padx=6, pady=6)

        self.btn_shop = theme.secondary_button(box, text="", compound="left", height=48,
                                               image=art.icon("cart", theme.TEXT, 18),
                                               command=self._shopping_clicked)
        self.btn_shop.pack(fill="x", padx=18, pady=(8, 4))
        self._wrapped.append((self.btn_shop, "button"))

        self.lbl_reset = theme.link_button(box, text="Revenir à la recette d'origine",
                                           image=art.icon("reset", theme.ACCENT_TEXT, 14), compound="left",
                                           command=lambda: self._change_servings(r.pour - self.servings))
        self.ingredient_list = ctk.CTkFrame(box, fg_color="transparent")
        self.ingredient_list.pack(fill="both", expand=True, padx=12, pady=(8, 18))
        self._render_ingredients()

    def _shopping_clicked(self) -> None:
        if self.recipe is None:
            return
        listed = self.app.shopping.servings_of(self.recipe.id)
        if listed == self.servings:
            self.app.show_shopping()
        else:
            self.app.add_to_shopping(self.recipe, self.servings)
            self._update_shopping_button()

    def _update_shopping_button(self) -> None:
        if self.recipe is None or not hasattr(self, "btn_shop"):
            return
        listed = self.app.shopping.servings_of(self.recipe.id)
        if listed is None:
            text = "Ajouter à la liste de courses"
        elif listed == self.servings:
            text = "Dans la liste de courses · Voir"
        else:
            text = f"Mettre à jour la liste ({self.servings} pers.)"
        self.btn_shop.configure(text=text)

    def _change_servings(self, delta: int) -> None:
        self.servings = max(1, min(99, self.servings + delta))
        self._render_ingredients()

    def _render_ingredients(self) -> None:
        r = self.recipe
        if r is None:
            return
        self.lbl_servings.configure(text=f"  {self.servings} personne{'s' if self.servings > 1 else ''}")
        self._update_shopping_button()
        if self.servings != r.pour:
            self.lbl_reset.configure(text=f"Revenir à {r.pour} personne{'s' if r.pour > 1 else ''}")
            self.lbl_reset.pack(after=self.lbl_servings.master, anchor="w", padx=16, pady=(4, 0))
        else:
            self.lbl_reset.pack_forget()

        for child in self.ingredient_list.winfo_children():
            child.destroy()
        if not r.ingredients:
            ctk.CTkLabel(self.ingredient_list, text="Aucun ingrédient pour l'instant.", font=theme.font(14),
                         text_color=theme.MUTED).pack(anchor="w", padx=10, pady=6)
            return
        factor = Fraction(self.servings, max(1, r.pour))
        for ing in r.ingredients:
            row = IngredientRow(self.ingredient_list, scale_quantity(ing.quantite, factor), ing.nom)
            row.pack(fill="x", pady=1)
            self._wrapped.append((row.name, "ingredient"))
        self._rewrap()

    # ---------------------------------------------------------------- steps
    def _build_steps(self, r: Recipe) -> None:
        column = ctk.CTkFrame(self.body, fg_color="transparent")
        self.steps_col = column
        column.grid_columnconfigure(0, weight=1)

        box = theme.card(column)
        box.grid(row=0, column=0, sticky="ew")
        head = ctk.CTkFrame(box, fg_color="transparent")
        head.pack(fill="x", padx=22, pady=(22, 6))
        theme.heading(head, "Préparation", size=22).pack(side="left")
        if r.etapes:
            ctk.CTkLabel(head, text=f"{len(r.etapes)} étape{'s' if len(r.etapes) > 1 else ''}",
                         font=theme.font(13), text_color=theme.MUTED).pack(side="left", padx=(12, 0), pady=(6, 0))
            ctk.CTkLabel(box, text="Astuce : cliquez sur une étape quand elle est faite.", font=theme.font(12),
                         text_color=theme.FAINT, anchor="w").pack(anchor="w", padx=22, pady=(0, 6))

        if not r.etapes:
            ctk.CTkLabel(box, text="Aucune étape pour l'instant.", font=theme.font(14), text_color=theme.MUTED).pack(
                anchor="w", padx=22)
            if self.app.can_edit(r):
                theme.secondary_button(box, text="Ajouter les étapes", image=art.icon("pencil", theme.TEXT, 16),
                                       compound="left", width=0, command=lambda: self.app.edit_recipe(r)).pack(
                    anchor="w", padx=22, pady=(12, 22))
            else:
                ctk.CTkFrame(box, fg_color="transparent", height=16).pack()
        else:
            steps = ctk.CTkFrame(box, fg_color="transparent")
            steps.pack(fill="x", padx=10, pady=(0, 14))
            for i, text in enumerate(r.etapes, 1):
                if i > 1:
                    theme.divider(steps).pack(fill="x", padx=(66, 16))
                row = StepRow(steps, i, text)
                row.pack(fill="x", pady=2)
                self._wrapped.append((row, "step"))

        if r.notes:
            note = ctk.CTkFrame(column, fg_color=theme.NOTE_BG, corner_radius=18)
            note.grid(row=1, column=0, sticky="ew", pady=(20, 0))
            note.grid_columnconfigure(1, weight=1)
            ctk.CTkFrame(note, width=5, fg_color=theme.NOTE_BAR, corner_radius=3).grid(
                row=0, column=0, rowspan=2, sticky="ns", padx=(16, 0), pady=18)
            theme.heading(note, "Le petit mot du chef", size=18).grid(row=0, column=1, sticky="w", padx=16,
                                                                      pady=(16, 0))
            label = ctk.CTkLabel(note, text=r.notes, font=theme.font(15, slant="italic"), text_color=theme.TEXT,
                                 justify="left", anchor="w", wraplength=420)
            label.grid(row=1, column=1, sticky="ew", padx=16, pady=(4, 18))
            self._wrapped.append((label, "note"))

    def _rewrap(self, _event=None) -> None:
        if not self._built:
            return
        width = self.body.winfo_width()
        if width <= 1:  # not on screen yet: estimate from the window
            width = self.app.winfo_width() - self.app.sidebar.winfo_width() - 2 * PAD
        scaling = ctk.ScalingTracker.get_widget_scaling(self)
        width = int(width / scaling) - 28
        wide = width >= 1000
        two_cols = width >= 820
        stacked = width < 720  # cover above the title instead of beside it
        self._apply_layout(wide, two_cols, stacked, width)
        self._compact_toolbar(width < 760)
        # very narrow: shorter "back" label, icon-only "edit" button
        tiny = width < 560
        if self.btn_edit.cget("text") != ("" if tiny else "Modifier"):
            self.btn_back.configure(text="Retour" if tiny else "Toutes les recettes")
            self.btn_edit.configure(text="" if tiny else "Modifier")

        cover_w = (COVER if wide else COVER_SMALL)[0]
        info_w = width - 10 if stacked else width - cover_w - 32
        left = width * 2 // 5 if two_cols else width
        right = width - left - 20 if two_cols else width
        for widget, kind in self._wrapped:
            try:
                if kind == "title":
                    widget.configure(wraplength=max(220, info_w))
                elif kind == "step":
                    widget.set_wrap(max(160, right - 120))
                elif kind == "note":
                    widget.configure(wraplength=max(160, right - 70))
                elif kind == "ingredient":
                    widget.configure(wraplength=max(120, left - 150))
                elif kind == "button":  # CTkButton cannot wrap by itself: wrap its inner label
                    widget._text_label.configure(wraplength=max(120, left - 90))
                elif kind == "full":
                    widget.configure(wraplength=max(200, width - 60))
            except Exception:  # widget destroyed while re-rendering
                pass

    def _compact_toolbar(self, compact: bool) -> None:
        """Icon-only favourite / delete buttons when the window is narrow."""
        if compact == self._compact:
            return
        self._compact = compact
        self.btn_delete.configure(text="" if compact else "Supprimer")
        self.btn_share.configure(text="" if compact else "Partager")
        self.btn_variant.configure(text="" if compact else "Créer une variante")
        self._update_fav_button()

    def _apply_layout(self, wide: bool, two_cols: bool, stacked: bool, width: int) -> None:
        """Re-arrange the page for the window width (only when the breakpoint changes)."""
        banner_w = (width // 40) * 40 if stacked else 0  # resize the banner in 40 px steps
        mode = (wide, two_cols, stacked, banner_w)
        if mode == self._mode:
            return
        self._mode = mode
        r = self.recipe
        if stacked:
            size = (max(240, banner_w - 10), 220)
            self.cover_label.grid(row=0, column=0, columnspan=2, sticky="nw")
            self.hero_info.grid(row=1, column=0, columnspan=2, sticky="nsew", padx=0, pady=(16, 0))
        else:
            size = COVER if wide else COVER_SMALL
            self.cover_label.grid(row=0, column=0, columnspan=1, sticky="nw")
            self.hero_info.grid(row=0, column=1, columnspan=1, sticky="nsew", padx=(32, 0), pady=0)
        self.cover_label.configure(image=art.cover(self._cover_photo, r.id, r.type_plat, r.nom, size, radius=20,
                                                   bg=theme.BG, tag=False))
        stats = self.tiles[0].master
        per_row = 4 if (wide or (stacked and width >= 560)) else 2
        for c in range(4):
            stats.grid_columnconfigure(c, weight=1 if c < per_row else 0, uniform="stat" if c < per_row else "")
        for i, tile in enumerate(self.tiles):
            col = i % per_row
            tile.grid(row=i // per_row, column=col, sticky="nsew", padx=(0 if col == 0 else 10, 0),
                      pady=(0 if i < per_row else 10, 0))
        if two_cols:
            self.body.grid_columnconfigure(0, weight=2, uniform="col")
            self.body.grid_columnconfigure(1, weight=3, uniform="col")
            self.ing_box.grid(row=1, column=0, columnspan=1, sticky="nsew", padx=(14, 10), pady=(0, 20))
            self.steps_col.grid(row=1, column=1, columnspan=1, sticky="nsew", padx=(10, 14), pady=(0, 20))
        else:
            self.body.grid_columnconfigure(0, weight=1, uniform="")
            self.body.grid_columnconfigure(1, weight=0, uniform="")
            self.ing_box.grid(row=1, column=0, columnspan=2, sticky="nsew", padx=14, pady=(0, 20))
            self.steps_col.grid(row=2, column=0, columnspan=2, sticky="nsew", padx=14, pady=(0, 20))

    # --------------------------------------------------------------- photos
    def _build_photos(self, r: Recipe) -> None:
        box = theme.card(self.body)
        box.grid(row=3, column=0, columnspan=2, sticky="ew", padx=14, pady=(0, 24))
        head = ctk.CTkFrame(box, fg_color="transparent")
        head.pack(fill="x", padx=22, pady=(20, 10))
        theme.heading(head, "Photos", size=22).pack(side="left")
        can_edit = self.app.can_edit(r)
        if can_edit:
            theme.secondary_button(head, text="Ajouter une photo", image=art.icon("photo", theme.TEXT, 17),
                                   compound="left", width=0, height=38,
                                   command=lambda: self.app.add_photo(r)).pack(side="right")

        paths = self.app.store.image_paths(r)
        if not paths:
            hint = ctk.CTkLabel(box, text=("Pas encore de photo. Ajoutez celle de votre plat, elle servira de couverture."
                                           if can_edit else "Pas de photo pour cette recette."),
                                font=theme.font(14), text_color=theme.MUTED, justify="left", anchor="w")
            hint.pack(anchor="w", padx=22, pady=(0, 22))
            self._wrapped.append((hint, "full"))
            return
        grid = ctk.CTkFrame(box, fg_color="transparent")
        grid.pack(fill="x", padx=16, pady=(0, 16))
        thumbs = []
        for path in paths:
            img = art.photo_thumb(path, THUMB)
            if img is None:
                continue
            thumb = ctk.CTkLabel(grid, text="", image=img, cursor="hand2")
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
        super().__init__(master, "Photo", 860, 720)
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)
        img = art.photo_thumb(path, (800, 580), radius=16, bg=theme.BG, fit=False)
        ctk.CTkLabel(self, text="", image=img).grid(row=0, column=0, padx=24, pady=(24, 12))
        row = ctk.CTkFrame(self, fg_color="transparent")
        row.grid(row=1, column=0, sticky="ew", padx=24, pady=(0, 20))
        if app.can_edit(recipe):
            theme.danger_button(row, text="Supprimer cette photo", image=art.icon("trash", theme.DANGER, 16),
                                compound="left", width=0,
                                command=lambda: (app.remove_photo(recipe, path.name), self.close())).pack(side="left")
        theme.primary_button(row, text="Fermer", width=120, command=self.close).pack(side="right")
