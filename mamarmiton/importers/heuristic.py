"""Rule-based recipe extraction (used when the AI model is unavailable, and
to fill gaps the AI leaves)."""

from __future__ import annotations

import re

from mamarmiton.models import Ingredient, Recipe, fold, parse_minutes
from mamarmiton.quantities import split_quantity_and_name

SECTION_RE = re.compile(
    r"^(?P<name>ingr[ée]dients?|ustensiles?|mat[ée]riel|pr[ée]paration|[ée]tapes?|instructions?"
    r"|m[ée]thode|d[ée]roulement|r[ée]alisation|astuces?|conseils?|notes?)"
    r"(?:\s+(?:pour|de la recette)\b[^:]*)?\s*:?\s*$",
    re.IGNORECASE,
)
INGREDIENT_SECTIONS = {"ingredient", "ingredients"}
STEP_SECTIONS = {"preparation", "etape", "etapes", "instruction", "instructions", "methode", "deroulement", "realisation"}
NOTE_SECTIONS = {"astuce", "astuces", "conseil", "conseils", "note", "notes"}

URL_RE = re.compile(r"(https?://|www\.|\.(fr|com|net|org)\b)", re.IGNORECASE)
STEP_START_RE = re.compile(r"^(?:[-•*]\s*|(?:[ée]tape\s*)?\d{1,2}\s*[).:\-–]?\s+)(?=\S)", re.IGNORECASE)
BARE_NUMBER_RE = re.compile(r"^\d{1,2}[).]?$")
STOPWORD_NAMES = {"", "de", "d'", "des", "du", "en", "et"}

DESSERT_WORDS = ["dessert", "gateau", "tarte au sucre", "chocolat", "mousse", "biscuit", "cake", "cookie",
                 "muffin", "crepe", "glace", "compote", "clafoutis", "tiramisu", "flan", "creme brulee",
                 "fondant", "brownie", "madeleine", "meringue", "sucre glace", "pate a choux"]
BREAD_WORDS = ["pain", "brioche", "baguette", "focaccia", "fougasse"]
STARTER_WORDS = ["entree", "salade", "soupe", "veloute", "verrine", "gaspacho", "tartinade", "aperitif"]


def _section_key(line: str) -> str | None:
    match = SECTION_RE.match(line.strip())
    return fold(match.group("name")) if match else None


def _nice_title(line: str) -> str:
    line = line.strip(" -:")
    if line.isupper():
        line = line.lower()
    return line[:1].upper() + line[1:]


def extract_title(lines: list[str]) -> str:
    for line in lines:
        text = line.strip()
        if len(text) > 3 and not _section_key(text) and not URL_RE.search(text):
            return _nice_title(text)
    return "Recette importée"


def extract_times(text: str) -> tuple[int, int]:
    folded = fold(text)
    duration = r"(\d+\s*h\s*\d*\s*(?:min|mn)?|\d+\s*(?:min(?:utes?)?|mn|h(?:eures?)?))"
    prep = 0
    cook = 0
    if m := re.search(r"(?:preparation|prep)\s*:?\s*" + duration, folded):
        prep = parse_minutes(m.group(1))
    if m := re.search(r"repos\s*:?\s*" + duration, folded):
        prep += parse_minutes(m.group(1))
    if m := re.search(r"cuisson\s*:?\s*" + duration, folded):
        cook = parse_minutes(m.group(1))
    elif m := re.search(r"(?:cuire|enfourne\w*|four)[^.\n]*?(?:pendant|durant|environ)?\s*" + duration, folded):
        cook = parse_minutes(m.group(1))
    return prep, cook


def find_servings(text: str) -> int | None:
    """Servings stated in the text, or None."""
    folded = fold(text)
    if m := re.search(r"(\d+)\s*(?:personnes?|pers\b|parts?\b|portions?|convives)", folded):
        return max(1, int(m.group(1)))
    if m := re.search(r"pour\s+(\d+)\b", folded):
        return max(1, int(m.group(1)))
    return None


def extract_servings(text: str) -> int:
    return find_servings(text) or 4


def detect_dish_type(title: str, text: str) -> str:
    for source in (fold(title), fold(text)):
        if any(w in source for w in BREAD_WORDS) and not any(w in source for w in ("pain d'epice", "pain perdu")):
            return "Pain"
        if any(w in source for w in DESSERT_WORDS):
            return "Dessert"
        if any(w in source for w in STARTER_WORDS):
            return "Entrée"
    return "Plat"


def detect_cooking_type(text: str) -> str:
    folded = fold(text)
    if re.search(r"\bfour\b|°c|\bth\.?\s*\d|thermostat|enfourne", folded):
        return "Four"
    if re.search(r"\bpoele\b|\bwok\b", folded):
        return "Poêle"
    if re.search(r"\bvapeur\b", folded):
        return "Vapeur"
    if re.search(r"\bcasserole\b|\bfaitout\b|\bbouillir\b|\bmijoter\b|cocotte", folded):
        return "Casserole"
    if re.search(r"micro-?ondes", folded):
        return "Micro-ondes"
    return "Sans cuisson"


def split_sections(lines: list[str]) -> dict[str, list[str]]:
    sections: dict[str, list[str]] = {"intro": []}
    current = "intro"
    for line in lines:
        key = _section_key(line)
        if key:
            if key in INGREDIENT_SECTIONS:
                current = "ingredients"
            elif key in STEP_SECTIONS:
                current = "steps"
            elif key in NOTE_SECTIONS:
                current = "notes"
            else:
                current = "other"
            sections.setdefault(current, [])
            continue
        sections.setdefault(current, []).append(line)
    return sections


def parse_ingredients(lines: list[str]) -> list[Ingredient]:
    result: list[Ingredient] = []
    for line in lines:
        line = line.strip().lstrip("-•* ").strip()
        if not line or URL_RE.search(line):
            continue
        # "125 g de beurre 4 oeufs" -> two ingredients (multi-column PDFs)
        for chunk in re.split(r"(?<=[^\d\s/,.])\s+(?=\d)", line):
            if ":" in chunk:
                nom, qty = chunk.split(":", 1)
            else:
                qty, nom = split_quantity_and_name(chunk)
            nom = nom.strip(" -,.")
            if fold(nom) in STOPWORD_NAMES:
                continue
            result.append(Ingredient(nom[:1].upper() + nom[1:], qty.strip()))
    return result


def parse_steps(lines: list[str]) -> list[str]:
    steps: list[str] = []
    for raw in lines:
        line = raw.strip()
        if not line or URL_RE.search(line) or BARE_NUMBER_RE.match(line):
            continue
        starts_new = bool(STEP_START_RE.match(line))
        text = STEP_START_RE.sub("", line).strip()
        if not steps or starts_new or steps[-1].endswith((".", "!", "?")):
            steps.append(text[:1].upper() + text[1:])
        else:
            steps[-1] = f"{steps[-1]} {text}"
    return steps


def parse_recipe_text(text: str) -> Recipe:
    lines = [ln.replace("\r", "").strip() for ln in text.splitlines()]
    lines = [re.sub(r"[•●▪]", "-", ln) for ln in lines if ln]
    sections = split_sections(lines)
    title = extract_title(lines)
    prep, cook = extract_times(text)
    return Recipe(
        nom=title,
        temps=prep,
        temps_cuisson=cook,
        pour=extract_servings(text),
        type_plat=detect_dish_type(title, text),
        type_cuisson=detect_cooking_type(text),
        ingredients=parse_ingredients(sections.get("ingredients", [])),
        etapes=parse_steps(sections.get("steps", [])),
        notes=" ".join(sections.get("notes", [])),
    )
