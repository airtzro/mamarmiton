"""Dialog windows: PDF import progress, help & settings."""

from __future__ import annotations

import logging
import os
import queue
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from tkinter import filedialog, messagebox
from typing import TYPE_CHECKING, Callable

import customtkinter as ctk

from mamarmiton import __version__, config
from mamarmiton.importers import ImportResult, import_pdf, import_url, llm, pdf_text
from mamarmiton.settings import APPEARANCES, TEXT_SIZES
from mamarmiton.ui import art, theme
from mamarmiton.ui.widgets import Choice, Modal, ScrollFrame

if TYPE_CHECKING:
    from mamarmiton.ui.app import App

log = logging.getLogger(__name__)

# --------------------------------------------------------------------------
# PDF import with progress
# --------------------------------------------------------------------------
class ImportDialog(Modal):
    def __init__(self, master, pdf_path: Path, use_ai: bool, on_done: Callable[[ImportResult], None]):
        super().__init__(master, "Import d'un PDF", 520, 250)
        self.resizable(False, False)
        self.on_done = on_done
        self.messages: queue.Queue = queue.Queue()

        box = ctk.CTkFrame(self, fg_color="transparent")
        box.pack(fill="both", expand=True, padx=30, pady=26)
        top = ctk.CTkFrame(box, fg_color="transparent")
        top.pack(fill="x")
        ctk.CTkLabel(top, text="", width=52, height=52, corner_radius=16, fg_color=theme.ACCENT_SOFT,
                     image=art.icon("pdf", theme.ACCENT_TEXT, 24)).pack(side="left")
        words = ctk.CTkFrame(top, fg_color="transparent")
        words.pack(side="left", fill="x", expand=True, padx=(16, 0))
        theme.heading(words, "Lecture de la recette…", size=20).pack(anchor="w")
        ctk.CTkLabel(words, text=pdf_path.name, font=theme.font(13), text_color=theme.MUTED, anchor="w",
                     wraplength=360, justify="left").pack(anchor="w")
        self.bar = ctk.CTkProgressBar(box, mode="indeterminate", height=8, corner_radius=4,
                                      progress_color=theme.ACCENT, fg_color=theme.SURFACE_ALT)
        self.bar.pack(fill="x", pady=(24, 10))
        self.bar.start()
        self.status = ctk.CTkLabel(box, text="Préparation…", font=theme.font(13), text_color=theme.MUTED, anchor="w")
        self.status.pack(anchor="w")
        ctk.CTkLabel(box, text="Cela peut prendre une minute. Vous pourrez tout vérifier ensuite.",
                     font=theme.font(12), text_color=theme.FAINT, anchor="w").pack(anchor="w", pady=(2, 0))
        self.protocol("WM_DELETE_WINDOW", lambda: None)  # cannot cancel a running native call
        self.unbind("<Escape>")

        def worker():
            try:
                result = import_pdf(pdf_path, use_llm=use_ai, progress=lambda m: self.messages.put(("progress", m)))
                self.messages.put(("done", result))
            except Exception as exc:
                log.exception("PDF import failed")
                self.messages.put(("error", exc))

        threading.Thread(target=worker, daemon=True).start()
        self.after(100, self._poll)

    def _poll(self) -> None:
        try:
            while True:
                kind, payload = self.messages.get_nowait()
                if kind == "progress":
                    self.status.configure(text=payload)
                elif kind == "done":
                    self.running = False
                    self.bar.stop()
                    self.close()
                    self.on_done(payload)
                    return
                else:
                    self.bar.stop()
                    parent = self.master
                    self.close()
                    messagebox.showerror("Import impossible", str(payload), parent=parent)
                    return
        except queue.Empty:
            pass
        self.after(150, self._poll)


# --------------------------------------------------------------------------
# Import from a recipe website
# --------------------------------------------------------------------------
class UrlImportDialog(Modal):
    """Asks for a web address, then fetches it in a background thread."""

    def __init__(self, master, on_done: Callable[[ImportResult], None]):
        super().__init__(master, "Importer depuis un site", 560, 300)
        self.resizable(False, False)
        self.on_done = on_done
        self.messages: queue.Queue = queue.Queue()
        self.cancelled = False
        self.running = False

        self.box = ctk.CTkFrame(self, fg_color="transparent")
        self.box.pack(fill="both", expand=True, padx=30, pady=26)
        theme.heading(self.box, "Importer depuis un site", size=22).pack(anchor="w")
        ctk.CTkLabel(self.box, text="Collez l'adresse d'une page de recette (Marmiton, 750g, Cuisine AZ…).",
                     font=theme.font(13), text_color=theme.MUTED, anchor="w", justify="left",
                     wraplength=490).pack(anchor="w", pady=(2, 14))
        # placeholder only works without a textvariable, so read the entry directly
        self.entry = theme.entry(self.box, placeholder_text="https://www.exemple.fr/recette/…")
        self.entry.pack(fill="x")
        self.entry.bind("<Return>", lambda _e: self._start())
        self.error = ctk.CTkLabel(self.box, text="", font=theme.font(13), text_color=theme.DANGER, anchor="w",
                                  justify="left", wraplength=490)
        self.error.pack(anchor="w", pady=(8, 0))
        self.buttons = ctk.CTkFrame(self.box, fg_color="transparent")
        self.buttons.pack(side="bottom", fill="x")
        theme.ghost_button(self.buttons, text="Annuler", width=110, command=self.close).pack(side="right")
        self.go = theme.primary_button(self.buttons, text="Importer", width=130, command=self._start)
        self.go.pack(side="right", padx=(0, 8))
        self.after(200, self.entry.focus_set)

    def close(self) -> None:
        self.cancelled = True
        if self.running:  # the worker keeps going: clean up its photo when it ends
            self._discard_pending()
        super().close()

    def _start(self) -> None:
        url = self.entry.get().strip()
        self.error.configure(text="")
        if not url:
            self.error.configure(text="Collez d'abord l'adresse de la recette.")
            return
        self.entry.configure(state="disabled")
        self.go.configure(state="disabled", text="Lecture…")
        self.bar = ctk.CTkProgressBar(self.box, mode="indeterminate", height=8, corner_radius=4,
                                      progress_color=theme.ACCENT, fg_color=theme.SURFACE_ALT)
        self.bar.pack(fill="x", pady=(10, 0), before=self.buttons)
        self.bar.start()
        self.error.configure(text="Connexion au site…", text_color=theme.MUTED)

        def worker():
            try:
                result = import_url(url, progress=lambda m: self.messages.put(("progress", m)))
                self.messages.put(("done", result))
            except Exception as exc:
                if not isinstance(exc, ValueError):
                    log.exception("URL import failed")
                    exc = ValueError("Une erreur inattendue est survenue pendant la lecture de la page.")
                self.messages.put(("error", exc))

        self.running = True
        threading.Thread(target=worker, daemon=True).start()
        self.after(100, self._poll)

    def _poll(self) -> None:
        try:
            while True:
                kind, payload = self.messages.get_nowait()
                if kind == "progress":
                    self.error.configure(text=payload)
                elif kind == "done":
                    self.running = False
                    self.bar.stop()
                    self.close()
                    self.on_done(payload)
                    return
                else:  # let the user fix the address and try again
                    self.running = False
                    self.bar.stop()
                    self.bar.pack_forget()
                    self.entry.configure(state="normal")
                    self.go.configure(state="normal", text="Importer")
                    self.error.configure(text=str(payload), text_color=theme.DANGER)
                    return
        except queue.Empty:
            pass
        self.after(150, self._poll)

    def _discard_pending(self) -> None:
        """The window was closed while fetching: drop any downloaded photo once the worker ends."""
        def check(tries=0):
            try:
                kind, payload = self.messages.get_nowait()
            except queue.Empty:
                if tries < 200:
                    self.master.after(150, check, tries + 1)
                return
            if kind == "done":
                for folder in {p.parent for p in payload.images}:
                    shutil.rmtree(folder, ignore_errors=True)
            elif kind == "progress":
                check(tries)
        self.master.after(150, check)


# --------------------------------------------------------------------------
# Help & settings
# --------------------------------------------------------------------------
HELP_ITEMS = [
    ("plus", "Ajouter une recette", "Bouton « Nouvelle recette » en bas à gauche, ou Ctrl+N. Pour chaque ingrédient : le nom, la quantité, puis l'unité. Entrée passe à la case suivante ; Ctrl+S enregistre."),
    ("pdf", "Importer un PDF", "L'application lit le PDF, puis vous montre la recette pour vérification avant de l'enregistrer."),
    ("globe", "Importer depuis un site", "Collez l'adresse d'une page de recette : l'application la lit, puis vous la montre pour vérification."),
    ("search", "Retrouver une recette", "Tapez un mot dans la barre de recherche (Ctrl+F). Les catégories à gauche et les filtres affinent la liste."),
    ("pot", "Cuisiner avec ce qu'on a", "Écrivez vos ingrédients dans « J'ai dans le frigo » : seules les recettes qui les utilisent restent."),
    ("users", "Changer le nombre de personnes", "Boutons − et + sur la page d'une recette. Les quantités s'adaptent, la recette enregistrée ne change pas."),
    ("check", "Suivre en cuisinant", "Cliquez sur un ingrédient ou une étape pour le cocher quand c'est fait."),
    ("star", "Favoris", "Cliquez sur l'étoile d'une recette ; retrouvez-les dans « Favoris » à gauche."),
    ("photo", "Photos", "Bouton « Ajouter une photo » sur la page d'une recette. La première sert de couverture."),
]


def open_folder(path: Path) -> None:
    try:
        if sys.platform.startswith("win"):
            os.startfile(path)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])
    except Exception:
        log.exception("Cannot open %s", path)


class HelpDialog(Modal):
    def __init__(self, master, app: "App"):
        super().__init__(master, "Aide et réglages", 700, 820)
        self.app = app
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        self.header("Aide et réglages", "Tout ce qu'il faut savoir, et quelques préférences.")
        body = ScrollFrame(self)
        body.grid(row=1, column=0, sticky="nsew", padx=(18, 8))
        body.grid_columnconfigure(0, weight=1)
        wrapped: list[tuple[ctk.CTkLabel, int]] = []  # (label, px used beside it in the body)

        def rewrap(event) -> None:
            width = event.width / ctk.ScalingTracker.get_widget_scaling(self)
            for label, used in wrapped:
                label.configure(wraplength=max(160, int(width - used)))

        body.bind("<Configure>", rewrap)

        # --- how-to
        card = theme.card(body)
        card.pack(fill="x", padx=10, pady=(0, 16))
        theme.heading(card, "Comment faire ?", size=19).pack(anchor="w", padx=20, pady=(18, 6))
        for icon, title, text in HELP_ITEMS:
            row = ctk.CTkFrame(card, fg_color="transparent")
            row.pack(fill="x", padx=16, pady=5)
            row.grid_columnconfigure(1, weight=1)
            ctk.CTkLabel(row, text="", width=36, height=36, corner_radius=12, fg_color=theme.ACCENT_SOFT,
                         image=art.icon(icon, theme.ACCENT_TEXT, 18)).grid(row=0, column=0, rowspan=2, sticky="n",
                                                                        padx=(4, 12))
            ctk.CTkLabel(row, text=title, font=theme.font(14, "bold"), text_color=theme.TEXT, anchor="w").grid(
                row=0, column=1, sticky="w")
            label = ctk.CTkLabel(row, text=text, font=theme.font(13), text_color=theme.MUTED, anchor="w",
                                 justify="left", wraplength=520)
            label.grid(row=1, column=1, sticky="w")
            wrapped.append((label, 2 * 10 + 2 * 16 + 52 + 8))  # card margin, row padding, icon
        ctk.CTkFrame(card, fg_color="transparent", height=1, width=1).pack(pady=(0, 10))

        # --- settings
        settings = app.settings
        card = theme.card(body)
        card.pack(fill="x", padx=10, pady=(0, 16))
        card.grid_columnconfigure(0, weight=1)
        theme.heading(card, "Réglages", size=19).grid(row=0, column=0, columnspan=2, sticky="w", padx=20, pady=(18, 6))

        def setting_row(row: int, label: str, hint: str, widget, below: bool = False):
            """Each setting uses grid rows 2*row (label) and 2*row+1 (control, when below)."""
            words = ctk.CTkFrame(card, fg_color="transparent")
            words.grid(row=2 * row, column=0, sticky="w", padx=20, pady=(10, 6 if below else 10))
            ctk.CTkLabel(words, text=label, font=theme.font(14, "bold"), text_color=theme.TEXT, anchor="w").pack(anchor="w")
            ctk.CTkLabel(words, text=hint, font=theme.font(12), text_color=theme.MUTED, anchor="w").pack(anchor="w")
            if below:  # a row of pills is too wide to sit next to its label
                widget.grid(row=2 * row + 1, column=0, columnspan=2, sticky="w", padx=20, pady=(0, 10))
            else:
                widget.grid(row=2 * row, column=1, sticky="e", padx=20)
            return widget

        appearance = setting_row(1, "Apparence", "Clair le jour, sombre le soir…",
                                 Choice(card, list(APPEARANCES), command=app.set_appearance, height=38), below=True)
        appearance.set(settings.appearance)
        size = setting_row(2, "Taille du texte", "Agrandit tout l'écran.",
                           Choice(card, list(TEXT_SIZES), command=app.set_text_size, height=38), below=True)
        size.set(settings.text_size)
        ai_var = ctk.BooleanVar(value=settings.use_ai)
        setting_row(3, "Lire les PDF avec l'IA", "Plus précis, mais un peu plus lent.",
                    theme.switch(card, text="", variable=ai_var, width=50, command=lambda: app.set_use_ai(ai_var.get())))
        upd_var = ctk.BooleanVar(value=settings.check_updates)
        setting_row(4, "Vérifier les mises à jour", "Prévient quand une nouvelle version existe.",
                    theme.switch(card, text="", variable=upd_var, width=50,
                                 command=lambda: app.set_check_updates(upd_var.get())))
        ctk.CTkFrame(card, fg_color="transparent", height=1, width=1).grid(row=10, column=0, pady=(0, 10))

        # --- backups
        card = theme.card(body)
        card.pack(fill="x", padx=10, pady=(0, 16))
        theme.heading(card, "Sauvegardes", size=19).pack(anchor="w", padx=20, pady=(18, 0))
        label = ctk.CTkLabel(card, text="Vos recettes sont enregistrées automatiquement. Vous pouvez aussi en garder "
                             "une copie.", font=theme.font(13), text_color=theme.MUTED, anchor="w", wraplength=560,
                             justify="left")
        label.pack(anchor="w", padx=20, pady=(2, 12))
        wrapped.append((label, 2 * 10 + 2 * 20 + 4))
        buttons = ctk.CTkFrame(card, fg_color="transparent")
        buttons.pack(fill="x", padx=20, pady=(0, 20))
        theme.secondary_button(buttons, text="Exporter mes recettes", width=0, command=app.export_recipes).pack(
            side="left", padx=(0, 8))
        theme.secondary_button(buttons, text="Importer un fichier", width=0, command=app.import_backup).pack(
            side="left", padx=(0, 8))
        theme.link_button(buttons, text="Ouvrir le dossier", height=42,
                          command=lambda: open_folder(config.data_dir())).pack(side="left")

        ai_status = "prête" if llm.is_available() else "indisponible (modèle ou llama-cpp manquant)"
        ocr_status = "disponible" if pdf_text.ocr_available() else "indisponible (Tesseract non installé)"
        info = (f"Version {__version__}  ·  Données : {config.data_dir()}\n"
                f"IA de lecture des PDF : {ai_status}  ·  Lecture des scans (OCR) : {ocr_status}")
        label = ctk.CTkLabel(body, text=info, font=theme.font(11), text_color=theme.FAINT, justify="left", anchor="w",
                             wraplength=600)
        label.pack(anchor="w", padx=14, pady=(0, 12))
        wrapped.append((label, 2 * 14 + 4))

        footer = ctk.CTkFrame(self, fg_color="transparent")
        footer.grid(row=2, column=0, sticky="ew", padx=28, pady=(6, 18))
        theme.primary_button(footer, text="Fermer", width=130, command=self.close).pack(side="right")


def ask_pdf(parent) -> Path | None:
    path = filedialog.askopenfilename(parent=parent, title="Choisir un PDF de recette",
                                      filetypes=[("PDF", "*.pdf"), ("Tous les fichiers", "*.*")])
    return Path(path) if path else None


def ask_image(parent) -> Path | None:
    path = filedialog.askopenfilename(parent=parent, title="Choisir une photo",
                                      filetypes=[("Images", "*.png *.jpg *.jpeg *.webp *.gif *.bmp"),
                                                 ("Tous les fichiers", "*.*")])
    return Path(path) if path else None
