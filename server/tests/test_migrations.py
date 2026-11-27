import sqlite3

import pytest
from fastapi.testclient import TestClient

from mamarmiton_server import __main__ as cli
from mamarmiton_server import db
from mamarmiton_server.api import create_app
from mamarmiton_server.config import Settings

# The schema exactly as the code created it before migrations existed (v4.0 / server 0.1.0).
OLD_SCHEMA = db.SCHEMA


def old_database(path):
    conn = sqlite3.connect(path)
    conn.executescript(OLD_SCHEMA)
    conn.execute("INSERT INTO users VALUES (1, 'maman', 'Maman', 'h', 1, 't')")
    conn.execute("INSERT INTO recipes VALUES ('r1', 1, '{}', 3, 7, 't', 0)")
    conn.execute("INSERT INTO favorites VALUES (1, 'r1')")
    conn.execute("UPDATE counters SET value = 7")
    conn.commit()
    conn.close()


def version(path):
    conn = sqlite3.connect(path)
    try:
        return conn.execute("PRAGMA user_version").fetchone()[0]
    finally:
        conn.close()


def test_fresh_database(tmp_path):
    path = tmp_path / "d" / "x.sqlite3"
    assert db.migrate(path) == (0, db.latest_version())
    assert version(path) == db.latest_version()
    assert list(path.parent.glob("*.bak")) == []  # nothing to protect
    assert db.migrate(path) == (db.latest_version(), db.latest_version())  # idempotent


def test_old_database_upgrades_and_keeps_rows(tmp_path):
    path = tmp_path / "x.sqlite3"
    old_database(path)
    assert version(path) == 0
    assert db.migrate(path) == (0, db.latest_version())
    assert version(path) == db.latest_version()
    conn = sqlite3.connect(path)
    assert conn.execute("SELECT id, version, seq FROM recipes").fetchall() == [("r1", 3, 7)]
    assert conn.execute("SELECT username FROM users").fetchall() == [("maman",)]
    assert conn.execute("SELECT value FROM counters").fetchall() == [(7,)]
    assert conn.execute("SELECT COUNT(*) FROM favorites").fetchone()[0] == 1
    conn.close()
    backups = list(tmp_path.glob("x.sqlite3.pre-v0-*.bak"))
    assert len(backups) == 1
    old = sqlite3.connect(backups[0])  # the backup is the untouched original
    assert old.execute("PRAGMA user_version").fetchone()[0] == 0
    assert old.execute("SELECT COUNT(*) FROM recipes").fetchone()[0] == 1
    old.close()


def test_dry_run_changes_nothing(tmp_path):
    path = tmp_path / "x.sqlite3"
    old_database(path)
    assert db.migrate(path, dry_run=True) == (0, db.latest_version())
    assert version(path) == 0
    assert list(tmp_path.glob("*.bak")) == []


def test_newer_database_is_refused(tmp_path, caplog):
    path = tmp_path / "mamarmiton.sqlite3"
    old_database(path)
    conn = sqlite3.connect(path)
    conn.execute(f"PRAGMA user_version = {db.latest_version() + 1}")
    conn.close()
    with pytest.raises(db.SchemaTooNew):
        db.migrate(path)
    with pytest.raises(db.SchemaTooNew):
        create_app(Settings(data_dir=tmp_path))  # create_app uses data_dir/mamarmiton.sqlite3
    assert version(path) == db.latest_version() + 1
    assert list(tmp_path.glob("*.bak")) == []


def test_newer_database_cli_exit_code(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("MAMARMITON_SERVER_DATA", str(tmp_path))
    path = tmp_path / "mamarmiton.sqlite3"
    old_database(path)
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA user_version = 99")
    conn.close()
    assert cli.main(["migrate"]) == 1
    assert cli.main(["users"]) == 1
    assert "version 99" in capsys.readouterr().err


def test_cli_migrate_and_dry_run(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("MAMARMITON_SERVER_DATA", str(tmp_path))
    old_database(tmp_path / "mamarmiton.sqlite3")
    assert cli.main(["migrate", "--dry-run"]) == 0
    assert version(tmp_path / "mamarmiton.sqlite3") == 0
    assert cli.main(["migrate"]) == 0
    assert version(tmp_path / "mamarmiton.sqlite3") == db.latest_version()
    assert cli.main(["migrate"]) == 0
    assert "déjà à jour" in capsys.readouterr().out


def test_failed_migration_rolls_back(tmp_path, monkeypatch):
    path = tmp_path / "x.sqlite3"
    old_database(path)

    def good(conn):
        conn.execute("ALTER TABLE users ADD COLUMN extra TEXT")

    def bad(conn):
        conn.execute("ALTER TABLE users ADD COLUMN other TEXT")
        raise RuntimeError("boom")

    top = db.latest_version()
    monkeypatch.setattr(db, "MIGRATIONS", [*db.MIGRATIONS, (top + 1, good), (top + 2, bad)])
    with pytest.raises(db.MigrationError):
        db.migrate(path)
    assert version(path) == 0  # not even step 1 or 2 stuck
    conn = sqlite3.connect(path)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(users)")]
    assert "extra" not in cols and "other" not in cols
    assert conn.execute("SELECT COUNT(*) FROM recipes").fetchone()[0] == 1
    conn.close()


def test_added_migration_applies_on_top(tmp_path, monkeypatch):
    path = tmp_path / "x.sqlite3"
    db.migrate(path)
    top = db.latest_version()
    monkeypatch.setattr(db, "MIGRATIONS", [*db.MIGRATIONS, (top + 1, lambda c: c.execute("CREATE TABLE t (a)"))])
    assert db.migrate(path) == (top, top + 1)
    assert version(path) == top + 1


def test_version_endpoint_and_min_version(tmp_path):
    http = TestClient(create_app(Settings(data_dir=tmp_path, min_desktop_version="4.1.0")))
    r = http.get("/version")
    assert r.status_code == 200 and r.json()["min_desktop"] == "4.1.0" and r.json()["server"]

    def get(client_header=None, path="/health"):
        return http.get(path, headers={"X-Mamarmiton-Client": client_header} if client_header else {})

    old = get("desktop/4.0.0")
    assert old.status_code == 426
    assert old.json()["detail"] == "Cette version de Mamarmiton est trop ancienne. Mettez-la à jour."
    assert old.json()["min_version"] == "4.1.0"
    assert get("desktop/4.0.9", "/recipes").status_code == 426  # even before authentication
    assert get("desktop/4.1.0").status_code == 200
    assert get("desktop/4.10.0").status_code == 200
    assert get("desktop/v5.0").status_code == 200
    assert get("web/0.0.1").status_code == 200
    assert get().status_code == 200  # no header: curl, health checks
    assert get("desktop/garbage").status_code == 200
    assert http.get("/version", headers={"X-Mamarmiton-Client": "desktop/4.0.0"}).status_code == 426


def test_no_minimum_by_default(tmp_path):
    http = TestClient(create_app(Settings(data_dir=tmp_path)))
    assert http.get("/version").json()["min_desktop"] is None
    assert http.get("/health", headers={"X-Mamarmiton-Client": "desktop/0.0.1"}).status_code == 200
