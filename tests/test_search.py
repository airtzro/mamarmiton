from mamarmiton.models import Ingredient, Recipe
from mamarmiton.search import Filters, parse_ingredient_query, search, tag_counts

RECIPES = [
    Recipe(nom="Gâteau au chocolat", temps=20, temps_cuisson=25, type_plat="Dessert", type_cuisson="Four",
           ingredients=[Ingredient("Chocolat noir", "200 g"), Ingredient("Oeufs", "4")]),
    Recipe(nom="Quiche lorraine", temps=15, temps_cuisson=35, type_plat="Plat", type_cuisson="Four", favori=True,
           ingredients=[Ingredient("Oeuf", "3"), Ingredient("Lardons", "200 g")]),
    Recipe(nom="Pain de campagne", temps=180, temps_cuisson=40, type_plat="Pain", type_cuisson="Four",
           ingredients=[Ingredient("Farine", "500 g")]),
]


def names(f, how="Nom (A → Z)"):
    return [r.nom for r in search(RECIPES, f, how)]


def test_text_ignores_case_and_accents():
    assert names(Filters(text="GATEAU")) == ["Gâteau au chocolat"]
    assert names(Filters(text="lardons")) == ["Quiche lorraine"]


def test_ingredients_partial_and_plural():
    assert names(Filters(ingredients=["oeufs"])) == ["Gâteau au chocolat", "Quiche lorraine"]
    assert names(Filters(ingredients=["chocolat"])) == ["Gâteau au chocolat"]
    assert names(Filters(ingredients=["oeuf", "lardon"])) == ["Quiche lorraine"]


def test_time_and_types():
    assert names(Filters(max_minutes=60)) == ["Gâteau au chocolat", "Quiche lorraine"]
    assert names(Filters(type_plat="Pain")) == ["Pain de campagne"]
    assert names(Filters(favorites_only=True)) == ["Quiche lorraine"]


def test_sorting():
    assert names(Filters(), "Favoris d'abord")[0] == "Quiche lorraine"
    assert names(Filters(), "Plus rapides")[-1] == "Pain de campagne"


def test_parse_ingredient_query():
    assert parse_ingredient_query("farine, oeufs\nbeurre: 100 g") == ["farine", "oeufs", "beurre"]


def test_tags_filter_needs_all_and_ignores_accents():
    a, b = RECIPES[0], RECIPES[1]
    a.tags, b.tags = ["Noël", "rapide"], ["rapide"]
    try:
        assert names(Filters(tags=["rapide"])) == ["Gâteau au chocolat", "Quiche lorraine"]
        assert names(Filters(tags=["NOEL", "Rapide"])) == ["Gâteau au chocolat"]
        assert names(Filters(tags=["inconnu"])) == []
        assert not Filters(tags=["rapide"]).is_empty()
        assert names(Filters(text="noel")) == ["Gâteau au chocolat"]  # free text finds tags too
        assert tag_counts(RECIPES) == [("rapide", 2), ("Noël", 1)]
    finally:
        a.tags, b.tags = [], []


def test_text_finds_the_source():
    mamie = Recipe(nom="Tarte", source="Mamie Jeanne")
    site = Recipe(nom="Soupe", source="https://www.marmiton.org/soupe")
    other = Recipe(nom="Gratin")
    assert search([mamie, site, other], Filters(text="jeanne")) == [mamie]
    assert search([mamie, site, other], Filters(text="marmiton")) == [site]
