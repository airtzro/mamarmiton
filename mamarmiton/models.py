"""Recipe data model.

The JSON layout stays compatible with v3 files (French keys, ``quantité``
with an accent). New keys (``id``, ``favori``, ``images``, ``notes``...)
are optional when reading, so old files load without changes.
"""

from __future__ import annotations

import re
import unicodedata
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from mamarmiton.quantities import split_quantity_and_name

DISH_TYPES = ["Entrée", "Plat", "Accompagnement", "Dessert", "Pain"]
COOKING_TYPES = ["Four", "Poêle", "Casserole", "Vapeur", "Micro-ondes", "Sans cuisson"]

DEFAULT_DISH = "Plat"
DEFAULT_COOKING = "Sans cuisson"


def strip_accents(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", text) if unicodedata.category(c) != "Mn")


def fold(text: str) -> str:
    """Lowercase, accent-free version of ``text`` for comparisons."""
    return strip_accents(str(text)).casefold().strip()


MAX_TAGS = 20
MAX_TAG_LEN = 30


def normalize_tags(raw: Any) -> list[str]:
    """Clean a list of tags: trimmed, single spaces, no duplicates (case and accents ignored).

    The first spelling wins; tags are cut to ``MAX_TAG_LEN`` characters and the
    list to ``MAX_TAGS`` entries. Anything that is not a list of strings is ignored.
    """
    if isinstance(raw, str):
        raw = raw.split(",")
    if not isinstance(raw, (list, tuple)):
        return []
    seen: set[str] = set()
    result: list[str] = []
    for item in raw:
        if not isinstance(item, str):
            continue
        tag = " ".join(item.split())[:MAX_TAG_LEN].strip()
        key = fold(tag)
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(tag)
        if len(result) >= MAX_TAGS:
            break
    return result


def _normalize_choice(value: Any, choices: list[str], default: str) -> str:
    wanted = fold(value or "")
    for choice in choices:
        if fold(choice) == wanted:
            return choice
    return default


def parse_minutes(value: Any) -> int:
    """Turn ``45``, ``"45"``, ``"45 min"``, ``"1h30"``, ``"1 h 15 min"`` into minutes.

    Anything unreadable becomes 0 instead of crashing the UI.
    """
    if value is None or isinstance(value, bool):
        return 0
    if isinstance(value, (int, float)):
        return max(0, int(round(value)))
    text = fold(value).replace(",", ".")
    if not text:
        return 0
    match = re.fullmatch(r"(\d+(?:\.\d+)?)\s*h(?:eures?|rs?)?\s*(?:et\s*)?(\d+)?\s*(?:min(?:utes?)?|mn)?", text)
    if match:
        hours = float(match.group(1))
        minutes = int(match.group(2) or 0)
        return int(round(hours * 60)) + minutes
    match = re.search(r"\d+(?:\.\d+)?", text)
    return int(round(float(match.group()))) if match else 0


def parse_int(value: Any, default: int = 0, minimum: int = 0) -> int:
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        return max(minimum, int(value))
    match = re.search(r"\d+", str(value or ""))
    return max(minimum, int(match.group())) if match else default


def format_minutes(minutes: int) -> str:
    minutes = max(0, int(minutes))
    hours, rest = divmod(minutes, 60)
    if hours == 0:
        return f"{rest} min"
    if rest == 0:
        return f"{hours} h"
    return f"{hours} h {rest:02d}"


@dataclass
class Ingredient:
    nom: str
    quantite: str = ""

    def to_dict(self) -> dict[str, str]:
        return {"nom": self.nom, "quantité": self.quantite}

    @classmethod
    def from_dict(cls, data: Any) -> "Ingredient | None":
        if isinstance(data, str):
            data = {"nom": data}
        if not isinstance(data, dict):
            return None
        nom = str(data.get("nom") or "").strip()
        qty = data.get("quantité", data.get("quantite", data.get("quantity", "")))
        qty = "" if qty is None else str(qty).strip()
        if not nom and not qty:
            return None
        return cls(nom=nom, quantite=qty)


@dataclass
class Recipe:
    nom: str
    temps: int = 0  # preparation time, minutes
    temps_cuisson: int = 0  # cooking time, minutes
    pour: int = 4  # servings
    type_plat: str = DEFAULT_DISH
    type_cuisson: str = DEFAULT_COOKING
    ingredients: list[Ingredient] = field(default_factory=list)
    etapes: list[str] = field(default_factory=list)
    notes: str = ""
    favori: bool = False
    images: list[str] = field(default_factory=list)  # file names inside images/<id>/
    tags: list[str] = field(default_factory=list)  # free labels: "Noël", "sans gluten"...
    pour_unite: str = ""  # what "pour" counts: "" = personnes, else e.g. "crêpes"
    source: str = ""  # e.g. the PDF file name it was imported from
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    cree_le: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    modifie_le: str = ""

    @property
    def temps_total(self) -> int:
        return self.temps + self.temps_cuisson

    def touch(self) -> None:
        self.modifie_le = datetime.now().isoformat(timespec="seconds")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "nom": self.nom,
            "temps": self.temps,
            "temps_cuisson": self.temps_cuisson,
            "pour": self.pour,
            "pour_unite": self.pour_unite,
            "type_plat": self.type_plat,
            "type_cuisson": self.type_cuisson,
            "ingredients": [i.to_dict() for i in self.ingredients],
            "etapes": list(self.etapes),
            "notes": self.notes,
            "favori": self.favori,
            "images": list(self.images),
            "tags": normalize_tags(self.tags),
            "source": self.source,
            "cree_le": self.cree_le,
            "modifie_le": self.modifie_le,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Recipe":
        """Build a recipe from any reasonably shaped dict (old file, LLM output...)."""
        if not isinstance(data, dict):
            raise ValueError("une recette doit être un objet JSON")
        ingredients_raw = data.get("ingredients") or []
        if not isinstance(ingredients_raw, list):
            ingredients_raw = []
        etapes_raw = data.get("etapes") or []
        if isinstance(etapes_raw, str):
            etapes_raw = etapes_raw.splitlines()
        kwargs: dict[str, Any] = dict(
            nom=str(data.get("nom") or "Recette sans nom").strip() or "Recette sans nom",
            temps=parse_minutes(data.get("temps")),
            temps_cuisson=parse_minutes(data.get("temps_cuisson")),
            pour=parse_int(data.get("pour"), default=4, minimum=1),
            pour_unite=" ".join(str(data.get("pour_unite") or "").split())[:40],
            type_plat=_normalize_choice(data.get("type_plat"), DISH_TYPES, DEFAULT_DISH),
            type_cuisson=_normalize_choice(data.get("type_cuisson"), COOKING_TYPES, DEFAULT_COOKING),
            ingredients=[i for i in (Ingredient.from_dict(x) for x in ingredients_raw) if i],
            etapes=[str(s).strip() for s in etapes_raw if str(s).strip()],
            notes=str(data.get("notes") or ""),
            favori=bool(data.get("favori", False)),
            images=[str(x) for x in data.get("images") or [] if isinstance(x, str)],
            tags=normalize_tags(data.get("tags")),
            source=str(data.get("source") or ""),
            modifie_le=str(data.get("modifie_le") or ""),
        )
        if data.get("id"):
            kwargs["id"] = str(data["id"])
        if data.get("cree_le"):
            kwargs["cree_le"] = str(data["cree_le"])
        return cls(**kwargs)


def servings_text(n: int, unite: str = "", short: bool = False) -> str:
    """"6 personnes" / "6 pers." / "6 crêpes" / "1 crêpe" (the unit can be typed singular or plural)."""
    unit = " ".join(unite.split())
    if not unit:
        return f"{n} pers." if short else f"{n} personne{'s' if n > 1 else ''}"
    head, _, tail = unit.partition(" ")  # "parts de gâteau": only the first word agrees
    if n > 1 and head[-1:].lower() not in "sxz":
        head += "s"
    elif n <= 1 and len(head) > 2 and head[-1:].lower() in "sx" and not head.lower().endswith("ss"):
        head = head[:-1]
    return f"{n} {' '.join(filter(None, (head, tail)))}"


def parse_ingredient_lines(text: str) -> list[Ingredient]:
    """Parse the editor's ingredient box: one ingredient per line.

    Accepted shapes: ``Farine: 500 g``, ``500 g de farine``, ``3 oeufs``, ``Sel``.
    """
    result: list[Ingredient] = []
    for raw in text.splitlines():
        line = raw.strip().lstrip("-•* ").strip()
        if not line:
            continue
        if ":" in line:
            nom, qty = line.split(":", 1)
            result.append(Ingredient(nom.strip(), qty.strip()))
            continue
        qty, nom = split_quantity_and_name(line)
        result.append(Ingredient(nom or line, qty))
    return result


def format_ingredient_lines(ingredients: list[Ingredient]) -> str:
    return "\n".join(f"{i.nom}: {i.quantite}" if i.quantite else i.nom for i in ingredients)
