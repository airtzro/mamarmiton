from PIL import Image

from mamarmiton import export
from mamarmiton.models import Ingredient, Recipe


def short_recipe() -> Recipe:
    return Recipe(
        nom="Crêpes", temps=10, temps_cuisson=20, pour=4, type_plat="Dessert", type_cuisson="Poêle",
        ingredients=[Ingredient("farine", "250 g"), Ingredient("lait", "50 cl"), Ingredient("oeufs", "3")],
        etapes=["Mélanger.", "Laisser reposer.", "Cuire."], notes="Très bon.", source="mamie.pdf",
    )


def long_recipe() -> Recipe:
    return Recipe(
        nom="Un très long titre " * 6, temps=90, temps_cuisson=185, pour=1,
        ingredients=[Ingredient(f"ingrédient numéro {i} avec un nom assez long " * 2, f"{i} g") for i in range(60)],
        etapes=[("Remuer doucement en versant le liquide. " * 12).strip() for _ in range(40)],
        notes="Une note.\n" * 40, source="x" * 300,
    )


def test_short_recipe_is_one_a4_page():
    pages = export.render_recipe_pages(short_recipe())
    assert len(pages) == 1
    assert pages[0].size == (1240, 1754)
    assert pages[0].mode == "RGB"


def test_long_recipe_overflows_to_several_pages():
    pages = export.render_recipe_pages(long_recipe(), dpi=72)
    assert len(pages) >= 3


def test_pdf_file_is_valid_and_page_count_matches(tmp_path):
    out = tmp_path / "sub" / "r.pdf"
    n = export.export_pdf(long_recipe(), out, dpi=72)
    data = out.read_bytes()
    assert data.startswith(b"%PDF")
    assert n == len(export.render_recipe_pages(long_recipe(), dpi=72)) >= 3
    assert f"/Count {n}".encode() in data
    assert not list(out.parent.glob("*.tmp"))


def test_export_many_starts_each_recipe_on_a_new_page(tmp_path):
    a, b = short_recipe(), short_recipe()
    out = tmp_path / "carnet.pdf"
    n = export.export_many([a, b], out, lambda r: None, dpi=72)
    assert n == 2
    assert out.read_bytes().startswith(b"%PDF")


def test_export_many_rejects_empty_list(tmp_path):
    import pytest

    with pytest.raises(ValueError):
        export.export_many([], tmp_path / "x.pdf")


def test_long_words_wrap_without_crashing():
    r = Recipe(nom="W" * 200, ingredients=[Ingredient("a" * 300, "b" * 200)], etapes=["z" * 500], notes="n" * 400)
    pages = export.render_recipe_pages(r, dpi=72)
    assert pages
    font = export._font("body", 20)
    for line in export.wrap_text("x" * 500 + " fin", font, 200):
        assert font.getlength(line) <= 200


def test_empty_fields_work(tmp_path):
    r = Recipe(nom="", pour=0, type_plat="", type_cuisson="")
    assert len(export.render_recipe_pages(r)) == 1
    export.export_pdf(r, tmp_path / "e.pdf")
    assert (tmp_path / "e.pdf").read_bytes().startswith(b"%PDF")


def test_photo_is_used_and_bad_photo_ignored(tmp_path):
    photo = tmp_path / "p.png"
    Image.new("RGB", (400, 900), (200, 50, 50)).save(photo)
    with_photo = export.render_recipe_pages(short_recipe(), photo)[0]
    without = export.render_recipe_pages(short_recipe())[0]
    assert with_photo.tobytes() != without.tobytes()
    bad = tmp_path / "bad.png"
    bad.write_bytes(b"not an image")
    assert export.render_recipe_pages(short_recipe(), bad)[0].tobytes() == without.tobytes()


def test_falls_back_to_default_font_when_ttf_missing(monkeypatch, tmp_path):
    export._font.cache_clear()
    monkeypatch.setattr(export.config, "resource_path", lambda rel: tmp_path / "nope" / rel)
    try:
        assert export.render_recipe_pages(short_recipe(), dpi=72)
    finally:
        export._font.cache_clear()


def test_lines_never_cross_the_bottom_margin():
    pages = export.render_recipe_pages(long_recipe(), dpi=72)
    for page in pages[:-1]:
        # the band between the content area and the footer rule stays white
        k = 72 / 25.4
        band = page.crop((0, int((297 - 22) * k), page.width, int((297 - 20) * k)))
        assert band.convert("L").getextrema()[0] == 255


def test_slugify():
    assert export.slugify("Tarte à la crème brûlée !") == "tarte-a-la-creme-brulee"
    assert export.slugify("???") == "recette"
