"""Main window: dark sidebar (brand, categories, actions) + home / recipe screens."""

from __future__ import annotations

import logging
import os
import shutil
import queue
import sys
import threading
from datetime import date
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk

from mamarmiton import APP_NAME, __version__, config, updates, usage
from mamarmiton.importers import ImportResult
from mamarmiton.models import DISH_TYPES, Recipe, fold
from mamarmiton.search import Filters, search, tag_counts
from mamarmiton.shopping import ShoppingState, build_list
from mamarmiton.settings import APPEARANCES, TEXT_SIZES, Settings
from mamarmiton.remote import AuthError, OfflineError, RemoteStore, UpgradeRequired, clear_account, open_store
from mamarmiton.storage import RecipeStore
from mamarmiton.ui.kitchen import KitchenView, keep_awake
from mamarmiton.ui.timerbar import TimerBar
from mamarmiton.ui import art, remote_logic as rl, theme
from mamarmiton.ui.account import AccountDialog
from mamarmiton.ui.conflicts import ConflictDialog
from mamarmiton.ui.detail import DetailView
from mamarmiton.ui.dialogs import HelpDialog, ReportDialog, UrlImportDialog, ask_image
from mamarmiton.ui.editor import EditorView
from mamarmiton.ui.home import HomeView
from mamarmiton.ui.shopping import ShoppingView
from mamarmiton.ui.widgets import IconLine, NavItem, ReportButton, ScrollFrame, Toast, UpdateBanner, bind_tree, fix_scaled_sizes, pointer_inside, set_cursor_tree, set_window_icon, use_portable_corners

log = logging.getLogger(__name__)

FAVORITES = "__favoris__"
SHOPPING = "__courses__"
PLURALS = {"Entrée": "Entrées", "Plat": "Plats", "Dessert": "Desserts", "Pain": "Pains"}


class Sidebar(ctk.CTkFrame):
    WIDTH = 264
    NARROW_WIDTH = 220  # small windows: the recipes need the room more than the sidebar
    NARROW_BELOW = 1000  # window width, in unscaled pixels
    TAGS_SHOWN = 6

    def __init__(self, master, app: "App"):
        super().__init__(master, fg_color=theme.SIDEBAR, corner_radius=0, width=self.WIDTH)
        self.app = app
        self.category: str | None = None  # None = all, FAVORITES, or a dish type
        self.grid_propagate(False)
        self.pack_propagate(False)
        self.narrow = False
        self.compact = False
        self._full_need: float | None = None  # height the full layout needs (see _fit)
        self._fitting = False

        self.brand = brand = ctk.CTkFrame(self, fg_color="transparent")
        brand.pack(fill="x", padx=20, pady=(26, 22))
        self.logo = ctk.CTkLabel(brand, text="", image=art.logo(44))
        self.logo.pack(side="left")
        words = ctk.CTkFrame(brand, fg_color="transparent")
        words.pack(side="left", padx=(12, 0))
        ctk.CTkLabel(words, text="Mamarmiton", font=theme.serif(21), text_color=theme.SIDEBAR_TEXT,
                     anchor="w", height=24).pack(anchor="w")
        self.tagline = ctk.CTkLabel(words, text="Les recettes de la famille", font=theme.font(12),
                                    text_color=theme.SIDEBAR_MUTED, anchor="w", height=18)
        self.tagline.pack(anchor="w")

        # The actions and the account area are packed first so they always stay whole. The navigation
        # fills what is left: a short window switches to the compact layout (set_compact), and if even
        # that is too tall the navigation scrolls. The tags come last, so they are the first to go out
        # of view; the categories stay as long as possible.
        self.bottom = bottom = ctk.CTkFrame(self, fg_color="transparent")
        bottom.pack(side="bottom", fill="x", padx=16, pady=(8, 18))
        self.middle = ScrollFrame(self, autohide=True, width=0, height=0, scrollbar_button_color=theme.BORDER_STRONG,
                                  scrollbar_button_hover_color=theme.SIDEBAR_MUTED)
        self.middle.pack(fill="both", expand=True, padx=(12, 4))
        self.middle._scrollbar.configure(width=10)  # thin: the labels keep their room when it shows
        self.middle._parent_canvas.bind("<Configure>", self._fit, add="+")  # window height or bottom area changed

        self.items: dict[str | None, NavItem] = {}
        self._nav(None, "Toutes les recettes", icon="grid")
        self._nav(FAVORITES, "Favoris", icon="star")
        self._nav(SHOPPING, "Liste de courses", icon="cart")
        self.shopping_active = False
        self.tags_expanded = False
        self._tags_signature = None

        self.categories_label = ctk.CTkLabel(self.middle, text="Catégories", font=theme.font(13, "bold"),
                                             text_color=theme.SIDEBAR_MUTED, anchor="w")
        self.categories_label.pack(fill="x", padx=14, pady=(22, 4))
        for dish in DISH_TYPES:
            self._nav(dish, PLURALS.get(dish, dish), dot=theme.dish(dish)["dot"])
        self.tag_box = ctk.CTkFrame(self.middle, fg_color="transparent")  # filled and packed by update_tags

        self.btn_new = theme.primary_button(bottom, text="Nouvelle recette", image=art.icon("plus", theme.ON_ACCENT, 18),
                                            compound="left", height=46, command=app.new_recipe)
        self.btn_new.pack(fill="x")
        self.btn_import = ctk.CTkButton(
            bottom, text="Importer une recette", image=art.icon("pdf", theme.SIDEBAR_TEXT, 18),
            compound="left", height=44, corner_radius=theme.RADIUS, fg_color="transparent",
            hover_color=theme.SIDEBAR_HOVER, border_width=1, border_color=theme.SIDEBAR_LINE,
            text_color=theme.SIDEBAR_TEXT, font=theme.font(14, "bold"), command=app.import_recipe)
        self.btn_import.pack(fill="x", pady=(8, 14))
        self.separator = ctk.CTkFrame(bottom, height=1, fg_color=theme.SIDEBAR_LINE, corner_radius=0)
        self.separator.pack(fill="x", pady=(0, 8))
        # "Se connecter" or the account box; in compact mode the settings button sits beside it
        self.account_row = ctk.CTkFrame(bottom, fg_color="transparent")
        self.account_row.pack(fill="x", pady=(0, 6))
        self.btn_help_small = ctk.CTkButton(
            self.account_row, text="", width=40, height=40, corner_radius=10, fg_color="transparent",
            hover_color=theme.SIDEBAR_HOVER, image=art.icon("settings", theme.SIDEBAR_MUTED, 18), command=app.show_help)
        help_row = self._help_row = ctk.CTkFrame(bottom, fg_color="transparent")
        help_row.pack(fill="x")
        ctk.CTkButton(help_row, text="Aide et réglages", image=art.icon("settings", theme.SIDEBAR_MUTED, 17),
                      compound="left", anchor="w", height=38, corner_radius=10, fg_color="transparent",
                      hover_color=theme.SIDEBAR_HOVER, text_color=theme.SIDEBAR_TEXT, font=theme.font(13),
                      command=app.show_help).pack(side="left", fill="x", expand=True)
        ctk.CTkLabel(help_row, text=f"v{__version__}", font=theme.font(11), text_color=theme.SIDEBAR_MUTED).pack(
            side="right", padx=(4, 6))
        self._build_account_area(self.account_row)

        self._highlight()

    def set_narrow(self, narrow: bool) -> None:
        if narrow == self.narrow:
            return
        self.narrow = narrow
        self.configure(width=self.NARROW_WIDTH if narrow else self.WIDTH)
        self._show_tagline()
        self.items[None].label.configure(text="Toutes" if narrow else "Toutes les recettes")
        self.items[SHOPPING].label.configure(text="Courses" if narrow else "Liste de courses")

    def _fit(self, _event=None) -> None:
        """Use the compact layout when the full one would push a category below the visible area.

        The need is measured on the full layout, in unscaled pixels so a text size change keeps it.
        It is only measured there: the compact layout keeps the last value, so the two cannot flip.
        """
        if self._fitting or not self.winfo_ismapped():
            return
        scaling = ctk.ScalingTracker.get_widget_scaling(self)
        if not self.compact:
            last = self.items[DISH_TYPES[-1]]
            if last.winfo_height() <= 1:  # not laid out yet
                return
            outside = self.winfo_height() - self.middle._parent_canvas.winfo_height()
            self._full_need = (outside + last.winfo_y() + last.winfo_height()) / scaling
        if self._full_need is None:
            return
        self._fitting = True
        try:
            self.set_compact(self.winfo_height() / scaling < self._full_need)
        finally:
            self._fitting = False

    def set_compact(self, compact: bool) -> None:
        """Short window: tighter spacing, so the four categories stay visible without scrolling."""
        if compact == self.compact:
            return
        self.compact = compact
        self.brand.pack_configure(pady=(14, 8) if compact else (26, 22))
        self.logo.configure(image=art.logo(36 if compact else 44))
        self._show_tagline()
        for item in self.items.values():
            item.set_height(NavItem.COMPACT_HEIGHT if compact else NavItem.HEIGHT)
        self.categories_label.pack_configure(pady=(8, 2) if compact else (22, 4))
        self.bottom.pack_configure(pady=(6, 10) if compact else (8, 18))
        self.btn_new.configure(height=42 if compact else 46)
        self.btn_import.configure(height=40 if compact else 44)
        self.btn_import.pack_configure(pady=(6, 8) if compact else (8, 14))
        self.separator.pack_configure(pady=(0, 6) if compact else (0, 8))
        self.account_row.pack_configure(pady=(0, 0) if compact else (0, 6))
        self._place_help()
        self.update_account()
        if hasattr(self.app, "home"):  # the tag rows follow the item height
            self._tags_signature = None
            self.update_tags(self.app.store.all())

    def _place_help(self) -> None:
        """"Aide et réglages" is a row of its own, or (compact) a settings button beside the account."""
        remote = self._account_mode == "remote"
        small = self.compact and not remote
        boxed = self.compact and remote  # inside the account box, so its status keeps the full width
        if not self.compact and not self._help_row.winfo_manager():
            self._help_row.pack(fill="x", after=self.account_row)
        elif self.compact and self._help_row.winfo_manager():
            self._help_row.pack_forget()
        if small != bool(self.btn_help_small.winfo_manager()):
            if small:
                self.btn_help_small.pack(side="right", padx=(6, 0), before=self.btn_login)
            else:
                self.btn_help_small.pack_forget()
        if boxed != bool(self.btn_help_box.winfo_manager()):
            if boxed:
                self.btn_help_box.grid()
            else:
                self.btn_help_box.grid_remove()

    def _show_tagline(self) -> None:
        if self.narrow or self.compact:
            self.tagline.pack_forget()
        else:
            self.tagline.pack(anchor="w")

    def _build_account_area(self, parent) -> None:
        app = self.app
        # local mode: one button
        self.btn_login = ctk.CTkButton(parent, text="Se connecter", image=art.icon("users", theme.SIDEBAR_TEXT, 18),
                                       compound="left", anchor="w", height=40, corner_radius=10,
                                       fg_color="transparent", hover_color=theme.SIDEBAR_HOVER,
                                       text_color=theme.SIDEBAR_TEXT, font=theme.font(13), command=app.show_account)
        # connected: name + sync button, then the status line
        self.account_box = ctk.CTkFrame(parent, fg_color=theme.SIDEBAR_HOVER, corner_radius=12)
        top = ctk.CTkFrame(self.account_box, fg_color="transparent")
        top.pack(fill="x", padx=(12, 6), pady=(8, 0))
        top.grid_columnconfigure(0, weight=1)
        self.account_name = ctk.CTkLabel(top, text="", font=theme.font(14, "bold"), text_color=theme.SIDEBAR_TEXT,
                                         anchor="w")
        self.account_name.grid(row=0, column=0, sticky="ew")
        self.btn_refresh = ctk.CTkButton(top, text="", width=30, height=30, corner_radius=15, fg_color="transparent",
                                         hover_color=theme.SIDEBAR_ACTIVE, image=art.icon("reset", theme.SIDEBAR_MUTED, 16),
                                         command=app.sync_now)
        self.btn_refresh.grid(row=0, column=1)
        self.btn_help_box = ctk.CTkButton(top, text="", width=30, height=30, corner_radius=15, fg_color="transparent",
                                          hover_color=theme.SIDEBAR_ACTIVE,
                                          image=art.icon("settings", theme.SIDEBAR_MUTED, 16), command=app.show_help)
        self.btn_help_box.grid(row=0, column=2)
        self.btn_help_box.grid_remove()  # compact layout only
        self.account_status = IconLine(self.account_box, theme.font(12), size=15, wraplength=160)
        self.account_status.pack(fill="x", padx=12, pady=(2, 10))
        skip = (self.btn_refresh, self.btn_help_box)
        bind_tree(self.account_box, "<Button-1>", lambda _e: app.show_account(), skip=skip)
        set_cursor_tree(self.account_box, "hand2", skip=skip)
        self._account_mode: str | None = None
        self.update_account()

    STATUS_COLORS = {"ok": ("#5E8F4C", "#8DB87B"), "busy": theme.SIDEBAR_MUTED, "warn": ("#C9951A", "#E5B84C"),
                     "bad": ("#C8553D", "#F2A283")}

    def update_account(self) -> None:
        """Show "Se connecter" (local mode) or the account name with its sync status."""
        app = self.app
        remote = app.is_remote
        mode = "remote" if remote else "local"
        if mode != self._account_mode:
            self._account_mode = mode
            self.btn_login.pack_forget()
            self.account_box.pack_forget()
            (self.account_box if remote else self.btn_login).pack(side="left", fill="x", expand=True)
            self._place_help()
        if not remote:
            return
        store = app.store
        icon, text, kind = rl.status_line(store.status, store.pending_count, store.last_error, short=self.compact)
        color = self.STATUS_COLORS[kind]
        self.account_name.configure(text=app.account.display_name or app.account.username)
        self.account_status.set(icon, text, color)
        self.btn_refresh.configure(state="disabled" if store.status == "syncing" else "normal")

    def _nav(self, key: str | None, text: str, icon: str | None = None, dot: str | None = None) -> None:
        item = NavItem(self.middle, text, command=lambda: self.select(key), icon=icon, dot=dot,
                       height=NavItem.COMPACT_HEIGHT if self.compact else NavItem.HEIGHT)
        item.pack(fill="x", padx=(0, 8), pady=1)
        self.items[key] = item

    def select(self, key: str | None) -> None:
        if not self.app.can_leave_editor():
            return
        if key == SHOPPING:
            return self.app.show_shopping()
        self.category = key
        self._highlight()
        self.app.go_home()

    def _highlight(self) -> None:
        for key, item in self.items.items():
            if key == SHOPPING:
                item.set_active(self.shopping_active)
            else:
                item.set_active(key == self.category and not self.shopping_active)

    def set_shopping_active(self, active: bool) -> None:
        if active != self.shopping_active:
            self.shopping_active = active
            self._highlight()

    def update_shopping_count(self) -> None:
        n = self.app.shopping_remaining()
        self.items[SHOPPING].set_count(n or None)

    def update_counts(self, recipes: list[Recipe]) -> None:
        self.items[None].set_count(len(recipes))
        self.items[FAVORITES].set_count(sum(r.favori for r in recipes))
        for dish in DISH_TYPES:
            self.items[dish].set_count(sum(r.type_plat == dish for r in recipes))
        self.update_shopping_count()
        self.update_tags(recipes)

    def update_tags(self, recipes: list[Recipe]) -> None:
        """The "Étiquettes" section: the tags of the book with their counts, most used first."""
        counts = tag_counts(recipes)
        active = tuple(sorted(fold(t) for t in self.app.home.tags))
        signature = (tuple(counts), active, self.tags_expanded)
        if signature == self._tags_signature:
            return
        self._tags_signature = signature
        for child in self.tag_box.winfo_children():
            child.destroy()
        if not counts:
            self.tag_box.pack_forget()
            return
        if not self.tag_box.winfo_ismapped():
            self.tag_box.pack(fill="x")
        ctk.CTkLabel(self.tag_box, text="Étiquettes", font=theme.font(13, "bold"), text_color=theme.SIDEBAR_MUTED,
                     anchor="w").pack(fill="x", padx=14, pady=(18, 4))
        many = len(counts) > self.TAGS_SHOWN
        # all of them when expanded: the whole navigation scrolls (no scroll area inside the scroll area)
        shown = counts if self.tags_expanded else counts[:self.TAGS_SHOWN]
        for tag, n in shown:
            label = tag if len(tag) <= 20 else tag[:19] + "…"
            item = NavItem(self.tag_box, label, command=lambda t=tag: self.app.toggle_tag(t), dot="#A88E76",
                           height=NavItem.COMPACT_HEIGHT if self.compact else 34)
            item.pack(fill="x", padx=(0, 8), pady=1)
            item.set_count(n)
            item.set_active(fold(tag) in active)
        if many:
            more = ctk.CTkButton(self.tag_box, text="Réduire" if self.tags_expanded else f"Voir tout ({len(counts)})",
                                 height=30, corner_radius=8, fg_color="transparent", hover_color=theme.SIDEBAR_HOVER,
                                 text_color=theme.SIDEBAR_ACCENT, font=theme.font(13, "bold"), anchor="w",
                                 command=self._toggle_tags_expanded)
            more.pack(fill="x", padx=(4, 8), pady=(2, 4))

    def _toggle_tags_expanded(self) -> None:
        self.tags_expanded = not self.tags_expanded
        self.update_tags(self.app.store.all())

    def title(self) -> str:
        if self.category is None:
            return "Toutes les recettes"
        if self.category == FAVORITES:
            return "Mes favoris"
        return PLURALS.get(self.category, self.category)


class App(ctk.CTk):
    def __init__(self):
        self.settings = Settings.load()
        usage.set_enabled(self.settings.share_usage)
        art.reset_tk_caches()  # a previous App (tests, restart) left images bound to its dead Tk root
        theme.font.cache_clear()
        theme.serif.cache_clear()
        theme.register_fonts()
        ctk.set_appearance_mode(APPEARANCES[self.settings.appearance])
        ctk.set_widget_scaling(TEXT_SIZES[self.settings.text_size])
        # className sets the window class (WM_CLASS) that Linux launchers use to match the window to its icon
        super().__init__(fg_color=theme.BG, className="Mamarmiton")
        use_portable_corners(self)
        fix_scaled_sizes()
        theme.resolve_fonts(self)
        self.title(f"{APP_NAME} — Recettes")
        self.geometry("1380x880")
        self.minsize(1000, 660)
        set_window_icon(self)

        self.store, self.account = open_store()
        self.shopping = ShoppingState.load()
        self.people: dict[str, str] = {}  # username -> display name
        self.account_dialog: AccountDialog | None = None
        self._local: RecipeStore | None = None if self.account else self.store
        self._conflict_dialog: ConflictDialog | None = None
        self._snoozed: set[str] = set()  # conflicts postponed with "Décider plus tard"
        self._fetching_people = False
        self._toast: Toast | None = None
        self.runner = rl.SyncRunner(self, self.on_sync_done, on_state=self._update_account_area)

        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self.sidebar = Sidebar(self, self)
        self.sidebar.grid(row=0, column=0, sticky="ns")
        self.home = HomeView(self, self)
        self.detail = DetailView(self, self)
        self.shopping_view = ShoppingView(self, self)
        self.editor = EditorView(self, self)
        self.current: ctk.CTkFrame | None = None
        self.timers = TimerBar(self)  # lives here so running timers survive changing screens
        self.kitchen: KitchenView | None = None
        self.report_button = ReportButton(self, self.report_problem, content_left=Sidebar.WIDTH)
        self.report_button.show()
        self.bind("<Configure>", self._on_window_resize, add="+")
        self._before_editor: Recipe | None = None  # recipe to return to when the editor closes

        self.bind_all("<Control-n>", lambda _e: self.new_recipe())
        self.bind_all("<Control-f>", lambda _e: self.focus_search())
        self.bind_all("<Control-s>", lambda _e: self.current is self.editor and self.editor.submit())
        for key, action in (("<Escape>", "close"), ("<Left>", "previous"), ("<Right>", "next"), ("<space>", "next")):
            self.bind(key, lambda _e, a=action: self._kitchen_key(a), add="+")
        self.protocol("WM_DELETE_WINDOW", self._quit)

        self.go_home()
        self.runner.attach(self.store)
        if self.store.warnings:
            self.after(300, lambda: messagebox.showwarning("Attention", "\n\n".join(self.store.warnings), parent=self))
        self._update_results: queue.Queue = queue.Queue()
        self._update_banner: UpdateBanner | None = None
        self._upgrade_banner: UpdateBanner | None = None
        self.after(2000, self.start_update_check)

    def _on_window_resize(self, event) -> None:
        if event.widget is not self:
            return
        scaling = ctk.ScalingTracker.get_widget_scaling(self)
        self.sidebar.set_narrow(event.width / scaling < Sidebar.NARROW_BELOW)
        self.report_button.set_content_left(Sidebar.NARROW_WIDTH if self.sidebar.narrow else Sidebar.WIDTH)

    # ------------------------------------------------------------ navigation
    def _switch(self, view: ctk.CTkFrame) -> None:
        self.sidebar.set_shopping_active(view is self.shopping_view)
        if self.current is not view:
            if self.current is not None:
                self.current.grid_forget()
            view.grid(row=0, column=1, sticky="nsew")
            self.current = view
            if view is not self.editor and self.is_remote:
                self.after(400, self._check_conflicts)

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

    # --------------------------------------------------------- shopping list
    def shopping_selection(self) -> list[tuple[Recipe, int]]:
        """``(recipe, servings)`` for the recipes of the list that still exist."""
        found = []
        for entry in self.shopping.recipes:
            recipe = self.store.get(entry["id"])
            if recipe is not None:
                found.append((recipe, entry["servings"]))
        return found

    def shopping_remaining(self) -> int:
        """Lines still to buy (the sidebar badge)."""
        items = build_list(self.shopping_selection())
        return (sum(i.key not in self.shopping.checked for i in items)
                + sum(not e["done"] for e in self.shopping.extras))

    def save_shopping(self) -> None:
        self.shopping.save()
        self.sidebar.update_shopping_count()

    def add_to_shopping(self, recipe: Recipe | None, servings: int) -> None:
        if recipe is None:
            return
        usage.hit("shopping_add")
        self.shopping.add_recipe(recipe.id, servings)
        self.save_shopping()

    def show_shopping(self) -> None:
        if not self.can_leave_editor():
            return
        usage.hit("shopping_open")
        self.shopping_view.show()
        self._switch(self.shopping_view)

    def toggle_tag(self, tag: str) -> None:
        """Add or remove a tag filter (sidebar, chips above the grid)."""
        if not self.can_leave_editor():
            return
        self.home.toggle_tag(tag)
        if any(fold(t) == fold(tag) for t in self.home.tags):  # added, not removed
            usage.hit("tag_filter")
        self.go_home()

    def filter_by_tag(self, tag: str) -> None:
        """Home, showing only the recipes with ``tag`` (from a recipe's tag chip)."""
        if not self.can_leave_editor():
            return
        self.home.clear_filters(silent=True)
        self.sidebar.category = None
        self.sidebar._highlight()
        self.home.tags = [tag]
        usage.hit("tag_filter")
        self.go_home()

    def reset_filters(self) -> None:
        self.home.clear_filters(silent=True)
        self.sidebar.category = None
        self.sidebar._highlight()
        self.go_home()

    def focus_search(self) -> None:
        if self.kitchen is not None:
            return
        if self.current is not self.home:
            self.go_home()
        if self.current is self.home:
            self.home.search.focus_set()

    def open_recipe(self, recipe: Recipe, count: bool = True) -> None:
        if not self.can_leave_editor():
            return
        if count:
            usage.hit("recipe_open")
        self.detail.show(recipe)
        self._switch(self.detail)

    # ------------------------------------------------- timers and kitchen mode
    def start_timer(self, label: str, seconds: int) -> None:
        usage.hit("timer_start")
        self.timers.start(label, seconds)

    def open_kitchen(self, recipe: Recipe, servings: int | None = None) -> None:
        if self.kitchen is not None or not recipe.etapes:
            return
        usage.hit("kitchen_open")
        self.kitchen = KitchenView(self, recipe, servings)
        self.report_button.hide()  # kitchen mode keeps the corners for its own big buttons
        self.timers.dock(True)
        keep_awake(True)

    def close_kitchen(self) -> None:
        if self.kitchen is None:
            return
        self.kitchen.destroy()
        self.kitchen = None
        self.report_button.show()
        self.bind("<Configure>", self._on_window_resize, add="+")
        self.timers.dock(False)
        keep_awake(False)

    def _kitchen_key(self, action: str) -> None:
        if self.kitchen is not None:
            getattr(self.kitchen, action)()

    def refresh(self, recipe: Recipe | None = None) -> None:
        if self.current is self.detail and recipe is not None and self.store.get(recipe.id):
            self.detail.show(self.store.get(recipe.id))
        elif self.current is self.home:  # keep the scroll position (clicking a star must not jump to the top)
            canvas = self.home.grid_area._parent_canvas
            top = canvas.yview()[0]
            self.go_home()
            self.after_idle(lambda: canvas.winfo_exists() and canvas.yview_moveto(top))
        else:
            self.go_home()

    # ------------------------------------------------------------ permissions
    @property
    def is_remote(self) -> bool:
        return isinstance(self.store, RemoteStore)

    def can_edit(self, recipe: Recipe | None) -> bool:
        return recipe is None or rl.can_edit(self.store, recipe.id)

    def can_delete(self, recipe: Recipe | None) -> bool:
        return recipe is None or rl.can_delete(self.store, recipe.id)

    def is_owner(self, recipe: Recipe) -> bool:
        return rl.is_owner(self.store, recipe.id)

    def owner_name(self, recipe: Recipe) -> str:
        """Display name of the recipe's owner ("" in local mode)."""
        return rl.person_name(self.people, rl.owner_of(self.store, recipe.id)) if self.is_remote else ""

    def sharer(self, recipe: Recipe) -> str:
        """Who shared this recipe with me ("" when it is mine)."""
        return rl.sharer_label(self.store, recipe.id, self.people)

    def _denied(self, exc: BaseException) -> None:
        messagebox.showinfo("Action impossible", str(exc) or "Vous n'avez pas le droit de faire cela.", parent=self)

    def _mutated(self) -> None:
        """A change was made: sync soon, and show the pending count."""
        self.runner.changed()
        self._update_account_area()
        self.sidebar.update_counts(self.store.all())  # counts and tags stay right on screens that are not home

    # ---------------------------------------------------------------- actions
    def new_recipe(self) -> None:
        if self.kitchen is not None:  # Ctrl+N must not open an editor hidden behind kitchen mode
            return
        def save(recipe: Recipe) -> None:
            self.store.add(recipe)
            usage.hit("recipe_create")
            self._mutated()
            self.open_recipe(recipe, count=False)

        self._open_editor(None, save)

    def edit_recipe(self, recipe: Recipe | None) -> None:
        if recipe is None:
            return
        if not self.can_edit(recipe):
            return self._denied(PermissionError("Cette recette est en lecture seule : vous ne pouvez pas la modifier."))

        def save(updated: Recipe) -> None:
            try:
                self.store.update(updated)
            except PermissionError as exc:
                self._denied(exc)
                return self.leave_editor()
            usage.hit("recipe_edit")
            self._mutated()
            self.open_recipe(updated, count=False)

        self._open_editor(recipe, save)

    def duplicate_recipe(self, recipe: Recipe | None) -> None:
        """Copy the recipe as "<titre> (variante)" in the same book, then open the editor on the copy."""
        if recipe is None:
            return
        if not self.can_leave_editor():
            return
        copy = self.store.duplicate(recipe.id)
        if copy is None:
            return
        usage.hit("variant_create")
        self._mutated()
        self.edit_recipe(copy)

    def delete_recipe(self, recipe: Recipe | None) -> None:
        if recipe is None:
            return
        self.finish_pending_delete()
        try:
            trashed = self.store.delete(recipe.id, keep_files=True)
        except PermissionError as exc:
            return self._denied(exc)
        if trashed is None:
            return
        usage.hit("recipe_delete")
        self._mutated()
        self.go_home()
        store = self.store

        def undo() -> None:
            self._toast = None
            if store is not self.store:  # the book was switched meanwhile
                return store.purge(trashed)
            store.restore(trashed)
            usage.hit("delete_undo")
            self._mutated()
            if self.current is self.home:  # never pull the user out of the editor they are typing in
                self.go_home()

        def expire() -> None:
            self._toast = None
            store.purge(trashed)
            if store.get(trashed.recipe.id) is None and self.shopping.servings_of(trashed.recipe.id) is not None:
                self.shopping.remove_recipe(trashed.recipe.id)
                self.save_shopping()

        self._toast = Toast(self, "Recette supprimée", "Annuler", undo, expire, seconds=10)

    def finish_pending_delete(self) -> None:
        """Make a delete that can still be undone final (new delete, quit, book switch)."""
        toast = getattr(self, "_toast", None)
        if toast is not None:
            toast.dismiss()

    def toggle_favorite(self, recipe: Recipe | None) -> None:
        if recipe is None:
            return
        self.store.toggle_favorite(recipe.id)
        usage.hit("favorite_toggle")
        self._mutated()
        self.refresh(recipe)

    def add_photo(self, recipe: Recipe) -> None:
        path = ask_image(self)
        if path:
            try:
                self.store.add_image(recipe, path)
            except PermissionError as exc:
                return self._denied(exc)
            usage.hit("photo_add")
            self._mutated()
            self.refresh(recipe)

    def remove_photo(self, recipe: Recipe, name: str) -> None:
        try:
            self.store.remove_image(recipe, name)
        except PermissionError as exc:
            return self._denied(exc)
        self._mutated()
        self.refresh(recipe)

    def share_recipe(self, recipe: Recipe | None) -> None:
        if recipe is None or not self.is_remote:
            return
        from mamarmiton.ui.sharing import ShareDialog

        ShareDialog(self, self, recipe)

    def print_recipe(self, recipe: Recipe | None) -> None:
        """Save the recipe as an A4 PDF, then open it so it can be printed from the viewer."""
        if recipe is None:
            return
        from mamarmiton import export

        home = Path.home()
        docs = home / "Documents"
        path = filedialog.asksaveasfilename(
            parent=self, title="Enregistrer la recette en PDF", defaultextension=".pdf",
            initialdir=str(docs if docs.is_dir() else home),
            initialfile=export.slugify(recipe.nom) + ".pdf", filetypes=[("Document PDF", "*.pdf")])
        if not path:
            return
        try:
            photos = self.store.image_paths(recipe)
            export.export_pdf(recipe, Path(path), photos[0] if photos else None)
        except Exception as exc:
            log.exception("export PDF")
            messagebox.showerror("Impression impossible",
                                 f"Le PDF n'a pas pu être créé :\n{exc}", parent=self)
            return
        usage.hit("pdf_export")
        try:
            export.open_file(Path(path))
        except Exception:
            log.exception("ouverture du PDF")
            messagebox.showinfo("PDF enregistré",
                                f"Le PDF a été enregistré ici :\n{path}\n\n"
                                "Ouvrez-le avec votre lecteur PDF pour l'imprimer.", parent=self)

    def import_recipe(self) -> None:
        """"Importer une recette": a file or photos, a web page, or pasted text."""
        if self.kitchen is not None or not self.can_leave_editor():
            return
        from mamarmiton.ui.import_dialog import ImportRecipeDialog

        ImportRecipeDialog(self, self)

    def import_pdf(self) -> None:
        """Former "Importer un PDF" entry point: the import dialog handles PDFs now."""
        self.import_recipe()

    def import_url(self) -> None:
        if not self.can_leave_editor():
            return
        UrlImportDialog(self, self._review_import)

    def _review_import(self, result: ImportResult) -> None:
        usage.hit("url_import" if result.method == "site" else "pdf_import")
        if result.method == "site":
            notes = ["Recette lue depuis le site : vérifiez-la avant d'enregistrer."]
        else:
            notes = [f"Recette lue {'par l’IA' if result.method == 'IA' else 'automatiquement'} : vérifiez-la avant d'enregistrer."]
        notes += result.warnings
        if result.images:
            origin = result.origin or ("le site" if result.method == "site" else "le PDF")
            notes.append(f"{len(result.images)} photo(s) trouvée(s) dans {origin} seront ajoutées.")

        def save(recipe: Recipe) -> None:
            self.store.add(recipe)
            for image in result.images:
                self.store.add_image(recipe, image, save=False)
            self.store.save()
            self._mutated()
            self._cleanup_import(result)
            self.open_recipe(recipe, count=False)

        self._open_editor(result.recipe, save, title="Vérifier la recette importée", banner="\n".join(notes),
                          on_cancel=lambda: self._cleanup_import(result))

    @staticmethod
    def _cleanup_import(result: ImportResult) -> None:
        folders = {p.parent for p in result.images}
        for folder in folders:
            shutil.rmtree(folder, ignore_errors=True)

    def show_help(self) -> None:
        HelpDialog(self, self)

    def report_problem(self) -> None:
        ReportDialog(self, self)

    def export_recipes(self) -> None:
        path = filedialog.asksaveasfilename(parent=self, title="Exporter les recettes", defaultextension=".json",
                                            initialfile="mes-recettes.json", filetypes=[("JSON", "*.json")])
        if path:
            self.store.export_to(Path(path))
            usage.hit("backup_export")
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
        usage.hit("backup_import")
        self._mutated()
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

    def set_share_usage(self, value: bool) -> None:
        self.settings.share_usage = value
        self.settings.save()
        usage.set_enabled(value)

    def set_check_updates(self, value: bool) -> None:
        self.settings.check_updates = value
        self.settings.save()

    # ---------------------------------------------------------------- updates
    def start_update_check(self) -> None:
        """At most once a day, in the background; silent when offline."""
        if not (self.settings.check_updates and updates.enabled() and updates.due(self.settings.last_update_check)):
            return
        self.settings.last_update_check = date.today().isoformat()
        self.settings.save()

        def work() -> None:
            self._update_results.put(updates.check_latest(__version__))

        threading.Thread(target=work, daemon=True).start()
        self.after(500, self._poll_update)

    def _poll_update(self) -> None:
        try:
            info = self._update_results.get_nowait()
        except queue.Empty:
            self.after(500, self._poll_update)
            return
        if info is not None:
            self.show_update(info)

    def show_update(self, info: updates.UpdateInfo) -> None:
        if info.version == self.settings.snoozed_version:
            return

        def later() -> None:
            self.settings.snoozed_version = info.version
            self.settings.save()

        def download() -> None:
            usage.hit("update_banner_click")
            webbrowser.open(info.url)

        self._update_banner = UpdateBanner(self, f"Une nouvelle version ({info.version}) est disponible",
                                           download, later)

    def show_upgrade_required(self) -> None:
        """The server refuses this version (426): say so once, without blocking the local recipes."""
        if self._upgrade_banner is not None and self._upgrade_banner.winfo_exists():
            return
        message = ("Cette version de Mamarmiton est trop ancienne pour se synchroniser. Mettez-la à jour : "
                   "vos recettes restent disponibles ici en attendant.")
        url = [updates.RELEASES_URL]

        def open_download() -> None:
            usage.hit("update_banner_click")
            webbrowser.open(url[0])

        def forget() -> None:
            self._upgrade_banner = None  # the sidebar keeps saying "Mise à jour nécessaire"

        self._upgrade_banner = UpdateBanner(self, message, open_download, forget)
        # the update check knows the exact release page; it is silent when offline
        rl.run_in_thread(self, lambda: updates.check_latest(__version__),
                         lambda info: info is not None and url.__setitem__(0, info.download_url or info.url))

    # ---------------------------------------------------------- account & sync
    def local_book(self) -> RecipeStore:
        """The local recipe file (kept as a backup once an account is used)."""
        if self._local is None:
            self._local = RecipeStore()
        return self._local

    def show_account(self) -> None:
        if self.account_dialog is not None and self.account_dialog.winfo_exists():
            self.account_dialog.lift()
            return
        if not self.can_leave_editor():
            return
        self.account_dialog = AccountDialog(self, self)

    def sign_in(self, client, account) -> None:
        """Switch to the server-backed book for ``account`` (``client`` holds a fresh token)."""
        self.finish_pending_delete()
        old = self.store
        self.runner.stop()
        if self.is_remote and old.username == account.username:
            old.client.token, old.client.user = client.token, client.user  # same book: just a new session
            old.status = "ok"
            old.last_error = ""
        else:
            if self.is_remote:
                old.save()
            self.store = RemoteStore(client, username=account.username)
            if isinstance(old, RecipeStore):
                self._local = old
        self.account = account
        self.people[account.username] = account.display_name or account.username
        self.detail.recipe = None
        self.go_home()
        self.runner.attach(self.store)
        self._update_account_area()

    def sign_out(self) -> None:
        """Back to the local book; the server session ends, the offline cache stays on disk."""
        self.finish_pending_delete()
        old = self.store
        self.runner.stop()
        if isinstance(old, RemoteStore):
            old.save()
            rl.run_in_thread(self, old.client.logout)  # best effort: the answer does not matter
        clear_account()
        self.account = None
        self.store = self.local_book()
        self.detail.recipe = None
        self.go_home()
        self._update_account_area()

    def sync_now(self) -> None:
        self.runner.sync_now()

    def note_network_error(self, exc: BaseException) -> None:
        """A dialog's own network call failed: reflect it in the sidebar status."""
        if not self.is_remote or self.store.status == "syncing":
            return
        if isinstance(exc, UpgradeRequired):
            self.store.status = "outdated"
            self.runner.blocked = True
            self.show_upgrade_required()
        elif isinstance(exc, AuthError):
            self.store.status = "auth"
        elif isinstance(exc, OfflineError):
            self.store.status = "offline"
        self._update_account_area()

    def _update_account_area(self) -> None:
        if not self.winfo_exists():
            return
        self.sidebar.update_account()
        if self.account_dialog is not None and self.account_dialog.winfo_exists():
            self.account_dialog.refresh_status()

    def on_sync_done(self, result) -> None:
        """A sync finished (called on the Tk thread)."""
        self._update_account_area()
        if isinstance(result, UpgradeRequired) or getattr(result, "outdated", False):
            self.show_upgrade_required()
            return
        if isinstance(result, Exception):
            log.error("Sync failed", exc_info=result)
            return
        if not self.is_remote or getattr(result, "skipped", False):
            return
        if result.changed:
            self._refresh_after_sync()
        self._fetch_people()
        self._check_conflicts()

    def _refresh_after_sync(self) -> None:
        """Redraw what is on screen after new data arrived, without disturbing the editor."""
        recipes = self.store.all()
        self.sidebar.update_counts(recipes)
        if self.current is self.home:
            canvas = self.home.grid_area._parent_canvas
            top = canvas.yview()[0]
            self.go_home()
            self.after_idle(lambda: canvas.yview_moveto(top))
        elif self.current is self.detail and self.detail.recipe is not None:
            fresh = self.store.get(self.detail.recipe.id)
            if fresh is None:
                messagebox.showinfo("Recette retirée", "Cette recette n'est plus disponible : elle a été "
                                    "supprimée ou n'est plus partagée avec vous.", parent=self)
                self.go_home()
            elif self.detail.signature_of(fresh) != self.detail.signature:
                self.detail.show(fresh)

    def _fetch_people(self) -> None:
        """Learn display names of recipe owners (for "partagée par ...")."""
        if self._fetching_people or not rl.unknown_owners(self.store, self.people):
            return
        self._fetching_people = True
        client = self.store.client

        def done(users) -> None:
            self._fetching_people = False
            known = len(self.people)
            self.people.update({u["username"]: u.get("display_name") or u["username"] for u in users})
            if len(self.people) != known and self.current is self.home:
                self._refresh_after_sync()

        def failed(_exc) -> None:
            self._fetching_people = False
            self.people.update({u: u for u in {self.store.owner(r.id) for r in self.store.all()} if u not in self.people})

        rl.run_in_thread(self, client.users, done, failed)

    # ------------------------------------------------------------- conflicts
    def _check_conflicts(self) -> None:
        if (not self.is_remote or self._conflict_dialog is not None or self.current is self.editor
                or self.account_dialog is not None):
            return
        for conflict in self.store.conflicts:
            if conflict.recipe_id not in self._snoozed:
                usage.hit("sync_conflict_shown")
                self._conflict_dialog = ConflictDialog(self, conflict, lambda choice, c=conflict: self._resolved(c, choice))
                return

    def _resolved(self, conflict, choice: str) -> None:
        self._conflict_dialog = None
        if choice == "later":
            self._snoozed.add(conflict.recipe_id)
        else:
            try:
                self.store.resolve_conflict(conflict.recipe_id, choice)
            except Exception as exc:  # noqa: BLE001
                log.exception("Cannot resolve conflict")
                messagebox.showerror("Recette", rl.error_text(exc), parent=self)
            self._mutated()
            self.sync_now()
            self._refresh_after_sync()
        self.after(300, self._check_conflicts)

    def _quit(self) -> None:
        if not self.can_leave_editor():  # closing the window must not silently drop unsaved edits
            return
        self.close_kitchen()
        self.finish_pending_delete()
        self.runner.stop()
        if self.is_remote:
            self.store.save()
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
