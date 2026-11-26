"""Filesystem locations and logging setup.

Data lives in a per-user folder so the app works no matter which directory
it is launched from:

* Windows: ``%LOCALAPPDATA%\\recette_app`` (the folder v3 already used, so
  existing recipes are picked up as-is).
* Linux / macOS: ``$XDG_DATA_HOME/recette_app`` (``~/.local/share/recette_app``).

Set ``MAMARMITON_DATA_DIR`` to override (handy for tests and development).
"""

from __future__ import annotations

import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

LLM_MODEL_FILENAME = "qwen2.5-1.5b-instruct-q4_0.gguf"


def data_dir() -> Path:
    override = os.environ.get("MAMARMITON_DATA_DIR")
    if override:
        base = Path(override)
    elif sys.platform.startswith("win"):
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "recette_app"
    else:
        xdg = os.environ.get("XDG_DATA_HOME")
        base = (Path(xdg) if xdg else Path.home() / ".local" / "share") / "recette_app"
    base.mkdir(parents=True, exist_ok=True)
    return base


def recipes_file() -> Path:
    return data_dir() / "recette.json"


def legacy_favorites_file() -> Path:
    return data_dir() / "recette_fav.json"


def images_dir() -> Path:
    path = data_dir() / "images"
    path.mkdir(parents=True, exist_ok=True)
    return path


def log_file() -> Path:
    return data_dir() / "mamarmiton.log"


def app_dir() -> Path:
    """Folder holding the executable (frozen) or the project root (source)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


ICON = "assets/icon.ico"


def resource_path(relative: str) -> Path:
    """Path of a file bundled with the app (works inside PyInstaller)."""
    base = Path(getattr(sys, "_MEIPASS", app_dir()))
    return base / relative


def model_candidates() -> list[Path]:
    """Places where the GGUF model may live, in priority order."""
    return [
        data_dir() / "model" / LLM_MODEL_FILENAME,
        app_dir() / "model" / LLM_MODEL_FILENAME,
        Path.cwd() / "model" / LLM_MODEL_FILENAME,
    ]


def find_model() -> Path | None:
    for candidate in model_candidates():
        if candidate.is_file():
            return candidate
    return None


def legacy_image_dirs() -> list[Path]:
    """v3 stored PDF images in ``./img/<recipe name>`` next to the exe."""
    seen: list[Path] = []
    for base in (app_dir(), Path.cwd()):
        path = (base / "img").resolve()
        if path.is_dir() and path not in seen:
            seen.append(path)
    return seen


def setup_logging(level: int = logging.INFO) -> None:
    root = logging.getLogger()
    if root.handlers:
        return
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    root.setLevel(level)
    try:
        file_handler = RotatingFileHandler(log_file(), maxBytes=1_000_000, backupCount=2, encoding="utf-8")
        file_handler.setFormatter(fmt)
        root.addHandler(file_handler)
    except OSError:
        pass
    if sys.stderr is not None:  # stderr is None in a windowed PyInstaller build
        stream = logging.StreamHandler()
        stream.setFormatter(fmt)
        root.addHandler(stream)
