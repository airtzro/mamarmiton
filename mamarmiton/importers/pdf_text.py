"""Text and image extraction from PDF files."""

from __future__ import annotations

import hashlib
import logging
import shutil
from pathlib import Path
from typing import BinaryIO

import pdfplumber

log = logging.getLogger(__name__)

MIN_TEXT_CHARS = 200  # below this the PDF is probably a scan -> try OCR
MIN_IMAGE_SIDE = 200  # px in the PDF; smaller images are icons / logos
MAX_IMAGES = 6


def ocr_available() -> bool:
    try:
        import pdf2image  # noqa: F401
        import pytesseract  # noqa: F401
    except ImportError:
        return False
    return shutil.which("tesseract") is not None


def extract_text_layer(source: Path | BinaryIO, max_pages: int | None = None) -> str:
    """The text layer of a PDF (a path or an open binary file); empty for a scan."""
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
