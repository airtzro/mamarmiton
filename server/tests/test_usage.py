import re
import sqlite3
from datetime import date, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from mamarmiton_server import __main__ as cli
from mamarmiton_server import db, usage
from mamarmiton_server.api import create_app
from mamarmiton_server.config import Settings

from test_api import PASSWORD, make_invite, register  # noqa: F401

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def settings(tmp_path):
    return Settings(data_dir=tmp_path)


@pytest.fixture
def client(settings):
    return TestClient(create_app(settings))


@pytest.fixture
def headers(client, settings):
    return register(client, settings, "maman")


def rows(settings, sql="SELECT * FROM usage_daily ORDER BY day, client_kind, event"):
    conn = sqlite3.connect(settings.db_path)
    try:
        return conn.execute(sql).fetchall()
    finally:
        conn.close()


TODAY = date.today().isoformat()


# ------------------------------------------------------------- migration
def test_migration_from_version_1(tmp_path):
    path = tmp_path / "x.sqlite3"
    conn = sqlite3.connect(path)
    conn.executescript(db.SCHEMA)  # exactly the v1 schema
    conn.execute("INSERT INTO users VALUES (1, 'maman', 'Maman', 'h', 1, 't')")
    conn.execute("PRAGMA user_version = 1")
    conn.commit()
    conn.close()
    assert db.migrate(path) == (1, db.latest_version())
    assert db.latest_version() >= 2
    conn = sqlite3.connect(path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == db.latest_version()
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert {"usage_daily", "reports"} <= tables
    assert conn.execute("SELECT username FROM users").fetchall() == [("maman",)]  # old rows kept
    assert list(tmp_path.glob("x.sqlite3.pre-v1-*.bak"))  # the safety copy
    conn.close()


def test_usage_table_has_no_identifier_column(settings, client):
    conn = sqlite3.connect(settings.db_path)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(usage_daily)")]
    assert cols == ["day", "client_kind", "event", "count"]
    assert conn.execute("PRAGMA foreign_key_list(usage_daily)").fetchall() == []
    conn.close()


# ------------------------------------------------------------------ /usage
def test_usage_requires_auth(client):
    assert client.post("/usage", json={"client": "web/1", "days": {}}).status_code == 401


def test_usage_aggregates_and_stores_no_identity(client, settings, headers):
    body = {"client": "desktop/4.1.0", "days": {TODAY: {"recipe_open": 3, "search": 1, "active": 1}}}
    assert client.post("/usage", json=body, headers=headers).json() == {"accepted": 3}
    other = register(client, settings, "papa")
    client.post("/usage", json=body, headers=other)
    client.post("/usage", json={"client": "web/1", "days": {TODAY: {"recipe_open": 2}}}, headers=headers)
    assert rows(settings) == [
        (TODAY, "desktop", "active", 2), (TODAY, "desktop", "recipe_open", 6), (TODAY, "desktop", "search", 2),
        (TODAY, "web", "recipe_open", 2)]
    dump = " ".join(str(c) for r in rows(settings) for c in r)
    assert "maman" not in dump and "4.1.0" not in dump


def test_usage_ignores_bad_entries_but_keeps_the_rest(client, settings, headers):
    old = (date.today() - timedelta(days=400)).isoformat()
    body = {"client": "desktop/1", "days": {
        TODAY: {"recipe_open": 2, "evil_event": 5, "search": -1, "kitchen_open": "9", "timer_start": True,
                "shopping_open": 10**9, "active": 50},
        old: {"recipe_open": 1}, "not-a-day": {"recipe_open": 1}, "2026-02-30": {"recipe_open": 1},
        "2999-01-01": {"recipe_open": 1}}}
    r = client.post("/usage", json=body, headers=headers)
    assert r.status_code == 200
    assert dict(((e), c) for _d, _k, e, c in rows(settings)) == {
        "recipe_open": 2, "shopping_open": usage.MAX_COUNT, "active": 1}


def test_usage_unknown_client_and_garbage_shapes(client, settings, headers):
    assert client.post("/usage", json={"client": "toaster/9", "days": {TODAY: {"search": 1}}},
                       headers=headers).status_code == 200
    assert rows(settings) == [(TODAY, "other", "search", 1)]
    for days in ([], "x", {TODAY: []}, {TODAY: {"search": [1]}}, None):
        assert client.post("/usage", json={"client": "web", "days": days}, headers=headers).status_code in (200, 422)
    assert len(rows(settings)) == 1


def test_batch_is_capped(client, settings, headers):
    days = {(date.today() - timedelta(days=i)).isoformat(): {"search": 1} for i in range(99)}
    days.update({f"2000-01-{i:02d}": {"search": 1} for i in range(1, 29)})
    client.post("/usage", json={"client": "web", "days": days}, headers=headers)
    assert len(rows(settings)) <= usage.MAX_DAYS


# ----------------------------------------------------------------- /reports
def report(**kw):
    return {"message": "Ça ne marche pas", "client": "desktop", "app_version": "4.1.0", "os": "Windows-11",
            "log_tail": "ligne 1\nligne 2", **kw}


def test_reports_need_auth(client):
    assert client.post("/reports", json=report()).status_code == 401


def test_report_is_stored_with_the_user(client, settings, headers):
    r = client.post("/reports", json=report(), headers=headers)
    assert r.status_code == 201
    got = rows(settings, "SELECT u.username, r.message, r.client, r.app_version, r.os, r.log_tail, r.read_at "
                         "FROM reports r JOIN users u ON u.id = r.user_id")
    assert got == [("maman", "Ça ne marche pas", "desktop", "4.1.0", "Windows-11", "ligne 1\nligne 2", None)]


def test_report_limits(client, headers):
    assert client.post("/reports", json=report(message="x" * 4000), headers=headers).status_code == 201
    assert client.post("/reports", json=report(message="x" * 4001), headers=headers).status_code == 422
    assert client.post("/reports", json=report(message="   "), headers=headers).status_code == 422
    assert client.post("/reports", json=report(log_tail="x" * 65536), headers=headers).status_code == 201
    assert client.post("/reports", json=report(log_tail="x" * 65537), headers=headers).status_code == 422
    # 64 KB of characters that take 2 bytes each is over the byte limit
    assert client.post("/reports", json=report(log_tail="é" * 40000), headers=headers).status_code == 413


def test_report_secrets_are_redacted_again_on_the_server(client, settings, headers):
    client.post("/reports", json=report(log_tail="Authorization: Bearer abcDEF123456\npassword=hunter2 ok"),
                headers=headers)
    tail = rows(settings, "SELECT log_tail FROM reports")[0][0]
    assert "abcDEF" not in tail and "hunter2" not in tail and "ok" in tail


def test_report_rate_limit(client, headers):
    codes = [client.post("/reports", json=report(), headers=headers).status_code for _ in range(22)]
    assert codes.count(201) == 20 and codes[-1] == 429


# --------------------------------------------------------------------- CLI
def test_usage_cli_lists_unused_first(client, settings, headers, capsys, monkeypatch):
    client.post("/usage", json={"client": "desktop/1", "days": {
        TODAY: {"recipe_open": 5, "search": 1, "active": 1},
        (date.today() - timedelta(days=1)).isoformat(): {"recipe_open": 2, "active": 1}}}, headers=headers)
    conn = db.connect(settings.db_path)
    text = cli.usage_table(conn, 56)
    conn.close()
    lines = text.splitlines()
    events = [ln.split()[0] for ln in lines if ln.split() and ln.split()[0] in usage.EVENTS]
    assert len(events) == len(usage.EVENTS)
    assert events[-1] == "recipe_open" and events[-2] == "search"  # least used first
    open_line = next(ln for ln in lines if ln.startswith("recipe_open"))
    assert open_line.split()[-2:] == ["7", "2"]  # total 7, used on 2 distinct days
    assert next(ln for ln in lines if ln.startswith("kitchen_open")).split()[-2:] == ["0", "0"]
    assert any(ln.startswith("actifs desktop") and ln.split()[-1] == "2" for ln in lines)

    monkeypatch.setenv("MAMARMITON_SERVER_DATA", str(settings.data_dir))
    assert cli.main(["usage", "--days", "28"]) == 0
    assert "kitchen_open" in capsys.readouterr().out


def test_reports_cli_marks_read(client, settings, headers, capsys, monkeypatch):
    monkeypatch.setenv("MAMARMITON_SERVER_DATA", str(settings.data_dir))
    client.post("/reports", json=report(message="premier"), headers=headers)
    assert cli.main(["reports"]) == 0
    out = capsys.readouterr().out
    assert "premier" in out and "@maman" in out and "ligne 2" in out
    assert cli.main(["reports"]) == 0
    assert "Aucun nouveau message" in capsys.readouterr().out
    assert cli.main(["reports", "--all"]) == 0
    assert "premier" in capsys.readouterr().out
    client.post("/reports", json=report(message="second"), headers=headers)
    assert cli.main(["reports"]) == 0
    out = capsys.readouterr().out
    assert "second" in out and "premier" not in out


# ----------------------------------------------- one event list everywhere
def test_event_lists_are_identical_on_the_three_clients():
    from mamarmiton import usage as desktop
    web = (ROOT / "server/mamarmiton_server/web/usage.js").read_text(encoding="utf-8")
    block = re.search(r"export const EVENTS = \[(.*?)\];", web, re.S).group(1)
    assert re.findall(r'"(\w+)"', block) == list(usage.EVENTS) == list(desktop.EVENTS)
    assert desktop.ACTIVE == usage.ACTIVE


def test_service_worker_precaches_new_modules():
    sw = (ROOT / "server/mamarmiton_server/web/sw.js").read_text(encoding="utf-8")
    for name in (ROOT / "server/mamarmiton_server/web").glob("*.js"):
        if name.name != "sw.js":
            assert f"/app/{name.name}" in sw, name.name
