"""PDF, documents, photos or text -> Recipe, combining the AI parser with the rule-based one."""

from __future__ import annotations

import logging
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from mamarmiton.importers import heuristic, llm, pdf_text
from mamarmiton.models import Recipe, fold

log = logging.getLogger(__name__)

QTY_LIKE = re.compile(r"^\s*[\d½¼¾]")
PLACEHOLDER_NAMES = {"", "test", "recette", "nom de la recette", "<nom de la recette>"}

Progress = Callable[[str], None]


@dataclass
class ImportResult:
    recipe: Recipe
    images: list[Path] = field(default_factory=list)  # temporary files, copy them before use
    method: str = "règles"  # "IA", "règles" or "site"
    warnings: list[str] = field(default_factory=list)
    origin: str = ""  # where the photos come from, for "N photo(s) trouvée(s) dans …" ("" = the PDF / the site)


def clean_ai_ingredients(recipe: Recipe) -> None:
    """Small models sometimes swap name/quantity or repeat lines: undo that."""
    seen: set[tuple[str, str]] = set()
    cleaned = []
    for ing in recipe.ingredients:
        if QTY_LIKE.match(ing.nom) and not QTY_LIKE.match(ing.quantite or "0"):
            ing.nom, ing.quantite = ing.quantite, ing.nom
        key = (fold(ing.nom), fold(ing.quantite))
        if key in seen or not ing.nom:
            continue
        seen.add(key)
        cleaned.append(ing)
    recipe.ingredients = cleaned
    steps = []
    for step in recipe.etapes:
        if step not in steps:
            steps.append(step)
    recipe.etapes = steps


def _numbers(text: str) -> set[str]:
    return {n.replace(",", ".") for n in re.findall(r"\d+(?:[.,]\d+)?", text)}


def check_against_source(ai: Recipe, rules: Recipe, source: str) -> None:
    """Replace facts the model invented with what the text explicitly says.

    The regex parser only fills a time / servings value when the text states
    it, so when it found one it wins. An AI quantity whose number never
    appears in the PDF is swapped for the rule-based one when available.
    """
    if rules.temps:
        ai.temps = rules.temps
    if rules.temps_cuisson:
        ai.temps_cuisson = rules.temps_cuisson
    stated = heuristic.find_servings(source)
    if stated:
        ai.pour = stated
    elif ai.pour < 2:  # "pour 1" is almost always a guess
        ai.pour = rules.pour
    if not rules.temps and ai.temps and ai.temps == ai.temps_cuisson:
        ai.temps = 0  # prep time not stated; the model copied the cooking time
    folded = fold(source)
    for attr in ("temps", "temps_cuisson"):
        value = getattr(ai, attr)
        if value and re.search(rf"\b{value}\s*°", folded):  # oven temperature taken for a time
            setattr(ai, attr, 0)

    known = _numbers(source)
    by_name = {fold(i.nom): i for i in rules.ingredients}
    for ing in ai.ingredients:
        nums = _numbers(ing.quantite)
        if not nums or nums <= known:
            continue
        name = fold(ing.nom)
        match = by_name.get(name) or next(
            (r for key, r in by_name.items() if key and (key in name or name in key)), None)
        if match and match.quantite:
            ing.quantite = match.quantite


def merge(ai: Recipe, rules: Recipe, source: str = "") -> Recipe:
    """Keep the AI answer but fill anything it left empty from the rule-based one."""
    clean_ai_ingredients(ai)
    if source:
        check_against_source(ai, rules, source)
    if fold(ai.nom) in PLACEHOLDER_NAMES or ai.nom == "Recette sans nom":
        ai.nom = rules.nom
    if not ai.ingredients:
        ai.ingredients = rules.ingredients
    if not ai.etapes:
        ai.etapes = rules.etapes
    if not ai.temps and rules.temps:
        ai.temps = rules.temps
    if not ai.temps_cuisson and rules.temps_cuisson:
        ai.temps_cuisson = rules.temps_cuisson
    if not ai.notes:
        ai.notes = rules.notes
    return ai


def recipe_from_text(text: str, use_llm: bool = True,
                     progress: Progress | None = None) -> tuple[Recipe, str, list[str]]:
    """Any recipe text -> ``(recipe, method, warnings)``: the rule-based parser, then the local AI
    (when installed and wanted) checked against the text and completed by the rules."""
    report = progress or (lambda _msg: None)
    warnings: list[str] = []
    rules = heuristic.parse_recipe_text(text)
    recipe = rules
    method = "règles"

    if use_llm and llm.is_available():
        report("Analyse par l'IA… (cela peut prendre une minute)")
        try:
            data = llm.parse_recipe_with_llm(text)
            if data:
                recipe = merge(Recipe.from_dict(data), rules, text)
                method = "IA"
            else:
                warnings.append("L'IA n'a pas donné de réponse exploitable ; analyse simple utilisée.")
        except Exception as exc:
            log.exception("LLM parsing failed")
            warnings.append(f"L'IA a échoué ({exc}) ; analyse simple utilisée.")
    elif use_llm:
        warnings.append("Modèle IA introuvable : analyse simple utilisée. Vérifiez bien la recette.")
    return recipe, method, warnings


def import_pdf(path: Path, use_llm: bool = True, progress: Progress | None = None) -> ImportResult:
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(path)
    report = progress or (lambda _msg: None)

    report("Lecture du PDF…")
    text = pdf_text.extract_text(path)
    if not text.strip():
        raise ValueError(NO_TEXT_IN_PDF)
    recipe, method, warnings = recipe_from_text(text, use_llm, report)
    images = _pdf_images(path, report)
    recipe.source = path.name
    return ImportResult(recipe=recipe, images=images, method=method, warnings=warnings)


NO_TEXT_IN_PDF = ("Aucun texte trouvé dans ce PDF. S'il s'agit d'un scan, installez Tesseract "
                  "pour activer la reconnaissance de texte (voir le README).")

# --------------------------------------------------------------------------
# Several files (pages of one recipe), text documents, photos, pasted text
# --------------------------------------------------------------------------
PDF_EXTENSIONS = {".pdf"}
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif", ".gif", ".bmp", ".tif", ".tiff"}
DOCUMENT_EXTENSIONS = {".txt", ".docx", ".odt"}


def kind_of(path: Path | str) -> str | None:
    """"pdf", "image", "document" (text, Word, LibreOffice) or None when the file is not supported."""
    ext = Path(path).suffix.lower()
    if ext in PDF_EXTENSIONS:
        return "pdf"
    if ext in IMAGE_EXTENSIONS:
        return "image"
    if ext in DOCUMENT_EXTENSIONS:
        return "document"
    return None


def document_text(path: Path) -> str:
    from mamarmiton.importers import documents

    data = Path(path).read_bytes()
    ext = Path(path).suffix.lower()
    if ext == ".docx":
        return documents.docx_text(data)
    if ext == ".odt":
        return documents.odt_text(data)
    return documents.plain_text(data)


def _pdf_images(path: Path, report: Progress) -> list[Path]:
    report("Extraction des photos…")
    try:
        return pdf_text.extract_images(path, Path(tempfile.mkdtemp(prefix="mamarmiton_")))
    except Exception:
        log.exception("Image extraction failed")
        return []


def import_text(text: str, use_llm: bool = True, progress: Progress | None = None,
                source: str = "") -> ImportResult:
    """Pasted text (or text read from documents) -> recipe."""
    if not text.strip():
        raise ValueError("Le texte est vide : collez d'abord la recette.")
    recipe, method, warnings = recipe_from_text(text, use_llm, progress)
    recipe.source = source
    return ImportResult(recipe=recipe, method=method, warnings=warnings)


def import_files(paths: list[Path], use_llm: bool = True, progress: Progress | None = None,
                 text: str = "") -> ImportResult:
    """Several files that make one recipe (e.g. the pages of a scan), plus optional pasted text.

    Photos are read with Tesseract, so call this with photos only when ``tesseract_available()``.
    The photos found in PDFs are kept; the photos given as files are pages, not dish photos.
    """
    report = progress or (lambda _msg: None)
    parts: list[str] = []
    images: list[Path] = []
    for n, path in enumerate(paths, 1):
        path = Path(path)
        if not path.is_file():
            raise FileNotFoundError(path)
        kind = kind_of(path)
        page = f" ({n}/{len(paths)})" if len(paths) > 1 else ""
        if kind == "pdf":
            report(f"Lecture du PDF{page}…")
            parts.append(pdf_text.extract_text(path))
            images += _pdf_images(path, report)
        elif kind == "image":
            report(f"Lecture de la photo{page}…")
            parts.append(pdf_text.ocr_image(path))
        elif kind == "document":
            report(f"Lecture du document{page}…")
            parts.append(document_text(path))
        else:
            raise ValueError(f"« {path.name} » n'est pas un format accepté.")
    if text.strip():
        parts.append(text)
    full = "\n\n".join(p.strip() for p in parts if p and p.strip())
    if not full:
        for folder in {p.parent for p in images}:
            shutil.rmtree(folder, ignore_errors=True)
        if all(kind_of(p) == "pdf" for p in paths):
            raise ValueError(NO_TEXT_IN_PDF)
        raise ValueError("Aucun texte n'a pu être lu. Pour une photo, prenez-la bien droite, en pleine lumière.")
    recipe, method, warnings = recipe_from_text(full, use_llm, report)
    recipe.source = Path(paths[0]).name if paths else ""
    return ImportResult(recipe=recipe, images=images, method=method, warnings=warnings)
