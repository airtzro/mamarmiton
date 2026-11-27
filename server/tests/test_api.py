import io
import sqlite3

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from mamarmiton_server import db, security
from mamarmiton_server.api import create_app
from mamarmiton_server.config import Settings

PASSWORD = "correct horse battery"


@pytest.fixture
def settings(tmp_path):
    return Settings(data_dir=tmp_path, login_attempts=3, login_window_s=60)


@pytest.fixture
def client(settings):
    return TestClient(create_app(settings))


def make_invite(settings) -> str:
    conn = db.connect(settings.db_path)
    admin = conn.execute("SELECT id FROM users WHERE is_admin = 1").fetchone()
    if admin is None:
        admin_id = conn.execute("INSERT INTO users (username, display_name, password_hash, is_admin, created_at) "
                                "VALUES ('admin', 'Admin', ?, 1, ?)",
                                (security.hash_password(PASSWORD), db.now())).lastrowid
    else:
        admin_id = admin[0]
    code = security.new_invite_code()
    conn.execute("INSERT INTO invites (code_hash, created_by, note, created_at, expires_at) VALUES (?, ?, '', ?, ?)",
                 (security.sha256(code), admin_id, db.now(), "2999-01-01T00:00:00+00:00"))
    conn.close()
    return code


def register(client, settings, username) -> dict:
    r = client.post("/auth/register", json={"invite": make_invite(settings), "username": username,
                                            "password": PASSWORD, "display_name": username.title()})
    assert r.status_code == 201, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def recipe(name="Gâteau", **extra):
    return {"nom": name, "ingredients": [{"nom": "farine", "quantité": "200 g"}], "etapes": ["Mélanger"], **extra}


RID = "0123456789abcdef0123456789abcdef"
RID2 = "fedcba9876543210fedcba9876543210"


def put(client, headers, rid=RID, base=None, **kw):
    return client.put(f"/recipes/{rid}", json={"recipe": recipe(**kw), "base_version": base}, headers=headers)


def jpeg_bytes(size=(64, 48)) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", size, "orange").save(out, "JPEG")
    return out.getvalue()


# --------------------------------------------------------------------- auth
def test_register_needs_valid_invite(client):
    r = client.post("/auth/register", json={"invite": "AAAA-BBBB-CCCC-DDDD", "username": "eve", "password": PASSWORD})
    assert r.status_code == 403


def test_invite_is_single_use(client, settings):
    code = make_invite(settings)
    body = {"invite": code.lower().replace("-", " "), "username": "maman", "password": PASSWORD}
    assert client.post("/auth/register", json=body).status_code == 201  # spacing/case do not matter
    body["username"] = "other"
    assert client.post("/auth/register", json=body).status_code == 403


def test_weak_password_and_bad_username_refused(client, settings):
    code = make_invite(settings)
    assert client.post("/auth/register", json={"invite": code, "username": "maman", "password": "short"}).status_code == 422
    assert client.post("/auth/register", json={"invite": code, "username": "../x", "password": PASSWORD}).status_code == 422


def test_login_logout(client, settings):
    register(client, settings, "maman")
    r = client.post("/auth/login", json={"username": "MAMAN", "password": PASSWORD})
    assert r.status_code == 200
    headers = {"Authorization": f"Bearer {r.json()['token']}"}
    assert client.get("/me", headers=headers).json()["username"] == "maman"
    assert client.post("/auth/logout", headers=headers).status_code == 204
    assert client.get("/me", headers=headers).status_code == 401


def test_requests_without_token_are_refused(client):
    assert client.get("/recipes").status_code == 401
    assert client.get("/recipes", headers={"Authorization": "Bearer nope"}).status_code == 401


def test_login_rate_limited(client, settings):
    register(client, settings, "maman")
    for _ in range(3):
        assert client.post("/auth/login", json={"username": "maman", "password": "wrong-password"}).status_code == 401
    r = client.post("/auth/login", json={"username": "maman", "password": PASSWORD})
    assert r.status_code == 429  # even the right password waits now
    assert int(r.headers["Retry-After"]) > 0


def test_tokens_are_stored_hashed(client, settings):
    headers = register(client, settings, "maman")
    token = headers["Authorization"].split()[1]
    conn = sqlite3.connect(settings.db_path)
    stored = [r[0] for r in conn.execute("SELECT token_hash FROM sessions")]
    assert token not in stored and security.sha256(token) in stored


def test_password_change_signs_out_other_devices(client, settings):
    first = register(client, settings, "maman")
    r = client.post("/auth/login", json={"username": "maman", "password": PASSWORD})
    second = {"Authorization": f"Bearer {r.json()['token']}"}
    r = client.post("/me/password", json={"old_password": PASSWORD, "new_password": "une autre phrase"}, headers=first)
    assert r.status_code == 204
    assert client.get("/me", headers=first).status_code == 200
    assert client.get("/me", headers=second).status_code == 401


def test_only_admin_creates_invites(client, settings):
    user = register(client, settings, "maman")
    assert client.post("/invites", json={}, headers=user).status_code == 403
    r = client.post("/auth/login", json={"username": "admin", "password": PASSWORD})
    admin = {"Authorization": f"Bearer {r.json()['token']}"}
    r = client.post("/invites", json={"note": "soeur"}, headers=admin)
    assert r.status_code == 201
    assert client.post("/auth/register", json={"invite": r.json()["code"], "username": "soeur",
                                               "password": PASSWORD}).status_code == 201


# ------------------------------------------------------------------ recipes
def test_create_read_update_with_versions(client, settings):
    h = register(client, settings, "maman")
    r = put(client, h)
    assert r.status_code == 200 and r.json()["version"] == 1 and r.json()["access"] == "owner"
    assert "favori" not in r.json()["recipe"]
    assert put(client, h).status_code == 409  # create again
    assert put(client, h, base=1, name="Gâteau v2").json()["version"] == 2
    stale = put(client, h, base=1, name="conflit")
    assert stale.status_code == 409
    assert stale.json()["detail"]["current"]["recipe"]["nom"] == "Gâteau v2"


def test_recipe_is_normalised_and_limited(client, settings):
    h = register(client, settings, "maman")
    r = client.put(f"/recipes/{RID}", json={"recipe": {"nom": "X", "temps": "1h30", "type_plat": "dessert"}}, headers=h)
    assert r.json()["recipe"]["temps"] == 90 and r.json()["recipe"]["type_plat"] == "Dessert"
    r = client.put(f"/recipes/{RID2}", json={"recipe": {"nom": "X", "notes": "a" * 30000}}, headers=h)
    assert r.status_code == 422
    assert client.put("/recipes/..%2Fetc", json={"recipe": recipe()}, headers=h).status_code in (404, 422)


def test_other_users_cannot_see_or_touch_recipes(client, settings):
    maman = register(client, settings, "maman")
    eve = register(client, settings, "eve")
    put(client, maman)
    assert client.get(f"/recipes/{RID}", headers=eve).status_code == 404
    assert put(client, eve, base=1).status_code == 404
    assert put(client, eve).status_code == 404  # cannot take over the id either
    assert client.delete(f"/recipes/{RID}", headers=eve).status_code == 404
    assert client.get("/recipes", headers=eve).json()["recipes"] == []


def test_sync_since_and_deletions(client, settings):
    h = register(client, settings, "maman")
    put(client, h)
    first = client.get("/recipes", headers=h).json()
    assert [r["id"] for r in first["recipes"]] == [RID]
    assert client.get(f"/recipes?since={first['seq']}", headers=h).json()["recipes"] == []
    put(client, h, rid=RID2)
    client.delete(f"/recipes/{RID}", headers=h)
    later = client.get(f"/recipes?since={first['seq']}", headers=h).json()
    by_id = {r["id"]: r for r in later["recipes"]}
    assert by_id[RID]["deleted"] and not by_id[RID2]["deleted"]
    assert later["visible_ids"] == [RID2]


def test_favorites_are_per_user(client, settings):
    maman = register(client, settings, "maman")
    soeur = register(client, settings, "soeur")
    put(client, maman)
    client.put("/shares/book/soeur", json={"can_edit": False}, headers=maman)
    assert client.put(f"/recipes/{RID}/favorite", headers=soeur).status_code == 204
    assert client.get("/recipes", headers=soeur).json()["favorites"] == [RID]
    assert client.get("/recipes", headers=maman).json()["favorites"] == []


# ------------------------------------------------------------------ sharing
def test_book_share_read_only_then_editable_then_revoked(client, settings):
    maman = register(client, settings, "maman")
    soeur = register(client, settings, "soeur")
    put(client, maman)
    seq = client.get("/recipes", headers=soeur).json()["seq"]
    client.put("/shares/book/soeur", json={"can_edit": False}, headers=maman)
    sync = client.get(f"/recipes?since={seq}", headers=soeur).json()
    assert [(r["id"], r["access"], r["owner"]) for r in sync["recipes"]] == [(RID, "read", "maman")]
    assert put(client, soeur, base=1).status_code == 403
    client.put("/shares/book/soeur", json={"can_edit": True}, headers=maman)
    assert put(client, soeur, base=1, name="corrigé").status_code == 200
    assert client.delete(f"/recipes/{RID}", headers=soeur).status_code == 403  # only the owner deletes
    client.delete("/shares/book/soeur", headers=maman)
    assert client.get("/recipes?since=0", headers=soeur).json()["visible_ids"] == []
    assert client.get(f"/recipes/{RID}", headers=soeur).status_code == 404


def test_single_recipe_share(client, settings):
    maman = register(client, settings, "maman")
    soeur = register(client, settings, "soeur")
    put(client, maman)
    put(client, maman, rid=RID2, name="secret")
    assert client.put(f"/recipes/{RID}/shares/soeur", json={"can_edit": False}, headers=maman).status_code == 204
    assert client.get("/recipes", headers=soeur).json()["visible_ids"] == [RID]
    assert client.put(f"/recipes/{RID}/shares/maman", json={}, headers=soeur).status_code == 403
    shares = client.get("/shares", headers=maman).json()
    assert shares["recipes_shared"][0]["username"] == "soeur"
    client.delete(f"/recipes/{RID}/shares/soeur", headers=maman)
    assert client.get("/recipes", headers=soeur).json()["visible_ids"] == []


def test_grantee_can_leave_a_book(client, settings):
    maman = register(client, settings, "maman")
    soeur = register(client, settings, "soeur")
    put(client, maman)
    client.put("/shares/book/soeur", json={}, headers=maman)
    assert client.delete("/shares/received/maman", headers=soeur).status_code == 204
    assert client.get("/recipes", headers=soeur).json()["visible_ids"] == []


def test_grantee_takes_one_recipe_of_a_shared_book_off_their_list(client, settings):
    maman = register(client, settings, "maman")
    soeur = register(client, settings, "soeur")
    eve = register(client, settings, "eve")
    put(client, maman)
    put(client, maman, rid=RID2, name="Tarte")
    client.put("/shares/book/soeur", json={"can_edit": True}, headers=maman)
    client.put(f"/recipes/{RID}/favorite", headers=soeur)

    assert client.put(f"/recipes/{RID}/hidden", headers=eve).status_code == 404  # not shared with eve
    assert client.put(f"/recipes/{RID}/hidden", headers=maman).status_code == 422  # owners delete instead
    assert client.put(f"/recipes/{RID}/hidden", headers=soeur).status_code == 204
    assert client.put(f"/recipes/{RID}/hidden", headers=soeur).status_code == 204  # idempotent

    assert sorted(client.get("/recipes", headers=soeur).json()["visible_ids"]) == [RID2]
    assert client.get(f"/recipes/{RID}", headers=soeur).status_code == 404
    assert put(client, soeur, base=1, name="x").status_code == 404
    assert client.get(f"/recipes/{RID}", headers=maman).json()["recipe"]["nom"] == "Gâteau"  # owner untouched
    hidden = client.get("/shares", headers=soeur).json()["hidden"]
    assert hidden == [{"recipe_id": RID, "nom": "Gâteau", "username": "maman", "display_name": "Maman"}]

    # back on the list: the next incremental sync brings it again, favourite kept
    seq = client.get("/recipes", headers=soeur).json()["seq"]
    assert client.delete(f"/recipes/{RID}/hidden", headers=soeur).status_code == 204
    sync = client.get(f"/recipes?since={seq}", headers=soeur).json()
    assert [r["id"] for r in sync["recipes"]] == [RID] and sync["favorites"] == [RID]
    assert client.get("/shares", headers=soeur).json()["hidden"] == []


def test_hidden_recipe_comes_back_when_shared_again_on_purpose(client, settings):
    maman = register(client, settings, "maman")
    soeur = register(client, settings, "soeur")
    put(client, maman)
    client.put("/shares/book/soeur", json={}, headers=maman)
    client.put(f"/recipes/{RID}/hidden", headers=soeur)
    client.put("/shares/book/soeur", json={"can_edit": True}, headers=maman)  # book again: stays off
    assert client.get("/recipes", headers=soeur).json()["visible_ids"] == []
    client.put(f"/recipes/{RID}/shares/soeur", json={}, headers=maman)  # this very recipe: back
    assert client.get("/recipes", headers=soeur).json()["visible_ids"] == [RID]


def test_hidden_list_skips_recipes_no_longer_shared(client, settings):
    maman = register(client, settings, "maman")
    soeur = register(client, settings, "soeur")
    put(client, maman)
    client.put("/shares/book/soeur", json={}, headers=maman)
    client.put(f"/recipes/{RID}/hidden", headers=soeur)
    client.delete("/shares/book/soeur", headers=maman)
    assert client.get("/shares", headers=soeur).json()["hidden"] == []
    assert client.delete(f"/recipes/{RID}/hidden", headers=soeur).status_code == 404


# ------------------------------------------------------------------- images
def test_image_upload_is_reencoded_and_private(client, settings):
    maman = register(client, settings, "maman")
    eve = register(client, settings, "eve")
    put(client, maman)
    r = client.post(f"/recipes/{RID}/images", files={"file": ("p.jpg", jpeg_bytes(), "image/jpeg")}, headers=maman)
    assert r.status_code == 201, r.text
    image_id = r.json()["id"]
    got = client.get(f"/recipes/{RID}/images/{image_id}", headers=maman)
    assert got.status_code == 200 and got.headers["content-type"] == "image/jpeg"
    assert Image.open(io.BytesIO(got.content)).size == (64, 48)
    assert client.get(f"/recipes/{RID}/images/{image_id}", headers=eve).status_code == 404
    assert client.get(f"/recipes/{RID}", headers=maman).json()["images"][0]["id"] == image_id
    assert client.delete(f"/recipes/{RID}/images/{image_id}", headers=maman).status_code == 204
    assert not list((settings.images_dir / RID).iterdir())


def test_non_images_refused(client, settings):
    maman = register(client, settings, "maman")
    put(client, maman)
    for payload in (b"<script>alert(1)</script>", b"%PDF-1.4 hello", b"GIF89a" + b"\0" * 20):
        r = client.post(f"/recipes/{RID}/images", files={"file": ("x.jpg", payload, "image/jpeg")}, headers=maman)
        assert r.status_code == 415


def test_large_images_are_shrunk(client, settings):
    maman = register(client, settings, "maman")
    put(client, maman)
    r = client.post(f"/recipes/{RID}/images", files={"file": ("big.jpg", jpeg_bytes((4000, 3000)), "image/jpeg")},
                    headers=maman)
    got = client.get(f"/recipes/{RID}/images/{r.json()['id']}", headers=maman)
    assert max(Image.open(io.BytesIO(got.content)).size) == 2560


def test_deleting_recipe_removes_its_images(client, settings):
    maman = register(client, settings, "maman")
    put(client, maman)
    client.post(f"/recipes/{RID}/images", files={"file": ("p.jpg", jpeg_bytes(), "image/jpeg")}, headers=maman)
    client.delete(f"/recipes/{RID}", headers=maman)
    assert not (settings.images_dir / RID).exists()


def test_tags_round_trip_and_validation(client, settings):
    h = register(client, settings, "maman")
    r = put(client, h, tags=["Noël", " noel", "sans   gluten", "", "x" * 50])
    assert r.status_code == 200
    assert r.json()["recipe"]["tags"] == ["Noël", "sans gluten", "x" * 30]
    assert client.get(f"/recipes/{RID}", headers=h).json()["recipe"]["tags"] == ["Noël", "sans gluten", "x" * 30]
    assert put(client, h, rid=RID2).json()["recipe"]["tags"] == []  # old clients send none
    too_many = put(client, h, rid=RID2, base=1, tags=[f"t{i}" for i in range(40)])
    assert len(too_many.json()["recipe"]["tags"]) == 20  # same cap as the desktop
    for bad in ("Noël", 3, {"a": 1}, ["ok", 4], [["nested"]]):
        assert put(client, h, rid=RID2, base=2, tags=bad).status_code == 422


def test_no_photo_or_favorite_on_a_deleted_recipe(client, settings):
    maman = register(client, settings, "maman")
    assert put(client, maman).status_code == 200
    assert client.delete(f"/recipes/{RID}", headers=maman).status_code == 204
    r = client.post(f"/recipes/{RID}/images", files={"file": ("p.jpg", jpeg_bytes(), "image/jpeg")}, headers=maman)
    assert r.status_code == 404
    assert client.put(f"/recipes/{RID}/favorite", headers=maman).status_code == 404
    assert client.delete(f"/recipes/{RID}", headers=maman).status_code == 204  # still idempotent
    assert client.get(f"/recipes/{RID}", headers=maman).json()["deleted"] is True
