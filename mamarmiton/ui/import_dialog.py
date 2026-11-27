""""Importer une recette": choose the source (file or photos, web page, pasted text), then wait for the reading.

Where each import is read (the server or this computer) is decided in ``import_logic``.
"""

from __future__ import annotations

import logging
import queue
import shutil
import threading
from pathlib import Path
from tkinter import filedialog, messagebox
from typing import TYPE_CHECKING, Callable

import customtkinter as ctk

from mamarmiton.importers import ImportResult
from mamarmiton.ui import art, theme
from mamarmiton.ui.import_logic import REFUSE, ImportJob, ImportRefused, plan_import, run_import
from mamarmiton.ui.widgets import Modal, bind_tree, pointer_inside, set_cursor_tree

if TYPE_CHECKING:
    from mamarmiton.ui.app import App

log = logging.getLogger(__name__)

TITLE = "Importer une recette"
WAIT_NOTE = "Cela peut prendre une à deux minutes. Vous pourrez tout vérifier ensuite."
FILE_TYPES = [
    ("Recettes", "*.pdf *.jpg *.jpeg *.png *.webp *.heic *.heif *.txt *.docx *.odt "
                 "*.PDF *.JPG *.JPEG *.PNG *.WEBP *.HEIC *.HEIF *.TXT *.DOCX *.ODT"),
    ("PDF", "*.pdf *.PDF"),
    ("Photos", "*.jpg *.jpeg *.png *.webp *.heic *.heif *.JPG *.JPEG *.PNG *.WEBP *.HEIC *.HEIF"),
    ("Textes et documents", "*.txt *.docx *.odt *.TXT *.DOCX *.ODT"),
    ("Tous les fichiers", "*.*"),
]


def _scaled(widget, *sizes: int) -> list[int]:
    """Window sizes follow the text size (customtkinter scales the widgets, not the windows)."""
    scaling = ctk.ScalingTracker.get_widget_scaling(widget)
    return [round(size * scaling) for size in sizes]


class ChoiceCard(ctk.CTkFrame):
    """A big clickable choice: icon, title, one line of explanation."""

    def __init__(self, master, icon: str, title: str, hint: str, command: Callable[[], None], wrap: int = 340):
        super().__init__(master, fg_color=theme.SURFACE, corner_radius=16, border_width=1, border_color=theme.BORDER)
        self.command = command
        self.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(self, text="", width=48, height=48, corner_radius=14, fg_color=theme.ACCENT_SOFT,
                     image=art.icon(icon, theme.ACCENT_TEXT, 24)).grid(row=0, column=0, rowspan=2, padx=(16, 14),
                                                                      pady=14)
        ctk.CTkLabel(self, text=title, font=theme.font(16, "bold"), text_color=theme.TEXT, anchor="w").grid(
            row=0, column=1, sticky="sw", pady=(14, 0))
        self.hint = ctk.CTkLabel(self, text=hint, font=theme.font(13), text_color=theme.MUTED, anchor="w",
                                 justify="left", wraplength=wrap)
        self.hint.grid(row=1, column=1, sticky="nw", pady=(0, 14))
        ctk.CTkLabel(self, text="", image=art.icon("forward", theme.FAINT, 18)).grid(row=0, column=2, rowspan=2,
                                                                                    padx=(8, 18))
        bind_tree(self, "<Button-1>", lambda _e: self.command())
        bind_tree(self, "<Enter>", lambda _e: self._paint(True))
        bind_tree(self, "<Leave>", lambda _e: self.after(20, lambda: self._paint(pointer_inside(self))))
        set_cursor_tree(self, "hand2")

    def _paint(self, hover: bool) -> None:
        if self.winfo_exists():
            self.configure(border_color=theme.ACCENT if hover else theme.BORDER,
                           fg_color=theme.SURFACE_HOVER if hover else theme.SURFACE)


class ImportRecipeDialog(Modal):
    """The three sources, and the "Coller un texte" page."""

    def __init__(self, master, app: "App"):
        super().__init__(master, TITLE, *_scaled(master, 580, 500))
        self.app = app
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self.body = ctk.CTkFrame(self, fg_color="transparent")
        self.body.grid(row=0, column=0, sticky="nsew", padx=32, pady=(26, 0))
        self.footer = ctk.CTkFrame(self, fg_color="transparent")
        self.footer.grid(row=1, column=0, sticky="ew", padx=28, pady=(10, 20))
        self.text: ctk.CTkTextbox | None = None
        self.show_choices()

    def _clear(self) -> None:
        for frame in (self.body, self.footer):
            for child in frame.winfo_children():
                child.destroy()
        self.text = None

    def _title(self, title: str, subtitle: str) -> None:
        theme.heading(self.body, title, size=26).pack(anchor="w")
        ctk.CTkLabel(self.body, text=subtitle, font=theme.font(13), text_color=theme.MUTED, anchor="w",
                     justify="left", wraplength=500).pack(anchor="w", pady=(2, 16))

    def show_choices(self) -> None:
        self._clear()
        self._title(TITLE, "D'où vient la recette ? Vous pourrez tout vérifier avant de l'enregistrer.")
        file_hint = "Un PDF, une ou plusieurs photos des pages, un texte, un document Word ou LibreOffice."
        if not self.app.is_remote and not _photos_readable_here():
            file_hint += " Les photos se lisent quand vous êtes connecté à votre compte."
        self.choices = [
            ChoiceCard(self.body, "photo", "Un fichier ou une photo", file_hint, self.choose_files),
            ChoiceCard(self.body, "globe", "Une page internet",
                       "Collez l'adresse d'une recette (Marmiton, 750g, Cuisine AZ…).", self.choose_web),
            ChoiceCard(self.body, "copy", "Coller un texte",
                       "Le texte d'un e-mail, d'un message ou d'un document, copié puis collé.", self.show_paste),
        ]
        for card in self.choices:
            card.pack(fill="x", pady=(0, 10))
        theme.secondary_button(self.footer, text="Annuler", width=0, command=self.close).pack(side="right")

    def show_paste(self) -> None:
        self._clear()
        self._title("Coller un texte", "Copiez la recette, puis collez-la ici (clic droit ou Ctrl+V).")
        self.text = theme.textbox(self.body, height=220)
        self.text.pack(fill="both", expand=True)
        self.error = ctk.CTkLabel(self.body, text="", font=theme.font(13), text_color=theme.DANGER, anchor="w")
        self.error.pack(anchor="w", pady=(6, 0))
        theme.primary_button(self.footer, text="Lire la recette", width=0, command=self.read_pasted).pack(side="right")
        theme.secondary_button(self.footer, text="Retour", width=0, command=self.show_choices).pack(
            side="right", padx=(0, 8))
        self.after(150, lambda: self.text is not None and self.text.focus_set())

    def choose_files(self) -> None:
        names = filedialog.askopenfilenames(parent=self, title="Choisir la recette (plusieurs pages possibles)",
                                            filetypes=FILE_TYPES)
        if not names:
            return
        self.close()
        start_import(self.app, [Path(n) for n in names])

    def choose_web(self) -> None:
        self.close()
        self.app.import_url()

    def read_pasted(self) -> None:
        text = self.text.get("1.0", "end").strip() if self.text is not None else ""
        if not text:
            self.error.configure(text="Collez d'abord le texte de la recette.")
            return
        self.close()
        start_import(self.app, [], text)


def _photos_readable_here() -> bool:
    from mamarmiton.importers.pdf_text import tesseract_available

    return tesseract_available()


def start_import(app: "App", paths: list[Path], text: str | None = None) -> "WaitingDialog | None":
    """Read the recipe in the background (server when connected, else this computer), then open the editor."""
    client = getattr(app.store, "client", None) if app.is_remote else None
    job = ImportJob(paths=list(paths), text=text, client=client, use_ai=app.settings.use_ai,
                    local_ocr=_photos_readable_here())
    if client is None:  # nothing to ask the server: say no right away when this computer cannot do it
        plan = plan_import(job.paths, text, connected=False, server=None, local_ocr=job.local_ocr)
        if plan.route == REFUSE:
            messagebox.showinfo(TITLE, plan.message, parent=app)
            return None

    def report_network() -> None:  # the sidebar status follows what the import saw
        for exc in job.network_errors:
            app.note_network_error(exc)

    subtitle = ", ".join(p.name for p in job.paths) if job.paths else "Texte collé"
    return WaitingDialog(app, subtitle, lambda progress: run_import(job, progress), app._review_import,
                         on_finish=report_network)


class WaitingDialog(Modal):
    """"Lecture de la recette…" while ``work`` runs in a thread. "Annuler" ignores its result."""

    def __init__(self, master, subtitle: str, work: Callable[[Callable[[str], None]], ImportResult],
                 on_done: Callable[[ImportResult], None], note: str = WAIT_NOTE,
                 on_finish: Callable[[], None] | None = None):
        super().__init__(master, TITLE, *_scaled(master, 540, 270))
        self.resizable(False, False)
        self.on_done = on_done
        self.on_finish = on_finish or (lambda: None)
        self.messages: queue.Queue = queue.Queue()
        self.running = True

        box = ctk.CTkFrame(self, fg_color="transparent")
        box.pack(fill="both", expand=True, padx=30, pady=(26, 0))
        top = ctk.CTkFrame(box, fg_color="transparent")
        top.pack(fill="x")
        ctk.CTkLabel(top, text="", width=52, height=52, corner_radius=16, fg_color=theme.ACCENT_SOFT,
                     image=art.icon("pdf", theme.ACCENT_TEXT, 24)).pack(side="left", anchor="n")
        words = ctk.CTkFrame(top, fg_color="transparent")
        words.pack(side="left", fill="x", expand=True, padx=(16, 0))
        theme.heading(words, "Lecture de la recette…", size=20).pack(anchor="w")
        shown = subtitle if len(subtitle) <= 90 else subtitle[:89] + "…"
        ctk.CTkLabel(words, text=shown, font=theme.font(13), text_color=theme.MUTED, anchor="w",
                     wraplength=380, justify="left").pack(anchor="w")
        self.bar = ctk.CTkProgressBar(box, mode="indeterminate", height=8, corner_radius=4,
                                      progress_color=theme.ACCENT, fg_color=theme.SURFACE_ALT)
        self.bar.pack(fill="x", pady=(22, 10))
        self.bar.start()
        self.status = ctk.CTkLabel(box, text="Préparation…", font=theme.font(13), text_color=theme.MUTED, anchor="w")
        self.status.pack(anchor="w")
        ctk.CTkLabel(box, text=note, font=theme.font(12), text_color=theme.FAINT, anchor="w", justify="left",
                     wraplength=470).pack(anchor="w", pady=(2, 0))
        footer = ctk.CTkFrame(self, fg_color="transparent")
        footer.pack(fill="x", padx=28, pady=(8, 20))
        theme.secondary_button(footer, text="Annuler", width=0, command=self.close).pack(side="right")

        def worker() -> None:
            try:
                self.messages.put(("done", work(lambda m: self.messages.put(("progress", m)))))
            except (ImportRefused, ValueError, FileNotFoundError) as exc:
                message = str(exc) if not isinstance(exc, FileNotFoundError) else "Ce fichier est introuvable."
                self.messages.put(("refused", message))
            except Exception:  # noqa: BLE001 - shown as a plain sentence, details in the log
                log.exception("Recipe import failed")
                self.messages.put(("error", "Une erreur inattendue est survenue pendant la lecture de la recette."))

        threading.Thread(target=worker, daemon=True).start()
        self.after(100, self._poll)

    def close(self) -> None:
        """Closing while reading (Annuler, Échap, the window's cross) ignores the result."""
        if self.running:
            self.running = False
            self._ignore_result()
        super().close()

    def _poll(self) -> None:
        try:
            while True:
                kind, payload = self.messages.get_nowait()
                if kind == "progress":
                    self.status.configure(text=payload)
                    continue
                self.running = False
                self.bar.stop()
                parent = self.master
                self.close()
                self.on_finish()
                if kind == "done":
                    self.on_done(payload)
                elif kind == "refused":
                    messagebox.showinfo(TITLE, payload, parent=parent)
                else:
                    messagebox.showerror("Import impossible", payload, parent=parent)
                return
        except queue.Empty:
            pass
        self.after(150, self._poll)

    def _ignore_result(self) -> None:
        """Cancelled: when the worker ends, drop its temporary photos (the network status still counts)."""
        master, messages, on_finish = self.master, self.messages, self.on_finish

        def check(tries: int = 0) -> None:
            try:
                while True:
                    kind, payload = messages.get_nowait()
                    if kind == "progress":
                        continue
                    if kind == "done":
                        for folder in {p.parent for p in payload.images}:
                            shutil.rmtree(folder, ignore_errors=True)
                    on_finish()
                    return
            except queue.Empty:
                pass
            if tries < 1200:  # the server may take its full timeout (a few minutes)
                master.after(500, check, tries + 1)

        master.after(500, check)
