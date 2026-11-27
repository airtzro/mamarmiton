"""Pictures drawn with Pillow: line icons, recipe cover art, rounded photos.

Drawing them ourselves keeps the app crisp on every Windows version (no
emoji or symbol-font fallbacks) and lets images follow Clair / Sombre:
each CTkImage gets a light and a dark variant.
"""

from __future__ import annotations

import hashlib
import logging
import math
import random
from functools import lru_cache
from pathlib import Path

import customtkinter as ctk
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

from mamarmiton.ui import theme

log = logging.getLogger(__name__)

SS = 4  # supersampling factor for icons
HI = 2  # pixel density of generated pictures (sharp on 150-200 % screens)


def _rgb(hex_color: str) -> tuple[int, int, int]:
    hex_color = hex_color.lstrip("#")
    return tuple(int(hex_color[i:i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


def _mix(a: str, b: str, t: float) -> tuple[int, int, int]:
    ca, cb = _rgb(a), _rgb(b)
    return tuple(round(x + (y - x) * t) for x, y in zip(ca, cb))  # type: ignore[return-value]


# ------------------------------------------------------------------ fonts
_FONT_CANDIDATES = {
    "serif": ["georgiab.ttf", "Georgia Bold.ttf", "NotoSerif-Bold.ttf", "DejaVuSerif-Bold.ttf"],
    "sans": ["segoeuib.ttf", "NotoSans-Bold.ttf", "DejaVuSans-Bold.ttf", "Arial Bold.ttf", "arialbd.ttf"],
}


@lru_cache(maxsize=None)
def pil_font(kind: str, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    bundled = theme.font_path("DMSerifDisplay-Regular.ttf" if kind == "serif" else "AtkinsonHyperlegible-Bold.ttf")
    for name in [str(bundled), *_FONT_CANDIDATES[kind]]:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # Pillow < 10.1
        return ImageFont.load_default()


# ------------------------------------------------------------------ icons
def _stroke(d: ImageDraw.ImageDraw, points, width: int, fill) -> None:
    d.line(points, fill=fill, width=width, joint="curve")
    r = width / 2
    for x, y in (points[0], points[-1]):
        d.ellipse((x - r, y - r, x + r, y + r), fill=fill)


def _star_points(cx: float, cy: float, outer: float, inner: float) -> list[tuple[float, float]]:
    pts = []
    for i in range(10):
        radius = outer if i % 2 == 0 else inner
        angle = -math.pi / 2 + i * math.pi / 5
        pts.append((cx + radius * math.cos(angle), cy + radius * math.sin(angle)))
    return pts


def _draw_icon(name: str, color: str) -> Image.Image:
    """Draw ``name`` on a 24×24 grid, supersampled."""
    n = 24 * SS
    img = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    c = _rgb(color) + (255,)
    w = round(2 * SS)
    s = SS  # grid unit

    def box(x0, y0, x1, y1):
        return (x0 * s, y0 * s, x1 * s, y1 * s)

    def pts(*coords):
        return [(x * s, y * s) for x, y in coords]

    if name == "search":
        d.ellipse(box(4, 4, 17, 17), outline=c, width=w)
        _stroke(d, pts((15.5, 15.5), (20.5, 20.5)), w, c)
    elif name == "plus":
        _stroke(d, pts((12, 5), (12, 19)), w, c)
        _stroke(d, pts((5, 12), (19, 12)), w, c)
    elif name == "minus":
        _stroke(d, pts((5, 12), (19, 12)), w, c)
    elif name == "back":
        _stroke(d, pts((19, 12), (5, 12)), w, c)
        _stroke(d, pts((11, 6), (5, 12), (11, 18)), w, c)
    elif name in ("star", "star_outline"):
        poly = _star_points(12 * s, 12.6 * s, 9.6 * s, 4.2 * s)
        if name == "star":
            d.polygon(poly, fill=c)
        else:
            d.line(poly + [poly[0], poly[1]], fill=c, width=w, joint="curve")
    elif name == "clock":
        d.ellipse(box(3, 3, 21, 21), outline=c, width=w)
        _stroke(d, pts((12, 7.5), (12, 12), (15, 14)), w, c)
    elif name == "users":
        d.ellipse(box(8, 3.5, 16, 11.5), outline=c, width=w)
        d.arc(box(4, 14, 20, 30), 180, 360, fill=c, width=w)
    elif name == "pencil":
        _stroke(d, pts((5, 19), (6, 15), (16, 5), (19, 8), (9, 18), (5, 19)), w, c)
        _stroke(d, pts((14, 7), (17, 10)), w, c)
    elif name == "trash":
        _stroke(d, pts((4, 7), (20, 7)), w, c)
        _stroke(d, pts((9, 7), (9, 4.5), (15, 4.5), (15, 7)), w, c)
        _stroke(d, pts((6, 7), (7, 20), (17, 20), (18, 7)), w, c)
        _stroke(d, pts((10, 11), (10, 16)), w, c)
        _stroke(d, pts((14, 11), (14, 16)), w, c)
    elif name == "pdf":
        _stroke(d, pts((14, 3), (6, 3), (6, 21), (18, 21), (18, 7), (14, 3)), w, c)
        _stroke(d, pts((12, 17.5), (12, 10.5)), w, c)
        _stroke(d, pts((9, 13), (12, 10), (15, 13)), w, c)
    elif name == "globe":
        d.ellipse(box(3, 3, 21, 21), outline=c, width=w)
        d.ellipse(box(8.5, 3, 15.5, 21), outline=c, width=w)
        _stroke(d, pts((3, 12), (21, 12)), w, c)
    elif name == "settings":
        for y, knob in ((6, 15), (12, 8), (18, 13)):
            _stroke(d, pts((4, y), (20, y)), w, c)
            r = 2.6 * s
            d.ellipse((knob * s - r, y * s - r, knob * s + r, y * s + r), fill=c)
    elif name == "grid":
        for x, y in ((4, 4), (13, 4), (4, 13), (13, 13)):
            d.rounded_rectangle(box(x, y, x + 7, y + 7), radius=2 * s, outline=c, width=w)
    elif name == "photo":
        d.rounded_rectangle(box(3, 5, 21, 19), radius=2.5 * s, outline=c, width=w)
        _stroke(d, pts((5, 17), (10, 11.5), (14, 15.5), (16.5, 13), (20, 17)), w, c)
        d.ellipse(box(14.5, 7.5, 17.5, 10.5), fill=c)
    elif name == "pot":
        _stroke(d, pts((5, 10), (6, 19), (18, 19), (19, 10)), w, c)
        _stroke(d, pts((3, 10), (21, 10)), w, c)
        _stroke(d, pts((10, 7), (10, 5.5), (14, 5.5), (14, 7)), w, c)
    elif name == "check":
        _stroke(d, pts((5, 12.5), (10, 17), (19, 7)), w, c)
    elif name == "help":
        d.ellipse(box(3, 3, 21, 21), outline=c, width=w)
        d.arc(box(9, 7, 15, 13), 180, 80, fill=c, width=w)
        _stroke(d, pts((12.4, 12.9), (12, 14)), w, c)
        r = 1.3 * s
        d.ellipse((12 * s - r, 17 * s - r, 12 * s + r, 17 * s + r), fill=c)
    elif name == "reset":
        d.arc(box(4.5, 4.5, 19.5, 19.5), 210, 140, fill=c, width=w)
        _stroke(d, pts((4.5, 4), (5, 9), (10, 8.5)), w, c)
    elif name in ("cloud", "cloud_off"):
        d.arc(box(3, 12, 10, 19), 90, 270, fill=c, width=w)
        d.arc(box(6, 6, 16, 16), 165, 350, fill=c, width=w)
        d.arc(box(13, 10, 21, 19), 270, 90, fill=c, width=w)
        _stroke(d, pts((6.5, 19), (17.5, 19)), w, c)
        _stroke(d, pts((6.5, 12), (7, 11.5)), w, c)
        if name == "cloud_off":
            _stroke(d, pts((4, 4), (20, 21)), w, c)
    elif name == "alert":
        _stroke(d, pts((12, 4), (21, 20), (3, 20), (12, 4)), w, c)
        _stroke(d, pts((12, 10), (12, 14)), w, c)
        r = 1.2 * s
        d.ellipse((12 * s - r, 17 * s - r, 12 * s + r, 17 * s + r), fill=c)
    elif name == "share":
        _stroke(d, pts((7.5, 11), (16.5, 7)), w, c)
        _stroke(d, pts((7.5, 13), (16.5, 17)), w, c)
        for x, y in ((6, 12), (18, 6), (18, 18)):
            r = 3 * s
            d.ellipse((x * s - r, y * s - r, x * s + r, y * s + r), fill=c)
    elif name == "eye":
        _stroke(d, pts((3, 12), (8, 7), (16, 7), (21, 12), (16, 17), (8, 17), (3, 12)), w, c)
        d.ellipse(box(9.5, 9.5, 14.5, 14.5), fill=c)
    elif name == "copy":
        d.rounded_rectangle(box(8, 8, 20, 20), radius=2.5 * s, outline=c, width=w)
        _stroke(d, pts((16, 8), (16, 5), (4, 5), (4, 16), (8, 16)), w, c)
    elif name == "close":
        _stroke(d, pts((6, 6), (18, 18)), w, c)
        _stroke(d, pts((18, 6), (6, 18)), w, c)
    else:
        raise ValueError(f"unknown icon {name!r}")
    return img


def _pair(color) -> tuple[str, str]:
    return (color, color) if isinstance(color, str) else (color[0], color[1])


@lru_cache(maxsize=None)
def icon(name: str, color: tuple[str, str] | str = theme.TEXT, size: int = 18) -> ctk.CTkImage:
    light, dark = _pair(color)
    return ctk.CTkImage(light_image=_draw_icon(name, light), dark_image=_draw_icon(name, dark), size=(size, size))


# ----------------------------------------------------------- picture tools
def _round(img: Image.Image, radius: int, corners: tuple[bool, bool, bool, bool], bg: str) -> Image.Image:
    """Round ``img`` corners, filling the outside with ``bg`` (the card colour)."""
    w, h = img.size
    mask = Image.new("L", (w * 3, h * 3), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, w * 3 - 1, h * 3 - 1), radius=radius * 3, fill=255,
                                           corners=corners)
    mask = mask.resize((w, h), Image.LANCZOS)
    out = Image.new("RGB", (w, h), _rgb(bg))
    out.paste(img.convert("RGB"), (0, 0), mask)
    return out


def _seed(text: str) -> int:
    return int(hashlib.md5(text.encode("utf-8")).hexdigest()[:8], 16)


def _cover_art(seed_text: str, dish: str, initial: str, w: int, h: int) -> Image.Image:
    """Generated cover for recipes without a photo: gradient, soft shapes, big initial."""
    pal = theme.dish(dish)
    rnd = random.Random(_seed(seed_text))
    # diagonal gradient (built small then scaled: fast and smooth)
    gw, gh = 64, max(8, round(64 * h / w))
    grad = Image.new("RGB", (gw, gh))
    px = grad.load()
    flip = rnd.random() < 0.5
    for y in range(gh):
        for x in range(gw):
            t = (x / gw * 0.65 + y / gh * 0.35)
            px[x, y] = _mix(pal["from"], pal["to"], 1 - t if flip else t)
    img = grad.resize((w, h), Image.BICUBIC).convert("RGBA")

    shapes = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(shapes)
    for _ in range(5):
        r = rnd.uniform(0.18, 0.45) * h
        cx, cy = rnd.uniform(-0.1, 1.1) * w, rnd.uniform(-0.2, 1.2) * h
        alpha = rnd.randint(22, 48)
        d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(255, 255, 255, alpha))
    # a dotted "tablecloth" band
    step = max(10, h // 9)
    for y in range(step // 2, h, step):
        for x in range(step // 2 + (y // step % 2) * step // 2, w, step):
            if rnd.random() < 0.35:
                rr = step * 0.09
                d.ellipse((x - rr, y - rr, x + rr, y + rr), fill=(255, 255, 255, 40))
    shapes = shapes.filter(ImageFilter.GaussianBlur(0.6))
    img = Image.alpha_composite(img, shapes)

    if initial:
        f = pil_font("serif", round(h * 0.62))
        layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        ld = ImageDraw.Draw(layer)
        bbox = ld.textbbox((0, 0), initial, font=f)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        x = w - tw - bbox[0] - round(w * 0.08)
        y = (h - th) / 2 - bbox[1]
        ld.text((x, y), initial, font=f, fill=(255, 255, 255, 150))
        img = Image.alpha_composite(img, layer)
    return img


def _draw_tag(img: Image.Image, text: str, dish: str, scale: int) -> Image.Image:
    """Bake a small rounded dish label in the bottom-left corner of a picture."""
    img = img.convert("RGBA")
    f = pil_font("sans", 12 * scale)
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    bbox = d.textbbox((0, 0), text, font=f)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    pad_x, pad_y, margin = 10 * scale, 6 * scale, 12 * scale
    x0 = margin
    y1 = img.height - margin
    y0 = y1 - th - 2 * pad_y
    x1 = x0 + tw + 2 * pad_x + 12 * scale
    d.rounded_rectangle((x0, y0, x1, y1), radius=(y1 - y0) // 2, fill=(255, 252, 248, 235))
    dot_r = 3.5 * scale
    cy = (y0 + y1) / 2
    cx = x0 + pad_x + dot_r
    d.ellipse((cx - dot_r, cy - dot_r, cx + dot_r, cy + dot_r), fill=_rgb(theme.dish(dish)["dot"]) + (255,))
    d.text((cx + dot_r + 6 * scale - bbox[0], cy - th / 2 - bbox[1]), text, font=f, fill=_rgb("#2B201C") + (255,))
    return Image.alpha_composite(img, layer)


_cache: dict[tuple, ctk.CTkImage | None] = {}


def cover(photo: Path | None, seed_text: str, dish: str, title: str, size: tuple[int, int],
          corners: tuple[bool, bool, bool, bool] = (True, True, True, True), radius: int = 16,
          bg: tuple[str, str] = theme.SURFACE, tag: bool = True) -> ctk.CTkImage:
    """A recipe picture: its photo if it has one, otherwise generated art.

    ``bg`` is the colour behind the rounded corners (light, dark).
    """
    try:
        mtime = photo.stat().st_mtime if photo else 0
    except OSError:
        photo, mtime = None, 0
    key = ("cover", str(photo), mtime, seed_text, dish, title[:1], size, corners, radius, bg, tag)
    if key in _cache:
        return _cache[key]  # type: ignore[return-value]

    w, h = size[0] * HI, size[1] * HI
    base = None
    if photo:
        try:
            with Image.open(photo) as im:
                base = ImageOps.fit(ImageOps.exif_transpose(im).convert("RGB"), (w, h), Image.LANCZOS)
        except Exception:
            log.exception("Cannot load image %s", photo)
    if base is None:
        initial = next((ch.upper() for ch in title if ch.isalpha()), "")
        base = _cover_art(seed_text, dish, initial, w, h)
    if tag:
        base = _draw_tag(base, dish, dish, HI)
    light = _round(base, radius * HI, corners, bg[0])
    dark = _round(base, radius * HI, corners, bg[1])
    image = ctk.CTkImage(light_image=light, dark_image=dark, size=size)
    _cache[key] = image
    return image


def photo_thumb(path: Path, size: tuple[int, int], radius: int = 12, bg: tuple[str, str] = theme.SURFACE,
                fit: bool = True) -> ctk.CTkImage | None:
    """A rounded photo. ``fit`` crops to fill ``size``; otherwise it keeps the whole picture."""
    try:
        key = ("thumb", str(path), path.stat().st_mtime, size, radius, bg, fit)
    except OSError:
        return None
    if key in _cache:
        return _cache[key]
    try:
        with Image.open(path) as im:
            im = ImageOps.exif_transpose(im).convert("RGB")
            if fit:
                im = ImageOps.fit(im, (size[0] * HI, size[1] * HI), Image.LANCZOS)
            else:
                im.thumbnail((size[0] * HI, size[1] * HI), Image.LANCZOS)
            out_size = (im.width // HI, im.height // HI)
            image = ctk.CTkImage(light_image=_round(im, radius * HI, (True,) * 4, bg[0]),
                                 dark_image=_round(im, radius * HI, (True,) * 4, bg[1]), size=out_size)
    except Exception:
        log.exception("Cannot load image %s", path)
        image = None
    _cache[key] = image
    return image


@lru_cache(maxsize=None)
def logo(size: int = 44) -> ctk.CTkImage:
    """The app mark: a terracotta tile with a white serif M and a little steam curl."""
    n = size * HI * 2
    img = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((0, 0, n - 1, n - 1), radius=n * 0.28, fill=_rgb("#C8553D") + (255,))
    f = pil_font("serif", round(n * 0.62))
    bbox = d.textbbox((0, 0), "M", font=f)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    d.text(((n - tw) / 2 - bbox[0], (n - th) / 2 - bbox[1] + n * 0.05), "M", font=f, fill=(255, 248, 240, 255))
    out = img.resize((size * HI, size * HI), Image.LANCZOS)
    return ctk.CTkImage(light_image=out, dark_image=out, size=(size, size))


def dot(color: str, size: int = 10) -> ctk.CTkImage:
    key = ("dot", color, size)
    if key not in _cache:
        n = size * 8
        img = Image.new("RGBA", (n, n), (0, 0, 0, 0))
        ImageDraw.Draw(img).ellipse((0, 0, n - 1, n - 1), fill=_rgb(color) + (255,))
        img = img.resize((size * HI, size * HI), Image.LANCZOS)
        _cache[key] = ctk.CTkImage(light_image=img, dark_image=img, size=(size, size))
    return _cache[key]  # type: ignore[return-value]
