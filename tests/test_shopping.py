from mamarmiton import config
from mamarmiton.models import Ingredient, Recipe
from mamarmiton.shopping import (
    AISLES, ShoppingState, aisle_of, build_list, format_amount, group_by_aisle, open_with_default_app,
    parse_amount, shopping_file, to_text,
)


def recipe(nom, pour, *ings):
    return Recipe(nom=nom, pour=pour, ingredients=[Ingredient(n, q) for n, q in ings])


def lines(items):
    return [i.label for i in items]


def test_merge_and_scale_mass():
    a = recipe("Gâteau", 4, ("farine", "500 g"), ("sucre", "200 g"))
    b = recipe("Crêpes", 2, ("Farine", "1 kg"))
    items = build_list([(a, 4), (b, 4)])  # crêpes doubled: 2 kg
    assert "2,5 kg de farine" in lines(items)
    assert "200 g de sucre" in lines(items)


def test_kg_boundary_and_decimal_comma():
    a = recipe("A", 1, ("farine", "1500 g"))
    assert lines(build_list([(a, 1)])) == ["1,5 kg de farine"]
    b = recipe("B", 1, ("sel", "500 g"), ("sel", "400 g"))
    assert lines(build_list([(b, 1)])) == ["900 g de sel"]
    c = recipe("C", 1, ("sucre", "500 mg"))
    assert lines(build_list([(c, 1)])) == ["500 mg de sucre"]


def test_volumes_merge_across_units():
    a = recipe("A", 1, ("lait", "25 cl"), ("lait", "1 dl"), ("lait", "1 l"), ("lait", "150 ml"))
    assert lines(build_list([(a, 1)])) == ["1,5 l de lait"]
    b = recipe("B", 1, ("crème", "20 cl"))
    assert lines(build_list([(b, 1)])) == ["200 ml de crème"]


def test_spoon_variants_merge():
    a = recipe("A", 1, ("huile d'olive", "2 c. à soupe"), ("huile d'olive", "1 cuillère à soupe"),
               ("huile d'olive", "1 cs"), ("sucre", "1 c. à café"), ("sucre", "2 cuillères à café"))
    items = lines(build_list([(a, 1)]))
    assert "4 c. à soupe d'huile d'olive" in items
    assert "3 c. à café de sucre" in items


def test_spoons_and_grams_stay_separate_lines():
    a = recipe("A", 1, ("sucre", "100 g"), ("sucre", "1 c. à soupe"))
    assert sorted(lines(build_list([(a, 1)]))) == ["1 c. à soupe de sucre", "100 g de sucre"]


def test_counts_plural_accents_and_oeufs():
    a = recipe("A", 4, ("oeuf", "1"), ("Oeufs", "2"), ("Œufs", "une"))
    items = build_list([(a, 4)])
    assert lines(items) == ["4 oeufs"]
    b = recipe("B", 2, ("poireau", "1"))
    assert lines(build_list([(b, 2)])) == ["1 poireau"]
    c = recipe("C", 2, ("tomates", "3"), ("Tomate", "2"))
    assert lines(build_list([(c, 2)])) == ["5 tomates"]


def test_accent_folding_in_names():
    a = recipe("A", 1, ("crème fraîche", "10 cl"), ("Creme fraiche", "10 cl"))
    assert lines(build_list([(a, 1)])) == ["200 ml de crème fraîche"]


def test_unitless_scaling_and_fractions():
    a = recipe("A", 4, ("oignon", "1/2"))
    assert lines(build_list([(a, 8)])) == ["1 oignon"]
    assert lines(build_list([(a, 4)])) == ["1/2 oignon"]


def test_named_units_pluralise():
    a = recipe("A", 2, ("ail", "1 gousse"), ("sachet de levure", ""), ("levure", "1 sachet"))
    b = recipe("B", 2, ("ail", "2 gousses"))
    items = lines(build_list([(a, 2), (b, 2)]))
    assert "3 gousses d'ail" in items
    assert "1 sachet de levure" in items


def test_no_quantity_listed_once_and_dropped_when_quantified():
    a = recipe("A", 1, ("sel", ""), ("poivre", ""), ("persil", ""))
    b = recipe("B", 1, ("Sel", ""), ("poivre", "1 pincée"))
    items = build_list([(a, 1), (b, 1)])
    assert lines(items).count("sel") == 1
    assert "1 pincée de poivre" in lines(items)
    assert "poivre" not in lines(items)
    sel = next(i for i in items if i.name.lower() == "sel")
    assert sorted(sel.recipes) == ["A", "B"]


def test_text_quantity_without_number_is_no_quantity():
    a = recipe("A", 1, ("sel", "au goût"))
    assert lines(build_list([(a, 1)])) == ["sel"]


def test_number_word_and_range():
    assert parse_amount("une pincée").dim == "unit"
    assert parse_amount("2-3 c. à soupe").value == 3
    assert parse_amount("") is None
    assert parse_amount("sel") is None


def test_empty_names_skipped():
    a = recipe("A", 1, ("", "2 g"))
    assert build_list([(a, 1)]) == []


def test_servings_scaling_uses_recipe_pour():
    a = recipe("A", 4, ("beurre", "125 g"))
    assert lines(build_list([(a, 6)])) == ["188 g de beurre"]


def test_aisles():
    assert aisle_of("tomates") == "Fruits et légumes"
    assert aisle_of("pommes de terre") == "Fruits et légumes"
    assert aisle_of("Lait demi-écrémé") == "Crèmerie"
    assert aisle_of("œufs") == "Crèmerie"
    assert aisle_of("blanc de poulet") == "Boucherie / poisson"
    assert aisle_of("farine") == "Épicerie"
    assert aisle_of("pâtes") == "Épicerie"
    assert aisle_of("pâte feuilletée") == "Crèmerie"
    assert aisle_of("baguette") == "Boulangerie"
    assert aisle_of("petits pois surgelés") == "Surgelés"
    assert aisle_of("vin blanc") == "Boissons"
    assert aisle_of("vinaigre") == "Épicerie"
    assert aisle_of("lait de coco") == "Épicerie"
    assert aisle_of("machin inconnu") == "Autres"
    assert aisle_of("huile d'olive") == "Épicerie"


def test_grouping_order_and_text():
    a = recipe("A", 1, ("farine", "500 g"), ("tomates", "3"), ("lait", "1 l"), ("truc", "1"))
    items = build_list([(a, 1)])
    groups = group_by_aisle(items)
    assert [g for g, _ in groups] == ["Fruits et légumes", "Crèmerie", "Épicerie", "Autres"]
    assert [g for g, _ in groups] == [x for x in AISLES if x in dict(groups)]
    text = to_text(items, extras=[{"text": "papier absorbant", "done": False}, {"text": "pile", "done": True}])
    assert text.startswith("Liste de courses\n")
    assert "- 3 tomates" in text and "- 500 g de farine" in text and "- papier absorbant" in text
    assert "pile" not in text
    tomato = next(i for i in items if "tomate" in i.name)
    assert "tomates" not in to_text(items, checked={tomato.key})
    assert "rien à acheter" in to_text([])


def test_format_amount():
    from fractions import Fraction
    from mamarmiton.shopping import Amount
    assert format_amount(Amount("mass", Fraction(1000))) == "1 kg"
    assert format_amount(Amount("mass", Fraction(2250))) == "2,25 kg"
    assert format_amount(Amount("vol", Fraction(2500))) == "2,5 l"
    assert format_amount(Amount("vol", Fraction(5, 2))) == "2,5 ml"
    assert format_amount(Amount("cas", Fraction(3, 2))) == "1 1/2 c. à soupe"


# ---------------------------------------------------------------- persistence
def test_state_roundtrip_and_location(isolated_data_dir):
    state = ShoppingState()
    state.add_recipe("r1", 4)
    state.add_recipe("r2", 2)
    state.add_recipe("r1", 6)  # updates, no duplicate
    state.set_checked("farine|mass", True)
    assert state.add_extra("  papier   absorbant ")
    assert not state.add_extra("   ")
    state.save()
    assert shopping_file().parent == config.data_dir()
    assert shopping_file().name == "courses.json"
    assert not (config.data_dir() / "recette.json").exists()
    loaded = ShoppingState.load()
    assert loaded.recipes == [{"id": "r1", "servings": 6}, {"id": "r2", "servings": 2}]
    assert loaded.checked == {"farine|mass"}
    assert loaded.extras == [{"text": "papier absorbant", "done": False}]
    loaded.remove_recipe("r1")
    loaded.clear()
    assert loaded.to_dict()["recipes"] == [] and not loaded.checked and not loaded.extras


def test_state_tolerates_garbage(isolated_data_dir):
    shopping_file().write_text("{not json", encoding="utf-8")
    assert ShoppingState.load().recipes == []
    s = ShoppingState.from_dict({"recipes": [{"id": "a", "servings": "x"}, {"id": "a"}, 3, {"servings": 2}],
                                 "checked": [1, "k"], "extras": [{"text": ""}, {"text": "ok", "done": 1}]})
    assert s.recipes == [{"id": "a", "servings": 4}]
    assert s.checked == {"k"} and s.extras == [{"text": "ok", "done": True}]
    assert ShoppingState.from_dict([]).recipes == []


def test_prune():
    s = ShoppingState()
    s.add_recipe("a", 1)
    s.add_recipe("b", 1)
    s.checked = {"x|mass", "y|none"}
    s.prune({"a"}, {"x|mass"})
    assert [e["id"] for e in s.recipes] == ["a"] and s.checked == {"x|mass"}


def test_open_with_default_app_guarded(monkeypatch, tmp_path):
    import subprocess
    def boom(*a, **k):
        raise FileNotFoundError("xdg-open")
    monkeypatch.setattr(subprocess, "Popen", boom)
    monkeypatch.delattr("os.startfile", raising=False)
    assert open_with_default_app(tmp_path / "x.txt") is False
