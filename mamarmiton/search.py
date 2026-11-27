"""Filtering and sorting recipes. Pure functions, no UI."""

from __future__ import annotations

from dataclasses import dataclass, field

from mamarmiton.models import Recipe, fold

TIME_LIMITS: dict[str, int | None] = {
    "Peu importe": None,
    "30 min max": 30,
    "1 h max": 60,
    "1 h 30 max": 90,
    "2 h max": 120,
    "3 h max": 180,
}

SORTS = ["Favoris d'abord", "Nom (A → Z)", "Plus rapides", "Plus récentes"]


def _singular(word: str) -> str:
    word = fold(word)
    if len(word) > 3 and word[-1] in "sx":
        return word[:-1]
    return word


@dataclass
class Filters:
    text: str = ""
    type_plat: str | None = None
    type_cuisson: str | None = None
    max_minutes: int | None = None
    ingredients: list[str] = field(default_factory=list)
    favorites_only: bool = False
    tags: list[str] = field(default_factory=list)  # the recipe must have all of them

    def is_empty(self) -> bool:
        return not (
            self.text.strip() or self.type_plat or self.type_cuisson or self.max_minutes
            or self.ingredients or self.favorites_only or self.tags
        )


def _has_ingredient(recipe: Recipe, wanted: str) -> bool:
    target = _singular(wanted)
    if not target:
        return True
    return any(target in _singular(i.nom) or target in fold(i.nom) for i in recipe.ingredients)


def matches(recipe: Recipe, f: Filters) -> bool:
    if f.favorites_only and not recipe.favori:
        return False
    if f.type_plat and recipe.type_plat != f.type_plat:
        return False
    if f.type_cuisson and recipe.type_cuisson != f.type_cuisson:
        return False
    if f.max_minutes is not None and recipe.temps_total > f.max_minutes:
        return False
    if any(not _has_ingredient(recipe, ing) for ing in f.ingredients):
        return False
    if f.tags:
        have = {fold(t) for t in recipe.tags}
        if any(fold(t) not in have for t in f.tags):
            return False
    words = fold(f.text).split()
    if words:
        haystack = " ".join([fold(recipe.nom), *(fold(i.nom) for i in recipe.ingredients),
                             *(fold(t) for t in recipe.tags), fold(recipe.source)])
        if not all(w in haystack for w in words):
            return False
    return True


def sort_recipes(recipes: list[Recipe], how: str = SORTS[0]) -> list[Recipe]:
    by_name = sorted(recipes, key=lambda r: fold(r.nom))
    if how == "Nom (A → Z)":
        return by_name
    if how == "Plus rapides":
        return sorted(by_name, key=lambda r: r.temps_total)
    if how == "Plus récentes":
        return sorted(by_name, key=lambda r: r.cree_le, reverse=True)
    return sorted(by_name, key=lambda r: not r.favori)


def search(recipes: list[Recipe], f: Filters, how: str = SORTS[0]) -> list[Recipe]:
    return sort_recipes([r for r in recipes if matches(r, f)], how)


def parse_ingredient_query(text: str) -> list[str]:
    """``"farine, oeufs\\nbeurre"`` -> ``["farine", "oeufs", "beurre"]``."""
    parts = text.replace(";", ",").replace("\n", ",").split(",")
    return [p.split(":")[0].strip() for p in parts if p.strip()]


def tag_counts(recipes: list[Recipe]) -> list[tuple[str, int]]:
    """Every tag of the book with its recipe count: most used first, then A to Z."""
    counts: dict[str, int] = {}
    names: dict[str, str] = {}
    for recipe in recipes:
        for tag in recipe.tags:
            key = fold(tag)
            names.setdefault(key, tag)
            counts[key] = counts.get(key, 0) + 1
    return sorted(((names[k], n) for k, n in counts.items()), key=lambda x: (-x[1], fold(x[0])))
