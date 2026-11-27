"""Account tiers and monthly AI credits: base = no AI, plus = 30 a month, admin can add more."""

import sqlite3

import pytest
from fastapi.testclient import TestClient

from mamarmiton_server import db, importer, tiers
from mamarmiton_server.api import create_app
from mamarmiton_server.config import Settings

from test_admin import PASSWORD, register  # noqa: F401
from test_import import RECIPE_TEXT, FakeModel, FakeOcr, post

ANSWER = {"nom": "Tarte", "ingredients": [{"nom": "farine", "quantité": "250 g"}], "etapes": ["Cuire."]}


@pytest.fixture
def settings(tmp_path):
    return Settings(data_dir=tmp_path, login_attempts=3, login_window_s=60)


@pytest.fixture
def app(settings, monkeypatch):
    monkeypatch.setattr(importer.pipeline, "rules_are_clear", lambda rules: False)  # always ask the model
    app = create_app(settings)
    app.state.importer.ocr = FakeOcr(ok=False)
    app.state.importer.model = FakeModel(ANSWER)
    return app


@pytest.fixture
def client(app):
    return TestClient(app)


@pytest.fixture
def admin_headers(client, settings):
    register(client, settings, "bootstrap")
    r = client.post("/auth/login", json={"username": "admin", "password": PASSWORD, "device": "admin"})
    return {"Authorization": f"Bearer {r.json()['token']}"}


@pytest.fixture
def maman(client, settings):
    return register(client, settings, "maman")


def set_user(client, admin_headers, **body):
    r = client.patch("/admin/api/users/maman", headers=admin_headers, json=body)
    assert r.status_code == 200, r.text
    return r.json()


def test_new_accounts_are_base_and_get_no_ai(client, maman):
    me = client.get("/me", headers=maman).json()
    assert me["tier"] == "base" and me["ai_allowance"] == 0
    body = post(client, maman, text=RECIPE_TEXT).json()
    assert body["method"] == "regles"
    assert importer.W_NO_AI_TIER in body["warnings"]


def test_plus_gets_thirty_then_falls_back_to_rules(client, app, settings, admin_headers, maman):
    assert set_user(client, admin_headers, tier="plus")["ai_allowance"] == tiers.TIERS["plus"] == 30
    uid = sqlite3.connect(settings.db_path).execute("SELECT id FROM users WHERE username='maman'").fetchone()[0]
    conn = db.connect(settings.db_path)
    conn.execute("INSERT INTO ai_usage (user_id, month, used) VALUES (?, ?, 29)", (uid, tiers.month()))
    assert post(client, maman, text=RECIPE_TEXT).json()["method"] == "ia"
    body = post(client, maman, text=RECIPE_TEXT).json()
    assert body["method"] == "regles" and importer.W_NO_AI_CREDIT in body["warnings"]
    status = client.get("/import/status", headers=maman).json()
    assert status["ai_used"] == 30 and status["ai_left"] == 0


def test_admin_extra_credits_and_reset(client, admin_headers, maman):
    r = set_user(client, admin_headers, ai_extra=2)  # still base: only the extra
    assert r["ai_allowance"] == 2
    assert post(client, maman, text=RECIPE_TEXT).json()["method"] == "ia"
    assert post(client, maman, text=RECIPE_TEXT).json()["method"] == "ia"
    assert post(client, maman, text=RECIPE_TEXT).json()["method"] == "regles"
    assert set_user(client, admin_headers, reset_ai_month=True)["ai_left"] == 2
    users = {u["username"]: u for u in client.get("/admin/api/users", headers=admin_headers).json()}
    assert users["maman"]["ai_extra"] == 2 and users["maman"]["ai_used"] == 0


def test_failed_model_gives_the_credit_back(client, app, admin_headers, maman):
    set_user(client, admin_headers, tier="plus")
    app.state.importer.model = FakeModel(None)  # model answers nothing
    assert post(client, maman, text=RECIPE_TEXT).json()["method"] == "regles"
    assert client.get("/me", headers=maman).json()["ai_used"] == 0


def test_only_admins_change_tiers(client, maman):
    assert client.patch("/admin/api/users/maman", headers=maman, json={"tier": "plus"}).status_code == 403
    assert client.get("/admin/api/tiers", headers=maman).status_code == 403


def test_migration_keeps_existing_accounts_on_plus(tmp_path):
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT, display_name TEXT, "
                 "password_hash TEXT, is_admin INTEGER, created_at TEXT)")
    conn.execute("INSERT INTO users VALUES (1, 'maman', 'Maman', 'x', 0, '2026-01-01')")
    conn.execute("PRAGMA user_version = 5")
    conn.commit()
    conn.close()
    conn = sqlite3.connect(path)
    conn.execute("BEGIN")
    db._m6_tiers_and_ai_credits(conn)
    conn.execute("COMMIT")
    assert conn.execute("SELECT tier, ai_extra FROM users").fetchone() == ("plus", 0)
