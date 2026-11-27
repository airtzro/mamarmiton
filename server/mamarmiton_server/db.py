"""SQLite storage. One connection per request; WAL mode allows concurrent reads.

Recipes are stored as the same JSON the desktop app writes (models.Recipe),
minus the per-user ``favori`` flag and the ``images`` list, which live in
their own tables.
"""

from __future__ import annotations

import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterator

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY,
    username      TEXT NOT NULL UNIQUE COLLATE NOCASE,
    display_name  TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    is_admin      INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT NOT NULL
);

-- opaque bearer tokens; only their SHA-256 is stored
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    device     TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    last_used  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS invites (
    code_hash  TEXT PRIMARY KEY,
    created_by INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    note       TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    used_by    INTEGER REFERENCES users(id) ON DELETE SET NULL
);

-- a counter bumped by every change, so devices can ask "what changed since N?"
-- (timestamps could miss a write that commits while another device is reading)
CREATE TABLE IF NOT EXISTS counters (
    name  TEXT PRIMARY KEY,
    value INTEGER NOT NULL
);
INSERT OR IGNORE INTO counters VALUES ('changes', 0);

CREATE TABLE IF NOT EXISTS recipes (
    id         TEXT PRIMARY KEY,
    owner_id   INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    data       TEXT NOT NULL,          -- JSON, see models.Recipe
    version    INTEGER NOT NULL DEFAULT 1,  -- content version, for edit conflicts
    seq        INTEGER NOT NULL,            -- value of the change counter at the last change
    updated_at TEXT NOT NULL,
    deleted    INTEGER NOT NULL DEFAULT 0  -- tombstone so other devices learn about deletions
);
CREATE INDEX IF NOT EXISTS recipes_owner ON recipes(owner_id, seq);
CREATE INDEX IF NOT EXISTS recipes_seq ON recipes(seq);

CREATE TABLE IF NOT EXISTS favorites (
    user_id   INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    recipe_id TEXT NOT NULL REFERENCES recipes(id) ON DELETE CASCADE,
    PRIMARY KEY (user_id, recipe_id)
);

CREATE TABLE IF NOT EXISTS images (
    id           TEXT PRIMARY KEY,
    recipe_id    TEXT NOT NULL REFERENCES recipes(id) ON DELETE CASCADE,
    filename     TEXT NOT NULL,   -- file inside images/<recipe_id>/
    content_type TEXT NOT NULL,
    size         INTEGER NOT NULL,
    position     INTEGER NOT NULL DEFAULT 0,
    created_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS images_recipe ON images(recipe_id, position);

-- the owner shares the whole book with someone
CREATE TABLE IF NOT EXISTS book_shares (
    owner_id   INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    grantee_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    can_edit   INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    PRIMARY KEY (owner_id, grantee_id)
);

-- or only one recipe
CREATE TABLE IF NOT EXISTS recipe_shares (
    recipe_id  TEXT NOT NULL REFERENCES recipes(id) ON DELETE CASCADE,
    grantee_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    can_edit   INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    PRIMARY KEY (recipe_id, grantee_id)
);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def connect(path: Path) -> sqlite3.Connection:
    # one connection per request, used by one thread at a time; FastAPI may hop threads between
    # the dependency and the handler, so the same-thread check would raise spuriously
    conn = sqlite3.connect(path, timeout=15, isolation_level=None, check_same_thread=False)  # autocommit; explicit BEGIN when needed
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 15000")
    return conn


class SchemaTooNew(RuntimeError):
    """The database was written by a newer server than this code."""


class MigrationError(RuntimeError):
    """A migration failed; the database was rolled back to its previous version."""


def _statements(script: str) -> Iterator[str]:
    """Split a SQL script into statements (``executescript`` would commit mid-transaction)."""
    buffer = ""
    for line in script.splitlines(keepends=True):
        buffer += line
        if sqlite3.complete_statement(buffer):
            if buffer.strip():
                yield buffer
            buffer = ""
    if buffer.strip() and not all(part.strip().startswith("--") or not part.strip()
                                  for part in buffer.splitlines()):
        yield buffer


def _m1_baseline(conn: sqlite3.Connection) -> None:
    """The v4.0 schema. Every statement is IF NOT EXISTS, so it is safe on a database that already
    has these tables but no ``user_version`` (everything created before migrations existed)."""
    for statement in _statements(SCHEMA):
        conn.execute(statement)


def _m2_usage_and_reports(conn: sqlite3.Connection) -> None:
    """Anonymous usage counters (no user id, no recipe id) and problem reports (tied to the sender,
    so the developer can answer)."""
    for statement in _statements(USAGE_REPORTS_SCHEMA):
        conn.execute(statement)


USAGE_REPORTS_SCHEMA = """
CREATE TABLE usage_daily (
    day         TEXT NOT NULL,      -- YYYY-MM-DD, the client's local day
    client_kind TEXT NOT NULL,      -- desktop | web | other (never a version, never a device)
    event       TEXT NOT NULL,      -- one of usage.EVENTS, or "active"
    count       INTEGER NOT NULL,
    PRIMARY KEY (day, client_kind, event)
);

CREATE TABLE reports (
    id          INTEGER PRIMARY KEY,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at  TEXT NOT NULL,
    message     TEXT NOT NULL,
    client      TEXT NOT NULL DEFAULT '',
    app_version TEXT NOT NULL DEFAULT '',
    os          TEXT NOT NULL DEFAULT '',
    log_tail    TEXT NOT NULL DEFAULT '',
    read_at     TEXT
);
CREATE INDEX reports_unread ON reports(read_at, id);
"""


# Ordered, append-only: (version, function(conn)). Never edit or reorder a released migration.
# A migration runs inside the transaction that also bumps ``user_version``: just execute SQL,
# never commit. See "Schema migrations" in server/README.md.
MIGRATIONS: list[tuple[int, Callable[[sqlite3.Connection], None]]] = [
    (1, _m1_baseline),
    (2, _m2_usage_and_reports),
]


def latest_version() -> int:
    return MIGRATIONS[-1][0]


def schema_version(conn: sqlite3.Connection) -> int:
    return conn.execute("PRAGMA user_version").fetchone()[0]


def _has_tables(conn: sqlite3.Connection) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' "
                        "AND name NOT LIKE 'sqlite_%' LIMIT 1").fetchone() is not None


def backup_to(conn: sqlite3.Connection, dest: Path) -> None:
    """Consistent snapshot of the open database into ``dest``, even mid-write."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    target = sqlite3.connect(dest)
    try:
        conn.backup(target)
    finally:
        target.close()


def migrate(path: Path, dry_run: bool = False) -> tuple[int, int]:
    """Bring the database at ``path`` to the latest schema; returns ``(from_version, to_version)``.

    Refuses (``SchemaTooNew``) a database from a newer server. Before changing a database that
    already holds tables, a copy is written next to it (``<name>.pre-v<N>-<timestamp>.bak``).
    All steps and the version bump are one transaction: a failing step leaves the file untouched.
    With ``dry_run`` nothing is written and ``to_version`` is the version it would reach.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(path)
    try:
        current = schema_version(conn)
        target = latest_version()
        if current > target:
            raise SchemaTooNew(f"La base de données est en version {current}, mais ce serveur ne connaît que la "
                               f"version {target}. Mettez le serveur à jour (la base n'a pas été modifiée).")
        if dry_run or current == target:
            return current, target
        conn.execute("PRAGMA journal_mode = WAL")
        if _has_tables(conn):
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            backup = path.with_name(f"{path.name}.pre-v{current}-{stamp}.bak")
            backup_to(conn, backup)
            log.info("Sauvegarde avant migration : %s", backup)
        conn.execute("BEGIN IMMEDIATE")
        try:
            current = schema_version(conn)  # another process may have migrated while we waited
            if current > target:
                raise SchemaTooNew(f"La base de données est en version {current}, ce serveur en connaît {target}.")
            start = current
            for version, step in MIGRATIONS:
                if version <= current:
                    continue
                step(conn)
                conn.execute(f"PRAGMA user_version = {int(version)}")  # no bound parameters in PRAGMA
                log.info("Migration %d appliquée", version)
            conn.execute("COMMIT")
        except BaseException as exc:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            if isinstance(exc, (SchemaTooNew, KeyboardInterrupt)):
                raise
            raise MigrationError(f"La migration de la base a échoué ({exc}); elle a été annulée.") from exc
        return start, target
    finally:
        conn.close()


def init(path: Path) -> None:
    migrate(path)


def next_seq(conn: sqlite3.Connection) -> int:
    """Bump the change counter. Call inside a transaction."""
    return conn.execute(
        "UPDATE counters SET value = value + 1 WHERE name = 'changes' RETURNING value").fetchone()[0]


def current_seq(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT value FROM counters WHERE name = 'changes'").fetchone()[0]


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")
