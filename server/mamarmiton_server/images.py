"""Uploaded photos: checked, resized and re-encoded before they are stored.

Re-encoding means the server never stores or serves the uploaded bytes
themselves: anything that is not a real picture is refused, and metadata
(including the GPS position phones write into photos) is dropped.
"""

from __future__ import annotations

import io

from PIL import Image, ImageOps, UnidentifiedImageError

MAX_SIDE = 2560  # plenty for a recipe photo, keeps the NAS tidy
Image.MAX_IMAGE_PIXELS = 80_000_000  # refuse "decompression bombs" (tiny files that expand to huge images)
ALLOWED_FORMATS = {"JPEG", "PNG", "WEBP", "GIF", "BMP", "MPO"}  # MPO: some cameras' JPEGs


class BadImage(ValueError):
    pass


def process(data: bytes) -> tuple[bytes, str, str]:
    """Return (bytes, extension, content type) of the cleaned image."""
    try:
        with Image.open(io.BytesIO(data)) as probe:
            if probe.format not in ALLOWED_FORMATS:
                raise BadImage("Format d'image non pris en charge.")
            if probe.width * probe.height > Image.MAX_IMAGE_PIXELS:
                # Pillow only warns between 1x and 2x the limit; refuse before decoding anything
                raise BadImage("Image trop grande.")
            probe.verify()  # structural check; the image must be reopened afterwards
        with Image.open(io.BytesIO(data)) as img:
            img.seek(0)  # first frame of an animated GIF
            img = ImageOps.exif_transpose(img)  # phones store rotation separately
            img.thumbnail((MAX_SIDE, MAX_SIDE))
            has_alpha = img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info)
            out = io.BytesIO()
            if has_alpha:
                img.convert("RGBA").save(out, "PNG", optimize=True)
                return out.getvalue(), "png", "image/png"
            img.convert("RGB").save(out, "JPEG", quality=88, optimize=True, progressive=True)
            return out.getvalue(), "jpg", "image/jpeg"
    except BadImage:
        raise
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError, SyntaxError, ValueError) as exc:
        raise BadImage("Ce fichier n'est pas une image lisible.") from exc
