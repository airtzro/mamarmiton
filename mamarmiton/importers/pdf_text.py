"""Text and image extraction from PDF files, and reading photos with Tesseract (OCR)."""

from __future__ import annotations

import hashlib
import logging
import shutil
from pathlib import Path
from typing import BinaryIO

log = logging.getLogger(__name__)

MIN_TEXT_CHARS = 200  # below this the PDF is probably a scan -> try OCR
MIN_IMAGE_SIDE = 200  # px in the PDF; smaller images are icons / logos
MAX_IMAGES = 6
OCR_MAX_SIDE = 3000  # phone photos are larger; Tesseract is slower and no better above this


def tesseract_available() -> bool:
    """Photos can be read on this computer (pytesseract and the Tesseract program are installed)."""
    try:
        import pytesseract  # noqa: F401
    except ImportError:
        return False
    return shutil.which("tesseract") is not None


def ocr_available() -> bool:
    """Scanned PDFs can be read (photo OCR plus pdf2image to turn the pages into pictures)."""
    try:
        import pdf2image  # noqa: F401
    except ImportError:
        return False
    return tesseract_available()


def open_photo(path: Path):
    """The photo as a PIL image, upright (phone photos store their rotation separately).

    HEIC photos need the optional ``pillow-heif``; raises ``ValueError`` when the file cannot be opened.
    """
    from PIL import Image, ImageOps, UnidentifiedImageError

    try:
        import pillow_heif  # type: ignore[import-not-found]

        pillow_heif.register_heif_opener()
    except Exception:  # optional
        pass
    try:
        with Image.open(path) as img:
            img.load()
            return ImageOps.exif_transpose(img)
    except (UnidentifiedImageError, OSError) as exc:
        raise ValueError(f"La photo « {Path(path).name} » ne peut pas être ouverte sur cet ordinateur.") from exc


def ocr_image(path: Path) -> str:
    """The text of a photo of a recipe (French, then any language if the French data is missing)."""
    import pytesseract

    img = open_photo(path).convert("L")
    if max(img.size) > OCR_MAX_SIDE:
        img.thumbnail((OCR_MAX_SIDE, OCR_MAX_SIDE))
    try:
        return pytesseract.image_to_string(img, lang="fra+eng").strip()
    except pytesseract.TesseractError:  # language data not installed
        log.warning("Tesseract without French data, reading %s with the default language", path)
        return pytesseract.image_to_string(img).strip()


def extract_text_layer(source: Path | BinaryIO, max_pages: int | None = None) -> str:
    """The text layer of a PDF (a path or an open binary file); empty for a scan."""
    import pdfplumber

    parts = []
    with pdfplumber.open(source) as pdf:
        for page in pdf.pages[:max_pages]:
            text = page.extract_text()
            if text:
                parts.append(text)
    return "\n".join(parts).strip()


def _extract_text_ocr(path: Path) -> str:
    from pdf2image import convert_from_path
    import pytesseract

    pages = convert_from_path(str(path))
    return "\n".join(pytesseract.image_to_string(p, lang="fra+eng") for p in pages).strip()


def extract_text(path: Path) -> str:
    text = extract_text_layer(path)
    if len(text) < MIN_TEXT_CHARS and ocr_available():
        try:
            ocr = _extract_text_ocr(path)
            if len(ocr) > len(text):
                text = ocr
        except Exception:  # OCR is best effort
            log.exception("OCR failed for %s", path)
    return text


def extract_images(path: Path, output_dir: Path, max_images: int = MAX_IMAGES) -> list[Path]:
    """Save the large photos of the PDF as PNGs and return their paths.

    Each image region is rendered from the page (rather than decoding raw
    PDF streams), which works for JPEG, Flate, CMYK and masked images alike.
    """
    import pdfplumber

    output_dir.mkdir(parents=True, exist_ok=True)
    saved: list[Path] = []
    seen: set[str] = set()
    with pdfplumber.open(path) as pdf:
        for page_no, page in enumerate(pdf.pages, 1):
            for img_no, img in enumerate(page.images, 1):
                if len(saved) >= max_images:
                    return saved
                width = img["x1"] - img["x0"]
                height = img["bottom"] - img["top"]
                if width < MIN_IMAGE_SIDE / 2 or height < MIN_IMAGE_SIDE / 2:
                    continue
                try:
                    bbox = (
                        max(img["x0"], page.bbox[0]),
                        max(img["top"], page.bbox[1]),
                        min(img["x1"], page.bbox[2]),
                        min(img["bottom"], page.bbox[3]),
                    )
                    if bbox[2] - bbox[0] < 10 or bbox[3] - bbox[1] < 10:
                        continue
                    rendered = page.crop(bbox).to_image(resolution=150).original
                    if rendered.width < MIN_IMAGE_SIDE or rendered.height < MIN_IMAGE_SIDE:
                        continue
                    digest = hashlib.md5(rendered.tobytes()).hexdigest()
                    if digest in seen:
                        continue
                    seen.add(digest)
                    target = output_dir / f"photo_p{page_no}_{img_no}.png"
                    rendered.convert("RGB").save(target)
                    saved.append(target)
                except Exception:
                    log.exception("Could not extract image %s on page %s", img_no, page_no)
    return saved
