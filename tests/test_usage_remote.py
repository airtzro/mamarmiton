"""Desktop client against the real server: counters and reports end to end."""

import sqlite3
from datetime import date

import pytest

pytest.importorskip("mamarmiton_server")

from fastapi.testclient import TestClient  # noqa: E402

from mamarmiton import report, usage  # noqa: E402
from mamarmiton.remote import ApiClient  # noqa: E402
from mamarmiton_server.api import create_app  # noqa: E402
from mamarmiton_server.config import Settings  # noqa: E402
from test_remote import PASSWORD, make_invite  # noqa: E402


def test_counters_and_reports_reach_the_server(tmp_path):
    settings = Settings(data_dir=tmp_path / "server")
    client = ApiClient("http://testserver", http=TestClient(create_app(settings)))
    client.register("alice", PASSWORD, make_invite(settings), display_name="Alice")
    usage.hit("recipe_open")
    usage.hit("search")
    assert usage.counter().flush(client, force=True)
    assert usage.counter().snapshot() == {}
    assert report.submit(client, report.build("ça plante", attach_log=False)) is True
    conn = sqlite3.connect(settings.db_path)
    assert sorted(conn.execute("SELECT day, client_kind, event, count FROM usage_daily")) == [
        (date.today().isoformat(), "desktop", "active", 1),
        (date.today().isoformat(), "desktop", "recipe_open", 1),
        (date.today().isoformat(), "desktop", "search", 1)]
    assert conn.execute("SELECT message, client FROM reports").fetchall() == [("ça plante", "desktop")]
    conn.close()
