"""Sharing dialog: share one recipe, or the whole book, with someone on the server."""

from __future__ import annotations

from tkinter import messagebox
from typing import TYPE_CHECKING

import customtkinter as ctk

from mamarmiton import usage
from mamarmiton.models import Recipe, fold
from mamarmiton.ui import art, theme
from mamarmiton.ui.remote_logic import error_text, run_in_thread
from mamarmiton.ui.widgets import Choice, Modal, ScrollFrame

if TYPE_CHECKING:
    from mamarmiton.ui.app import App

# what a share lets the other person do
READ, COPY, WRITE = "Lecture seule", "Lecture + variantes", "Modification"
LEVEL_HINTS = {
    READ: "Elle pourra lire, sans modifier ni faire sa propre version.",
    COPY: "Elle pourra lire et créer sa propre variante. Votre nom restera sur la variante.",
    WRITE: "Elle pourra modifier directement, et créer des variantes.",
}


def level_of(share: dict) -> str:
    """The level of a share from ``GET /shares`` (older servers send no ``can_copy``: variants were allowed)."""
    if share.get("can_edit"):
        return WRITE
    return COPY if share.get("can_copy", True) else READ


class ShareDialog(Modal):
    """``recipe=None`` shares the whole book."""

    def __init__(self, master, app: "App", recipe: Recipe | None = None):
        super().__init__(master, "Partager", 600, 780)
        self.app, self.recipe = app, recipe
        self.client = app.store.client
        self.users: list[dict] = []
        self.shared: list[dict] = []
        self.selected: str | None = None
        self._last_width = None
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        what = f"« {recipe.nom} »" if recipe else "mon carnet"
        self._intro = self.header(f"Partager {what}",
                                  "Choisissez une personne : elle pourra voir "
                                  + ("cette recette." if recipe else "toutes vos recettes."))
        self.body = ScrollFrame(self)
        self.body.grid(row=1, column=0, sticky="nsew", padx=(18, 8))
        self.body.grid_columnconfigure(0, weight=1)

        self.status = ctk.CTkLabel(self.body, text="Chargement…", font=theme.font(14), text_color=theme.MUTED,
                                   anchor="w", justify="left", wraplength=480)
        self.status.grid(row=0, column=0, sticky="ew", padx=14, pady=10)
        self.body.bind("<Configure>", self._rewrap, add="+")
        self.add_card = self.shared_card = None
        footer = ctk.CTkFrame(self, fg_color="transparent")
        footer.grid(row=2, column=0, sticky="ew", padx=28, pady=(6, 18))
        theme.primary_button(footer, text="Fermer", width=130, command=self.close).pack(side="right")
        self._load()

    # ---------------------------------------------------------------- data
    def _load(self) -> None:
        self.status.configure(text="Chargement…", text_color=theme.MUTED)
        self.status.grid()

        def work():
            return self.client.users(), self.client.shares()

        run_in_thread(self, work, self._loaded, self._failed)

    def _failed(self, exc: BaseException) -> None:
        self.status.configure(text=error_text(exc), text_color=theme.DANGER)
        self.status.grid()
        self.app.note_network_error(exc)

    def _loaded(self, data) -> None:
        self.users, shares = data
        if self.recipe is None:
            self.shared = list(shares.get("book_shared_with", []))
        else:
            self.shared = [s for s in shares.get("recipes_shared", []) if s.get("recipe_id") == self.recipe.id]
        self.status.grid_remove()
        self._build()

    def _rewrap(self, event=None, force: bool = False) -> None:
        width = int(self.body.winfo_width() / ctk.ScalingTracker.get_widget_scaling(self)) // 10 * 10 - 70
        if width == self._last_width and not force:
            return
        self._last_width = width
        self.status.configure(wraplength=max(200, width))
        for label in getattr(self, "_wrapped", []):
            label.configure(wraplength=max(160, width - 40))
        if getattr(self, "mode", None):
            self.mode.reflow(max(200, width))

    # ------------------------------------------------------------------ ui
    def _build(self) -> None:
        for card in (self.add_card, self.shared_card):
            if card is not None:
                card.destroy()
        self._wrapped = []
        # --- add someone
        card = self.add_card = theme.card(self.body)
        card.grid(row=1, column=0, sticky="ew", padx=10, pady=(0, 14))
        theme.heading(card, "Avec qui partager ?", size=19).pack(anchor="w", padx=20, pady=(18, 8))
        if not self.users:
            label = ctk.CTkLabel(card, text="Il n'y a personne d'autre sur ce serveur pour l'instant.",
                                 font=theme.font(14), text_color=theme.MUTED, anchor="w", justify="left")
            label.pack(anchor="w", padx=20, pady=(0, 18))
            self._wrapped.append(label)
        else:
            self.search = theme.entry(card, placeholder_text="Chercher une personne…")
            self.search.pack(fill="x", padx=20)
            self.search.bind("<KeyRelease>", lambda _e: self._fill_people(), add="+")
            self.people = ctk.CTkFrame(card, fg_color=theme.SURFACE_ALT, corner_radius=theme.RADIUS)
            self.people.pack(fill="x", padx=20, pady=(10, 0))
            self._fill_people()
            self.mode = Choice(card, [READ, COPY, WRITE], height=38, command=self._show_hint)
            self.mode.set(READ)
            self.mode.pack(anchor="w", padx=20, pady=(14, 0))
            self.hint = ctk.CTkLabel(card, text=LEVEL_HINTS[READ], font=theme.font(13), text_color=theme.MUTED,
                                     anchor="w", justify="left", wraplength=440)
            self.hint.pack(anchor="w", padx=20, pady=(6, 0))
            self._wrapped.append(self.hint)
            self.btn_share = theme.primary_button(card, text="Partager", width=0,
                                                  image=art.icon("share", theme.ON_ACCENT, 17), compound="left",
                                                  command=self._share)
            self.btn_share.pack(anchor="w", padx=20, pady=(14, 6))
            self.msg = ctk.CTkLabel(card, text="", font=theme.font(13), text_color=theme.DANGER, anchor="w",
                                    justify="left", wraplength=440)
            self.msg.pack(anchor="w", padx=20, pady=(0, 16))
            self._wrapped.append(self.msg)
        # --- current shares
        card = self.shared_card = theme.card(self.body)
        card.grid(row=2, column=0, sticky="ew", padx=10, pady=(0, 14))
        theme.heading(card, "Déjà partagé avec", size=19).pack(anchor="w", padx=20, pady=(18, 6))
        if not self.shared:
            ctk.CTkLabel(card, text="Personne pour l'instant.", font=theme.font(14), text_color=theme.MUTED,
                         anchor="w").pack(anchor="w", padx=20, pady=(0, 18))
        for share in self.shared:
            row = ctk.CTkFrame(card, fg_color="transparent")
            row.pack(fill="x", padx=14, pady=3)
            row.grid_columnconfigure(0, weight=1)
            words = ctk.CTkFrame(row, fg_color="transparent")
            words.grid(row=0, column=0, sticky="w", padx=6)
            ctk.CTkLabel(words, text=share.get("display_name") or share["username"], font=theme.font(14, "bold"),
                         text_color=theme.TEXT, anchor="w").pack(anchor="w")
            ctk.CTkLabel(words, text=level_of(share), font=theme.font(12),
                         text_color=theme.MUTED, anchor="w").pack(anchor="w")
            theme.danger_button(row, text="Retirer", width=0, height=36,
                                command=lambda s=share: self._remove(s)).grid(row=0, column=1, padx=(8, 4))
        ctk.CTkFrame(card, fg_color="transparent", height=12).pack()
        self.after(50, lambda: self._rewrap(force=True))

    def _fill_people(self) -> None:
        for child in self.people.winfo_children():
            child.destroy()
        needle = fold(self.search.get())
        matches = [u for u in self.users if not needle or needle in fold(
            f"{u.get('display_name', '')} {u['username']}")]
        if not matches:
            ctk.CTkLabel(self.people, text="Personne ne correspond.", font=theme.font(14),
                         text_color=theme.MUTED).pack(padx=14, pady=12)
        for user in matches[:30]:
            picked = user["username"] == self.selected
            name = user.get("display_name") or user["username"]
            text = name if name == user["username"] else f"{name}  ({user['username']})"
            ctk.CTkButton(
                self.people, text=("✓  " if picked else "") + text, anchor="w", height=40, corner_radius=10,
                fg_color=theme.ACCENT_SOFT if picked else "transparent", hover_color=theme.ACCENT_SOFT,
                text_color=theme.TEXT, font=theme.font(14, "bold" if picked else "normal"),
                command=lambda u=user["username"]: self._pick(u)).pack(fill="x", padx=4, pady=2)

    def _show_hint(self, level: str) -> None:
        self.hint.configure(text=LEVEL_HINTS[level])

    def _pick(self, username: str) -> None:
        self.selected = username
        self._fill_people()

    # ------------------------------------------------------------- actions
    def _busy(self, busy: bool) -> None:
        self.btn_share.configure(state="disabled" if busy else "normal", text="Un instant…" if busy else "Partager")

    def _share(self) -> None:
        if not self.selected:
            self.msg.configure(text="Choisissez d'abord une personne dans la liste.")
            return
        username, level = self.selected, self.mode.get()
        can_edit, can_copy = level == WRITE, level in (COPY, WRITE)
        self.msg.configure(text="")
        self._busy(True)

        def work():
            if self.recipe is None:
                self.client.share_book(username, can_edit, can_copy)
            else:
                self.client.share_recipe(self.recipe.id, username, can_edit, can_copy)
            return self.client.shares()

        def done(shares):
            usage.hit("share_create")
            self.selected = None
            self._loaded((self.users, shares))
            self.app.sync_now()

        def failed(exc):
            self._busy(False)
            self.msg.configure(text=error_text(exc))
            self.app.note_network_error(exc)

        run_in_thread(self, work, done, failed)

    def _remove(self, share: dict) -> None:
        name = share.get("display_name") or share["username"]
        if not messagebox.askyesno("Retirer le partage", f"{name} ne verra plus "
                                   + ("cette recette" if self.recipe else "votre carnet") + ". Continuer ?",
                                   parent=self):
            return

        def work():
            if self.recipe is None:
                self.client.unshare_book(share["username"])
            else:
                self.client.unshare_recipe(self.recipe.id, share["username"])
            return self.client.shares()

        def failed(exc):
            messagebox.showerror("Partage", error_text(exc), parent=self)
            self.app.note_network_error(exc)

        run_in_thread(self, work, lambda shares: self._loaded((self.users, shares)), failed)
