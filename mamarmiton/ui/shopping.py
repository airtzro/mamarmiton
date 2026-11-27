"""Shopping list page: chosen recipes with servings, merged ingredients by aisle, extra items."""

from __future__ import annotations

from tkinter import messagebox
from typing import TYPE_CHECKING

import customtkinter as ctk

from mamarmiton import config
from mamarmiton.models import Recipe
from mamarmiton.shopping import ShoppingItem, build_list, group_by_aisle, open_with_default_app, to_text
from mamarmiton.ui import art, theme
from mamarmiton.ui.detail import IngredientRow
from mamarmiton.ui.widgets import ScrollFrame, set_cursor_tree

if TYPE_CHECKING:
    from mamarmiton.ui.app import App

PAD = 36
TWO_COLS = 860  # content width (unscaled px) from which recipes and list sit side by side


class ShoppingRow(IngredientRow):
    """An ingredient row that tells its owner when it is (un)ticked, optionally with a remove button."""

    def __init__(self, master, qty: str, name: str, checked: bool, on_toggle, on_remove=None):
        super().__init__(master, qty, name)
        self._on_toggle = on_toggle
        if on_remove is not None:
            ctk.CTkButton(self, text="", width=30, height=30, corner_radius=15, fg_color="transparent",
                          hover_color=theme.SURFACE_HOVER, image=art.icon("close", theme.MUTED, 14),
                          command=on_remove).grid(row=0, column=3, padx=(0, 8))
        if checked:
            self.var.set(True)
            super()._paint()

    def _paint(self) -> None:
        super()._paint()
        self._on_toggle(self.var.get())


class RecipeRow(ctk.CTkFrame):
    """A recipe in the list: title, servings stepper, remove button."""

    def __init__(self, master, recipe: Recipe, servings: int, on_change, on_remove, on_open):
        super().__init__(master, fg_color="transparent", corner_radius=10)
        self.grid_columnconfigure(0, weight=1)
        self.name = ctk.CTkLabel(self, text=recipe.nom, font=theme.font(15, "bold"), text_color=theme.TEXT,
                                 anchor="w", justify="left")
        self.name.grid(row=0, column=0, sticky="ew", padx=(10, 6), pady=(8, 2))
        self.name.bind("<Button-1>", lambda _e: on_open())
        set_cursor_tree(self.name, "hand2")
        self.stepper = ctk.CTkFrame(self, fg_color="transparent")
        btn = dict(text="", width=32, height=32, corner_radius=16, fg_color=theme.SURFACE_ALT,
                   hover_color=theme.ACCENT_SOFT, border_width=1, border_color=theme.BORDER_STRONG)
        ctk.CTkButton(self.stepper, image=art.icon("minus", theme.TEXT, 14),
                      command=lambda: on_change(servings - 1), **btn).pack(side="left")
        self.count = ctk.CTkLabel(self.stepper, text=f"{servings} pers.", font=theme.font(14), text_color=theme.TEXT,
                                  width=72)
        self.count.pack(side="left")
        ctk.CTkButton(self.stepper, image=art.icon("plus", theme.TEXT, 14),
                      command=lambda: on_change(servings + 1), **btn).pack(side="left")
        self.remove = ctk.CTkButton(self, text="", width=30, height=30, corner_radius=15, fg_color="transparent",
                                    hover_color=theme.SURFACE_HOVER, image=art.icon("close", theme.MUTED, 14),
                                    command=on_remove)
        self.remove.grid(row=0, column=2, padx=(0, 8), pady=(6, 0), sticky="n")
        self.narrow: bool | None = None
        self.set_narrow(False)

    def set_narrow(self, narrow: bool) -> None:
        if narrow == self.narrow:
            return
        self.narrow = narrow
        if narrow:
            self.stepper.grid(row=1, column=0, columnspan=3, sticky="w", padx=10, pady=(0, 8))
        else:
            self.stepper.grid(row=0, column=1, padx=(0, 6), pady=6)

    def set_wrap(self, width: int) -> None:
        self.name.configure(wraplength=max(120, width))


class ShoppingView(ctk.CTkFrame):
    def __init__(self, master, app: "App"):
        super().__init__(master, fg_color="transparent")
        self.app = app
        self.items: list[ShoppingItem] = []
        self._rows: dict[str, ShoppingRow] = {}
        self._recipe_rows: list[RecipeRow] = []
        self._wrapped: list = []
        self._built = False
        self._two_cols: bool | None = None
        self._compact: bool | None = None
        self._copy_timer = None

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.grid(row=0, column=0, sticky="ew", padx=PAD - 10, pady=(22, 8))
        self.btn_back = theme.ghost_button(bar, text="Toutes les recettes", image=art.icon("back", theme.TEXT, 18),
                                           compound="left", width=0, command=app.go_home)
        self.btn_back.pack(side="left")
        self.btn_clear = theme.danger_button(bar, text="Vider la liste", image=art.icon("trash", theme.DANGER, 17),
                                             compound="left", width=0, command=self.clear)
        self.btn_clear.pack(side="right", padx=(8, 10))
        self.btn_print = theme.secondary_button(bar, text="Imprimer", image=art.icon("print", theme.TEXT, 17),
                                                compound="left", width=0, command=self.print_list)
        self.btn_print.pack(side="right", padx=(8, 0))
        self.btn_copy = theme.secondary_button(bar, text="Copier", image=art.icon("copy", theme.TEXT, 17),
                                               compound="left", width=0, command=self.copy)
        self.btn_copy.pack(side="right", padx=(8, 0))

        self.body = ScrollFrame(self)
        self.body.grid(row=1, column=0, sticky="nsew", padx=(PAD - 14, 8), pady=(0, 8))
        self.body.bind("<Configure>", self._rewrap, add="+")

    # ------------------------------------------------------------------ data
    def _selection(self) -> list[tuple[Recipe, int]]:
        return self.app.shopping_selection()

    def show(self) -> None:
        """(Re)build the page from the saved list."""
        canvas = self.body._parent_canvas
        top = canvas.yview()[0]
        for child in self.body.winfo_children():
            child.destroy()
        self._rows, self._recipe_rows, self._wrapped = {}, [], []
        self._built = False
        self._two_cols = None
        state = self.app.shopping
        selection = self._selection()
        self.items = build_list(selection)
        # no pruning here: recipes missing from this book may belong to the other account's book
        # (or not be synced yet); deleted recipes are removed from the list by App.delete_recipe

        head = ctk.CTkFrame(self.body, fg_color="transparent")
        head.grid(row=0, column=0, columnspan=2, sticky="ew", padx=14, pady=(4, 18))
        theme.heading(head, "Liste de courses", size=30).pack(anchor="w")
        self.subtitle = ctk.CTkLabel(head, text="", font=theme.font(14), text_color=theme.MUTED, anchor="w",
                                     justify="left")
        self.subtitle.pack(anchor="w", pady=(2, 0))
        self._wrapped.append((self.subtitle, "full"))

        self._build_recipes(selection)
        self._build_items()
        self._update_subtitle()
        self._update_buttons()
        self._built = True
        self._rewrap()
        self.after_idle(lambda: canvas.yview_moveto(top))

    def _build_recipes(self, selection) -> None:
        self.recipes_box = theme.card(self.body)
        theme.heading(self.recipes_box, "Recettes", size=20).pack(anchor="w", padx=22, pady=(20, 4))
        if not selection:
            label = ctk.CTkLabel(self.recipes_box, anchor="w", justify="left", font=theme.font(14),
                                 text_color=theme.MUTED,
                                 text="Aucune recette pour l'instant. Ouvrez une recette et touchez "
                                      "« Ajouter à la liste de courses ».")
            label.pack(fill="x", padx=22, pady=(4, 20))
            self._wrapped.append((label, "recipes_text"))
            return
        holder = ctk.CTkFrame(self.recipes_box, fg_color="transparent")
        holder.pack(fill="x", padx=12, pady=(4, 16))
        for recipe, servings in selection:
            row = RecipeRow(holder, recipe, servings,
                            on_change=lambda n, r=recipe: self.set_servings(r, n),
                            on_remove=lambda r=recipe: self.remove_recipe(r),
                            on_open=lambda r=recipe: self.app.open_recipe(r))
            row.pack(fill="x", pady=1)
            self._recipe_rows.append(row)

    def _build_items(self) -> None:
        state = self.app.shopping
        self.items_box = theme.card(self.body)
        theme.heading(self.items_box, "À acheter", size=20).pack(anchor="w", padx=22, pady=(20, 4))

        add = ctk.CTkFrame(self.items_box, fg_color="transparent")
        add.pack(fill="x", padx=18, pady=(6, 6))
        add.grid_columnconfigure(0, weight=1)
        self.entry = theme.entry(add, placeholder_text="Ajouter un article (ex. : papier absorbant)")
        self.entry.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        self.entry.bind("<Return>", lambda _e: self.add_extra())
        self.btn_add = theme.secondary_button(add, text="Ajouter", width=0, image=art.icon("plus", theme.TEXT, 16),
                                              compound="left", command=self.add_extra)
        self.btn_add.grid(row=0, column=1)

        holder = ctk.CTkFrame(self.items_box, fg_color="transparent")
        holder.pack(fill="x", padx=12, pady=(0, 16))
        if not self.items and not state.extras:
            label = ctk.CTkLabel(holder, text="Rien à acheter pour l'instant.", font=theme.font(14),
                                 text_color=theme.MUTED, anchor="w")
            label.pack(fill="x", padx=10, pady=6)
        for aisle, group in group_by_aisle(self.items):
            theme.eyebrow(holder, aisle).pack(fill="x", padx=12, pady=(14, 2))
            for item in group:
                row = ShoppingRow(holder, item.quantity, item.name, item.key in state.checked,
                                  on_toggle=lambda value, k=item.key: self._ticked(k, value))
                row.pack(fill="x", pady=1)
                self._rows[item.key] = row
                self._wrapped.append((row.name, "item"))
        if state.extras:
            theme.eyebrow(holder, "Autres articles").pack(fill="x", padx=12, pady=(14, 2))
            for index, extra in enumerate(state.extras):
                row = ShoppingRow(holder, "", extra["text"], extra["done"],
                                  on_toggle=lambda value, i=index: self._extra_ticked(i, value),
                                  on_remove=lambda i=index: self.remove_extra(i))
                row.pack(fill="x", pady=1)
                self._wrapped.append((row.name, "extra"))

    # --------------------------------------------------------------- actions
    def _changed(self, rebuild: bool = False) -> None:
        self.app.save_shopping()
        if rebuild:
            self.show()
        else:
            self._update_subtitle()
            self._update_buttons()

    def _ticked(self, key: str, value: bool) -> None:
        self.app.shopping.set_checked(key, value)
        self._changed()

    def _extra_ticked(self, index: int, value: bool) -> None:
        self.app.shopping.extras[index]["done"] = value
        self._changed()

    def set_servings(self, recipe: Recipe, servings: int) -> None:
        self.app.shopping.set_servings(recipe.id, servings)
        self._changed(rebuild=True)

    def remove_recipe(self, recipe: Recipe) -> None:
        self.app.shopping.remove_recipe(recipe.id)
        self._changed(rebuild=True)

    def add_extra(self) -> None:
        if self.app.shopping.add_extra(self.entry.get()):
            self._changed(rebuild=True)
            self.after(50, lambda: self.winfo_exists() and self.entry.focus_set())

    def remove_extra(self, index: int) -> None:
        if 0 <= index < len(self.app.shopping.extras):
            del self.app.shopping.extras[index]
            self._changed(rebuild=True)

    def clear(self) -> None:
        state = self.app.shopping
        if not (state.recipes or state.extras or state.checked):
            return
        if messagebox.askyesno("Vider la liste", "Enlever toutes les recettes et tous les articles de la liste "
                                                  "de courses ?", icon="warning", parent=self.app):
            state.clear()
            self._changed(rebuild=True)

    def plain_text(self) -> str:
        state = self.app.shopping
        return to_text(self.items, state.checked, state.extras)

    def copy(self) -> None:
        self.clipboard_clear()
        self.clipboard_append(self.plain_text())
        self.update()  # keep the text on the clipboard after the window closes (X11)
        self.btn_copy.configure(text="Copié !" if not self._compact else "")
        if self._copy_timer:
            self.after_cancel(self._copy_timer)
        self._copy_timer = self.after(2000, self._reset_copy)

    def _reset_copy(self) -> None:
        self._copy_timer = None
        if self.winfo_exists():
            self.btn_copy.configure(text="" if self._compact else "Copier")

    def print_list(self) -> None:
        """Write the list to a .txt file and open it: the user prints from there."""
        path = config.data_dir() / "liste-de-courses.txt"
        try:
            path.write_text(self.plain_text(), encoding="utf-8")
        except OSError as exc:
            messagebox.showerror("Impression impossible", f"Le fichier n'a pas pu être créé :\n{exc}",
                                 parent=self.app)
            return
        if not open_with_default_app(path):
            messagebox.showinfo("Liste de courses", f"La liste est enregistrée ici :\n{path}\n\n"
                                                    "Ouvrez ce fichier pour l'imprimer.", parent=self.app)

    # ------------------------------------------------------------------ view
    def _remaining(self) -> int:
        state = self.app.shopping
        return (sum(i.key not in state.checked for i in self.items)
                + sum(not e["done"] for e in state.extras))

    def _update_subtitle(self) -> None:
        state = self.app.shopping
        total = len(self.items) + len(state.extras)
        if not total:
            text = "Ajoutez des recettes ou des articles : la liste se remplit toute seule."
        else:
            left = self._remaining()
            text = (f"{left} article{'s' if left > 1 else ''} à acheter sur {total}" if left
                    else "Tout est dans le panier !")
        self.subtitle.configure(text=text)
        self.app.sidebar.update_shopping_count()

    def _update_buttons(self) -> None:
        state = self.app.shopping
        has_any = bool(self.items or state.extras)
        self.btn_copy.configure(state="normal" if has_any else "disabled")
        self.btn_print.configure(state="normal" if has_any else "disabled")
        self.btn_clear.configure(state="normal" if (state.recipes or state.extras or state.checked) else "disabled")

    def _rewrap(self, _event=None) -> None:
        if not self._built:
            return
        width = self.body.winfo_width()
        if width <= 1:
            width = self.app.winfo_width() - self.app.sidebar.winfo_width() - 2 * PAD
        scaling = ctk.ScalingTracker.get_widget_scaling(self)
        width = int(width / scaling) - 28
        two = width >= TWO_COLS
        if two != self._two_cols:
            self._two_cols = two
            if two:
                self.body.grid_columnconfigure(0, weight=2, uniform="col")
                self.body.grid_columnconfigure(1, weight=3, uniform="col")
                self.recipes_box.grid(row=1, column=0, sticky="new", padx=(14, 10), pady=(0, 20))
                self.items_box.grid(row=1, column=1, sticky="new", padx=(10, 14), pady=(0, 20))
            else:
                self.body.grid_columnconfigure(0, weight=1, uniform="")
                self.body.grid_columnconfigure(1, weight=0, uniform="")
                self.recipes_box.grid(row=1, column=0, columnspan=2, sticky="new", padx=14, pady=(0, 20))
                self.items_box.grid(row=2, column=0, columnspan=2, sticky="new", padx=14, pady=(0, 20))
        left = width * 2 // 5 if two else width
        right = width - left - 20 if two else width
        for row in self._recipe_rows:
            row.set_narrow(left < 460)
            row.set_wrap(left - (150 if left >= 460 else 90))
        for widget, kind in self._wrapped:
            try:
                if kind == "item":
                    widget.configure(wraplength=max(120, right - 190))
                elif kind == "extra":
                    widget.configure(wraplength=max(120, right - 140))
                elif kind == "recipes_text":
                    widget.configure(wraplength=max(160, left - 70))
                else:
                    widget.configure(wraplength=max(200, width - 60))
            except Exception:  # widget destroyed while re-rendering
                pass
        self._compact_toolbar(width < 760)
        self.btn_add.configure(text="" if width < 520 else "Ajouter")

    def _compact_toolbar(self, compact: bool) -> None:
        if compact == self._compact:
            return
        self._compact = compact
        self.btn_clear.configure(text="" if compact else "Vider la liste")
        self.btn_print.configure(text="" if compact else "Imprimer")
        self.btn_copy.configure(text="" if compact else "Copier")
        self.btn_back.configure(text="Retour" if compact else "Toutes les recettes")
