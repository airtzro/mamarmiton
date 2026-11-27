"""Recipe credit (the author's name follows every variant) and the three share levels."""

import sqlite3

from mamarmiton_server import db
from tests.test_api import PASSWORD, RID, RID2, client, jpeg_bytes, put, recipe, register, settings  # noqa: F401

RID3 = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"


def share(client, owner, username, *, rid=RID, edit=False, copy=None):
    body = {"can_edit": edit} if copy is None else {"can_edit": edit, "can_copy": copy}
    r = client.put(f"/recipes/{rid}/shares/{username}", json=body, headers=owner)
    assert r.status_code == 204, r.text


def variant_put(client, headers, source, rid=RID2):
    return client.put(f"/recipes/{rid}", json={"recipe": recipe("Ma tarte"), "base_version": None,
                                               "variant_of": source}, headers=headers)


def test_own_recipe_is_credited_to_its_author_and_editors_do_not_change_that(client, settings):
    maman, fille = register(client, settings, "maman"), register(client, settings, "fille")
    out = put(client, maman).json()
    assert out["credit"] == {"author": "maman", "author_name": "Maman", "variant_of": None, "variant_of_nom": ""}
    assert out["can_copy"] is True
    share(client, maman, "fille", edit=True)
    # a client cannot write the credit: unknown keys in the recipe are dropped
    out = client.put(f"/recipes/{RID}", json={"recipe": recipe("Gâteau", credit={"author": "fille"}, author="fille"),
                                              "base_version": 1}, headers=fille).json()
    assert out["credit"]["author"] == "maman" and "author" not in out["recipe"]


def test_read_only_share_refuses_variants(client, settings):
    maman, fille = register(client, settings, "maman"), register(client, settings, "fille")
    put(client, maman)
    share(client, maman, "fille", copy=False)
    seen = client.get(f"/recipes/{RID}", headers=fille).json()
    assert seen["access"] == "read" and seen["can_copy"] is False
    assert client.get("/recipes", headers=fille).json()["recipes"][0]["can_copy"] is False
    assert client.post(f"/recipes/{RID}/variant", headers=fille).status_code == 403
    r = variant_put(client, fille, RID)
    assert r.status_code == 403 and "variantes" in r.json()["detail"]
    assert client.get(f"/recipes/{RID2}", headers=fille).status_code == 404  # nothing was created


def test_variant_keeps_the_original_authors_credit(client, settings):
    maman, fille = register(client, settings, "maman"), register(client, settings, "fille")
    put(client, maman, name="Tarte aux pommes")
    client.post(f"/recipes/{RID}/images", files={"file": ("a.jpg", jpeg_bytes(), "image/jpeg")}, headers=maman)
    share(client, maman, "fille", copy=True)
    out = client.post(f"/recipes/{RID}/variant", headers=fille)
    assert out.status_code == 201, out.text
    copy = out.json()
    assert copy["owner"] == "fille" and copy["access"] == "owner"
    assert copy["recipe"]["nom"] == "Tarte aux pommes (variante)"
    assert copy["credit"] == {"author": "maman", "author_name": "Maman", "variant_of": RID,
                              "variant_of_nom": "Tarte aux pommes"}
    assert len(copy["images"]) == 1
    assert client.get(f"/recipes/{copy['id']}/images/{copy['images'][0]['id']}", headers=fille).status_code == 200
    # editing her variant never removes the credit
    edited = client.put(f"/recipes/{copy['id']}", json={"recipe": recipe("Ma tarte à moi"), "base_version": 1},
                        headers=fille).json()
    assert edited["credit"]["author"] == "maman"
    # a variant of the variant still credits the first author
    out = client.post(f"/recipes/{copy['id']}/variant", headers=fille).json()
    assert out["credit"]["author"] == "maman" and out["credit"]["variant_of_nom"] == "Ma tarte à moi"


def test_desktop_variant_through_put_and_deleted_source(client, settings):
    maman, fille = register(client, settings, "maman"), register(client, settings, "fille")
    put(client, maman, name="Clafoutis")
    share(client, maman, "fille", copy=True)
    out = variant_put(client, fille, RID)
    assert out.status_code == 200, out.text
    assert out.json()["credit"]["author"] == "maman" and out.json()["credit"]["variant_of_nom"] == "Clafoutis"
    # made offline, sent after the original was deleted: the credit still comes along
    assert client.delete(f"/recipes/{RID}", headers=maman).status_code == 204
    out = variant_put(client, fille, RID, rid=RID3).json()
    assert out["credit"]["author"] == "maman" and out["credit"]["variant_of"] == RID
    # a recipe I cannot see is no source
    assert variant_put(client, maman, "b" * 32, rid="c" * 32).status_code == 404


def test_undo_delete_keeps_the_credit(client, settings):
    maman, fille = register(client, settings, "maman"), register(client, settings, "fille")
    put(client, maman)
    share(client, maman, "fille", copy=True)
    variant_put(client, fille, RID)
    assert client.delete(f"/recipes/{RID2}", headers=fille).status_code == 204
    out = client.put(f"/recipes/{RID2}", json={"recipe": recipe("Ma tarte"), "base_version": None},
                     headers=fille).json()
    assert out["credit"]["author"] == "maman"


def test_share_levels_and_older_apps(client, settings):
    maman, fille = register(client, settings, "maman"), register(client, settings, "fille")
    put(client, maman)
    share(client, maman, "fille")  # an older app: no can_copy, a new share allows variants as before
    level = lambda: client.get("/shares", headers=maman).json()["recipes_shared"][0]  # noqa: E731
    assert level()["can_copy"] is True
    share(client, maman, "fille", copy=False)
    assert level()["can_copy"] is False and level()["can_edit"] is False
    share(client, maman, "fille")  # an older app changing something else keeps the choice
    assert level()["can_copy"] is False
    share(client, maman, "fille", edit=True, copy=False)  # editing always allows variants
    assert level()["can_copy"] is True
    # a whole book, read only
    client.put("/shares/book/fille", json={"can_edit": False, "can_copy": False}, headers=maman)
    put(client, maman, rid=RID3, name="Autre")
    assert client.post(f"/recipes/{RID3}/variant", headers=fille).status_code == 403
    assert client.get("/shares", headers=fille).json()["books_shared_with_me"][0]["can_copy"] is False
    # the single-recipe share (edit) still allows variants of that one
    assert client.post(f"/recipes/{RID}/variant", headers=fille).status_code == 201


def test_variant_keeps_the_name_when_the_authors_account_is_deleted(client, settings):
    admin = register(client, settings, "bootstrap")  # noqa: F841  creates the admin row
    maman, fille = register(client, settings, "maman"), register(client, settings, "fille")
    put(client, maman)
    share(client, maman, "fille", copy=True)
    rid = client.post(f"/recipes/{RID}/variant", headers=fille).json()["id"]
    token = client.post("/auth/login", json={"username": "admin", "password": PASSWORD}).json()["token"]
    r = client.post("/admin/api/users/maman/delete", json={"admin_password": PASSWORD, "confirm_username": "maman"},
                    headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, r.text
    credit = client.get(f"/recipes/{rid}", headers=fille).json()["credit"]
    assert credit["author"] is None and credit["author_name"] == "Maman"


def test_migration_credits_existing_recipes_and_keeps_shares_as_they_were(tmp_path):
    path = tmp_path / "x.sqlite3"
    conn = sqlite3.connect(path)
    conn.executescript(db.SCHEMA)
    conn.execute("INSERT INTO users VALUES (1, 'maman', 'Maman', 'h', 1, 't')")
    conn.execute("INSERT INTO users VALUES (2, 'fille', 'Fille', 'h', 0, 't')")
    conn.execute("INSERT INTO recipes VALUES ('r1', 1, '{}', 1, 1, 't', 0)")
    conn.execute("INSERT INTO recipe_shares VALUES ('r1', 2, 0, 't')")
    conn.execute("INSERT INTO book_shares VALUES (1, 2, 0, 't')")
    conn.commit()
    conn.close()
    db.migrate(path)
    conn = sqlite3.connect(path)
    assert conn.execute("SELECT author_id, author_name, variant_of FROM recipes").fetchone() == (1, "Maman", None)
    assert conn.execute("SELECT can_copy FROM recipe_shares").fetchone() == (1,)
    assert conn.execute("SELECT can_copy FROM book_shares").fetchone() == (1,)
    conn.close()
