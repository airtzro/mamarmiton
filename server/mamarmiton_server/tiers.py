"""Account tiers and monthly AI credits.

A tier says how many times a month the local model may structure an import for that person:

- ``base``: no AI. Imports still work, read by the rules (OCR + heuristics).
- ``plus``: ``TIERS["plus"]`` AI imports per calendar month (UTC).

An admin can add ``ai_extra`` credits on top of the tier, per month, for one person.
One credit is used only when the model actually runs on an import; if it fails or gives
nothing back, the credit is returned. Imports the rules read clearly never touch the model.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from mamarmiton_server import db

TIERS: dict[str, int] = {"base": 0, "plus": 30}  # AI imports per month
DEFAULT_TIER = "base"  # new accounts
TIER_LABELS = {"base": "Base", "plus": "Plus"}


def month(now: datetime | None = None) -> str:
    return (now or datetime.now(timezone.utc)).strftime("%Y-%m")


def monthly_allowance(tier: str, extra: int) -> int:
    return max(0, TIERS.get(tier, 0) + int(extra or 0))


def used(conn: sqlite3.Connection, user_id: int, when: str | None = None) -> int:
    row = conn.execute("SELECT used FROM ai_usage WHERE user_id = ? AND month = ?",
                       (user_id, when or month())).fetchone()
    return row[0] if row else 0


def summary(conn: sqlite3.Connection, user_id: int) -> dict:
    """What the person (and the admin) sees: tier, allowance, used and left this month."""
    row = conn.execute("SELECT tier, ai_extra FROM users WHERE id = ?", (user_id,)).fetchone()
    tier, extra = (row["tier"], row["ai_extra"]) if row else (DEFAULT_TIER, 0)
    allowance = monthly_allowance(tier, extra)
    spent = used(conn, user_id)
    return {"tier": tier, "ai_extra": extra, "ai_allowance": allowance, "ai_used": spent,
            "ai_left": max(0, allowance - spent), "month": month()}


class AiCredits:
    """Takes and gives back credits; each call opens its own short connection (imports run long)."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path

    def take(self, user_id: int) -> bool:
        conn = db.connect(self.db_path)
        try:
            with db.transaction(conn):
                row = conn.execute("SELECT tier, ai_extra FROM users WHERE id = ?", (user_id,)).fetchone()
                if row is None:
                    return False
                if used(conn, user_id) >= monthly_allowance(row["tier"], row["ai_extra"]):
                    return False
                conn.execute("INSERT INTO ai_usage (user_id, month, used) VALUES (?, ?, 1) "
                             "ON CONFLICT (user_id, month) DO UPDATE SET used = used + 1", (user_id, month()))
                return True
        finally:
            conn.close()

    def give_back(self, user_id: int) -> None:
        conn = db.connect(self.db_path)
        try:
            conn.execute("UPDATE ai_usage SET used = used - 1 WHERE user_id = ? AND month = ? AND used > 0",
                         (user_id, month()))
        finally:
            conn.close()

    def allowance(self, user_id: int) -> int:
        return self.summary(user_id)["ai_allowance"]

    def summary(self, user_id: int) -> dict:
        conn = db.connect(self.db_path)
        try:
            return summary(conn, user_id)
        finally:
            conn.close()
