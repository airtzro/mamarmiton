from mamarmiton.importers import heuristic
from mamarmiton.importers.llm import extract_json
from mamarmiton.importers.pipeline import merge
from mamarmiton.models import Recipe

SAMPLE = """GÂTEAU AU CHOCOLAT
Pour 6 personnes
Préparation : 20 min
Cuisson : 25 min
Ingrédients :
200 g de chocolat
125 g de beurre
4 oeufs
1 sachet de levure
Préparation :
1. Casse le chocolat en petits morceaux dans un
saladier.
2. Fais fondre au micro-ondes.
3. Fais cuire au four à 180°C.
lejournaldunemaitresse.eklablog.fr
"""


def test_heuristic_parser():
    r = heuristic.parse_recipe_text(SAMPLE)
    assert r.nom == "Gâteau au chocolat"
    assert (r.temps, r.temps_cuisson, r.pour) == (20, 25, 6)
    assert (r.type_plat, r.type_cuisson) == ("Dessert", "Four")
    assert [(i.nom, i.quantite) for i in r.ingredients] == [
        ("Chocolat", "200 g"), ("Beurre", "125 g"), ("Oeufs", "4"), ("Levure", "1 sachet")]
    assert r.etapes == [
        "Casse le chocolat en petits morceaux dans un saladier.",
        "Fais fondre au micro-ondes.",
        "Fais cuire au four à 180°C.",
    ]


def test_heuristic_splits_multi_column_lines():
    items = heuristic.parse_ingredients(["125 g de beurre 4 oeufs 200 g de sucre"])
    assert [(i.nom, i.quantite) for i in items] == [("Beurre", "125 g"), ("Oeufs", "4"), ("Sucre", "200 g")]


def test_cooking_time_from_steps():
    assert heuristic.extract_times("Fais cuire à 180°C pendant 25 minutes.") == (0, 25)


def test_extract_json_tolerates_noise():
    assert extract_json('Voici:\n```json\n{"nom": "a {b}", "x": [1]}\n```') == {"nom": "a {b}", "x": [1]}
    assert extract_json("pas de json") is None
    assert extract_json('{"nom": ') is None


def test_merge_fills_gaps_from_rules():
    ai = Recipe(nom="test", etapes=["Cuire"])
    rules = Recipe(nom="Tarte", temps=10, etapes=["x"], ingredients=heuristic.parse_ingredients(["2 oeufs"]))
    merged = merge(ai, rules)
    assert merged.nom == "Tarte"
    assert merged.etapes == ["Cuire"]
    assert merged.temps == 10
    assert merged.ingredients[0].nom == "Oeufs"


def test_merge_unswaps_and_dedupes_ai_ingredients():
    from mamarmiton.models import Ingredient

    ai = Recipe(nom="Gâteau", ingredients=[Ingredient("100 g", "farine"), Ingredient("100 g", "farine"),
                                           Ingredient("oeufs", "4")], etapes=["a", "a", "b"])
    merged = merge(ai, Recipe(nom="x"))
    assert [(i.nom, i.quantite) for i in merged.ingredients] == [("farine", "100 g"), ("oeufs", "4")]
    assert merged.etapes == ["a", "b"]


def test_merge_prefers_stated_facts_over_invented_ones():
    from mamarmiton.models import Ingredient

    source = "Gâteau\nPour 6 personnes\nIngrédients :\n100 g de farine\n4 oeufs\nCuire à 180°C pendant 25 minutes."
    rules = heuristic.parse_recipe_text(source)
    ai = Recipe(nom="Gâteau", temps=10, temps_cuisson=180, pour=1,
                ingredients=[Ingredient("farine", "400 g"), Ingredient("oeufs", "4"), Ingredient("sel", "3 g")])
    merged = merge(ai, rules, source)
    assert (merged.temps_cuisson, merged.pour) == (25, 6)
    assert [(i.nom, i.quantite) for i in merged.ingredients] == [("farine", "100 g"), ("oeufs", "4"), ("sel", "3 g")]
