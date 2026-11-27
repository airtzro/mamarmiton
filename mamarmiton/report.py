""""Signaler un problème": build a report, hide secrets in the log, keep unsent reports in a small file.

No Tk here. The server applies the same ``redact`` again (a second net, not the first one).
"""

from __future__ import annotations

import json
import logging
import os
import platform
import re
import threading
from pathlib import Path

from mamarmiton import __version__, config

log = logging.getLogger(__name__)

MAX_MESSAGE = 4000
MAX_LOG_BYTES = 60 * 1024   # the server refuses more than 64 KB
LOG_LINES = 200
MAX_QUEUED = 5              # unsent reports kept on disk

from mamarmiton.redact import MASK, redact  # noqa: F401  (re-exported)


def log_tail(path: Path | None = None, lines: int = LOG_LINES, max_bytes: int = MAX_LOG_BYTES) -> str:
    """The last ``lines`` lines of the application log, redacted and at most ``max_bytes`` long."""
    path = path or config.log_file()
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - 4 * max_bytes))
            raw = f.read()
    except OSError:
        return ""
    text = redact(raw.decode("utf-8", errors="replace"))
    text = "\n".join(text.splitlines()[-lines:])
    data = text.encode("utf-8")
    if len(data) > max_bytes:
        text = data[-max_bytes:].decode("utf-8", errors="ignore")
    return text


def build(message: str, attach_log: bool = True) -> dict:
    return {
        "message": message.strip()[:MAX_MESSAGE],
        "client": "desktop",
        "app_version": __version__,
        "os": platform.platform()[:300],
        "log_tail": log_tail() if attach_log else "",
    }


# ------------------------------------------------------------------ offline queue
_lock = threading.Lock()


def queue_file() -> Path:
    return config.data_dir() / "reports_queue.json"


def _read() -> list[dict]:
    try:
        data = json.loads(queue_file().read_text(encoding="utf-8"))
        return [r for r in data if isinstance(r, dict) and r.get("message")] if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def _write(items: list[dict]) -> None:
    try:
        if items:
            tmp = queue_file().with_suffix(".tmp")
            tmp.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, queue_file())
        else:
            queue_file().unlink(missing_ok=True)
    except OSError:
        log.warning("Cannot write the report queue", exc_info=True)


def enqueue(report: dict) -> None:
    with _lock:
        _write((_read() + [report])[-MAX_QUEUED:])


def pending() -> int:
    with _lock:
        return len(_read())


def send_pending(client) -> int:
    """Send every queued report with ``client.send_report``; returns how many went through.

    Stops at the first failure (offline, signed out) and keeps the rest. A report the server
    refuses for good (4xx other than 401) is dropped so it cannot block the queue.
    """
    from mamarmiton.remote.client import ApiError, AuthError, OfflineError

    sent = 0
    while True:
        with _lock:
            items = _read()
        if not items:
            return sent
        try:
            client.send_report(items[0])
        except (OfflineError, AuthError):
            return sent
        except ApiError:
            log.warning("A queued report was refused by the server and dropped")
        except Exception:  # noqa: BLE001 - never break the sync thread
            return sent
        with _lock:
            _write(_read()[1:])
        sent += 1


def submit(client, report: dict) -> bool:
    """Send now; queue it when that is not possible. Returns True when it reached the server.

    Raises ``ApiError`` only for a refusal that retrying cannot fix (the caller shows its message).
    """
    from mamarmiton.remote.client import AuthError, OfflineError

    if client is None:
        enqueue(report)
        return False
    try:
        client.send_report(report)
        return True
    except (OfflineError, AuthError):
        enqueue(report)
        return False
