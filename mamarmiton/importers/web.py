"""Recipe website URL -> Recipe, reading the schema.org JSON-LD most sites embed."""

from __future__ import annotations

import html
import json
import logging
import re
import shutil
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable

from mamarmiton import tls
from mamarmiton.importers.pipeline import ImportResult
from mamarmiton.models import (COOKING_TYPES, DEFAULT_COOKING, DEFAULT_DISH, DISH_TYPES, Ingredient, Recipe,
                               fold, parse_int)
from mamarmiton.quantities import split_quantity_and_name

log = logging.getLogger(__name__)

TIMEOUT = 15  # seconds
MAX_PAGE_BYTES = 5 * 1024 * 1024
MAX_IMAGE_BYTES = 10 * 1024 * 1024
USER_AGENT = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/124.0 Safari/537.36")

Progress = Callable[[str], None]


class WebImportError(ValueError):
    """An import failure whose message is meant to be shown to the user (French)."""


# --------------------------------------------------------------------------
# Fetching
# --------------------------------------------------------------------------
def normalize_url(text: str) -> str:
    url = (text or "").strip()
    if url and "://" not in url and " " not in url and "." in url:
        url = "https://" + url  # "marmiton.org/..." typed without the scheme
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.netloc or " " in url:
        raise WebImportError("Ce n'est pas une adresse de site valide. Copiez l'adresse complète de la page "
                             "(elle commence par « https:// »).")
    # accents typed or pasted as-is would make urllib fail with an encoding error
    safe = "%:/?#[]@!$&'()*+,;=-._~"
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, urllib.parse.quote(parts.path, safe=safe),
                                    urllib.parse.quote(parts.query, safe=safe),
                                    urllib.parse.quote(parts.fragment, safe=safe)))


def _read_limited(response, limit: int) -> bytes:
    data = response.read(limit + 1)
    if len(data) > limit:
        raise WebImportError("Cette page est trop volumineuse pour être lue.")
    return data


def _open(url: str, accept: str, limit: int) -> tuple[bytes, str]:
    request = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT, "Accept": accept, "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.5"})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT, context=tls.context()) as response:
            data = _read_limited(response, limit)
            return data, response.headers.get_content_charset() or ""
    except WebImportError:
        raise
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403, 429):
            raise WebImportError("Ce site refuse l'accès automatique à cette page. "
                                 "Essayez de l'enregistrer en PDF, puis d'importer le PDF.") from exc
        if exc.code == 404:
            raise WebImportError("Cette page n'existe pas (erreur 404). Vérifiez l'adresse.") from exc
        raise WebImportError(f"Le site a répondu par une erreur ({exc.code}).") from exc
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        raise WebImportError("Impossible de joindre le site. Vérifiez l'adresse et votre connexion "
                             "internet, puis réessayez.") from exc


def fetch_html(url: str) -> str:
    data, charset = _open(url, "text/html,application/xhtml+xml;q=0.9,*/*;q=0.5", MAX_PAGE_BYTES)
    if not charset:
        match = re.search(rb"<meta[^>]+charset=[\"']?([\w-]+)", data[:4096], re.I)
        charset = match.group(1).decode("ascii", "ignore") if match else "utf-8"
    try:
        return data.decode(charset, errors="replace")
    except LookupError:
        return data.decode("utf-8", errors="replace")


def download_image(url: str, folder: Path) -> Path | None:
    """Fetch the recipe photo into ``folder``; returns None when it is not a usable image."""
    if urllib.parse.urlsplit(url).scheme not in ("http", "https"):
        return None  # never let a page point us at file:// or other local schemes
    try:
        data, _ = _open(url, "image/*", MAX_IMAGE_BYTES)
        from io import BytesIO

        from PIL import Image

        with Image.open(BytesIO(data)) as img:
            img.verify()
            ext = {"JPEG": ".jpg", "PNG": ".png", "WEBP": ".webp", "GIF": ".gif"}.get(img.format or "")
        if not ext:
            return None
        path = folder / f"photo{ext}"
        path.write_bytes(data)
        return path
    except Exception:
        log.warning("Could not download recipe image %s", url, exc_info=True)
        return None


# --------------------------------------------------------------------------
# JSON-LD extraction
# --------------------------------------------------------------------------
class _LdJsonCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.blocks: list[str] = []
        self._current: list[str] | None = None

    def handle_starttag(self, tag, attrs):
        if tag == "script" and "ld+json" in (dict(attrs).get("type") or "").lower():
            self._current = []

    def handle_endtag(self, tag):
        if tag == "script" and self._current is not None:
            self.blocks.append("".join(self._current))
            self._current = None

    def handle_data(self, data):
        if self._current is not None:
            self._current.append(data)


def _json_blocks(page: str) -> list[Any]:
    collector = _LdJsonCollector()
    try:
        collector.feed(page)
        collector.close()
    except Exception:
        log.warning("HTML parsing stopped early", exc_info=True)
    found = []
    for raw in collector.blocks:
        raw = raw.strip().removeprefix("<!--").removesuffix("-->").strip()
        try:
            found.append(json.loads(raw, strict=False))
        except ValueError:
            continue
    return found


def _is_recipe(node: Any) -> bool:
    if not isinstance(node, dict):
        return False
    kind = node.get("@type")
    kinds = kind if isinstance(kind, list) else [kind]
    return any(isinstance(k, str) and k.rsplit("/", 1)[-1].casefold() == "recipe" for k in kinds)


def _find_recipe(node: Any, depth: int = 0) -> dict | None:
    if depth > 6:
        return None
    if _is_recipe(node):
        return node
    if isinstance(node, list):
        for item in node:
            hit = _find_recipe(item, depth + 1)
            if hit:
                return hit
    elif isinstance(node, dict):
        for key in ("@graph", "mainEntity", "mainEntityOfPage", "itemListElement", "item"):
            if key in node:
                hit = _find_recipe(node[key], depth + 1)
                if hit:
                    return hit
    return None


# --------------------------------------------------------------------------
# Field conversion
# --------------------------------------------------------------------------
_TAGS = re.compile(r"<[^>]+>")


def _text(value: Any) -> str:
    """Plain, single-spaced text from a JSON-LD value (strings may contain HTML)."""
    if value is None or isinstance(value, bool):
        return ""
    if isinstance(value, dict):
        value = value.get("text") or value.get("name") or ""
    value = html.unescape(_TAGS.sub(" ", str(value)))
    return re.sub(r"[ \t\r\f\v\xa0]+", " ", value).strip()


_DURATION = re.compile(
    r"^P(?:(\d+(?:[.,]\d+)?)W)?(?:(\d+(?:[.,]\d+)?)D)?"
    r"(?:T(?:(\d+(?:[.,]\d+)?)H)?(?:(\d+(?:[.,]\d+)?)M)?(?:(\d+(?:[.,]\d+)?)S)?)?$", re.I)


def parse_duration(value: Any) -> int:
    """ISO-8601 duration (``PT1H30M``, ``P0DT45M``) -> whole minutes; 0 when unreadable."""
    if isinstance(value, (list, tuple)):
        value = value[0] if value else ""
    if isinstance(value, bool) or value is None:
        return 0
    if isinstance(value, (int, float)):
        return max(0, int(value))
    match = _DURATION.match(str(value).strip().replace(" ", ""))
    if not match:
        return 0
    weeks, days, hours, minutes, seconds = (float((g or "0").replace(",", ".")) for g in match.groups())
    return int(round(weeks * 10080 + days * 1440 + hours * 60 + minutes + seconds / 60))


def _steps(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        text = html.unescape(re.sub(r"(?i)<\s*(br|/p|/li)\s*/?>", "\n", value))
        lines = [re.sub(r"\s+", " ", _TAGS.sub(" ", line)).strip() for line in text.splitlines()]
        return [line for line in lines if line]
    if isinstance(value, list):
        return [s for item in value for s in _steps(item)]
    if isinstance(value, dict):
        nested = value.get("itemListElement")
        if nested is not None and (_text(value.get("text")) == "" or "HowToSection" in str(value.get("@type"))):
            return _steps(nested)
        return _steps(value.get("text") or value.get("name"))
    return []


def _servings(value: Any) -> int:
    if isinstance(value, list):
        # ["4", "4 personnes"]: the first entry with a number wins
        for item in value:
            n = parse_int(item, default=0, minimum=1)
            if n:
                return n
        return 4
    return parse_int(value, default=4, minimum=1) or 4


def _images(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return _images(value.get("url") or value.get("contentUrl"))
    if isinstance(value, list):
        return [u for item in value for u in _images(item)]
    return []


def _keywords(value: Any) -> str:
    if isinstance(value, list):
        return " ".join(_keywords(v) for v in value)
    return _text(value)


def guess_dish_type(category: str, name: str = "") -> str:
    text = fold(category)
    if not text:
        return DEFAULT_DISH
    for keyword, dish in (("dessert", "Dessert"), ("gateau", "Dessert"), ("sucre", "Dessert"),
                          ("entree", "Entrée"), ("apero", "Entrée"), ("starter", "Entrée"),
                          ("accompagnement", "Accompagnement"), ("garniture", "Accompagnement"),
                          ("pain", "Pain"), ("brioche", "Pain"), ("viennoiserie", "Pain"),
                          ("plat", "Plat"), ("main", "Plat")):
        if keyword in text:
            return dish
    return DEFAULT_DISH


def guess_cooking_type(steps: list[str]) -> str:
    text = fold(" ".join(steps))
    for keyword, cooking in (("four", "Four"), ("poele", "Poêle"), ("sauteuse", "Poêle"),
                             ("casserole", "Casserole"), ("cocotte", "Casserole"), ("vapeur", "Vapeur"),
                             ("micro-onde", "Micro-ondes"), ("micro onde", "Micro-ondes")):
        if re.search(rf"\b{keyword}", text):
            return cooking
    return DEFAULT_COOKING


def _ingredient(line: str) -> Ingredient | None:
    line = _text(line)
    if not line:
        return None
    qty, name = split_quantity_and_name(line)
    return Ingredient(name or line, qty)


def recipe_from_ld(node: dict, source: str = "") -> tuple[Recipe, list[str]]:
    """Build a Recipe from a schema.org Recipe node; also returns the photo URLs."""
    raw_ings = node.get("recipeIngredient", node.get("ingredients")) or []
    if isinstance(raw_ings, str):
        raw_ings = raw_ings.splitlines()
    ingredients = [i for i in (_ingredient(x) for x in raw_ings) if i]

    steps = _steps(node.get("recipeInstructions"))
    prep = parse_duration(node.get("prepTime"))
    cook = parse_duration(node.get("cookTime"))
    total = parse_duration(node.get("totalTime"))
    if not cook and total > prep:
        cook = total - prep  # only the total is given: show the rest as cooking time
    elif not prep and not cook:
        prep = total

    category = _keywords(node.get("recipeCategory"))
    recipe = Recipe(
        nom=_text(node.get("name")) or "Recette sans nom",
        temps=prep,
        temps_cuisson=cook,
        pour=_servings(node.get("recipeYield")),
        type_plat=guess_dish_type(category),
        type_cuisson=guess_cooking_type(steps),
        ingredients=ingredients,
        etapes=steps,
        notes=_text(node.get("description")),
        source=source,
    )
    assert recipe.type_plat in DISH_TYPES and recipe.type_cuisson in COOKING_TYPES
    return recipe, _images(node.get("image"))


def _parse(page: str, base_url: str) -> tuple[Recipe, list[str]]:
    node = None
    for block in _json_blocks(page):
        node = _find_recipe(block)
        if node:
            break
    if node is None:
        raise WebImportError("Aucune recette n'a été trouvée sur cette page. Vérifiez que l'adresse mène bien "
                             "à une recette (et pas à une liste de recettes).")
    recipe, images = recipe_from_ld(node, source=base_url or "")
    if not recipe.ingredients and not recipe.etapes:
        raise WebImportError("La page contient une recette, mais sans ingrédients ni étapes lisibles.")
    return recipe, [urllib.parse.urljoin(base_url or "", u) for u in images]


def parse_recipe_html(html: str, base_url: str = "") -> Recipe:  # noqa: A002 - name fixed by the task
    """Pure parsing: page HTML -> Recipe (``source`` = ``base_url``). Raises WebImportError."""
    return _parse(html, base_url)[0]


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------
def import_url(text: str, progress: Progress | None = None) -> ImportResult:
    report = progress or (lambda _msg: None)
    url = normalize_url(text)
    report("Connexion au site…")
    page = fetch_html(url)
    report("Lecture de la recette…")
    recipe, image_urls = _parse(page, url)

    images: list[Path] = []
    warnings: list[str] = []
    if image_urls:
        report("Téléchargement de la photo…")
        folder = Path(tempfile.mkdtemp(prefix="mamarmiton_"))
        for image_url in image_urls[:3]:
            path = download_image(image_url, folder)
            if path:
                images.append(path)
                break
        if not images:
            shutil.rmtree(folder, ignore_errors=True)  # nothing downloaded: do not leave an empty folder behind
            warnings.append("La photo de la recette n'a pas pu être récupérée.")
    return ImportResult(recipe=recipe, images=images, method="site", warnings=warnings)
