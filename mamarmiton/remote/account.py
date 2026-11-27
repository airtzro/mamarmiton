"""The signed-in account, remembered in ``account.json`` (data folder)."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass

from mamarmiton import config
from mamarmiton.storage import _atomic_write_json

log = logging.getLogger(__name__)

DEFAULT_SERVER_URL = "https://mamarmiton.duckdns.org:51820"


@dataclass
class Account:
    server_url: str = DEFAULT_SERVER_URL
    token: str = ""
    username: str = ""
    display_name: str = ""
    is_admin: bool = False


def _path():
    return config.data_dir() / "account.json"


def load_account() -> Account | None:
    """The saved account, or None when nobody is signed in."""
    try:
        with open(_path(), encoding="utf-8") as f:
            data = json.load(f)
        known = {k: v for k, v in data.items() if k in Account.__dataclass_fields__}
        account = Account(**known)
    except (OSError, ValueError, TypeError, AttributeError):
        return None
    return account if account.token and account.username else None


def save_account(account: Account) -> None:
    """Write the account atomically, readable by the owner only (POSIX)."""
    path = _path()
    _atomic_write_json(path, asdict(account))
    try:
        os.chmod(path, 0o600)
    except OSError:
        log.debug("chmod not supported here")


def clear_account() -> None:
    _path().unlink(missing_ok=True)
