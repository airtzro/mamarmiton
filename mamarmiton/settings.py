"""User preferences stored in ``settings.json`` next to the recipes."""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass

from mamarmiton import config

log = logging.getLogger(__name__)

APPEARANCES = {"Clair": "light", "Sombre": "dark", "Comme le système": "system"}
TEXT_SIZES = {"Normale": 1.0, "Grande": 1.15, "Très grande": 1.3}


@dataclass
class Settings:
    appearance: str = "Clair"
    text_size: str = "Normale"
    use_ai: bool = True
    sort: str = "Favoris d'abord"

    @classmethod
    def load(cls) -> "Settings":
        path = config.data_dir() / "settings.json"
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            known = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
            settings = cls(**known)
        except (OSError, json.JSONDecodeError, TypeError):
            settings = cls()
        if settings.appearance not in APPEARANCES:
            settings.appearance = "Clair"
        if settings.text_size not in TEXT_SIZES:
            settings.text_size = "Normale"
        return settings

    def save(self) -> None:
        path = config.data_dir() / "settings.json"
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(asdict(self), f, indent=2, ensure_ascii=False)
        except OSError:
            log.exception("Could not save settings")
