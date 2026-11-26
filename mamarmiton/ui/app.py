"""Main window: dark sidebar (brand, categories, actions) + home / recipe screens."""

from __future__ import annotations

import logging
import os
import shutil
import sys
from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk

from mamarmiton import APP_NAME, __version__, config
from mamarmiton.importers import ImportResult
from mamarmiton.models import DISH_TYPES, Recipe
from mamarmiton.search import Filters, search
from mamarmiton.settings import APPEARANCES, TEXT_SIZES, Settings
from mamarmiton.storage import RecipeStore
from mamarmiton.ui import art, theme
from mamarmiton.ui.detail import DetailView
from mamarmiton.ui.dialogs import HelpDialog, ImportDialog, ask_image, ask_pdf
from mamarmiton.ui.editor import EditorView
from mamarmiton.ui.home import HomeView
from mamarmiton.ui.widgets import NavItem, fix_scaled_sizes, set_window_icon, use_portable_corners

log = logging.getLogger(__name__)

FAVORITES = "__favoris__"
PLURALS = {"Entrée": "Entrées", "Plat": "Plats", "Dessert": "Desserts", "Pain": "Pains"}


class Sidebar(ctk.CTkFrame):
    WIDTH = 264

    def __init__(self, master, app: "App"):
        super().__init__(master, fg_color=theme.SIDEBAR, corner_radius=0, width=self.WIDTH)
        self.app = app
        self.category: str | None = None  # None = all, FAVORITES, or a dish type
        self.grid_propagate(False)
        self.pack_propagate(False)

        brand = ctk.CTkFrame(self, fg_color="transparent")
        brand.pack(fill="x", padx=20, pady=(26, 22))
        ctk.CTkLabel(brand, text="", image=art.logo(44)).pack(side="left")
        words = ctk.CTkFrame(brand, fg_color="transparent")
        words.pack(side="left", padx=(12, 0))
        ctk.CTkLabel(words, text="Mamarmiton", font=theme.serif(21), text_color=theme.SIDEBAR_TEXT,
                     anchor="w", height=24).pack(anchor="w")
        ctk.CTkLabel(words, text="Les recettes de la famille", font=theme.font(12), text_color=theme.SIDEBAR_MUTED,
                     anchor="w", height=18).pack(anchor="w")

        self.items: dict[str | None, NavItem] = {}
        self._nav(None, "Toutes les recettes", icon="grid")
        self._nav(FAVORITES, "Favoris", icon="star")

        ctk.CTkLabel(self, text="CATÉGORIES", font=theme.font(11, "bold"), text_color=theme.SIDEBAR_MUTED,
                     anchor="w").pack(fill="x", padx=26, pady=(22, 4))
        for dish in DISH_TYPES:
            self._nav(dish, PLURALS.get(dish, dish), dot=theme.dish(dish)["dot"])

        bottom = ctk.CTkFrame(self, fg_color="transparent")
        bottom.pack(side="bottom", fill="x", padx=16, pady=(0, 18))
        theme.primary_button(bottom, text="Nouvelle recette", image=art.icon("plus", theme.ON_ACCENT, 18),
                             compound="left", height=46, command=app.new_recipe).pack(fill="x")
        ctk.CTkButton(bottom, text="Importer un PDF", image=art.icon("pdf", theme.SIDEBAR_TEXT, 18), compound="left",
                      height=44, corner_radius=theme.RADIUS, fg_color="transparent", hover_color=theme.SIDEBAR_HOVER,
                      border_width=1, border_color=theme.SIDEBAR_LINE, text_color=theme.SIDEBAR_TEXT,
                      font=theme.font(14, "bold"), command=app.import_pdf).pack(fill="x", pady=(8, 14))
        ctk.CTkFrame(bottom, height=1, fg_color=theme.SIDEBAR_LINE, corner_radius=0).pack(fill="x", pady=(0, 8))
        help_row = ctk.CTkFrame(bottom, fg_color="transparent")
        help_row.pack(fill="x")
        ctk.CTkButton(help_row, text="Aide et réglages", image=art.icon("settings", theme.SIDEBAR_MUTED, 17),
                      compound="left", anchor="w", height=38, corner_radius=10, fg_color="transparent",
                      hover_color=theme.SIDEBAR_HOVER, text_color=theme.SIDEBAR_TEXT, font=theme.font(13),
                      command=app.show_help).pack(side="left", fill="x", expand=True)
        ctk.CTkLabel(help_row, text=f"v{__version__}", font=theme.font(11), text_color=theme.SIDEBAR_MUTED).pack(
            side="right", padx=(4, 6))

        self._highlight()

    def _nav(self, key: str | None, text: str, icon: str | None = None, dot: str | None = None) -> None:
        item = NavItem(self, text, command=lambda: self.select(key), icon=icon, dot=dot)
        item.pack(fill="x", padx=12, pady=1)
        self.items[key] = item

    def select(self, key: str | None) -> None:
        if not self.app.can_leave_editor():
            return
        self.category = key
        self._highlight()
        self.app.go_home()

    def _highlight(self) -> None:
        for key, item in self.items.items():
            item.set_active(key == self.category)

    def update_counts(self, recipes: list[Recipe]) -> None:
        self.items[None].set_count(len(recipes))
        self.items[FAVORITES].set_count(sum(r.favori for r in recipes))
        for dish in DISH_TYPES:
            self.items[dish].set_count(sum(r.type_plat == dish for r in recipes))

    def title(self) -> str:
        if self.category is None:
            return "Toutes les recettes"
        if self.category == FAVORITES:
            return "Mes favoris"
        return PLURALS.get(self.category, self.category)


class App(ctk.CTk):
    def __init__(self):
        self.settings = Settings.load()
        theme.register_fonts()
        ctk.set_appearance_mode(APPEARANCES[self.settings.appearance])
        ctk.set_widget_scaling(TEXT_SIZES[self.settings.text_size])
        super().__init__(fg_color=theme.BG)
        use_portable_corners(self)
        fix_scaled_sizes()
        theme.resolve_fonts(self)
        self.title(f"{APP_NAME} — Recettes")
        self.geometry("1380x880")
        self.minsize(1000, 660)
        set_window_icon(self)

        self.store = RecipeStore()

        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self.sidebar = Sidebar(self, self)
        self.sidebar.grid(row=0, column=0, sticky="ns")
        self.home = HomeView(self, self)
        self.detail = DetailView(self, self)
        self.editor = EditorView(self, self)
        self.current: ctk.CTkFrame | None = None
        self._before_editor: Recipe | None = None  # recipe to return to when the editor closes

        self.bind_all("<Control-n>", lambda _e: self.new_recipe())
        self.bind_all("<Control-f>", lambda _e: self.focus_search())
        self.bind_all("<Control-s>", lambda _e: self.current is self.editor and self.editor.submit())
        self.protocol("WM_DELETE_WINDOW", self._quit)

        self.go_home()
        if self.store.warnings:
            self.after(300, lambda: messagebox.showwarning("Attention", "\n\n".join(self.store.warnings), parent=self))

    # ------------------------------------------------------------ navigation
    def _switch(self, view: ctk.CTkFrame) -> None:
        if self.current is not view:
            if self.current is not None:
                self.current.grid_forget()
            view.grid(row=0, column=1, sticky="nsew")
            self.current = view

    def filters(self) -> Filters:
        f = self.home.filters()
        category = self.sidebar.category
        if category == FAVORITES:
            f.favorites_only = True
        elif category:
            f.type_plat = category
        return f

    def can_leave_editor(self) -> bool:
        """Ask before throwing away unsaved edits. True when it is fine to navigate away."""
        if self.current is not self.editor or not self.editor.dirty():
            return True
        if not messagebox.askyesno("Modifications non enregistrées",
                                   "Quitter sans enregistrer vos modifications ?", icon="warning", parent=self):
            return False
        self.editor.discard()
        return True

    def leave_editor(self) -> None:
        """Close the editor without saving: back to the recipe it came from, or home."""
        recipe = self._before_editor and self.store.get(self._before_editor.id)
        if recipe:
            self.open_recipe(recipe)
        else:
            self.go_home()

    def _open_editor(self, recipe: Recipe | None, on_save, **kwargs) -> None:
        if not self.can_leave_editor():
            return
        self._before_editor = self.detail.recipe if self.current is self.detail else None
        self.editor.open(recipe, on_save, **kwargs)
        self._switch(self.editor)

    def go_home(self) -> None:
        if not self.can_leave_editor():
            return
        recipes = self.store.all()
        f = self.filters()
        results = search(recipes, f, self.home.sort())
        self.sidebar.update_counts(recipes)
        self.home.show(results, total=len(recipes), filtered=not f.is_empty(),
                       title=self.sidebar.title(), category=self.sidebar.category)
        self._switch(self.home)

    def reset_filters(self) -> None:
        self.home.clear_filters(silent=True)
        self.sidebar.category = None
        self.sidebar._highlight()
        self.go_home()

    def focus_search(self) -> None:
        if self.current is not self.home:
            self.go_home()
        if self.current is self.home:
            self.home.search.focus_set()

    def open_recipe(self, recipe: Recipe) -> None:
        if not self.can_leave_editor():
            return
        self.detail.show(recipe)
        self._switch(self.detail)

    def refresh(self, recipe: Recipe | None = None) -> None:
        if self.current is self.detail and recipe is not None and self.store.get(recipe.id):
            self.detail.show(self.store.get(recipe.id))
        else:
            self.go_home()

    # ---------------------------------------------------------------- actions
    def new_recipe(self) -> None:
        def save(recipe: Recipe) -> None:
            self.store.add(recipe)
            self.open_recipe(recipe)

        self._open_editor(None, save)

    def edit_recipe(self, recipe: Recipe | None) -> None:
        if recipe is None:
            return

        def save(updated: Recipe) -> None:
            self.store.update(updated)
            self.open_recipe(updated)

        self._open_editor(recipe, save)

    def delete_recipe(self, recipe: Recipe | None) -> None:
        if recipe is None:
            return
        if not messagebox.askyesno("Supprimer la recette",
                                   f"Supprimer définitivement « {recipe.nom} » ?\nCette action est irréversible.",
                                   icon="warning", parent=self):
            return
        self.store.delete(recipe.id)
        self.go_home()

    def toggle_favorite(self, recipe: Recipe | None) -> None:
        if recipe is None:
            return
        self.store.toggle_favorite(recipe.id)
        self.refresh(recipe)

    def add_photo(self, recipe: Recipe) -> None:
        path = ask_image(self)
        if path:
            self.store.add_image(recipe, path)
            self.refresh(recipe)

    def remove_photo(self, recipe: Recipe, name: str) -> None:
        self.store.remove_image(recipe, name)
        self.refresh(recipe)

    def import_pdf(self) -> None:
        if not self.can_leave_editor():
            return
        path = ask_pdf(self)
        if path:
            ImportDialog(self, path, self.settings.use_ai, self._review_import)

    def _review_import(self, result: ImportResult) -> None:
        notes = [f"Recette lue {'par l’IA' if result.method == 'IA' else 'automatiquement'} : vérifiez-la avant d'enregistrer."]
        notes += result.warnings
        if result.images:
            notes.append(f"{len(result.images)} photo(s) trouvée(s) dans le PDF seront ajoutées.")

        def save(recipe: Recipe) -> None:
            self.store.add(recipe)
            for image in result.images:
                self.store.add_image(recipe, image, save=False)
            self.store.save()
            self._cleanup_import(result)
            self.open_recipe(recipe)

        self._open_editor(result.recipe, save, title="Vérifier la recette importée", banner="\n".join(notes),
                          on_cancel=lambda: self._cleanup_import(result))

    @staticmethod
    def _cleanup_import(result: ImportResult) -> None:
        folders = {p.parent for p in result.images}
        for folder in folders:
            shutil.rmtree(folder, ignore_errors=True)

    def show_help(self) -> None:
        HelpDialog(self, self)

    def export_recipes(self) -> None:
        path = filedialog.asksaveasfilename(parent=self, title="Exporter les recettes", defaultextension=".json",
                                            initialfile="mes-recettes.json", filetypes=[("JSON", "*.json")])
        if path:
            self.store.export_to(Path(path))
            messagebox.showinfo("Export terminé", f"{len(self.store)} recettes exportées.", parent=self)

    def import_backup(self) -> None:
        path = filedialog.askopenfilename(parent=self, title="Importer des recettes", filetypes=[("JSON", "*.json")])
        if not path:
            return
        try:
            count = self.store.import_from(Path(path))
        except Exception as exc:
            messagebox.showerror("Import impossible", f"Fichier illisible :\n{exc}", parent=self)
            return
        messagebox.showinfo("Import terminé", f"{count} nouvelle(s) recette(s) ajoutée(s).", parent=self)
        self.go_home()

    # --------------------------------------------------------------- settings
    def set_appearance(self, label: str) -> None:
        self.settings.appearance = label
        self.settings.save()
        ctk.set_appearance_mode(APPEARANCES[label])

    def set_text_size(self, label: str) -> None:
        self.settings.text_size = label
        self.settings.save()
        ctk.set_widget_scaling(TEXT_SIZES[label])

    def set_use_ai(self, value: bool) -> None:
        self.settings.use_ai = value
        self.settings.save()

    def _quit(self) -> None:
        self.settings.save()
        self.destroy()


def main() -> None:
    # A windowed (no console) PyInstaller build has no stdout/stderr; some
    # libraries (llama-cpp) call .fileno() on them, so give them a sink.
    for name in ("stdout", "stderr"):
        if getattr(sys, name) is None:
            setattr(sys, name, open(os.devnull, "w", encoding="utf-8"))
    config.setup_logging()
    log.info("%s %s starting, data in %s", APP_NAME, __version__, config.data_dir())
    try:
        app = App()
    except Exception:
        log.exception("Startup failed")
        raise
    app.mainloop()
