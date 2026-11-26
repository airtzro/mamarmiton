import pytest

from mamarmiton.models import Recipe, format_minutes, parse_ingredient_lines, parse_minutes


@pytest.mark.parametrize("value, expected", [
    (45, 45), ("45", 45), ("45 min", 45), ("1h30", 90), ("1 h 15 min", 75), ("2h", 120),
    ("", 0), (None, 0), ("environ 20 minutes", 20), ("1,5 h", 90),
])
def test_parse_minutes(value, expected):
    assert parse_minutes(value) == expected


def test_format_minutes():
    assert format_minutes(45) == "45 min"
    assert format_minutes(60) == "1 h"
    assert format_minutes(95) == "1 h 35"


def test_from_v3_dict_with_string_times_and_odd_values():
    r = Recipe.from_dict({
        "nom": "Tarte", "temps": "", "temps_cuisson": "30", "pour": "6 personnes",
        "type_plat": "dessert", "type_cuisson": "four",
        "ingredients": [{"nom": "farine", "quantité": "200 g"}, {"nom": "", "quantité": ""}, "sucre"],
        "etapes": ["Mélanger", "  ", "Cuire"],
    })
    assert (r.temps, r.temps_cuisson, r.pour) == (0, 30, 6)
    assert (r.type_plat, r.type_cuisson) == ("Dessert", "Four")
    assert [i.nom for i in r.ingredients] == ["farine", "sucre"]
    assert r.etapes == ["Mélanger", "Cuire"]
    assert r.id


def test_round_trip_keeps_v3_keys():
    r = Recipe(nom="X")
    d = r.to_dict()
    assert "quantité" not in d  # only on ingredients
    r.ingredients = parse_ingredient_lines("Farine: 500 g")
    assert r.to_dict()["ingredients"] == [{"nom": "Farine", "quantité": "500 g"}]
    assert Recipe.from_dict(r.to_dict()) == r


def test_from_dict_rejects_non_dict():
    with pytest.raises(ValueError):
        Recipe.from_dict(None)


def test_parse_ingredient_lines():
    items = parse_ingredient_lines("Farine: 500 g\n3 oeufs\n\n- Sel\n")
    assert [(i.nom, i.quantite) for i in items] == [("Farine", "500 g"), ("oeufs", "3"), ("Sel", "")]
