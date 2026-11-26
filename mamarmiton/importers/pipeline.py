"""PDF -> Recipe, combining the AI parser with the rule-based one."""

from __future__ import annotations

import logging
import re
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
    method: str = "règles"  # "IA" or "règles"
    warnings: list[str] = field(default_factory=list)


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


def import_pdf(path: Path, use_llm: bool = True, progress: Progress | None = None) -> ImportResult:
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(path)
    report = progress or (lambda _msg: None)
    warnings: list[str] = []

    report("Lecture du PDF…")
    text = pdf_text.extract_text(path)
    if not text.strip():
        raise ValueError(
            "Aucun texte trouvé dans ce PDF. S'il s'agit d'un scan, installez Tesseract "
            "pour activer la reconnaissance de texte (voir le README)."
        )

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

    report("Extraction des photos…")
    images: list[Path] = []
    try:
        tmp = Path(tempfile.mkdtemp(prefix="mamarmiton_"))
        images = pdf_text.extract_images(path, tmp)
    except Exception:
        log.exception("Image extraction failed")

    recipe.source = path.name
    return ImportResult(recipe=recipe, images=images, method=method, warnings=warnings)
