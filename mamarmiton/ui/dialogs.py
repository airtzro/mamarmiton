"""Dialog windows: recipe editor, PDF import progress, help & settings."""

from __future__ import annotations

import copy
import logging
import os
import queue
import re
import subprocess
import sys
import threading
from pathlib import Path
from tkinter import filedialog, messagebox
from typing import TYPE_CHECKING, Callable

import customtkinter as ctk

from mamarmiton import __version__, config
from mamarmiton.importers import ImportResult, import_pdf, llm, pdf_text
from mamarmiton.models import (
    COOKING_TYPES,
    DISH_TYPES,
    Recipe,
    format_ingredient_lines,
    parse_ingredient_lines,
    parse_int,
    parse_minutes,
)
from mamarmiton.settings import APPEARANCES, TEXT_SIZES
from mamarmiton.ui import theme
from mamarmiton.ui.widgets import Modal

if TYPE_CHECKING:
    from mamarmiton.ui.app import App

log = logging.getLogger(__name__)

def _strip_step_number(line: str) -> str:
    return re.sub(r"^\s*(?:[ée]tape\s*)?\d{1,2}\s*[).:\-–]\s*|^\s*[-•*]\s*", "", line, flags=re.IGNORECASE).strip()


# --------------------------------------------------------------------------
# Recipe editor
# --------------------------------------------------------------------------
class RecipeForm(Modal):
    """Create or edit a recipe. ``on_save`` receives the new Recipe object."""

    def __init__(self, master, recipe: Recipe | None, on_save: Callable[[Recipe], None],
                 title: str | None = None, banner: str | None = None):
        self.original = recipe
        self.recipe = copy.deepcopy(recipe) if recipe else Recipe(nom="")
        self.on_save = on_save
        heading = title or ("Modifier la recette" if recipe and recipe.nom else "Nouvelle recette")
        super().__init__(master, heading, 760, 860)
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        ctk.CTkLabel(self, text=heading, font=theme.font(24, "bold"), text_color=theme.TEXT).grid(
            row=0, column=0, sticky="w", padx=28, pady=(22, 6))

        form = ctk.CTkScrollableFrame(self, fg_color="transparent")
        form.grid(row=1, column=0, sticky="nsew", padx=14)
        form.grid_columnconfigure((0, 1, 2), weight=1, uniform="f")
        self.form = form
        row = 0

        if banner:
            ban = ctk.CTkLabel(form, text=banner, fg_color=theme.ACCENT_SOFT, text_color=theme.TEXT, corner_radius=10,
                               font=theme.font(13), justify="left", anchor="w", wraplength=650)
            ban.grid(row=row, column=0, columnspan=3, sticky="ew", padx=14, pady=(4, 12), ipadx=10, ipady=8)
            row += 1

        r = self.recipe
        self.e_nom = self._field(form, row, 0, 3, "Nom de la recette *", theme.entry(form, placeholder_text="ex. Gâteau au chocolat"))
        self.e_nom.insert(0, r.nom)
        row += 2

        self.e_prep = self._field(form, row, 0, 1, "Préparation (min)", theme.entry(form, placeholder_text="ex. 20 ou 1h30"))
        self.e_cook = self._field(form, row, 1, 1, "Cuisson (min)", theme.entry(form, placeholder_text="ex. 45"))
        self.e_pour = self._field(form, row, 2, 1, "Nombre de personnes", theme.entry(form))
        if r.temps:
            self.e_prep.insert(0, str(r.temps))
        if r.temps_cuisson:
            self.e_cook.insert(0, str(r.temps_cuisson))
        self.e_pour.insert(0, str(r.pour))
        row += 2

        self.m_plat = self._field(form, row, 0, 1, "Type de plat", theme.option_menu(form, DISH_TYPES))
        self.m_plat.set(r.type_plat)
        self.m_cuisson = self._field(form, row, 1, 1, "Cuisson", theme.option_menu(form, COOKING_TYPES))
        self.m_cuisson.set(r.type_cuisson)
        self.fav = ctk.BooleanVar(value=r.favori)
        fav_box = ctk.CTkCheckBox(form, text="★ Favori", variable=self.fav, font=theme.font(14), text_color=theme.TEXT,
                                  fg_color=theme.ACCENT, hover_color=theme.ACCENT_HOVER, border_color=theme.BORDER)
        fav_box.grid(row=row + 1, column=2, sticky="w", padx=14)
        row += 2

        self.t_ing = self._field(form, row, 0, 3, "Ingrédients — un par ligne (ex. « Farine: 500 g » ou « 3 oeufs »)",
                                 theme.textbox(form, height=190))
        self.t_ing.insert("1.0", format_ingredient_lines(r.ingredients))
        row += 2

        self.t_steps = self._field(form, row, 0, 3, "Étapes — une par ligne, dans l'ordre", theme.textbox(form, height=230))
        self.t_steps.insert("1.0", "\n".join(r.etapes))
        row += 2

        self.t_notes = self._field(form, row, 0, 3, "Notes (astuces, variantes…)", theme.textbox(form, height=80))
        self.t_notes.insert("1.0", r.notes)
        row += 2

        footer = ctk.CTkFrame(self, fg_color="transparent")
        footer.grid(row=2, column=0, sticky="ew", padx=28, pady=(8, 20))
        self.error = ctk.CTkLabel(footer, text="", text_color=theme.DANGER, font=theme.font(13, "bold"))
        self.error.pack(side="left")
        theme.primary_button(footer, text="Enregistrer", width=140, command=self._submit).pack(side="right")
        theme.secondary_button(footer, text="Annuler", width=110, command=self.close).pack(side="right", padx=10)
        self.bind("<Control-s>", lambda _e: self._submit())
        self.after(150, self.e_nom.focus_set)

    @staticmethod
    def _field(form, row: int, col: int, span: int, label: str, widget):
        ctk.CTkLabel(form, text=label, font=theme.font(13, "bold"), text_color=theme.MUTED, anchor="w").grid(
            row=row, column=col, columnspan=span, sticky="w", padx=14, pady=(10, 4))
        widget.grid(row=row + 1, column=col, columnspan=span, sticky="ew", padx=14)
        return widget

    def _submit(self) -> None:
        nom = self.e_nom.get().strip()
        if not nom:
            self.error.configure(text="Donnez un nom à la recette.")
            self.e_nom.configure(border_color=theme.DANGER)
            self.e_nom.focus_set()
            return
        for widget, label in ((self.e_prep, "préparation"), (self.e_cook, "cuisson")):
            raw = widget.get().strip()
            if raw and parse_minutes(raw) == 0 and not raw.startswith("0"):
                self.error.configure(text=f"Temps de {label} illisible : écrivez par ex. 20 ou 1h30.")
                widget.configure(border_color=theme.DANGER)
                return
        r = self.recipe
        r.nom = nom
        r.temps = parse_minutes(self.e_prep.get())
        r.temps_cuisson = parse_minutes(self.e_cook.get())
        r.pour = parse_int(self.e_pour.get(), default=4, minimum=1)
        r.type_plat = self.m_plat.get()
        r.type_cuisson = self.m_cuisson.get()
        r.favori = self.fav.get()
        r.ingredients = parse_ingredient_lines(self.t_ing.get("1.0", "end"))
        r.etapes = [s for s in (_strip_step_number(x) for x in self.t_steps.get("1.0", "end").splitlines()) if s]
        r.notes = self.t_notes.get("1.0", "end").strip()
        try:
            self.on_save(r)
        except Exception as exc:
            log.exception("Saving recipe failed")
            messagebox.showerror("Erreur", f"Impossible d'enregistrer la recette :\n{exc}", parent=self)
            return
        self.close()


# --------------------------------------------------------------------------
# PDF import with progress
# --------------------------------------------------------------------------
class ImportDialog(Modal):
    def __init__(self, master, pdf_path: Path, use_ai: bool, on_done: Callable[[ImportResult], None]):
        super().__init__(master, "Import d'un PDF", 480, 200)
        self.resizable(False, False)
        self.on_done = on_done
        self.messages: queue.Queue = queue.Queue()

        ctk.CTkLabel(self, text=f"Import de « {pdf_path.name} »", font=theme.font(17, "bold"),
                     text_color=theme.TEXT, wraplength=420).pack(padx=24, pady=(24, 6))
        self.status = ctk.CTkLabel(self, text="Préparation…", font=theme.font(13), text_color=theme.MUTED)
        self.status.pack(padx=24)
        self.bar = ctk.CTkProgressBar(self, mode="indeterminate", width=400, progress_color=theme.ACCENT)
        self.bar.pack(padx=24, pady=18)
        self.bar.start()
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
# Help & settings
# --------------------------------------------------------------------------
HELP_TEXT = """\
• Nouvelle recette : bouton « + Nouvelle recette » à gauche.
• Importer un PDF : l'application lit le PDF, puis vous montre la recette pour vérification avant de l'enregistrer.
• Rechercher : tapez un mot (nom ou ingrédient) dans la barre de recherche ; les filtres en dessous affinent la liste.
• Changer le nombre de personnes : boutons - et + sur la page d'une recette. Les quantités s'adaptent, la recette enregistrée ne change pas.
• Favoris : cliquez sur l'étoile ☆ d'une recette ; les favoris apparaissent en premier.
• Photos : bouton « + Ajouter » dans la case Photos d'une recette.
• Tout est enregistré automatiquement sur cet ordinateur, avec une copie de sécurité."""


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
        super().__init__(master, "Aide et réglages", 640, 760)
        self.app = app
        body = ctk.CTkScrollableFrame(self, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=12, pady=12)

        ctk.CTkLabel(body, text="Aide", font=theme.font(24, "bold"), text_color=theme.TEXT).pack(anchor="w", padx=12, pady=(8, 6))
        ctk.CTkLabel(body, text=HELP_TEXT, font=theme.font(14), text_color=theme.TEXT, justify="left", anchor="w",
                     wraplength=560).pack(anchor="w", padx=12)

        ctk.CTkLabel(body, text="Réglages", font=theme.font(24, "bold"), text_color=theme.TEXT).pack(anchor="w", padx=12, pady=(24, 6))
        settings = app.settings

        def setting_row(label: str, widget_factory):
            row = ctk.CTkFrame(body, fg_color="transparent")
            row.pack(fill="x", padx=12, pady=5)
            ctk.CTkLabel(row, text=label, font=theme.font(14), text_color=theme.TEXT).pack(side="left")
            widget = widget_factory(row)
            widget.pack(side="right")
            return widget

        appearance = setting_row("Apparence", lambda m: theme.option_menu(m, list(APPEARANCES), width=200,
                                                                            command=app.set_appearance))
        appearance.set(settings.appearance)
        size = setting_row("Taille du texte", lambda m: theme.option_menu(m, list(TEXT_SIZES), width=200,
                                                                           command=app.set_text_size))
        size.set(settings.text_size)
        ai_var = ctk.BooleanVar(value=settings.use_ai)
        setting_row("Utiliser l'IA pour lire les PDF", lambda m: ctk.CTkSwitch(
            m, text="", variable=ai_var, progress_color=theme.ACCENT, command=lambda: app.set_use_ai(ai_var.get())))

        ctk.CTkLabel(body, text="Sauvegardes", font=theme.font(24, "bold"), text_color=theme.TEXT).pack(anchor="w", padx=12, pady=(24, 6))
        buttons = ctk.CTkFrame(body, fg_color="transparent")
        buttons.pack(fill="x", padx=12)
        theme.secondary_button(buttons, text="Exporter mes recettes…", command=app.export_recipes).pack(side="left", padx=(0, 8))
        theme.secondary_button(buttons, text="Importer un fichier…", command=app.import_backup).pack(side="left", padx=(0, 8))
        theme.ghost_button(buttons, text="Ouvrir le dossier", text_color=theme.ACCENT,
                           command=lambda: open_folder(config.data_dir())).pack(side="left")

        ai_status = "prête" if llm.is_available() else "indisponible (modèle ou llama-cpp manquant)"
        ocr_status = "disponible" if pdf_text.ocr_available() else "indisponible (Tesseract non installé)"
        info = (f"Version {__version__}\nDonnées : {config.data_dir()}\n"
                f"IA de lecture des PDF : {ai_status}\nLecture des scans (OCR) : {ocr_status}")
        ctk.CTkLabel(body, text=info, font=theme.font(12), text_color=theme.MUTED, justify="left", anchor="w",
                     wraplength=560).pack(anchor="w", padx=12, pady=(24, 8))

        theme.primary_button(self, text="Fermer", width=120, command=self.close).pack(pady=(0, 16))


def ask_pdf(parent) -> Path | None:
    path = filedialog.askopenfilename(parent=parent, title="Choisir un PDF de recette",
                                      filetypes=[("PDF", "*.pdf"), ("Tous les fichiers", "*.*")])
    return Path(path) if path else None


def ask_image(parent) -> Path | None:
    path = filedialog.askopenfilename(parent=parent, title="Choisir une photo",
                                      filetypes=[("Images", "*.png *.jpg *.jpeg *.webp *.gif *.bmp"),
                                                 ("Tous les fichiers", "*.*")])
    return Path(path) if path else None
