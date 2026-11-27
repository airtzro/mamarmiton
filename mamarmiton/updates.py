"""Check GitHub for a newer release. Never downloads or replaces anything."""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import urllib.request
from dataclasses import dataclass
from datetime import date

log = logging.getLogger(__name__)

API_URL = "https://api.github.com/repos/airtzro/mamarmiton/releases/latest"
USER_AGENT = "mamarmiton-update-check"

_VERSION_RE = re.compile(r"^v?(\d+)\.(\d+)(?:\.(\d+))?$")


@dataclass(frozen=True)
class UpdateInfo:
    version: str  # "4.1.0"
    url: str  # release page
    download_url: str = ""  # the .exe asset, "" when absent


def parse_version(text: str) -> tuple[int, int, int] | None:
    """"v4.1.0" -> (4, 1, 0). None for anything else (prereleases such as "4.1.0-rc1" included)."""
    m = _VERSION_RE.match((text or "").strip())
    if not m:
        return None
    return int(m.group(1)), int(m.group(2)), int(m.group(3) or 0)


def parse_release(data: object, current_version: str) -> UpdateInfo | None:
    """The update described by a GitHub "latest release" payload, if newer than ``current_version``."""
    if not isinstance(data, dict) or data.get("draft") or data.get("prerelease"):
        return None
    latest = parse_version(str(data.get("tag_name") or ""))
    current = parse_version(current_version)
    if latest is None or current is None or latest <= current:
        return None
    download = ""
    for asset in data.get("assets") or []:
        if isinstance(asset, dict) and str(asset.get("name", "")).lower().endswith(".exe"):
            download = str(asset.get("browser_download_url") or "")
            break
    url = str(data.get("html_url") or "")
    if not url.startswith("https://"):
        return None
    return UpdateInfo(".".join(map(str, latest)), url, download)


def check_latest(current_version: str, timeout: float = 5) -> UpdateInfo | None:
    """Ask GitHub for the latest release. Any failure (offline, private repo, bad JSON) gives None."""
    try:
        req = urllib.request.Request(API_URL, headers={"User-Agent": USER_AGENT,
                                                       "Accept": "application/vnd.github+json"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read(1_000_000).decode("utf-8"))
        return parse_release(data, current_version)
    except Exception as exc:  # silent by design
        log.debug("Update check failed: %s", exc)
        return None


def enabled() -> bool:
    """Only the packaged .exe checks by default; ``MAMARMITON_UPDATE_CHECK=1`` forces it from source."""
    return bool(getattr(sys, "frozen", False)) or os.environ.get("MAMARMITON_UPDATE_CHECK") == "1"


def due(last_check: str, today: date | None = None) -> bool:
    """True when the last check (ISO date) was not today."""
    return last_check != (today or date.today()).isoformat()
