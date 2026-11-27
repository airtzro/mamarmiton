"""Shopping list: merge the ingredients of several recipes into one list grouped by aisle.

The first half is pure logic (``build_list``, ``group_by_aisle``, ``to_text``).
The second half keeps the current list on disk in ``courses.json`` next to
``settings.json``; it never touches ``recette.json``.
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path

from mamarmiton import config
from mamarmiton.models import Recipe, fold
from mamarmiton.quantities import NUMBER_RE, format_number, parse_number, split_amount_unit

log = logging.getLogger(__name__)

AISLES = ["Fruits et légumes", "Crèmerie", "Boucherie / poisson", "Épicerie", "Boulangerie", "Surgelés",
          "Boissons", "Autres"]
OTHER = "Autres"

# ---------------------------------------------------------------- units
# (regex on accent-free lowercase text, dimension, factor to the base unit)
# Base units: grams, millilitres; spoons and the rest count as themselves.
_UNIT_PATTERNS: list[tuple[re.Pattern, str, int]] = [
    (re.compile(p), dim, factor) for p, dim, factor in [
        (r"^(?:c\.?\s*a\.?\s*c(?:afe)?|cuill?eres?\s*a\s*cafe|cuil\.?\s*a\s*cafe|cac|cc)(?![a-z])", "cac", 1),
        (r"^(?:c\.?\s*a\.?\s*s(?:oupe)?|cuill?eres?\s*a\s*soupe|cuil\.?\s*a\s*soupe|cas|cs|cuill?eres?|cuil\.?)(?![a-z])",
         "cas", 1),
        (r"^(?:kg|kilos?|kilogrammes?)(?![a-z])", "mass", 1000),
        (r"^(?:g|gr|grammes?)(?![a-z])", "mass", 1),
        (r"^mg(?![a-z])", "mass", Fraction(1, 1000)),
        (r"^(?:l|litres?)(?![a-z])", "vol", 1000),
        (r"^dl(?![a-z])", "vol", 100),
        (r"^cl(?![a-z])", "vol", 10),
        (r"^ml(?![a-z])", "vol", 1),
    ]
]
_SPOON_NAMES = {"cas": "c. à soupe", "cac": "c. à café"}
# Units kept as their own kind of count ("2 gousses d'ail" and "1 gousse d'ail" merge)
_COUNT_UNITS = {
    "pincee", "sachet", "tranche", "morceau", "verre", "tasse", "bol", "boite", "gousse", "brin", "feuille",
    "botte", "noix", "noisette", "pot", "brique", "paquet", "tablette", "carre", "poignee", "filet", "zeste",
}
_UNIT_DISPLAY = {"pincee": "pincée", "boite": "boîte", "poignee": "poignée", "carre": "carré"}
_INVARIABLE = {"noix"}


def _singular_word(word: str) -> str:
    if len(word) <= 3 or word in _KEEP_S:
        return word
    if word.endswith("eaux"):
        return word[:-1]
    if word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


_KEEP_S = {"pois", "mais", "ananas", "anis", "bois", "brebis", "couscous", "tapas", "cassis", "jus", "radis",
           "paris", "pastis", "tournesol"}


def _fold_name(text: str) -> str:
    text = fold(text).replace("œ", "oe").replace("’", "'")
    text = re.sub(r"\(.*?\)", " ", text)
    text = text.split(",")[0]
    text = re.sub(r"^(?:de la |de l'|du |des |de |d'|le |la |les |l')", "", text.strip())
    words = re.findall(r"[a-z0-9]+", text)
    return " ".join(_singular_word(w) for w in words)


def _pluralize(name: str) -> str:
    """Plural of an ingredient name when a count is 2 or more ("oeuf" -> "oeufs")."""
    words = name.split(" ")
    if len(words) > 1 and fold(words[1]) not in ("de", "du", "au") and not fold(words[1]).startswith("d'"):
        return name  # "petit pois", "oeuf dur": leave alone
    w = words[0]
    if not w or w[-1] in "sxz" or w[-1].isdigit():
        return name
    words[0] = w + "s"
    return " ".join(words)


@dataclass(frozen=True)
class Amount:
    """A parsed quantity: ``dim`` is mass / vol / cas / cac / count / ("u", unit); ``value`` is in base units."""

    dim: str
    value: Fraction
    unit: str = ""  # display name for dim == "unit"


def parse_amount(quantity: str) -> Amount | None:
    """``"1,5 kg"`` -> mass 1500; ``"3"`` -> count 3; ``"sel"`` / ``""`` -> None (no quantity)."""
    number, unit = split_amount_unit(quantity or "")
    if not number:
        return None
    match = NUMBER_RE.fullmatch(number)
    if match is None:
        return None
    value = parse_number(match.group("num2") or match.group("num"))
    if value is None or value <= 0:
        return None
    unit = fold(unit).replace("’", "'")
    if not unit:
        return Amount("count", value)
    for regex, dim, factor in _UNIT_PATTERNS:
        if regex.match(unit):
            return Amount(dim, value * factor)
    first = _singular_word(re.split(r"[\s.,;]+", unit)[0])
    if first in _COUNT_UNITS:
        return Amount("unit", value, first)
    return Amount("unit", value, unit)


def _fmt(value: Fraction | float) -> str:
    """French decimal: ``1.5`` -> ``"1,5"``, ``200`` -> ``"200"``."""
    text = f"{float(value):.2f}".rstrip("0").rstrip(".")
    return text.replace(".", ",")


def format_amount(amount: Amount) -> str:
    """Readable quantity in a sensible unit: 1500 g -> ``1,5 kg``, 1200 ml -> ``1,2 l``."""
    v = amount.value
    if amount.dim == "mass":
        if v >= 1000:
            return f"{_fmt(v / 1000)} kg"
        if v < 1:
            return f"{round(v * 1000)} mg"
        return f"{_fmt(round(v)) if v >= 10 else _fmt(round(float(v), 1))} g"
    if amount.dim == "vol":
        if v >= 1000:
            return f"{_fmt(v / 1000)} l"
        return f"{_fmt(round(v)) if v >= 10 else _fmt(round(float(v), 1))} ml"
    number = format_number(v)
    if amount.dim == "count":
        return number
    if amount.dim in _SPOON_NAMES:
        return f"{number} {_SPOON_NAMES[amount.dim]}"
    unit = _UNIT_DISPLAY.get(amount.unit, amount.unit)
    if v >= 2 and amount.unit not in _INVARIABLE and unit and unit[-1] not in "sx":
        unit += "s"
    return f"{number} {unit}"


# ---------------------------------------------------------------- aisles
_PHRASES = {  # checked first: more specific than single words
    "lait de coco": "Épicerie", "creme de coco": "Épicerie", "noix de coco": "Épicerie",
    "pate feuillete": "Crèmerie", "pate brise": "Crèmerie", "pate sable": "Crèmerie", "pate a tarte": "Crèmerie",
    "pomme de terre": "Fruits et légumes", "pois chiche": "Épicerie", "haricot rouge": "Épicerie",
    "haricot blanc": "Épicerie", "huile d'olive": "Épicerie", "sucre glace": "Épicerie",
    "beurre de cacahuete": "Épicerie", "poudre d'amande": "Épicerie", "creme de marron": "Épicerie",
    "chocolat noir": "Épicerie", "chocolat au lait": "Épicerie", "eau de fleur": "Épicerie",
    "petit pois": "Fruits et légumes", "haricot vert": "Fruits et légumes",
}
_KEYWORDS: list[tuple[str, set[str]]] = [
    ("Surgelés", {"surgele", "congele", "glace", "sorbet"}),
    ("Boissons", {"eau", "vin", "biere", "jus", "cidre", "rhum", "cognac", "whisky", "champagne", "sirop", "soda",
                  "limonade", "cafe", "the", "infusion", "boisson", "porto", "kirsch", "liqueur", "armagnac"}),
    ("Boulangerie", {"pain", "baguette", "brioche", "croissant", "biscotte", "chapelure", "pita", "tortilla",
                     "burger", "wrap", "naan"}),
    ("Boucherie / poisson", {"boeuf", "veau", "porc", "poulet", "dinde", "canard", "agneau", "lapin", "jambon",
                             "lardon", "bacon", "saucisse", "saucisson", "chorizo", "merguez", "steak", "viande",
                             "hache", "escalope", "cote", "gigot", "roti", "filet", "magret", "poisson", "saumon",
                             "thon", "cabillaud", "colin", "sardine", "maquereau", "truite", "crevette", "moule",
                             "huitre", "calamar", "lotte", "bar", "dorade", "merlu", "anchois", "lieu", "poulpe",
                             "coquille", "langoustine", "crabe", "foie", "andouillette", "rillette", "pate"}),
    ("Crèmerie", {"lait", "beurre", "creme", "fromage", "yaourt", "yogourt", "oeuf", "mozzarella", "parmesan",
                  "gruyere", "emmental", "comte", "chevre", "feta", "ricotta", "mascarpone", "raclette",
                  "camembert", "brie", "roquefort", "reblochon", "cheddar", "skyr", "faisselle", "margarine",
                  "gorgonzola", "boursin", "fondue", "cancoillotte", "babybel", "maroilles", "munster"}),
    ("Épicerie", {"farine", "sucre", "sel", "poivre", "huile", "vinaigre", "pate", "riz", "semoule", "lentille",
                  "conserve", "moutarde", "miel", "chocolat", "levure", "bicarbonate", "cacao", "amande", "noix",
                  "noisette", "pistache", "cacahuete", "raisin", "coulis", "concentre", "bouillon", "sauce",
                  "epice", "cannelle", "curry", "paprika", "cumin", "curcuma", "muscade", "vanille", "safran",
                  "fecule", "maizena", "ketchup", "mayonnaise", "cornichon", "capre", "olive", "confiture",
                  "nutella", "cereale", "flocon", "avoine", "muesli", "quinoa", "boulgour", "couscous", "tapioca",
                  "gelatine", "agar", "pruneau", "abricot sec", "datte", "figue", "tahini", "sesame", "pignon",
                  "harissa", "tabasco", "soja", "nuoc", "wasabi", "biscuit", "speculoos", "pain d'epice",
                  "colorant", "arome", "sucre", "thym sec", "herbe de provence", "laurier", "clou de girofle",
                  "bouquet garni", "piment", "piment d'espelette", "gingembre moulu", "massepain", "praline",
                  "boudoir", "cassonade", "vergeoise", "edulcorant", "stevia", "spaghetti", "tagliatelle",
                  "penne", "macaroni", "fusilli", "lasagne", "nouille", "vermicelle", "polenta", "puree", "mais",
                  "tomate pelee", "coulis de tomate", "concentre de tomate", "passata", "pois", "feve", "flageolet"}),
    ("Fruits et légumes", {"tomate", "oignon", "echalote", "ail", "carotte", "pomme", "poire", "poireau",
                           "courgette", "aubergine", "poivron", "salade", "laitue", "mache", "roquette", "epinard",
                           "citron", "orange", "banane", "fraise", "framboise", "myrtille", "cerise", "peche",
                           "nectarine", "prune", "mangue", "ananas", "kiwi", "melon", "pasteque", "avocat",
                           "concombre", "radis", "navet", "betterave", "celeri", "fenouil", "champignon",
                           "chou", "brocoli", "haricot", "patate", "potiron", "citrouille", "courge", "butternut",
                           "persil", "basilic", "ciboulette", "coriandre", "menthe", "thym", "romarin", "sauge",
                           "aneth", "estragon", "cerfeuil", "origan", "herbe", "gingembre", "citronnelle", "mure",
                           "groseille", "cassis", "rhubarbe", "clementine", "mandarine", "pamplemousse", "lime",
                           "raisin", "figue", "abricot", "grenade", "litchi", "papaye", "endive", "artichaut",
                           "asperge", "cresson", "oseille", "panais", "topinambour", "legume", "fruit", "piment",
                           "salsifis", "blette", "cornichon frais", "echalion", "scarole", "chicoree", "mais frais"}),
]
# a few words appear in several lists: the first match in this order wins
_PRIORITY_WORDS = {"pate": "Épicerie", "gousse": "Fruits et légumes", "piment": "Épicerie", "raisin": "Fruits et légumes",
                   "figue": "Fruits et légumes", "mais": "Épicerie", "pois": "Épicerie", "glace": "Surgelés",
                   "sucre": "Épicerie", "noix": "Épicerie", "olive": "Épicerie", "cornichon": "Épicerie",
                   "thym": "Fruits et légumes", "haricot": "Fruits et légumes", "poivre": "Épicerie",
                   "poivron": "Fruits et légumes", "abricot": "Fruits et légumes", "gingembre": "Fruits et légumes",
                   "herbe": "Fruits et légumes", "filet": "Boucherie / poisson", "foie": "Boucherie / poisson"}


def aisle_of(name: str) -> str:
    """Aisle of an ingredient name, from a small built-in French keyword list."""
    key = _fold_name(name)
    padded = f" {key} "
    if " surgele" in padded or " congele" in padded:
        return "Surgelés"
    for phrase, aisle in _PHRASES.items():
        if f" {phrase}" in padded or f" {_fold_name(phrase)}" in padded:
            return aisle
    words = key.replace("'", " ").split()
    for word in words:
        if word in _PRIORITY_WORDS:
            return _PRIORITY_WORDS[word]
    for aisle, keywords in _KEYWORDS:
        for kw in keywords:
            if " " in kw:
                if f" {kw} " in padded:
                    return aisle
            elif kw in words:
                return aisle
    return OTHER


# --------------------------------------------------------------- merging
@dataclass
class ShoppingItem:
    key: str  # stable id used for ticking: "<folded name>|<dimension>"
    name: str
    quantity: str  # display text, "" when unknown
    aisle: str
    recipes: list[str] = field(default_factory=list)  # titles of the recipes that need it

    @property
    def label(self) -> str:
        """One line of plain text: ``500 g de farine``, ``3 oeufs``, ``sel``."""
        if not self.quantity:
            return self.name
        if not re.search(r"[^\W\d_]", self.quantity):  # only digits: a plain count
            return f"{self.quantity} {self.name}"
        folded = fold(self.name)
        link = "d'" if folded[:1] in "aeiouhy" else "de "
        return f"{self.quantity} {link}{self.name}"


def _scale(value: Fraction, recipe: Recipe, servings: int) -> Fraction:
    return value * Fraction(max(1, servings), max(1, recipe.pour))


def build_list(selection: list[tuple[Recipe, int]]) -> list[ShoppingItem]:
    """Merge the ingredients of ``(recipe, servings)`` pairs into shopping lines.

    Same ingredient (ignoring accents, case, plural) in the same kind of unit is added up
    (g + kg, ml + cl + l, spoons, counts). Different kinds stay on separate lines. An
    ingredient without any quantity ("sel") appears once, unless a quantified line exists.
    """
    merged: dict[str, dict] = {}
    for recipe, servings in selection:
        for ing in recipe.ingredients:
            key_name = _fold_name(ing.nom)
            if not key_name:
                continue
            amount = parse_amount(ing.quantite)
            dim = "none" if amount is None else amount.dim if amount.dim != "unit" else f"unit:{amount.unit}"
            key = f"{key_name}|{dim}"
            entry = merged.setdefault(key, {"name": ing.nom.strip(), "amount": amount, "recipes": [],
                                            "total": Fraction(0), "key_name": key_name})
            if amount is not None:
                entry["total"] += _scale(amount.value, recipe, servings)
            if recipe.nom not in entry["recipes"]:
                entry["recipes"].append(recipe.nom)

    quantified = {e["key_name"] for k, e in merged.items() if e["amount"] is not None}
    items: list[ShoppingItem] = []
    for key, entry in merged.items():
        amount: Amount | None = entry["amount"]
        name = _tidy_name(entry["name"])
        if amount is None:
            if entry["key_name"] in quantified:
                continue  # "sel" next to "1 pincée de sel": the quantified line is enough
            quantity = ""
        else:
            total = Amount(amount.dim, entry["total"], amount.unit)
            quantity = format_amount(total)
            if amount.dim == "count" and entry["total"] >= 2:
                name = _pluralize(name)
        items.append(ShoppingItem(key, name, quantity, aisle_of(entry["name"]), entry["recipes"]))
    items.sort(key=lambda i: (AISLES.index(i.aisle), fold(i.name), i.key))
    return items


def _tidy_name(name: str) -> str:
    name = re.sub(r"\s+", " ", name.strip())
    name = re.sub(r"^(?:de la |de l'|du |des |de |d')", "", name, flags=re.IGNORECASE)
    return name


def group_by_aisle(items: list[ShoppingItem]) -> list[tuple[str, list[ShoppingItem]]]:
    """``[(aisle, items)]`` in shop order, empty aisles left out."""
    groups = []
    for aisle in AISLES:
        found = [i for i in items if i.aisle == aisle]
        if found:
            groups.append((aisle, found))
    return groups


def to_text(items: list[ShoppingItem], checked: set[str] | frozenset[str] = frozenset(),
            extras: list[dict] | None = None, title: str = "Liste de courses") -> str:
    """Plain text for a phone message or a printout. Ticked lines are left out."""
    lines = [title, ""]
    for aisle, group in group_by_aisle([i for i in items if i.key not in checked]):
        lines.append(aisle.upper())
        lines.extend(f"- {i.label}" for i in group)
        lines.append("")
    todo = [e["text"] for e in (extras or []) if not e.get("done")]
    if todo:
        lines.append("AUTRES ARTICLES")
        lines.extend(f"- {t}" for t in todo)
        lines.append("")
    if len(lines) == 2:
        lines.append("(rien à acheter)")
    return "\n".join(lines).rstrip() + "\n"


def open_with_default_app(path: Path) -> bool:
    """Open ``path`` with the system's default program. False if that was not possible."""
    try:
        if hasattr(os, "startfile"):
            os.startfile(str(path))  # noqa: S606  (Windows)
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except (OSError, ValueError):
        log.exception("Cannot open %s", path)
        return False


# ------------------------------------------------------------ persistence
def shopping_file() -> Path:
    return config.data_dir() / "courses.json"


@dataclass
class ShoppingState:
    """The current list: chosen recipes, ticked lines, hand-written lines."""

    recipes: list[dict] = field(default_factory=list)  # {"id": str, "servings": int}
    checked: set[str] = field(default_factory=set)
    extras: list[dict] = field(default_factory=list)  # {"text": str, "done": bool}

    # --- editing
    def servings_of(self, recipe_id: str) -> int | None:
        for entry in self.recipes:
            if entry["id"] == recipe_id:
                return entry["servings"]
        return None

    def add_recipe(self, recipe_id: str, servings: int) -> None:
        servings = max(1, min(99, int(servings)))
        for entry in self.recipes:
            if entry["id"] == recipe_id:
                entry["servings"] = servings
                return
        self.recipes.append({"id": recipe_id, "servings": servings})

    def set_servings(self, recipe_id: str, servings: int) -> None:
        if self.servings_of(recipe_id) is not None:
            self.add_recipe(recipe_id, servings)

    def remove_recipe(self, recipe_id: str) -> None:
        self.recipes = [e for e in self.recipes if e["id"] != recipe_id]

    def set_checked(self, key: str, value: bool) -> None:
        (self.checked.add if value else self.checked.discard)(key)

    def add_extra(self, text: str) -> bool:
        text = " ".join(text.split())
        if not text:
            return False
        self.extras.append({"text": text, "done": False})
        return True

    def clear(self) -> None:
        self.recipes, self.checked, self.extras = [], set(), []

    def prune(self, known_ids: set[str], valid_keys: set[str]) -> None:
        """Forget recipes that no longer exist and ticks of lines that are gone."""
        self.recipes = [e for e in self.recipes if e["id"] in known_ids]
        self.checked &= valid_keys

    # --- file
    def to_dict(self) -> dict:
        return {"version": 1, "recipes": self.recipes, "checked": sorted(self.checked), "extras": self.extras}

    @classmethod
    def from_dict(cls, data) -> "ShoppingState":
        state = cls()
        if not isinstance(data, dict):
            return state
        for entry in data.get("recipes") or []:
            if isinstance(entry, dict) and entry.get("id"):
                try:
                    servings = max(1, min(99, int(entry.get("servings", 4))))
                except (TypeError, ValueError):
                    servings = 4
                if state.servings_of(str(entry["id"])) is None:
                    state.recipes.append({"id": str(entry["id"]), "servings": servings})
        state.checked = {str(k) for k in data.get("checked") or [] if isinstance(k, str)}
        for extra in data.get("extras") or []:
            if isinstance(extra, dict) and str(extra.get("text") or "").strip():
                state.extras.append({"text": str(extra["text"]).strip(), "done": bool(extra.get("done"))})
        return state

    @classmethod
    def load(cls, path: Path | None = None) -> "ShoppingState":
        try:
            with open(path or shopping_file(), encoding="utf-8") as f:
                return cls.from_dict(json.load(f))
        except (OSError, ValueError):
            return cls()

    def save(self, path: Path | None = None) -> None:
        target = path or shopping_file()
        tmp = target.with_suffix(".json.tmp")
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.to_dict(), f, indent=2, ensure_ascii=False)
            os.replace(tmp, target)
        except OSError:
            log.exception("Could not save the shopping list")
