import pytest

from mamarmiton.quantities import format_number, scale_quantity, split_quantity_and_name


@pytest.mark.parametrize("qty, factor, expected", [
    ("200 g", 1.5, "300 g"),
    ("200 g", 0.5, "100 g"),
    ("1/2 sachet", 2, "1 sachet"),
    ("1 1/2 c. à s.", 2, "3 c. à s."),
    ("½ citron", 2, "1 citron"),
    ("2,5 kg", 2, "5 kg"),
    ("0,5 l", 1.5, "0,75 l"),
    ("une pincée", 2, "2 pincée"),
    ("2-3 oeufs", 2, "4-6 oeufs"),
    ("sel", 3, "sel"),
    ("", 2, ""),
    ("4", 1, "4"),
])
def test_scale_quantity(qty, factor, expected):
    assert scale_quantity(qty, factor) == expected


def test_scaling_does_not_compound():
    # v3 rounded each +/- click and drifted; scaling is now always from the original
    assert scale_quantity("125 g", 9 / 6) == "188 g"
    assert scale_quantity("125 g", 6 / 6) == "125 g"


def test_format_number():
    assert format_number(0.25) == "1/4"
    assert format_number(1.5) == "1 1/2"
    assert format_number(12.4) == "12"
    assert format_number(2.5, decimal=True) == "2,5"


@pytest.mark.parametrize("line, expected", [
    ("500 g de farine", ("500 g", "farine")),
    ("3 oeufs", ("3", "oeufs")),
    ("1 sachet de levure", ("1 sachet", "levure")),
    ("2 cuillères à soupe d'huile", ("2 cuillères à soupe", "huile")),
    ("farine 500g", ("500g", "farine")),
    ("une pincée de sel", ("une pincée", "sel")),
    ("Sel", ("", "Sel")),
])
def test_split_quantity_and_name(line, expected):
    assert split_quantity_and_name(line) == expected


@pytest.mark.parametrize("qty, expected", [
    ("500 g", ("500", "g")),
    ("1/2 sachet", ("1/2", "sachet")),
    ("2-3", ("2-3", "")),
    ("2,5 kg", ("2,5", "kg")),
    ("une pincée", ("1", "pincée")),
    ("½ citron", ("1/2", "citron")),
    ("", ("", "")),
    ("un peu", ("1", "peu")),
])
def test_split_amount_unit(qty, expected):
    from mamarmiton.quantities import join_amount_unit, split_amount_unit

    assert split_amount_unit(qty) == expected
    amount, unit = expected
    if amount:
        assert scale_quantity(join_amount_unit(amount, unit), 2) != join_amount_unit(amount, unit)


def test_join_amount_unit():
    from mamarmiton.quantities import join_amount_unit, scale_quantity

    assert join_amount_unit("500", "g") == "500 g"
    assert join_amount_unit("", "") == ""
    assert join_amount_unit("3", "") == "3"
    assert scale_quantity(join_amount_unit("2", "cuil. à soupe"), 2) == "4 cuil. à soupe"
