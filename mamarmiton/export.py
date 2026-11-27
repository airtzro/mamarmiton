"""Print a recipe as a clean A4 PDF (black on white, ready for a home printer).

Pages are drawn with Pillow and saved as a multi-page PDF, so no extra
dependency is needed. Text is wrapped by measuring it with the real font and
a line is never split across two pages.
"""

from __future__ import annotations

import logging
import os
import re
import tempfile
import unicodedata
from functools import lru_cache
from pathlib import Path
from typing import Callable, Iterable

from PIL import Image, ImageDraw, ImageFont, ImageOps

from mamarmiton import config
from mamarmiton.models import DEFAULT_COOKING, Recipe, format_minutes

log = logging.getLogger(__name__)

A4_MM = (210, 297)
MARGIN_MM = 18
PHOTO_MM = (58, 52)  # maximum size of the photo, top right
BLACK = (0, 0, 0)
GREY = (90, 90, 90)
LIGHT = (170, 170, 170)

_FONT_FILES = {
    "title": "DMSerifDisplay-Regular.ttf",
    "body": "AtkinsonHyperlegible-Regular.ttf",
    "bold": "AtkinsonHyperlegible-Bold.ttf",
    "italic": "AtkinsonHyperlegible-Italic.ttf",
}


@lru_cache(maxsize=None)
def _font(kind: str, px: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    px = max(6, int(px))
    try:
        return ImageFont.truetype(str(config.resource_path(f"assets/fonts/{_FONT_FILES[kind]}")), px)
    except OSError:
        pass
    try:
        return ImageFont.load_default(size=px)
    except TypeError:  # Pillow < 10.1: fixed-size bitmap font
        return ImageFont.load_default()


def _text_w(font, text: str) -> float:
    return font.getlength(text) if text else 0.0


def _line_h(font, factor: float) -> int:
    try:
        ascent, descent = font.getmetrics()
        base = ascent + descent
    except AttributeError:
        base = 12
    return int(round(base * factor))


def wrap_text(text: str, font, width: float) -> list[str]:
    """Break ``text`` into lines no wider than ``width``; over-long words are split."""
    lines: list[str] = []
    for paragraph in str(text).replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        words = paragraph.split()
        if not words:
            lines.append("")
            continue
        current = ""
        for word in words:
            candidate = f"{current} {word}" if current else word
            if _text_w(font, candidate) <= width:
                current = candidate
                continue
            if current:
                lines.append(current)
                current = ""
            while _text_w(font, word) > width and len(word) > 1:
                cut = len(word) - 1
                while cut > 1 and _text_w(font, word[:cut]) > width:
                    cut -= 1
                lines.append(word[:cut])
                word = word[cut:]
            current = word
        lines.append(current)
    while lines and not lines[-1]:
        lines.pop()
    return lines or [""]


def _mid(font, line_height: int) -> int:
    """Vertical middle of lower-case text inside a line box (where bullets and badges are centred)."""
    try:
        return int(font.getmetrics()[0] * 0.62)
    except AttributeError:
        return line_height // 2


def _meta_parts(r: Recipe) -> list[str]:
    parts = [r.type_plat] if r.type_plat else []
    if r.temps:
        parts.append(f"Préparation {format_minutes(r.temps)}")
    cook = []
    if r.temps_cuisson:
        cook.append(f"Cuisson {format_minutes(r.temps_cuisson)}")
    if r.type_cuisson and (r.temps_cuisson or r.type_cuisson != DEFAULT_COOKING):
        cook.append(r.type_cuisson if cook else f"Cuisson : {r.type_cuisson}")
    if cook:
        parts.append(" · ".join(cook) if len(cook) == 1 else f"{cook[0]} ({cook[1]})")
    if r.pour:
        parts.append(f"Pour {r.pour} personne{'s' if r.pour > 1 else ''}")
    return parts


def _wrap_parts(parts: list[str], font, width: float, sep: str = "  ·  ") -> list[str]:
    """Wrap the meta line between its parts so "Pour 6 personnes" is never split."""
    lines: list[str] = []
    for part in parts:
        if lines and _text_w(font, lines[-1] + sep + part) <= width:
            lines[-1] += sep + part
        else:
            lines.append(part)
    return [x for line in lines for x in wrap_text(line, font, width)]


class _Layout:
    def __init__(self, dpi: int):
        self.dpi = dpi
        self.k = dpi / 72  # pixels per point
        self.mm = dpi / 25.4
        self.w = round(A4_MM[0] * self.mm)
        self.h = round(A4_MM[1] * self.mm)
        self.margin = round(MARGIN_MM * self.mm)
        self.footer_h = round(23 * self.mm)
        self.bottom = self.h - self.footer_h
        self.pages: list[Image.Image] = []
        self.draw: ImageDraw.ImageDraw
        self.y = 0
        self.new_page()

    # sizes in points -> fonts
    def f(self, kind: str, pt: float):
        return _font(kind, round(pt * self.k))

    def new_page(self) -> None:
        img = Image.new("RGB", (self.w, self.h), "white")
        self.pages.append(img)
        self.draw = ImageDraw.Draw(img)
        self.y = self.margin

    @property
    def content_w(self) -> int:
        return self.w - 2 * self.margin

    def ensure(self, height: int) -> None:
        if self.y + height > self.bottom and self.y > self.margin:
            self.new_page()

    def gap(self, pt: float) -> None:
        self.y += round(pt * self.k)

    def heading(self, text: str) -> None:
        font = self.f("bold", 12)
        lh = _line_h(font, 1.2)
        self.ensure(lh + round(30 * self.k))  # keep a heading with what follows
        self.draw.text((self.margin, self.y), text.upper(), font=font, fill=BLACK)
        self.y += lh + round(3 * self.k)
        self.draw.line((self.margin, self.y, self.w - self.margin, self.y), fill=BLACK, width=max(1, round(0.7 * self.k)))
        self.y += round(11 * self.k)

    def paragraph_lines(self, lines: list[str], font, x: int, fill=BLACK, factor: float = 1.12,
                        first_prefix: Callable[[int, int], None] | None = None) -> None:
        """Draw lines one by one, starting a new page when the next one does not fit."""
        lh = _line_h(font, factor)
        keep = min(2, len(lines))  # avoid a lone first line at the page bottom
        self.ensure(lh * keep)
        for i, line in enumerate(lines):
            if self.y + lh > self.bottom:
                self.new_page()
            if i == 0 and first_prefix:
                first_prefix(self.y, lh)
            if line:
                self.draw.text((x, self.y), line, font=font, fill=fill)
            self.y += lh


def _load_photo(path: Path | str | None) -> Image.Image | None:
    if not path:
        return None
    try:
        with Image.open(path) as src:
            img = ImageOps.exif_transpose(src)
            if img.mode in ("RGBA", "LA", "P"):
                img = img.convert("RGBA")
                base = Image.new("RGB", img.size, "white")
                base.paste(img, mask=img.getchannel("A"))
                img = base
            else:
                img = img.convert("RGB")
            img.load()
            return img
    except Exception as exc:  # unreadable photo: print the recipe without it
        log.warning("photo ignorée pour le PDF (%s): %s", path, exc)
        return None


def _draw_header(lay: _Layout, r: Recipe, photo_path) -> None:
    d, m = lay.draw, lay.margin
    photo = _load_photo(photo_path)
    photo_w = 0
    photo_h = 0
    if photo is not None:
        box = (round(PHOTO_MM[0] * lay.mm), round(PHOTO_MM[1] * lay.mm))
        photo = ImageOps.contain(photo, box, Image.LANCZOS)
        photo_w, photo_h = photo.size
        lay.pages[-1].paste(photo, (lay.w - m - photo_w, lay.y))
        d.rectangle((lay.w - m - photo_w, lay.y, lay.w - m - 1, lay.y + photo_h - 1), outline=LIGHT, width=1)
    text_w = lay.content_w - (photo_w + round(7 * lay.mm) if photo_w else 0)
    top = lay.y

    title_font = lay.f("title", 28)
    for line in wrap_text(r.nom or "Recette sans nom", title_font, text_w):
        d.text((m, lay.y), line, font=title_font, fill=BLACK)
        lay.y += _line_h(title_font, 0.95)
    lay.gap(5)

    meta = _meta_parts(r)
    if meta:
        meta_font = lay.f("body", 11)
        for line in _wrap_parts(meta, meta_font, text_w):
            d.text((m, lay.y), line, font=meta_font, fill=GREY)
            lay.y += _line_h(meta_font, 1.2)
    lay.y = max(lay.y, top + photo_h) + round(8 * lay.k)
    d.line((m, lay.y, lay.w - m, lay.y), fill=BLACK, width=max(1, round(1.2 * lay.k)))
    lay.y += round(14 * lay.k)


def _draw_ingredients(lay: _Layout, r: Recipe) -> None:
    items = [i for i in r.ingredients if i.nom or i.quantite]
    if not items:
        return
    lay.heading("Ingrédients")
    body, bold = lay.f("body", 11.5), lay.f("bold", 11.5)
    lh = _line_h(body, 1.12)
    pad = round(3.5 * lay.k)
    two_cols = len(items) >= 6
    gutter = round(9 * lay.mm / 2) if two_cols else 0
    col_w = (lay.content_w - gutter) // 2 if two_cols else lay.content_w
    bullet = round(10 * lay.k)

    def rows(item) -> tuple[list[tuple[str, object, float]], str, float]:
        """Printed lines as (text, font, x offset), plus the quantity shown inline on line 1."""
        qty = " ".join(item.quantite.split())  # a line break would make Pillow's layout fail
        avail = col_w - bullet
        shift = _text_w(bold, qty + "  ") if qty else 0
        if qty and shift > avail * 0.5:  # very long quantity: give it its own lines
            lines = [(t, bold, 0) for t in wrap_text(qty, bold, avail)]
            if item.nom:
                lines += [(t, body, 0) for t in wrap_text(item.nom, body, avail)]
            return lines, "", 0
        return [(t, body, shift) for t in wrap_text(item.nom, body, avail - shift)], qty, shift

    def height(item) -> int:
        return len(rows(item)[0]) * lh + pad

    def draw_item(item, x: int, y: int) -> None:
        lines, qty, shift = rows(item)
        r = round(1.8 * lay.k)
        cy = y + _mid(body, lh)
        lay.draw.ellipse((x + 1, cy - r, x + 1 + 2 * r, cy + r), fill=BLACK)
        tx = x + bullet
        if qty:
            lay.draw.text((tx, y), qty, font=bold, fill=BLACK)
        for text, font, dx in lines:
            if text:
                lay.draw.text((tx + dx, y), text, font=font, fill=BLACK)
            y += lh

    remaining = items
    while remaining:
        room = lay.bottom - lay.y
        chosen = 0
        for k in range(len(remaining), 0, -1):
            chunk = remaining[:k]
            cut = (k + 1) // 2 if two_cols else k
            left, right = chunk[:cut], chunk[cut:]
            if max(sum(map(height, left)), sum(map(height, right)) if right else 0) <= room:
                chosen = k
                break
        if not chosen:
            if lay.y > lay.margin:
                lay.new_page()
                continue
            chosen = 1  # absurdly tall single item: draw it anyway
        chunk = remaining[:chosen]
        cut = (chosen + 1) // 2 if two_cols else chosen
        columns = [(lay.margin, chunk[:cut]), (lay.margin + col_w + gutter, chunk[cut:])]
        start = lay.y
        ends = []
        for x, col in columns:
            y = start
            for item in col:
                draw_item(item, x, y)
                y += height(item)
            ends.append(y)
        lay.y = max(ends)
        remaining = remaining[chosen:]
        if remaining:
            lay.new_page()
    lay.gap(10)


def _draw_steps(lay: _Layout, r: Recipe) -> None:
    steps = [s.strip() for s in r.etapes if s and s.strip()]
    if not steps:
        return
    lay.heading("Préparation")
    body = lay.f("body", 11.5)
    num_font = lay.f("bold", 10.5)
    indent = round(11 * lay.mm)
    radius = round(3.6 * lay.mm)
    text_w = lay.content_w - indent

    for n, step in enumerate(steps, 1):
        lines = wrap_text(step, body, text_w)

        def badge(y: int, lh: int, n=n) -> None:
            cx, cy = lay.margin + radius, y + _mid(num_font, lh)
            lay.draw.ellipse((cx - radius, cy - radius, cx + radius, cy + radius), outline=BLACK,
                             width=max(1, round(1.1 * lay.k)))
            lay.draw.text((cx, cy), str(n), font=num_font, fill=BLACK, anchor="mm")

        lay.paragraph_lines(lines, body, lay.margin + indent, first_prefix=badge, factor=1.15)
        lay.gap(9)
    lay.gap(4)


def _draw_notes(lay: _Layout, r: Recipe) -> None:
    notes = (r.notes or "").strip()
    if not notes:
        return
    lay.heading("Notes")
    font = lay.f("italic", 11)
    lay.paragraph_lines(wrap_text(notes, font, lay.content_w), font, lay.margin, fill=(40, 40, 40), factor=1.15)
    lay.gap(10)


def _draw_source(lay: _Layout, r: Recipe) -> None:
    source = (r.source or "").strip()
    if not source:
        return
    font = lay.f("body", 8.5)
    lay.paragraph_lines(wrap_text(f"Source : {source}", font, lay.content_w), font, lay.margin, fill=GREY, factor=1.15)


def _draw_footers(lay: _Layout, first: int) -> None:
    pages = lay.pages[first:]
    font = lay.f("body", 8.5)
    for i, img in enumerate(pages, 1):
        d = ImageDraw.Draw(img)
        y = lay.h - lay.margin + round(2 * lay.mm)
        d.line((lay.margin, y - round(3 * lay.mm), lay.w - lay.margin, y - round(3 * lay.mm)), fill=LIGHT, width=1)
        d.text((lay.w // 2, y), f"MAMARMITON · page {i}/{len(pages)}", font=font, fill=GREY, anchor="ma")


def _render_into(lay: _Layout, recipe: Recipe, photo_path) -> None:
    first = len(lay.pages) - 1
    _draw_header(lay, recipe, photo_path)
    _draw_ingredients(lay, recipe)
    _draw_steps(lay, recipe)
    _draw_notes(lay, recipe)
    _draw_source(lay, recipe)
    _draw_footers(lay, first)


def render_recipe_pages(recipe: Recipe, photo_path: Path | str | None = None, *, dpi: int = 150) -> list[Image.Image]:
    """Render ``recipe`` as a list of A4 portrait RGB page images."""
    lay = _Layout(dpi)
    _render_into(lay, recipe, photo_path)
    return lay.pages


def _save(pages: list[Image.Image], path: Path | str, dpi: int) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name, suffix=".tmp", dir=path.parent)
    os.close(fd)
    try:
        pages[0].save(tmp, "PDF", save_all=True, append_images=pages[1:], resolution=float(dpi), quality=95)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def export_pdf(recipe: Recipe, path: Path | str, photo_path: Path | str | None = None, *, dpi: int = 150) -> int:
    """Write ``recipe`` to a PDF at ``path``. Returns the page count."""
    pages = render_recipe_pages(recipe, photo_path, dpi=dpi)
    _save(pages, path, dpi)
    return len(pages)


def export_many(recipes: Iterable[Recipe], path: Path | str,
                photo_lookup: Callable[[Recipe], Path | str | None] | None = None, *, dpi: int = 150) -> int:
    """Several recipes in one PDF, each starting on a new page. Returns the page count."""
    recipes = list(recipes)
    if not recipes:
        raise ValueError("aucune recette à exporter")
    lay = _Layout(dpi)
    for n, recipe in enumerate(recipes):
        if n:
            lay.new_page()
        _render_into(lay, recipe, photo_lookup(recipe) if photo_lookup else None)
    _save(lay.pages, path, dpi)
    return len(lay.pages)


def slugify(title: str, default: str = "recette") -> str:
    """File-name friendly version of a title (no accents, no spaces)."""
    text = unicodedata.normalize("NFKD", title or "")
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-").lower()
    return text[:60].strip("-") or default


def open_file(path: Path | str) -> None:
    """Open ``path`` with the system's default viewer. Raises if nothing could be started."""
    import subprocess
    import sys

    if sys.platform.startswith("win"):
        os.startfile(str(path))  # type: ignore[attr-defined]
    else:
        subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(path)],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
