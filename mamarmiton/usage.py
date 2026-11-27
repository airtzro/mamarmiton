"""Anonymous usage counters: how many times per day each feature was used. Nothing else.

Counted: an event name (from ``EVENTS``) and the day. Never counted: recipe names or content,
search words, who you are, which device. The counts live in ``usage.json`` in the data folder.
Signed-in users send them with the sync (``flush``); the server adds them up with everybody's.
Local-only users keep the last 90 days on their own disk and never send anything.

Nothing here can raise or block: every public function swallows its errors.
The same list exists in ``server/mamarmiton_server/usage.py`` and ``web/usage.js``.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import date, timedelta
from pathlib import Path

from mamarmiton import __version__, config

log = logging.getLogger(__name__)

EVENTS = (
    "recipe_open", "recipe_create", "recipe_edit", "recipe_delete", "recipe_hide", "delete_undo", "variant_create",
    "search", "favorite_toggle", "photo_add", "pdf_import", "url_import", "pdf_export",
    "kitchen_open", "timer_start", "shopping_add", "shopping_open", "shopping_copy_or_print",
    "tag_filter", "tag_add", "share_create", "sync_conflict_shown", "update_banner_click",
    "backup_export", "backup_import",
)
ACTIVE = "active"  # at most 1 per day: "the app was used today"
KEEP_DAYS = 90
SEND_EVERY_S = 300  # a batch at most every 5 minutes

EXPLANATION = (
    "Pour savoir quelles fonctions servent vraiment, l'application compte combien de fois par jour chaque "
    "fonction est utilisée (par exemple « une recette ouverte », « une liste de courses copiée »). "
    "Ces nombres sont anonymes : ils ne contiennent ni le nom de vos recettes, ni vos recherches, ni votre "
    "nom, et ne sont pas associés à votre compte. Rien n'est envoyé à une société extérieure, seulement à "
    "votre serveur Mamarmiton."
)


class Counter:
    def __init__(self, path: Path):
        self.path = path
        self.enabled = True
        self._lock = threading.Lock()
        self._days: dict[str, dict[str, int]] = {}
        self._last_sent = 0.0
        self._load()

    # ------------------------------------------------------------ storage
    def _load(self) -> None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if isinstance(data, dict):
            self._days = {d: {e: n for e, n in ev.items() if isinstance(n, int) and n >= 0}
                          for d, ev in data.items() if isinstance(ev, dict)}
            self._prune()

    def _prune(self, today: date | None = None) -> None:
        limit = ((today or date.today()) - timedelta(days=KEEP_DAYS)).isoformat()
        for day in [d for d in self._days if d < limit]:
            del self._days[day]

    def _save(self) -> None:
        try:
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._days, separators=(",", ":")), encoding="utf-8")
            os.replace(tmp, self.path)
        except OSError:
            pass

    # --------------------------------------------------------------- API
    def hit(self, event: str, today: date | None = None) -> None:
        if not self.enabled or event not in EVENTS:
            return
        day = (today or date.today()).isoformat()
        with self._lock:
            events = self._days.setdefault(day, {})
            events[event] = events.get(event, 0) + 1
            events.setdefault(ACTIVE, 1)
            self._prune(today)
            self._save()

    def snapshot(self) -> dict[str, dict[str, int]]:
        """What is waiting to be sent (a 0 means "already sent")."""
        with self._lock:
            out = {d: {e: n for e, n in ev.items() if n > 0} for d, ev in self._days.items()}
            return {d: ev for d, ev in out.items() if ev}

    def acknowledge(self, sent: dict[str, dict[str, int]]) -> None:
        """Remove what the server has taken (counts added meanwhile stay). ``active`` stays at 0 for
        that day, so the same day is not reported twice."""
        with self._lock:
            for day, events in sent.items():
                mine = self._days.get(day)
                if not mine:
                    continue
                for event, n in events.items():
                    if event in mine:
                        mine[event] = 0 if event == ACTIVE else max(0, mine[event] - n)
                self._days[day] = {e: n for e, n in mine.items() if n > 0 or e == ACTIVE}
            self._save()

    def clear(self) -> None:
        with self._lock:
            self._days = {}
            self._save()

    def flush(self, client, force: bool = False) -> bool:
        """Send the counts with ``client.send_usage(client_id, days)``; clear them on success. Silent on failure."""
        if not self.enabled:
            return False
        if not force and time.monotonic() - self._last_sent < SEND_EVERY_S and self._last_sent:
            return False
        batch = self.snapshot()
        if not batch:
            return False
        self._last_sent = time.monotonic()  # failures wait too: no new attempt for 5 minutes
        try:
            client.send_usage(f"desktop/{__version__}", batch)
        except Exception:  # noqa: BLE001 - offline, old server without /usage, anything: try again later
            return False
        self.acknowledge(batch)
        return True


_counter: Counter | None = None
_guard = threading.Lock()


def counter() -> Counter:
    global _counter
    with _guard:
        path = config.data_dir() / "usage.json"
        if _counter is None or _counter.path != path:
            _counter = Counter(path)
        return _counter


def hit(event: str) -> None:
    """Count one use of ``event``. Never raises."""
    try:
        counter().hit(event)
    except Exception:  # noqa: BLE001
        pass


def set_enabled(value: bool) -> None:
    try:
        c = counter()
        c.enabled = bool(value)
        if not value:
            c.clear()  # opting out also forgets what was counted
    except Exception:  # noqa: BLE001
        pass


def flush(client) -> None:
    """Called by the sync thread after a sync. Never raises."""
    try:
        counter().flush(client)
    except Exception:  # noqa: BLE001
        pass
