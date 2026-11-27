"""Anonymous usage counters and problem reports (storage rules).

``usage_daily`` holds one number per (day, client kind, event): no user id, no recipe id, no text.
The list of events is the same on the three clients (``mamarmiton/usage.py`` and ``web/usage.js``);
``tests/test_usage.py`` fails when they drift apart.
"""

from __future__ import annotations

import re
import sqlite3
from datetime import date, timedelta

EVENTS = (
    "recipe_open", "recipe_create", "recipe_edit", "recipe_delete", "recipe_hide", "delete_undo", "variant_create",
    "search", "favorite_toggle", "photo_add", "pdf_import", "url_import", "pdf_export",
    "kitchen_open", "timer_start", "shopping_add", "shopping_open", "shopping_copy_or_print",
    "tag_filter", "tag_add", "share_create", "sync_conflict_shown", "update_banner_click",
    "backup_export", "backup_import",
)
ACTIVE = "active"  # one per device and day: how many devices were used that day
ALLOWED = frozenset(EVENTS) | {ACTIVE}
KINDS = ("desktop", "web")

MAX_DAYS = 100           # days in one batch
MAX_EVENTS_PER_DAY = len(ALLOWED)
MAX_COUNT = 10_000       # per (day, event) in one batch
MAX_AGE_DAYS = 100       # older days are ignored
_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# one batch per device every few minutes at most
MAX_BATCHES_PER_HOUR = 30


def client_kind(client: str) -> str:
    """"desktop/4.1.0" -> "desktop"; anything unknown -> "other" (the version is not kept)."""
    kind = str(client or "").partition("/")[0].strip().lower()
    return kind if kind in KINDS else "other"


def clean_batch(days: object, today: date | None = None) -> list[tuple[str, str, int]]:
    """Valid ``(day, event, count)`` rows of a client batch. Unknown events, bad days and bad
    numbers are dropped silently; the rest of the batch is kept."""
    today = today or date.today()
    oldest, newest = today - timedelta(days=MAX_AGE_DAYS), today + timedelta(days=1)  # time zones
    rows: list[tuple[str, str, int]] = []
    if not isinstance(days, dict):
        return rows
    for day, events in list(days.items())[:MAX_DAYS]:
        if not isinstance(day, str) or not _DAY.match(day) or not isinstance(events, dict):
            continue
        try:
            when = date.fromisoformat(day)
        except ValueError:
            continue
        if not oldest <= when <= newest:
            continue
        for event, count in list(events.items())[:MAX_EVENTS_PER_DAY]:
            if event not in ALLOWED or isinstance(count, bool) or not isinstance(count, int) or count < 1:
                continue
            rows.append((day, event, min(count, 1 if event == ACTIVE else MAX_COUNT)))
    return rows


def record(conn: sqlite3.Connection, kind: str, rows: list[tuple[str, str, int]]) -> None:
    conn.executemany(
        "INSERT INTO usage_daily (day, client_kind, event, count) VALUES (?, ?, ?, ?) "
        "ON CONFLICT (day, client_kind, event) DO UPDATE SET count = count + excluded.count",
        [(day, kind, event, count) for day, event, count in rows])


def weekly_summary(conn: sqlite3.Connection, days: int = 56, today: date | None = None) -> dict:
    """Events x weeks over the last ``days`` (rounded up to whole weeks ending today).

    ``events`` is sorted least used first (total, then distinct days, then name): what nobody uses
    is what the product plan wants to see. ``active`` holds device-days per client kind.
    """
    today = today or date.today()
    days = max(7, min(days, 7 * 52))
    weeks = (days + 6) // 7
    start = today - timedelta(days=weeks * 7 - 1)
    totals: dict[str, list[int]] = {e: [0] * weeks for e in EVENTS}
    used_days: dict[str, set[str]] = {e: set() for e in EVENTS}
    active = {kind: [0] * weeks for kind in (*KINDS, "other")}
    for row in conn.execute("SELECT day, client_kind, event, count FROM usage_daily WHERE day >= ? AND day <= ?",
                            (start.isoformat(), today.isoformat())):
        week = (date.fromisoformat(row[0]) - start).days // 7
        if row[2] == ACTIVE:
            active.setdefault(row[1], [0] * weeks)[week] += row[3]
        elif row[2] in totals:
            totals[row[2]][week] += row[3]
            used_days[row[2]].add(row[0])
    order = sorted(EVENTS, key=lambda e: (sum(totals[e]), len(used_days[e]), e))
    return {
        "start": start.isoformat(),
        "end": today.isoformat(),
        "weeks": [(start + timedelta(days=7 * w)).isoformat() for w in range(weeks)],
        "events": [{"event": e, "counts": totals[e], "total": sum(totals[e]), "days": len(used_days[e])}
                   for e in order],
        "active": {kind: counts for kind, counts in active.items() if any(counts) or kind in KINDS},
    }
