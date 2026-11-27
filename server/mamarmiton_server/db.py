"""SQLite storage. One connection per request; WAL mode allows concurrent reads.

Recipes are stored as the same JSON the desktop app writes (models.Recipe),
minus the per-user ``favori`` flag and the ``images`` list, which live in
their own tables.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

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


def init(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(path)
    try:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.executescript(SCHEMA)
    finally:
        conn.close()


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
