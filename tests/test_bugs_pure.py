"""Regression tests (no window) for bugs found by the desktop bug hunt."""

import json

from mamarmiton.importers import web
from mamarmiton.models import Recipe
from mamarmiton.storage import RecipeStore


def test_store_reads_file_saved_with_bom(tmp_path):
    """Notepad adds a BOM when a file is re-saved: that must not look like a corrupt file."""
    path = tmp_path / "recette.json"
    path.write_bytes(b"\xef\xbb\xbf" + json.dumps([{"nom": "Tarte"}]).encode("utf-8"))
    store = RecipeStore(path, tmp_path / "img")
    assert [r.nom for r in store.all()] == ["Tarte"]
    assert store.warnings == []


def test_import_from_reads_bom(tmp_path):
    path = tmp_path / "in.json"
    path.write_bytes(b"\xef\xbb\xbf" + json.dumps([{"nom": "Tarte"}]).encode("utf-8"))
    store = RecipeStore(tmp_path / "recette.json", tmp_path / "img")
    assert store.import_from(path) == 1


def test_normalize_url_percent_encodes_accents():
    url = web.normalize_url("https://www.marmiton.org/recettes/crème-brûlée.aspx?q=é")
    assert url.isascii()
    assert url == "https://www.marmiton.org/recettes/cr%C3%A8me-br%C3%BBl%C3%A9e.aspx?q=%C3%A9"


def test_normalize_url_keeps_existing_escapes():
    url = "https://exemple.fr/a%20b?x=1&y=2#frag"
    assert web.normalize_url(url) == url


def test_normalize_url_leaves_host_alone():
    assert web.normalize_url("https://café.fr/recette") == "https://café.fr/recette"


def test_pdf_export_survives_line_break_in_quantity():
    from mamarmiton import export
    from mamarmiton.models import Ingredient
    recipe = Recipe(nom="Tarte", ingredients=[Ingredient("farine", "100 g\nenviron"), Ingredient("sel", "")])
    assert len(export.render_recipe_pages(recipe, dpi=40)) == 1


def test_import_url_removes_temp_folder_when_photo_fails(monkeypatch, tmp_path):
    page = ('<script type="application/ld+json">{"@type":"Recipe","name":"T","image":"https://x.fr/a.jpg",'
            '"recipeIngredient":["1 oeuf"]}</script>')
    made = []
    real = web.tempfile.mkdtemp

    def fake_mkdtemp(*a, **k):
        d = real(*a, dir=str(tmp_path), **{kk: v for kk, v in k.items() if kk != "dir"})
        made.append(d)
        return d

    monkeypatch.setattr(web.tempfile, "mkdtemp", fake_mkdtemp)
    monkeypatch.setattr(web, "fetch_html", lambda url: page)
    monkeypatch.setattr(web, "download_image", lambda url, folder: None)
    result = web.import_url("https://x.fr/r")
    assert result.images == [] and result.warnings
    assert made and not any(__import__("os").path.exists(d) for d in made)
