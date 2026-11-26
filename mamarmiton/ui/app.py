"""Main window: sidebar (actions, search, filters) + home / recipe screens."""

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
from mamarmiton.models import COOKING_TYPES, DISH_TYPES, Recipe
from mamarmiton.search import SORTS, TIME_LIMITS, Filters, parse_ingredient_query, search
from mamarmiton.settings import APPEARANCES, TEXT_SIZES, Settings
from mamarmiton.storage import RecipeStore
from mamarmiton.ui import theme
from mamarmiton.ui.detail import DetailView
from mamarmiton.ui.dialogs import HelpDialog, ImportDialog, RecipeForm, ask_image, ask_pdf
from mamarmiton.ui.home import HomeView
from mamarmiton.ui.widgets import set_window_icon

log = logging.getLogger(__name__)

ALL = "Tous"


class Sidebar(ctk.CTkFrame):
    def __init__(self, master, app: "App"):
        super().__init__(master, fg_color=theme.SIDEBAR, corner_radius=0, width=290)
        self.app = app
        self._debounce: str | None = None
        self.grid_propagate(False)
        self.pack_propagate(False)

        ctk.CTkLabel(self, text=APP_NAME, font=theme.font(26, "bold"), text_color=theme.ACCENT).pack(
            anchor="w", padx=24, pady=(26, 0))
        ctk.CTkLabel(self, text="Le carnet de recettes de la famille", font=theme.font(13),
                     text_color=theme.MUTED).pack(anchor="w", padx=24, pady=(0, 20))

        theme.primary_button(self, text="+  Nouvelle recette", height=42, command=app.new_recipe).pack(
            fill="x", padx=20, pady=(0, 8))
        theme.secondary_button(self, text="Importer un PDF", height=42, command=app.import_pdf).pack(
            fill="x", padx=20, pady=(0, 22))

        self.search_var = ctk.StringVar()
        self.search = theme.entry(self, textvariable=self.search_var, placeholder_text="Rechercher une recette…", height=40)
        self.search.pack(fill="x", padx=20, pady=(0, 14))
        self.search_var.trace_add("write", lambda *_: self._schedule())

        self.m_plat = self._filter("Type de plat", [ALL, *DISH_TYPES])
        self.m_cuisson = self._filter("Cuisson", [ALL, *COOKING_TYPES])
        self.m_temps = self._filter("Durée totale", list(TIME_LIMITS))

        ctk.CTkLabel(self, text="Avec les ingrédients", font=theme.font(12, "bold"), text_color=theme.MUTED).pack(
            anchor="w", padx=22, pady=(8, 2))
        self.ing_var = ctk.StringVar()
        theme.entry(self, textvariable=self.ing_var, placeholder_text="ex. farine, oeufs").pack(fill="x", padx=20)
        self.ing_var.trace_add("write", lambda *_: self._schedule())

        self.fav_var = ctk.BooleanVar(value=False)
        ctk.CTkSwitch(self, text="Favoris seulement", variable=self.fav_var, font=theme.font(13),
                      text_color=theme.TEXT, progress_color=theme.ACCENT, command=self._changed).pack(
            anchor="w", padx=22, pady=(14, 4))

        self.m_sort = self._filter("Trier par", SORTS)
        self.m_sort.set(app.settings.sort if app.settings.sort in SORTS else SORTS[0])

        self.btn_reset = ctk.CTkButton(self, text="Effacer les filtres", fg_color="transparent", height=28,
                                       hover_color=theme.ACCENT_SOFT, text_color=theme.ACCENT,
                                       font=theme.font(13, "bold"), command=self.reset_filters)
        self.btn_reset.pack(anchor="w", padx=14, pady=(10, 0))

        bottom = ctk.CTkFrame(self, fg_color="transparent")
        bottom.pack(side="bottom", fill="x", padx=20, pady=18)
        theme.ghost_button(bottom, text="?  Aide et réglages", anchor="w", command=app.show_help).pack(fill="x")
        ctk.CTkLabel(bottom, text=f"v{__version__}", font=theme.font(11), text_color=theme.MUTED).pack(anchor="w", padx=8)

    def _filter(self, label: str, values: list[str]) -> ctk.CTkOptionMenu:
        ctk.CTkLabel(self, text=label, font=theme.font(12, "bold"), text_color=theme.MUTED).pack(
            anchor="w", padx=22, pady=(6, 2))
        menu = theme.option_menu(self, values, command=lambda _v: self._changed())
        menu.pack(fill="x", padx=20)
        menu.set(values[0])
        return menu

    def _schedule(self) -> None:
        if self._debounce:
            self.after_cancel(self._debounce)
        self._debounce = self.after(220, self._changed)

    def _changed(self) -> None:
        self._debounce = None
        self.app.settings.sort = self.m_sort.get()
        self.app.go_home()

    def filters(self) -> Filters:
        plat = self.m_plat.get()
        cuisson = self.m_cuisson.get()
        return Filters(
            text=self.search_var.get(),
            type_plat=None if plat == ALL else plat,
            type_cuisson=None if cuisson == ALL else cuisson,
            max_minutes=TIME_LIMITS.get(self.m_temps.get()),
            ingredients=parse_ingredient_query(self.ing_var.get()),
            favorites_only=self.fav_var.get(),
        )

    def reset_filters(self) -> None:
        self.search_var.set("")
        self.ing_var.set("")
        self.m_plat.set(ALL)
        self.m_cuisson.set(ALL)
        self.m_temps.set(list(TIME_LIMITS)[0])
        self.fav_var.set(False)
        self._changed()


class App(ctk.CTk):
    def __init__(self):
        self.settings = Settings.load()
        ctk.set_appearance_mode(APPEARANCES[self.settings.appearance])
        ctk.set_widget_scaling(TEXT_SIZES[self.settings.text_size])
        super().__init__(fg_color=theme.BG)
        self.title(f"{APP_NAME} — Recettes")
        self.geometry("1360x860")
        self.minsize(960, 640)
        set_window_icon(self)

        self.store = RecipeStore()

        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self.sidebar = Sidebar(self, self)
        self.sidebar.grid(row=0, column=0, sticky="ns")
        self.home = HomeView(self, self)
        self.detail = DetailView(self, self)
        self.current: ctk.CTkFrame | None = None

        self.bind_all("<Control-n>", lambda _e: self.new_recipe())
        self.bind_all("<Control-f>", lambda _e: self.sidebar.search.focus_set())
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

    def go_home(self) -> None:
        f = self.sidebar.filters()
        results = search(self.store.all(), f, self.sidebar.m_sort.get())
        self.home.show(results, total=len(self.store), filtered=not f.is_empty())
        self._switch(self.home)

    def open_recipe(self, recipe: Recipe) -> None:
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

        RecipeForm(self, None, save)

    def edit_recipe(self, recipe: Recipe | None) -> None:
        if recipe is None:
            return

        def save(updated: Recipe) -> None:
            self.store.update(updated)
            self.open_recipe(updated)

        RecipeForm(self, recipe, save)

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

        form = RecipeForm(self, result.recipe, save, title="Vérifier la recette importée", banner="\n".join(notes))
        form.bind("<Destroy>", lambda e: self._cleanup_import(result) if e.widget is form else None, add="+")

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
