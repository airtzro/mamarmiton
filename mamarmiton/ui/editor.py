"""Recipe editor, shown inside the main window (no pop-up).

Ingredients are entered one per row: name, quantity, unit. Steps stay a
simple "one per line" box.
"""

from __future__ import annotations

import copy
import logging
import re
from tkinter import messagebox
from typing import TYPE_CHECKING, Callable

import customtkinter as ctk

from mamarmiton import usage
from mamarmiton.models import (COOKING_TYPES, DISH_TYPES, MAX_TAGS, Ingredient, Recipe, fold,
                               normalize_tags, parse_int, parse_minutes)
from mamarmiton.quantities import COMMON_UNITS, join_amount_unit, split_amount_unit
from mamarmiton.ui import art, theme
from mamarmiton.search import tag_counts
from mamarmiton.ui.widgets import Choice, FlowFrame, ScrollFrame

if TYPE_CHECKING:
    from mamarmiton.ui.app import App

log = logging.getLogger(__name__)

MAX_WIDTH = 920  # the form stays readable on very wide windows
AMOUNT_W, UNIT_W = 96, 170  # ingredient columns on a roomy window
AMOUNT_MIN, UNIT_MIN = 72, 120  # ... and how far they may shrink
NAME_MIN = 150  # the name field never gets narrower than this
REMOVE_W = 46  # the remove button and its gap


def strip_step_number(line: str) -> str:
    return re.sub(r"^\s*(?:[ée]tape\s*)?\d{1,2}\s*[).:\-–]\s*|^\s*[-•*]\s*", "", line, flags=re.IGNORECASE).strip()


def labelled(parent, label: str) -> ctk.CTkFrame:
    """A small frame holding a field's label; pack the field into it."""
    box = ctk.CTkFrame(parent, fg_color="transparent")
    theme.eyebrow(box, label).pack(anchor="w", pady=(0, 4))
    return box


class IngredientEditRow(ctk.CTkFrame):
    """Name | quantity | unit | remove (quantity and unit go under the name when narrow)."""

    def __init__(self, master, editor: "EditorView", ingredient: Ingredient | None = None):
        super().__init__(master, fg_color="transparent")
        self.editor = editor
        self.grid_columnconfigure(0, weight=1)
        self.name = theme.entry(self, placeholder_text="ex. Farine")
        self.name.grid(row=0, column=0, sticky="ew")
        self.qty = ctk.CTkFrame(self, fg_color="transparent")
        self.amount = theme.entry(self.qty, width=AMOUNT_W, placeholder_text="ex. 500")
        self.amount.pack(side="left")
        self.unit = theme.combo(self.qty, COMMON_UNITS, width=UNIT_W)
        self.unit.pack(side="left", padx=(8, 0))
        ctk.CTkButton(self, text="", width=40, height=40, corner_radius=20, fg_color="transparent",
                      hover_color=theme.DANGER_HOVER, image=art.icon("close", theme.MUTED, 15),
                      command=self.remove).grid(row=0, column=2, padx=(6, 0))
        self.layout = None

        amount, unit = split_amount_unit(ingredient.quantite) if ingredient else ("", "")
        if ingredient and ingredient.nom:
            self.name.insert(0, ingredient.nom)
        if amount:
            self.amount.insert(0, amount)
        self.unit.set(unit)

        # Enter walks name -> quantity -> unit -> next ingredient
        self.name.bind("<Return>", lambda _e: self.amount.focus_set())
        self.amount.bind("<Return>", lambda _e: self.unit._entry.focus_set())
        self.unit.bind("<Return>", lambda _e: self.editor.next_ingredient(self))
        self.fit(editor.ingredient_layout)

    def fit(self, layout: tuple[bool, int, int]) -> None:
        """``layout`` = (stacked, quantity width, unit width), see EditorView._fit."""
        if layout == self.layout:
            return
        self.layout = layout
        stacked, amount_w, unit_w = layout
        self.amount.configure(width=amount_w)
        self.unit.configure(width=unit_w)
        if stacked:
            self.qty.grid(row=1, column=0, sticky="w", pady=(6, 0), padx=0)
        else:
            self.qty.grid(row=0, column=1, sticky="", pady=0, padx=(8, 0))

    def value(self) -> Ingredient | None:
        name = self.name.get().strip()
        qty = join_amount_unit(self.amount.get(), self.unit.get())
        if not name and not qty:
            return None
        return Ingredient(nom=name or qty, quantite=qty if name else "")

    def state(self) -> tuple[str, str, str]:
        return self.name.get(), self.amount.get(), self.unit.get()

    def remove(self) -> None:
        self.editor.remove_ingredient(self)


class EditorView(ctk.CTkFrame):
    def __init__(self, master, app: "App"):
        super().__init__(master, fg_color="transparent")
        self.app = app
        self.recipe: Recipe | None = None
        self.on_save: Callable[[Recipe], None] | None = None
        self.on_cancel: Callable[[], None] | None = None
        self._snapshot = None
        self.rows: list[IngredientEditRow] = []
        self.ingredient_layout = (False, AMOUNT_W, UNIT_W)
        self._wrapped: list[ctk.CTkLabel] = []  # hints that wrap to the form's width
        self._inner_w = 0

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        # --- top bar: cancel | title | error + save
        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.grid(row=0, column=0, sticky="ew", padx=26, pady=(22, 8))
        theme.ghost_button(bar, text="Annuler", image=art.icon("back", theme.TEXT, 18), compound="left",
                           width=0, command=self.cancel).pack(side="left")
        self.btn_save = theme.primary_button(bar, text="Enregistrer", image=art.icon("check", theme.ON_ACCENT, 17),
                                             compound="left", width=150, command=self.submit)
        self.btn_save.pack(side="right")
        self.error = ctk.CTkLabel(bar, text="", text_color=theme.DANGER, font=theme.font(13, "bold"))
        self.error.pack(side="right", padx=14)

        self.body = ScrollFrame(self)
        self.body.grid(row=1, column=0, sticky="nsew", padx=(10, 8), pady=(0, 8))
        self.body.grid_columnconfigure(0, weight=1)
        self.body.bind("<Configure>", self._center, add="+")
        self.column: ctk.CTkFrame | None = None

    # ------------------------------------------------------------------ api
    def open(self, recipe: Recipe | None, on_save: Callable[[Recipe], None], title: str | None = None,
             banner: str | None = None, on_cancel: Callable[[], None] | None = None) -> None:
        self.recipe = copy.deepcopy(recipe) if recipe else Recipe(nom="")
        self.on_save = on_save
        self.on_cancel = on_cancel
        heading = title or ("Modifier la recette" if recipe and recipe.nom else "Nouvelle recette")
        self.error.configure(text="")
        self._build(heading, banner)
        self.body._parent_canvas.yview_moveto(0)
        # an imported recipe is not saved yet: leaving it always asks first
        self._snapshot = () if on_cancel else self._state()
        self.after(120, self.e_nom.focus_set)

    def dirty(self) -> bool:
        return self.recipe is not None and self._state() != self._snapshot

    def discard(self) -> None:
        """Forget the form without saving (runs the cancel hook once)."""
        callback, self.on_cancel = self.on_cancel, None
        self.recipe = None
        if callback:
            callback()

    def cancel(self) -> None:
        if self.dirty() and not messagebox.askyesno(
                "Modifications non enregistrées", "Quitter sans enregistrer vos modifications ?",
                icon="warning", parent=self):
            return
        self.discard()
        self.app.leave_editor()

    # ---------------------------------------------------------------- build
    def _center(self, event=None) -> None:
        if self.column is None:
            return
        scaling = ctk.ScalingTracker.get_widget_scaling(self)
        width = (event.width if event else self.body.winfo_width()) / scaling
        side = max(10, int((width - MAX_WIDTH) / 2))
        self.column.grid_configure(padx=side)
        self._fit(int(width - 2 * side - 46))  # minus the cards' padding and border

    def _fit(self, inner: int) -> None:
        """Adapt the form to its width (unscaled px inside a card)."""
        if inner <= 0 or inner == self._inner_w:
            return
        self._inner_w = inner
        for label in self._wrapped:
            if label.winfo_exists():
                label.configure(wraplength=max(160, inner))
        # name | quantity | unit | remove on one line while the name keeps
        # NAME_MIN; quantity and unit shrink first, then move under the name
        spare = inner - NAME_MIN - REMOVE_W - 8
        if spare >= AMOUNT_W + 8 + UNIT_W:
            layout = (False, AMOUNT_W, UNIT_W)
        elif spare >= AMOUNT_MIN + 8 + UNIT_MIN:
            amount = max(AMOUNT_MIN, min(AMOUNT_W, int((spare - 8) * 0.36)))
            layout = (False, amount, spare - 8 - amount)
        else:
            layout = (True, AMOUNT_W, max(UNIT_MIN, min(UNIT_W, inner - REMOVE_W - AMOUNT_W - 8)))
        if layout != self.ingredient_layout:
            self.ingredient_layout = layout
            for row in self.rows:
                row.fit(layout)
            stacked, amount_w, unit_w = layout
            if stacked:  # the placeholders say what goes where
                self.h_amount.grid_remove()
                self.h_unit.grid_remove()
            else:
                self.h_amount.configure(width=amount_w)
                self.h_unit.configure(width=unit_w)
                self.h_amount.grid()
                self.h_unit.grid()
        self.m_plat.reflow(inner)
        # the Favori switch goes under the cooking menu when they do not fit side by side
        stacked = inner < 240 + 28 + 120
        if stacked != self._fav_stacked:
            self._fav_stacked = stacked
            if stacked:
                self.s_fav.grid_configure(row=1, column=0, padx=0, pady=(14, 0))
            else:
                self.s_fav.grid_configure(row=0, column=1, padx=(28, 0), pady=(22, 0))

    def _build(self, heading: str, banner: str | None) -> None:
        for child in self.body.winfo_children():
            child.destroy()
        self.rows = []
        self._wrapped = []
        self._inner_w = 0
        self.column = None  # no resize handling until the form is complete
        col = ctk.CTkFrame(self.body, fg_color="transparent")
        col.grid(row=0, column=0, sticky="ew", padx=10)
        r = self.recipe

        theme.heading(col, heading, size=32).pack(anchor="w", padx=4, pady=(4, 2))
        sub = ctk.CTkLabel(col, text="Seul le nom est obligatoire. Le reste peut être complété plus tard.",
                           font=theme.font(14), text_color=theme.MUTED, anchor="w", justify="left")
        sub.pack(anchor="w", padx=4, pady=(0, 16))
        self._wrapped.append(sub)

        if banner:
            ban = ctk.CTkFrame(col, fg_color=theme.NOTE_BG, corner_radius=14)
            ban.pack(fill="x", pady=(0, 16))
            ctk.CTkFrame(ban, width=5, fg_color=theme.NOTE_BAR, corner_radius=3).pack(side="left", fill="y",
                                                                                     padx=(14, 0), pady=14)
            text = ctk.CTkLabel(ban, text=banner, text_color=theme.TEXT, font=theme.font(14), justify="left",
                                anchor="w", wraplength=760)
            text.pack(side="left", fill="x", padx=14, pady=12)
            self._wrapped.append(text)

        # --- essentials
        card = self._section(col, "L'essentiel")
        box = labelled(card, "Nom de la recette *")
        box.pack(fill="x", pady=(0, 14))
        self.e_nom = theme.entry(box, placeholder_text="ex. Gâteau au chocolat de mamie", height=48,
                                 font=theme.font(17))
        self.e_nom.pack(fill="x")
        self.e_nom.insert(0, r.nom)

        numbers = ctk.CTkFrame(card, fg_color="transparent")
        numbers.pack(fill="x", pady=(0, 14))
        numbers.grid_columnconfigure((0, 1, 2), weight=1, uniform="n")
        self.e_prep = self._number(numbers, 0, "Préparation", "min", "ex. 20", r.temps)
        self.e_cook = self._number(numbers, 1, "Cuisson", "min", "ex. 45", r.temps_cuisson)
        self.e_pour = self._number(numbers, 2, "Pour", "pers.", "4", r.pour)

        box = labelled(card, "Type de plat")
        box.pack(fill="x", pady=(0, 14))
        self.m_plat = Choice(box, DISH_TYPES)
        self.m_plat.pack(anchor="w")
        self.m_plat.set(r.type_plat)

        row = ctk.CTkFrame(card, fg_color="transparent")
        row.pack(fill="x")
        box = labelled(row, "Mode de cuisson")
        box.grid(row=0, column=0, sticky="w")
        self.m_cuisson = theme.option_menu(box, COOKING_TYPES, width=240)
        self.m_cuisson.pack(anchor="w")
        self.m_cuisson.set(r.type_cuisson)
        self.fav = ctk.BooleanVar(value=r.favori)
        self.s_fav = theme.switch(row, text="  Favori", variable=self.fav)
        self.s_fav.grid(row=0, column=1, sticky="w", padx=(28, 0), pady=(22, 0))
        self._fav_stacked = False

        # --- tags
        card = self._section(col, "Étiquettes", "Pour retrouver la recette : Noël, rapide, sans gluten… "
                             "Tapez un mot puis Entrée (ou une virgule).")
        self.tags = list(r.tags)
        self.e_tag = theme.entry(card, placeholder_text="ex. Noël")
        self.e_tag.pack(fill="x")
        self.e_tag.bind("<Return>", lambda _e: self._commit_tag_text())
        self.e_tag.bind("<KeyRelease>", lambda e: self._tag_typed(), add="+")
        self.tag_chips = FlowFrame(card)
        self.tag_chips.pack(fill="x", pady=(10, 0))
        self.tag_hint = ctk.CTkLabel(card, text="Déjà utilisées :", font=theme.font(12), text_color=theme.MUTED,
                                     anchor="w")
        self.tag_suggestions = FlowFrame(card)
        self._refresh_tags()

        # --- ingredients
        card = self._section(col, "Ingrédients", "Une ligne par ingrédient. Appuyez sur Entrée pour passer à la case suivante.")
        head = ctk.CTkFrame(card, fg_color="transparent")
        head.pack(fill="x")
        head.grid_columnconfigure(0, weight=1)
        theme.eyebrow(head, "Ingrédient").grid(row=0, column=0, sticky="w")
        stacked, amount_w, unit_w = self.ingredient_layout
        self.h_amount = theme.eyebrow(head, "Quantité", width=amount_w)
        self.h_amount.grid(row=0, column=1, sticky="w", padx=(8, 0))
        self.h_unit = theme.eyebrow(head, "Unité", width=unit_w)
        self.h_unit.grid(row=0, column=2, sticky="w", padx=(8, 0))
        if stacked:
            self.h_amount.grid_remove()
            self.h_unit.grid_remove()
        ctk.CTkFrame(head, fg_color="transparent", width=REMOVE_W, height=1).grid(row=0, column=3)
        self.ing_list = ctk.CTkFrame(card, fg_color="transparent")
        self.ing_list.pack(fill="x", pady=(4, 0))
        for ingredient in r.ingredients or [None]:
            self.add_ingredient(ingredient, focus=False)
        theme.secondary_button(card, text="Ajouter un ingrédient", image=art.icon("plus", theme.TEXT, 16),
                               compound="left", width=0, height=40,
                               command=lambda: self.add_ingredient()).pack(anchor="w", pady=(10, 0))

        # --- steps & notes
        card = self._section(col, "Étapes", "Une étape par ligne, dans l'ordre. Pas besoin de les numéroter.")
        self.t_steps = theme.textbox(card, height=240, font=theme.font(15))
        self.t_steps.pack(fill="x")
        self.t_steps.insert("1.0", "\n".join(r.etapes))

        card = self._section(col, "Notes", "Astuces, variantes, souvenirs…")
        self.t_notes = theme.textbox(card, height=100, font=theme.font(15))
        self.t_notes.pack(fill="x")
        self.t_notes.insert("1.0", r.notes)

        theme.primary_button(col, text="Enregistrer la recette", image=art.icon("check", theme.ON_ACCENT, 17),
                             compound="left", height=48, command=self.submit).pack(anchor="e", pady=(0, 24))
        self.column = col
        self._center()

    def _section(self, col, title: str, hint: str = "") -> ctk.CTkFrame:
        card = theme.card(col)
        card.pack(fill="x", pady=(0, 18))
        theme.heading(card, title, size=21).pack(anchor="w", padx=22, pady=(20, 0))
        if hint:
            label = ctk.CTkLabel(card, text=hint, font=theme.font(13), text_color=theme.MUTED, anchor="w",
                                 justify="left")
            label.pack(anchor="w", padx=22)
            self._wrapped.append(label)
        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="x", padx=22, pady=(14, 22))  # keeps clear of the rounded corners
        return inner

    @staticmethod
    def _number(parent, column: int, label: str, suffix: str, placeholder: str, value: int) -> ctk.CTkEntry:
        box = labelled(parent, label)
        box.grid(row=0, column=column, sticky="ew", padx=(0 if column == 0 else 12, 0))
        field = ctk.CTkFrame(box, fg_color="transparent")
        field.pack(fill="x")
        field.grid_columnconfigure(0, weight=1)
        entry = theme.entry(field, placeholder_text=placeholder)
        entry.grid(row=0, column=0, sticky="ew")
        ctk.CTkLabel(field, text=suffix, font=theme.font(14), text_color=theme.MUTED).grid(row=0, column=1,
                                                                                           padx=(8, 0))
        if value:
            entry.insert(0, str(value))
        return entry

    # ------------------------------------------------------------------ tags
    def _add_tags(self, *names: str) -> None:
        before = len(self.tags)
        self.tags = normalize_tags([*self.tags, *names])
        if len(self.tags) > before:
            usage.hit("tag_add")
        self._refresh_tags()

    def _remove_tag(self, tag: str) -> None:
        self.tags = [t for t in self.tags if fold(t) != fold(tag)]
        self._refresh_tags()

    def _commit_tag_text(self) -> None:
        text = self.e_tag.get()
        self.e_tag.delete(0, "end")
        self._add_tags(*text.split(","))

    def _tag_typed(self) -> None:
        text = self.e_tag.get()
        if "," in text:
            done, _, rest = text.rpartition(",")
            self.e_tag.delete(0, "end")
            self.e_tag.insert(0, rest.lstrip())
            self._add_tags(*done.split(","))
        else:
            self._refresh_suggestions()

    def _refresh_tags(self) -> None:
        chips = [ctk.CTkButton(self.tag_chips, text=f"{t}   ✕", height=32, width=0, corner_radius=16,
                               font=theme.font(14, "bold"), fg_color=theme.ACCENT_SOFT, hover_color=theme.BORDER,
                               text_color=theme.ACCENT_TEXT, command=lambda t=t: self._remove_tag(t))
                 for t in self.tags]
        self.tag_chips.set_items(chips)
        self.e_tag.configure(state="normal" if len(self.tags) < MAX_TAGS else "disabled")
        self._refresh_suggestions()

    def _refresh_suggestions(self) -> None:
        """Tags already used in the book (not yet on this recipe), filtered by what is being typed."""
        typed = fold(self.e_tag.get())
        have = {fold(t) for t in self.tags}
        known = [t for t, _n in tag_counts(self.app.store.all()) if fold(t) not in have and typed in fold(t)]
        known = known[:12]
        pills = [ctk.CTkButton(self.tag_suggestions, text=t, height=30, width=0, corner_radius=15,
                               font=theme.font(13), fg_color=theme.SURFACE, hover_color=theme.SURFACE_ALT,
                               text_color=theme.TEXT, border_width=1, border_color=theme.BORDER_STRONG,
                               command=lambda t=t: self._pick_suggestion(t)) for t in known]
        if pills:
            if not self.tag_suggestions.winfo_manager():
                self.tag_hint.pack(anchor="w", pady=(12, 4))
                self.tag_suggestions.pack(fill="x")
        else:
            self.tag_hint.pack_forget()
            self.tag_suggestions.pack_forget()
        self.tag_suggestions.set_items(pills)

    def _pick_suggestion(self, tag: str) -> None:
        self.e_tag.delete(0, "end")
        self._add_tags(tag)

    def _final_tags(self) -> list[str]:
        return normalize_tags([*self.tags, *self.e_tag.get().split(",")])

    # ----------------------------------------------------------- ingredients
    def add_ingredient(self, ingredient: Ingredient | None = None, focus: bool = True) -> IngredientEditRow:
        row = IngredientEditRow(self.ing_list, self, ingredient)
        row.pack(fill="x", pady=4)
        self.rows.append(row)
        if focus:
            self.after(30, row.name.focus_set)
        return row

    def next_ingredient(self, row: IngredientEditRow) -> None:
        index = self.rows.index(row)
        if index + 1 < len(self.rows):
            self.rows[index + 1].name.focus_set()
        else:
            self.add_ingredient()

    def remove_ingredient(self, row: IngredientEditRow) -> None:
        self.rows.remove(row)
        row.destroy()
        if not self.rows:
            self.add_ingredient(focus=False)

    # ---------------------------------------------------------------- save
    def _state(self) -> tuple:
        if self.recipe is None or not hasattr(self, "e_nom") or not self.e_nom.winfo_exists():
            return ()
        return (self.e_nom.get(), self.e_prep.get(), self.e_cook.get(), self.e_pour.get(), self.m_plat.get(),
                self.m_cuisson.get(), self.fav.get(), tuple(self.tags), self.e_tag.get().strip(), tuple(r.state() for r in self.rows),
                self.t_steps.get("1.0", "end").strip(), self.t_notes.get("1.0", "end").strip())

    def _fail(self, message: str, widget=None) -> None:
        self.error.configure(text=message)
        if widget is not None:
            widget.configure(border_color=theme.DANGER)
            widget.focus_set()

    def submit(self) -> None:
        if self.recipe is None:
            return
        for widget in (self.e_nom, self.e_prep, self.e_cook):
            widget.configure(border_color=theme.BORDER_STRONG)
        nom = self.e_nom.get().strip()
        if not nom:
            self.body._parent_canvas.yview_moveto(0)
            return self._fail("Donnez un nom à la recette.", self.e_nom)
        for widget, label in ((self.e_prep, "préparation"), (self.e_cook, "cuisson")):
            raw = widget.get().strip()
            if raw and parse_minutes(raw) == 0 and not raw.startswith("0"):
                return self._fail(f"Temps de {label} illisible : écrivez par ex. 20 ou 1h30.", widget)
        r = self.recipe
        r.nom = nom
        r.temps = parse_minutes(self.e_prep.get())
        r.temps_cuisson = parse_minutes(self.e_cook.get())
        r.pour = parse_int(self.e_pour.get(), default=4, minimum=1)
        r.type_plat = self.m_plat.get() or r.type_plat
        r.type_cuisson = self.m_cuisson.get()
        r.favori = self.fav.get()
        r.tags = self._final_tags()
        r.ingredients = [i for i in (row.value() for row in self.rows) if i]
        r.etapes = [s for s in (strip_step_number(x) for x in self.t_steps.get("1.0", "end").splitlines()) if s]
        r.notes = self.t_notes.get("1.0", "end").strip()
        on_save = self.on_save
        self._snapshot = self._state()  # saving: leaving the editor must not ask "discard changes?"
        try:
            if on_save:
                on_save(r)
        except Exception as exc:
            log.exception("Saving recipe failed")
            messagebox.showerror("Erreur", f"Impossible d'enregistrer la recette :\n{exc}", parent=self)
            return
        self.on_cancel = None  # saved: nothing to clean up
        self.recipe = None
