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

    @property
    def db_path(self) -> Path:
        return self.data_dir / "mamarmiton.sqlite3"

    @property
    def images_dir(self) -> Path:
        return self.data_dir / "images"

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.images_dir.mkdir(parents=True, exist_ok=True)
