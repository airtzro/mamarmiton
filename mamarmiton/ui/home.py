"""Home screen: greeting, search and filter bar, responsive grid of recipe cards."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

import customtkinter as ctk

from mamarmiton import usage
from mamarmiton.models import COOKING_TYPES, Recipe, fold
from mamarmiton.search import SORTS, TIME_LIMITS, Filters, parse_ingredient_query
from mamarmiton.ui import art, theme
from mamarmiton.ui.widgets import FlowFrame, RecipeCard, ScrollFrame

if TYPE_CHECKING:
    from mamarmiton.ui.app import App

GAP = 14
PAD = 36  # left/right margin of the page

ALL_COOKING = "Toutes cuissons"
ANY_TIME = "Toute durée"
TIME_LABELS = {(ANY_TIME if v is None else k): k for k, v in TIME_LIMITS.items()}
SORT_LABELS = {f"Tri : {s[0].lower()}{s[1:]}": s for s in SORTS}


def greeting() -> str:
    hour = datetime.now().hour
    if hour < 5 or hour >= 18:
        return "Bonsoir"
    return "Bonjour"


class SearchBox(ctk.CTkFrame):
    """An entry with an icon and a clear button, inside a rounded field.

    No ``textvariable`` on purpose: customtkinter hides the placeholder when
    one is set.
    """

    def __init__(self, master, icon: str, placeholder: str, on_change, height: int = 48):
        super().__init__(master, fg_color=theme.SURFACE, border_color=theme.BORDER_STRONG, border_width=1,
                         corner_radius=height // 2, height=height)
        self.on_change = on_change
        self._last = ""
        self._box_height = height
        self.grid_columnconfigure(1, weight=1)
        self.grid_propagate(False)
        self.grid_rowconfigure(0, weight=1)
        ctk.CTkLabel(self, text="", image=art.icon(icon, theme.MUTED, 18), width=20).grid(
            row=0, column=0, padx=(18, 6))
        self.entry = ctk.CTkEntry(self, placeholder_text=placeholder, border_width=0,
                                  fg_color="transparent", text_color=theme.TEXT, placeholder_text_color=theme.FAINT,
                                  font=theme.font(15), height=height - 8)
        self.entry.grid(row=0, column=1, sticky="ew", padx=(0, height // 2 - 4))  # stay off the round end
        self.clear = ctk.CTkButton(self, text="", width=30, height=30, corner_radius=15, fg_color="transparent",
                                   hover_color=theme.SURFACE_ALT, image=art.icon("close", theme.MUTED, 14),
                                   command=lambda: self.set("", notify=True))
        self.entry.bind("<KeyRelease>", lambda _e: self._typed(), add="+")
        self.entry.bind("<<Paste>>", lambda _e: self.after(10, self._typed), add="+")
        self.entry.bind("<FocusIn>", lambda _e: self.configure(border_color=theme.ACCENT), add="+")
        self.entry.bind("<FocusOut>", lambda _e: self.configure(border_color=theme.BORDER_STRONG), add="+")
        self.bind("<Button-1>", lambda _e: self.entry.focus_set())

    def get(self) -> str:
        # while the placeholder shows, CTkEntry.get() already returns ""
        return self.entry.get()

    def set(self, value: str, notify: bool = False) -> None:
        self.entry.delete(0, "end")
        if value:
            self.entry.insert(0, value)
        elif self.focus_get() is not self.entry._entry:
            self.entry._activate_placeholder()
        self._typed(notify)

    def _typed(self, notify: bool = True) -> None:
        value = self.get()
        if value:
            self.clear.grid(row=0, column=2, padx=(0, 14))
            self.entry.grid_configure(padx=(0, 4))
        else:
            self.clear.grid_forget()
            self.entry.grid_configure(padx=(0, self._box_height // 2 - 4))
        if value != self._last:
            self._last = value
            if notify:
                self.on_change()

    def focus_set(self) -> None:
        self.entry.focus_set()


class HomeView(ctk.CTkFrame):
    def __init__(self, master, app: "App"):
        super().__init__(master, fg_color="transparent")
        self.app = app
        self._cards: list[RecipeCard] = []
        self._layout_key: tuple[int, int] | None = None
        self._debounce: str | None = None
        self._resize_job: str | None = None

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        # --- greeting + title
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=PAD, pady=(30, 16))
        # "Bonsoir," leads into the question: one sentence over two lines
        self.eyebrow = ctk.CTkLabel(header, text="", font=theme.serif(20), text_color=theme.MUTED, anchor="w")
        self.eyebrow.pack(anchor="w")
        title_row = ctk.CTkFrame(header, fg_color="transparent")
        title_row.pack(fill="x", pady=(2, 0))
        self.title = theme.heading(title_row, "", size=34)
        self.title.pack(side="left")
        self.count = ctk.CTkLabel(title_row, text="", font=theme.font(14), text_color=theme.MUTED)
        self.count.pack(side="left", padx=(14, 0), pady=(10, 0))

        # --- search + filters
        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.grid(row=1, column=0, sticky="ew", padx=PAD, pady=(0, 18))
        self.bar = bar
        self._narrow: bool | None = None
        bar.grid_columnconfigure(0, weight=3)
        bar.grid_columnconfigure(1, weight=2)

        self.search = SearchBox(bar, "search", "Rechercher une recette ou un ingrédient…", self._schedule)
        self.search.grid(row=0, column=0, sticky="ew", padx=(0, 12))
        self.ingredients = SearchBox(bar, "pot", "J'ai dans le frigo : œufs, farine…", self._schedule)
        self.ingredients.grid(row=0, column=1, sticky="ew")

        pills = ctk.CTkFrame(bar, fg_color="transparent")
        self.pills = pills
        self.m_cuisson = theme.pill_menu(pills, [ALL_COOKING, *COOKING_TYPES], command=lambda _v: self._changed())
        self.m_temps = theme.pill_menu(pills, list(TIME_LABELS), command=lambda _v: self._changed())
        self.btn_clear = theme.link_button(pills, text="Effacer les filtres", image=art.icon("reset", theme.ACCENT_TEXT, 14),
                                           compound="left", command=self.app.reset_filters)
        self.m_sort = theme.pill_menu(pills, list(SORT_LABELS), width=210, command=lambda _v: self._sort_changed())
        self._clear_shown = False
        self.tags: list[str] = []  # tag filters picked in the sidebar, shown as removable chips
        self.tag_flow = FlowFrame(bar)
        self._tight: bool | None = None
        self.clear_filters(silent=True)
        saved = next((label for label, key in SORT_LABELS.items() if key == app.settings.sort), None)
        self.m_sort.set(saved or next(iter(SORT_LABELS)))

        # --- cards
        self.grid_area = ScrollFrame(self)
        self.grid_area.grid(row=2, column=0, sticky="nsew", padx=(PAD - 6, 8), pady=(0, 8))
        self.grid_area.bind("<Configure>", self._on_resize, add="+")
        self.bind("<Configure>", lambda _e: self._arrange_bar(), add="+")
        self.pills.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(12, 0))

    # -------------------------------------------------------------- filters
    def filters(self) -> Filters:
        cuisson = self.m_cuisson.get()
        return Filters(
            text=self.search.get(),
            type_cuisson=None if cuisson == ALL_COOKING else cuisson,
            max_minutes=TIME_LIMITS.get(TIME_LABELS.get(self.m_temps.get(), "")),
            ingredients=parse_ingredient_query(self.ingredients.get()),
            tags=list(self.tags),
        )

    def sort(self) -> str:
        return SORT_LABELS.get(self.m_sort.get(), SORTS[0])

    def clear_filters(self, silent: bool = False) -> None:
        for box in (self.search, self.ingredients):
            box.set("")
        self.m_cuisson.set(ALL_COOKING)
        self.m_temps.set(ANY_TIME)
        self.tags = []
        if self._debounce:
            self.after_cancel(self._debounce)
            self._debounce = None
        if not silent:
            self._changed()

    def toggle_tag(self, tag: str) -> None:
        key = fold(tag)
        if any(fold(t) == key for t in self.tags):
            self.tags = [t for t in self.tags if fold(t) != key]
        else:
            self.tags.append(tag)

    def _show_tag_chips(self) -> None:
        chips = []
        for tag in self.tags:
            label = tag if len(tag) <= 24 else tag[:23] + "…"
            chips.append(ctk.CTkButton(
                self.tag_flow, text=f"{label}   ✕", height=30, width=0, corner_radius=15, font=theme.font(13, "bold"),
                fg_color=theme.ACCENT_SOFT, hover_color=theme.BORDER, text_color=theme.ACCENT_TEXT,
                command=lambda t=tag: self.app.toggle_tag(t)))
        if chips:
            self.tag_flow.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(12, 0))
        else:
            self.tag_flow.grid_forget()
        self.tag_flow.set_items(chips)

    def _schedule(self) -> None:
        if self._debounce:
            self.after_cancel(self._debounce)
        self._typed = True
        self._debounce = self.after(220, self._changed)

    def _changed(self) -> None:
        self._debounce = None
        if getattr(self, "_typed", False):  # a search typed, then paused: one use of the search box
            self._typed = False
            if self.search.get().strip() or self.ingredients.get().strip():
                usage.hit("search")
        if self.app.current not in (self, None):  # typed, then left for a recipe: do not drag them back
            return
        self.app.go_home()

    def _sort_changed(self) -> None:
        self.app.settings.sort = self.sort()
        self._changed()

    # ------------------------------------------------------------------ api
    def show(self, recipes: list[Recipe], total: int, filtered: bool, title: str, category: str | None) -> None:
        for child in self.grid_area.winfo_children():
            child.destroy()
        self._cards = []
        self._empty_labels = []
        self._layout_key = None

        if category is None:
            name = (self.app.account.display_name or self.app.account.username) if self.app.account else ""
            self.eyebrow.configure(text=f"{greeting()}{f' {name}' if name else ''},")
            self.eyebrow.pack(anchor="w", before=self.title.master)
            self.title.configure(text="qu'est-ce qu'on cuisine ?")
        else:
            self.eyebrow.pack_forget()
            self.title.configure(text=title)
        n = len(recipes)
        self.count.configure(text=f"{n} recette{'s' if n > 1 else ''}" if total else "")

        self._show_tag_chips()
        has_filters = not self.filters().is_empty()
        if has_filters != self._clear_shown:
            self._clear_shown = has_filters
            self._place_pills()

        if not recipes:
            self._empty_state(total, has_filters, category)
            return

        store = self.app.store
        width = self._card_width()[1]
        for recipe in recipes:
            photos = store.image_paths(recipe)
            card = RecipeCard(self.grid_area, recipe, photos[0] if photos else None, width,
                              on_open=self.app.open_recipe, on_favorite=self.app.toggle_favorite,
                              shared_by=self.app.sharer(recipe))
            self._cards.append(card)
        self._layout()
        self.grid_area._parent_canvas.yview_moveto(0)

    # -------------------------------------------------------------- layout
    def _available_width(self) -> int:
        width = self.grid_area.winfo_width()
        if width <= 1:
            width = self.winfo_width() - 2 * PAD
        if width <= 1:
            width = self.app.winfo_width() - 264 - 2 * PAD
        scaling = ctk.ScalingTracker.get_widget_scaling(self)
        return max(RecipeCard.MIN_WIDTH, int(width / scaling) - 16)

    def _card_width(self) -> tuple[int, int]:
        avail = self._available_width()
        cols = max(1, min(6, (avail + GAP) // (RecipeCard.MIN_WIDTH + GAP)))
        return cols, (avail - GAP * (cols - 1)) // cols

    def _place_pills(self) -> None:
        """Filter menus, "clear" link and sort menu; the sort menu gets its own line when tight."""
        pills = self.pills
        for w in pills.winfo_children():
            w.grid_forget()
        pills.grid_columnconfigure(3, weight=1)
        self.m_cuisson.grid(row=0, column=0, sticky="w", padx=(0, 8))
        self.m_temps.grid(row=0, column=1, sticky="w", padx=(0, 8))
        if self._tight:
            self.m_sort.grid(row=1, column=0, columnspan=4, sticky="w", pady=(8, 0))
            if self._clear_shown:
                self.btn_clear.grid(row=2, column=0, columnspan=4, sticky="w", pady=(6, 0))
        else:
            if self._clear_shown:
                self.btn_clear.grid(row=0, column=2, sticky="w", padx=(4, 0))
            self.m_sort.grid(row=0, column=4, sticky="e")

    def _arrange_bar(self) -> None:
        """Search boxes side by side, or stacked when the window is narrow."""
        scaling = ctk.ScalingTracker.get_widget_scaling(self)
        width = self.winfo_width() / scaling - 2 * PAD
        if width > 0:
            count_w = self.count.winfo_reqwidth() / scaling + 14  # the count stays beside the title
            self.title.configure(wraplength=max(200, int(width - count_w) - 20))
        tight = 0 < width < 560
        if tight != self._tight:
            self._tight = tight
            self._place_pills()
        narrow = 0 < width < 760
        if narrow == self._narrow:
            return
        self._narrow = narrow
        if narrow:
            self.bar.grid_columnconfigure(1, weight=0)
            self.search.grid(row=0, column=0, columnspan=2, sticky="ew", padx=0)
            self.ingredients.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(10, 0))
            self.m_cuisson.configure(width=165)
            self.m_temps.configure(width=140)
            self.m_sort.configure(width=180)
        else:
            self.bar.grid_columnconfigure(1, weight=2)
            self.search.grid(row=0, column=0, columnspan=1, sticky="ew", padx=(0, 12))
            self.ingredients.grid(row=0, column=1, columnspan=1, sticky="ew", pady=0)
            self.m_cuisson.configure(width=170)
            self.m_temps.configure(width=170)
            self.m_sort.configure(width=210)
        self.pills.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(12, 0))

    def _on_resize(self, _event=None) -> None:
        self._arrange_bar()
        wrap = max(220, min(520, self._available_width() - 80))
        for label in getattr(self, "_empty_labels", []):
            if label.winfo_exists():
                label.configure(wraplength=wrap)
        if self._resize_job:
            self.after_cancel(self._resize_job)
        self._resize_job = self.after(120, self._layout)

    def _layout(self) -> None:
        self._resize_job = None
        if not self._cards:
            return
        cols, width = self._card_width()
        if self._layout_key == (cols, width):
            return
        self._layout_key = (cols, width)
        for c in range(8):
            self.grid_area.grid_columnconfigure(c, weight=0, uniform="")
        for i, card in enumerate(self._cards):
            card.set_width(width)
            col = i % cols
            card.grid(row=i // cols, column=col, padx=(6 if col == 0 else GAP, 0), pady=(0, GAP), sticky="nsew")

    def _empty_state(self, total: int, filtered: bool, category: str | None) -> None:
        for c in range(8):
            self.grid_area.grid_columnconfigure(c, weight=0, uniform="")
        self.grid_area.grid_columnconfigure(0, weight=1)
        wrap = max(220, min(520, self._available_width() - 80))  # text wraps inside the card at any width
        box = theme.card(self.grid_area, fg_color=theme.SURFACE)
        box.grid(row=0, column=0, sticky="ew", padx=(6, 12), pady=(10, 0))
        inner = ctk.CTkFrame(box, fg_color="transparent")
        inner.pack(pady=48, padx=28)
        ctk.CTkLabel(inner, text="", image=art.logo(64)).pack(pady=(0, 18))

        def say(title: str, text: str) -> None:
            head = theme.heading(inner, title, size=26, wraplength=wrap, justify="center", anchor="center")
            head.pack()
            body = ctk.CTkLabel(inner, text=text, font=theme.font(15), text_color=theme.MUTED, wraplength=wrap,
                                justify="center")
            body.pack(pady=(8, 22))
            self._empty_labels = [head, body]

        # the actions are stacked: they stay whole in a narrow window and read as a list of choices
        button = dict(compound="left", width=min(280, wrap), height=46)
        if total == 0:
            say("Votre carnet est encore vide",
                "Ajoutez votre première recette : écrivez-la, ou importez-la (un PDF, une photo, un site…).")
            theme.primary_button(inner, text="Écrire une recette", image=art.icon("pencil", theme.ON_ACCENT, 17),
                                 command=self.app.new_recipe, **button).pack()
            theme.secondary_button(inner, text="Importer une recette", image=art.icon("pdf", theme.TEXT, 17),
                                   command=self.app.import_recipe, **button).pack(pady=(10, 0))
        elif filtered:
            say("Aucune recette ne correspond", "Essayez un autre mot, ou enlevez un filtre.")
            theme.secondary_button(inner, text="Effacer les filtres", image=art.icon("reset", theme.TEXT, 16),
                                   command=self.app.reset_filters, **button).pack()
        else:
            what = "favori" if category and category.startswith("__") else "recette dans cette catégorie"
            hint = ("Cliquez sur l'étoile d'une recette pour la retrouver ici." if what == "favori"
                    else "Ajoutez-en une, elle apparaîtra ici.")
            say("Rien ici pour l'instant", f"Pas encore de {what}. {hint}")
            theme.primary_button(inner, text="Nouvelle recette", image=art.icon("plus", theme.ON_ACCENT, 17),
                                 command=self.app.new_recipe, **button).pack()
