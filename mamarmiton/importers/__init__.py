"""Turning PDFs, web pages, photos and documents into recipes.

The heavy importers (pdfplumber, the local AI, httpx) load on first use, so the
server can import ``heuristic`` and ``documents`` without the desktop dependencies.
"""

from __future__ import annotations

from typing import Any

__all__ = ["ImportResult", "WebImportError", "import_pdf", "import_url"]

_LAZY = {"ImportResult": "pipeline", "import_pdf": "pipeline", "WebImportError": "web", "import_url": "web"}


def __getattr__(name: str) -> Any:
    module = _LAZY.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from importlib import import_module

    return getattr(import_module(f"{__name__}.{module}"), name)
