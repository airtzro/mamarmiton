"""Settings, read from environment variables (see .env.example)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


@dataclass
class Settings:
    data_dir: Path = field(default_factory=lambda: Path(os.environ.get("MAMARMITON_SERVER_DATA", "/data")))
    # a session token stays valid this long after its last use
    token_idle_days: int = field(default_factory=lambda: _int("MAMARMITON_TOKEN_IDLE_DAYS", 90))
    invite_days: int = field(default_factory=lambda: _int("MAMARMITON_INVITE_DAYS", 7))
    max_image_mb: int = field(default_factory=lambda: _int("MAMARMITON_MAX_IMAGE_MB", 15))
    max_images_per_recipe: int = field(default_factory=lambda: _int("MAMARMITON_MAX_IMAGES", 20))
    # failed logins allowed per (IP, username) and per IP within the window
    login_attempts: int = field(default_factory=lambda: _int("MAMARMITON_LOGIN_ATTEMPTS", 5))
    login_window_s: int = field(default_factory=lambda: _int("MAMARMITON_LOGIN_WINDOW_S", 900))

    # desktop clients older than this get 426 (empty = no minimum), e.g. "4.1.0"
    min_desktop_version: str = field(default_factory=lambda: (
        os.environ.get("MAMARMITON_MIN_DESKTOP_VERSION") or os.environ.get("MIN_DESKTOP_VERSION") or "").strip())

    # "import automatique": files -> recipe (OCR with Tesseract, optional local model)
    import_max_mb: int = field(default_factory=lambda: _int("MAMARMITON_IMPORT_MAX_MB", 30))  # all files together
    import_daily: int = field(default_factory=lambda: _int("MAMARMITON_IMPORT_DAILY", 40))  # per account
    import_daily_all: int = field(default_factory=lambda: _int("MAMARMITON_IMPORT_DAILY_ALL", 300))  # whole server
    import_timeout_s: int = field(default_factory=lambda: _int("MAMARMITON_IMPORT_TIMEOUT_S", 240))
    import_max_pages: int = field(default_factory=lambda: _int("MAMARMITON_IMPORT_MAX_PAGES", 12))  # read per PDF
    import_parallel: int = field(default_factory=lambda: _int("MAMARMITON_IMPORT_PARALLEL", 2))
    # the GGUF file of the local model; empty = <data dir>/models/<the file name the desktop app uses>
    import_model_path: str = field(default_factory=lambda: os.environ.get("MAMARMITON_IMPORT_MODEL_PATH", "").strip())

    @property
    def import_model_file(self) -> Path:
        if self.import_model_path:
            return Path(self.import_model_path)
        from mamarmiton.config import LLM_MODEL_FILENAME

        return self.data_dir / "models" / LLM_MODEL_FILENAME

    @property
    def db_path(self) -> Path:
        return self.data_dir / "mamarmiton.sqlite3"

    @property
    def images_dir(self) -> Path:
        return self.data_dir / "images"

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.images_dir.mkdir(parents=True, exist_ok=True)
