import json

import pytest

from mamarmiton.importers import web
from mamarmiton.importers.web import WebImportError, parse_duration, parse_recipe_html


def page(*blocks: dict | list) -> str:
    scripts = "".join(f'<script type="application/ld+json">{json.dumps(b)}</script>' for b in blocks)
    return f"<html><head><title>x</title>{scripts}</head><body><p>hello</p></body></html>"


MARMITON_LIKE = """<html><head>
<script type="application/ld+json">{"@context":"https://schema.org","@graph":[
 {"@type":"WebSite","name":"Site"},
 {"@type":["Recipe","NewsArticle"],"name":"Tarte aux pommes &amp; cannelle",
  "image":[{"@type":"ImageObject","url":"/img/tarte.jpg"},"https://cdn.example.com/b.jpg"],
  "recipeCategory":"Dessert","recipeYield":["6","6 personnes"],
  "prepTime":"PT20M","cookTime":"PT1H","totalTime":"PT1H20M",
  "recipeIngredient":["250 g de farine","3 pommes","1 pincée de sel"],
  "recipeInstructions":[{"@type":"HowToStep","text":"Préchauffer le four à 180°C."},
                        {"@type":"HowToStep","text":"Étaler la pâte.<br>Garnir."}]}]}
</script></head><body></body></html>"""

SEVEN_FIFTY_LIKE = page({
    "@context": "http://schema.org", "@type": "Recipe", "name": "Gratin dauphinois",
    "image": {"@type": "ImageObject", "url": "https://img.example.com/g.jpg"},
    "recipeCategory": "Plat principal", "recipeYield": 4,
    "totalTime": "PT1H30M", "prepTime": "PT15M",
    "recipeIngredient": ["800 g de pommes de terre", "50 cl de crème"],
    "recipeInstructions": [
        {"@type": "HowToSection", "name": "Préparation", "itemListElement": [
            {"@type": "HowToStep", "text": "Éplucher les pommes de terre."},
            {"@type": "HowToStep", "text": "Les couper en rondelles."}]},
        {"@type": "HowToSection", "name": "Cuisson", "itemListElement": [
            {"@type": "HowToStep", "text": "Enfourner 1 h à 160°C."}]},
    ]})


def test_graph_marmiton_like():
    r = parse_recipe_html(MARMITON_LIKE, "https://www.example.com/recette/tarte")
    assert r.nom == "Tarte aux pommes & cannelle"
    assert (r.temps, r.temps_cuisson, r.pour) == (20, 60, 6)
    assert r.type_plat == "Dessert"
    assert r.type_cuisson == "Four"
    assert [(i.nom, i.quantite) for i in r.ingredients] == [
        ("farine", "250 g"), ("pommes", "3"), ("sel", "1 pincée")]
    assert r.etapes == ["Préchauffer le four à 180°C.", "Étaler la pâte.", "Garnir."]
    assert r.source == "https://www.example.com/recette/tarte"


def test_howto_sections_and_total_time():
    r = parse_recipe_html(SEVEN_FIFTY_LIKE, "https://x.test/")
    assert r.etapes == ["Éplucher les pommes de terre.", "Les couper en rondelles.", "Enfourner 1 h à 160°C."]
    assert r.temps == 15 and r.temps_cuisson == 75  # total minus prep
    assert r.type_plat == "Plat"
    assert r.pour == 4


def test_minimal_recipe_with_string_instructions():
    r = parse_recipe_html(page({"@type": "Recipe", "name": "Omelette",
                                "recipeIngredient": ["3 oeufs"],
                                "recipeInstructions": "Battre les oeufs.\nCuire à la poêle."}), "")
    assert r.nom == "Omelette"
    assert r.etapes == ["Battre les oeufs.", "Cuire à la poêle."]
    assert r.type_cuisson == "Poêle"
    assert r.type_plat == "Plat" and r.pour == 4 and r.temps == 0


def test_recipe_in_top_level_list():
    html = page([{"@type": "BreadcrumbList"}, {"@type": "Recipe", "name": "Crêpes",
                                              "recipeIngredient": ["farine"], "recipeInstructions": ["Mélanger"]}])
    assert parse_recipe_html(html, "").nom == "Crêpes"


def test_image_urls_resolved():
    recipe, images = web._parse(MARMITON_LIKE, "https://www.example.com/recette/tarte")
    assert images == ["https://www.example.com/img/tarte.jpg", "https://cdn.example.com/b.jpg"]


def test_no_recipe_raises_french_error():
    with pytest.raises(WebImportError, match="Aucune recette"):
        parse_recipe_html(page({"@type": "WebSite", "name": "Blog"}), "https://x.test")
    with pytest.raises(WebImportError, match="Aucune recette"):
        parse_recipe_html("<html><body>rien</body></html>", "")


def test_broken_json_block_is_skipped():
    html = '<script type="application/ld+json">{oops</script>' + page({"@type": "Recipe", "name": "Soupe",
                                                                     "recipeIngredient": ["eau"]})
    assert parse_recipe_html(html, "").nom == "Soupe"


@pytest.mark.parametrize("value,minutes", [("PT45M", 45), ("PT1H30M", 90), ("P0DT1H", 60), ("PT90M", 90),
                                           ("P1DT2H", 1560), ("", 0), ("bientôt", 0), (None, 0), (25, 25)])
def test_parse_duration(value, minutes):
    assert parse_duration(value) == minutes


@pytest.mark.parametrize("text", ["", "pas une adresse", "ftp://x.test/a", "file:///etc/passwd", "javascript:alert(1)"])
def test_bad_urls(text):
    with pytest.raises(WebImportError, match="adresse"):
        web.normalize_url(text)


def test_url_without_scheme_gets_https():
    assert web.normalize_url(" marmiton.org/recettes/x ") == "https://marmiton.org/recettes/x"


def test_import_url_uses_fetch_and_downloads_photo(monkeypatch, tmp_path):
    monkeypatch.setattr(web, "fetch_html", lambda url: MARMITON_LIKE)
    seen = []

    def fake_download(url, folder):
        seen.append(url)
        p = folder / "photo.jpg"
        p.write_bytes(b"x")
        return p

    monkeypatch.setattr(web, "download_image", fake_download)
    result = web.import_url("https://www.example.com/recette/tarte")
    assert result.recipe.nom.startswith("Tarte")
    assert seen == ["https://www.example.com/img/tarte.jpg"]
    assert result.images and result.images[0].is_file()


def test_network_error_message(monkeypatch):
    import urllib.error

    def boom(*a, **k):
        raise urllib.error.URLError("dns")

    monkeypatch.setattr(web.urllib.request, "urlopen", boom)
    with pytest.raises(WebImportError, match="Impossible de joindre"):
        web.import_url("https://nowhere.invalid/")


def test_download_image_refuses_local_schemes(tmp_path):
    from mamarmiton.importers.web import download_image

    secret = tmp_path / "secret.png"
    from PIL import Image
    Image.new("RGB", (4, 4)).save(secret)
    assert download_image(secret.as_uri(), tmp_path / "out") is None
