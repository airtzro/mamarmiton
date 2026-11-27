"""Backup export and import (GET /export, POST /import/backup)."""

import io
import json
import zipfile

from fastapi.testclient import TestClient

from mamarmiton.models import Recipe
from mamarmiton_server.api import create_app
from mamarmiton_server import portable

from test_api import RID, RID2, client, jpeg_bytes, put, recipe, register, settings  # noqa: F401


def import_file(client, headers, name, content):
    return client.post("/import/backup", files={"file": (name, content)}, headers=headers)


def make_zip(entries: dict) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        for name, data in entries.items():
            z.writestr(name, data)
    return out.getvalue()


def book(client, headers) -> dict:
    data = client.get("/recipes", headers=headers).json()
    return {r["id"]: r for r in data["recipes"]}


def test_auth_required(client):
    assert client.get("/export").status_code == 401
    assert client.post("/import/backup", files={"file": ("a.json", b"[]")}).status_code == 401


def test_round_trip_with_photos(client, settings):
    a = register(client, settings, "maman")
    put(client, a, RID, nom="Tarte", tags=["Noël"])
    put(client, a, RID2, nom="Soupe")
    client.put(f"/recipes/{RID}/favorite", headers=a)
    for _ in range(2):
        assert client.post(f"/recipes/{RID}/images", files={"file": ("p.jpg", jpeg_bytes())}, headers=a).status_code == 201
    r = client.get("/export", headers=a)
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/zip"
    assert "mamarmiton-sauvegarde-" in r.headers["content-disposition"]
    z = zipfile.ZipFile(io.BytesIO(r.content))
    exported = {x["id"]: x for x in json.loads(z.read("recettes.json"))}
    assert set(exported) == {RID, RID2}
    assert exported[RID]["favori"] and len(exported[RID]["images"]) == 2
    for name in exported[RID]["images"]:
        assert f"images/{RID}/{name}" in z.namelist()
    Recipe.from_dict(exported[RID])  # readable by the desktop
    assert not list(settings.data_dir.glob(".export-*"))  # temp file cleaned up

    b = register(client, settings, "tata")
    r = import_file(client, b, "x.zip", r.content)
    assert r.json() == {"added": 2, "skipped": 0, "invalid": 0, "photos": 2}
    mine = {e["recipe"]["nom"]: e for e in book(client, b).values()}  # maman owns the ids: new ones here
    tarte = mine["Tarte"]
    assert tarte["id"] != RID and len(tarte["images"]) == 2 and tarte["recipe"]["tags"] == ["Noël"]
    assert tarte["owner"] == "tata" and tarte["credit"]["author"] == "tata"
    assert client.get(f"/recipes/{tarte['id']}/images/{tarte['images'][0]['id']}", headers=b).status_code == 200
    assert tarte["id"] in client.get("/recipes", headers=b).json()["favorites"]


def test_import_is_idempotent(client, settings):
    a = register(client, settings, "maman")
    put(client, a, RID)
    data = client.get("/export", headers=a).content
    assert import_file(client, a, "x.zip", data).json() == {"added": 0, "skipped": 1, "invalid": 0, "photos": 0}


def test_desktop_v4_with_bom(client, settings):
    a = register(client, settings, "maman")
    raw = json.dumps([{"id": RID, "nom": "Crêpes", "pour": 6, "ingredients": [{"nom": "lait", "quantité": "1 L"}],
                       "etapes": ["Battre"], "favori": True}], ensure_ascii=False).encode("utf-8-sig")
    assert import_file(client, a, "recette.json", raw).json()["added"] == 1
    entry = book(client, a)[RID]
    assert entry["recipe"]["nom"] == "Crêpes" and entry["recipe"]["ingredients"][0]["quantité"] == "1 L"


def test_desktop_v3_without_ids(client, settings):
    a = register(client, settings, "maman")
    v3 = [{"nom": "Flan", "temps": 30, "ingredients": ["oeufs", {"nom": "sucre", "quantité": "100 g"}], "etapes": "Cuire"},
          {"nom": "Flan", "temps": 30}]
    r = import_file(client, a, "recette.json", json.dumps({"recettes": v3}).encode())
    assert r.json()["added"] == 2  # no id: each is its own recipe, like the desktop
    assert len(book(client, a)) == 2


def test_id_owned_by_another_user_gets_a_fresh_id(client, settings):
    a = register(client, settings, "maman")
    b = register(client, settings, "tata")
    put(client, a, RID, nom="Secret de maman")
    raw = json.dumps([{"id": RID, "nom": "Ma version"}]).encode()
    assert import_file(client, b, "r.json", raw).json()["added"] == 1
    mine = book(client, b)
    assert RID not in mine and [e["recipe"]["nom"] for e in mine.values()] == ["Ma version"]
    assert book(client, a)[RID]["recipe"]["nom"] == "Secret de maman"


def test_deleted_recipe_comes_back(client, settings):
    a = register(client, settings, "maman")
    put(client, a, RID, nom="Tarte")
    client.delete(f"/recipes/{RID}", headers=a)
    raw = json.dumps([{"id": RID, "nom": "Tarte"}]).encode()
    assert import_file(client, a, "r.json", raw).json()["added"] == 1
    assert book(client, a)[RID]["recipe"]["nom"] == "Tarte"


def test_invalid_entries_are_counted_not_fatal(client, settings):
    a = register(client, settings, "maman")
    items = [{"id": RID, "nom": "Bonne"}, "pas une recette", 42, {"id": RID2, "nom": "x" * 500},
             {"nom": "Bonne", "id": RID}, {"nom": "Tags", "tags": "pas une liste"}]
    r = import_file(client, a, "r.json", json.dumps(items).encode())
    assert r.json() == {"added": 1, "skipped": 1, "invalid": 4, "photos": 0}


def test_not_a_backup(client, settings):
    a = register(client, settings, "maman")
    assert import_file(client, a, "r.json", b"not json").status_code == 422
    assert import_file(client, a, "r.json", b'"text"').status_code == 422
    assert import_file(client, a, "r.zip", make_zip({"notes.txt": "x"})).status_code == 422
    assert import_file(client, a, "r.zip", b"PK\x03\x04garbage").status_code == 422


def test_zipped_desktop_folder(client, settings):
    a = register(client, settings, "maman")
    content = json.dumps([{"id": RID, "nom": "Tarte", "images": ["a.jpg", "../evil.jpg", "missing.jpg", "bad.jpg"]}])
    z = make_zip({"recette_app/recette.json": content.encode("utf-8-sig"),
                  f"recette_app/images/{RID}/a.jpg": jpeg_bytes(), f"recette_app/images/{RID}/bad.jpg": b"nope",
                  "images/evil.jpg": jpeg_bytes()})
    assert import_file(client, a, "recette_app.zip", z).json() == {"added": 1, "skipped": 0, "invalid": 0, "photos": 1}
    assert len(book(client, a)[RID]["images"]) == 1


def test_traversal_and_symlink_entries_ignored(client, settings):
    a = register(client, settings, "maman")
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        z.writestr("recettes.json", json.dumps([{"id": RID, "nom": "T", "images": ["l.jpg", "x.jpg"]}]))
        z.writestr(f"images/{RID}/x.jpg", jpeg_bytes())
        link = zipfile.ZipInfo(f"images/{RID}/l.jpg")
        link.external_attr = 0o120777 << 16
        z.writestr(link, f"x.jpg")
        z.writestr("../../etc/recettes.json", "[]")
        z.writestr("/abs/recettes.json", "[]")
    r = import_file(client, a, "r.zip", out.getvalue())
    assert r.json() == {"added": 1, "skipped": 0, "invalid": 0, "photos": 1}
    assert not (settings.data_dir.parent / "etc").exists()


def test_path_helpers():
    assert portable.safe_name("a/../b") is None
    assert portable.safe_name("/etc/passwd") is None
    assert portable.safe_name("C:\\x") is None
    assert portable.safe_name("a\\b/./c") == "a/b/c"


def test_too_many_files_refused(client, settings, monkeypatch):
    a = register(client, settings, "maman")
    monkeypatch.setattr(portable, "MAX_ZIP_FILES", 3)
    z = make_zip({"recettes.json": "[]", "a": "1", "b": "2", "c": "3"})
    assert import_file(client, a, "r.zip", z).status_code == 422


def test_oversized_archive_refused(client, settings, monkeypatch):
    a = register(client, settings, "maman")
    monkeypatch.setattr(portable, "MAX_ZIP_TOTAL", 1000)
    z = make_zip({"recettes.json": "[]", "big.bin": b"0" * 5000})
    assert import_file(client, a, "r.zip", z).status_code == 422


def test_oversized_json_and_photo_refused(client, settings, monkeypatch):
    a = register(client, settings, "maman")
    monkeypatch.setattr(portable, "MAX_JSON_BYTES", 100)
    assert import_file(client, a, "r.json", b" " * 200 + b"[]").status_code == 422
    assert import_file(client, a, "r.zip", make_zip({"recettes.json": b" " * 200 + b"[]"})).status_code == 422
    monkeypatch.setattr(portable, "MAX_JSON_BYTES", 10_000_000)
    settings.max_image_mb = 0  # every photo is over the per-file limit: recipe still comes in, photo skipped
    z = make_zip({"recettes.json": json.dumps([{"id": RID, "nom": "T", "images": ["x.jpg"]}]),
                  f"images/{RID}/x.jpg": jpeg_bytes()})
    assert import_file(client, a, "r.zip", z).json() == {"added": 1, "skipped": 0, "invalid": 0, "photos": 0}


def test_failure_leaves_nothing_behind(client, settings, monkeypatch):
    a = register(client, settings, "maman")
    monkeypatch.setattr("mamarmiton_server.api.MAX_RECIPES_PER_USER", 1)
    items = [{"id": RID, "nom": "A", "images": ["x.jpg"]}, {"id": RID2, "nom": "B"}]
    z = make_zip({"recettes.json": json.dumps(items), f"images/{RID}/x.jpg": jpeg_bytes()})
    assert import_file(client, a, "r.zip", z).status_code == 409
    assert book(client, a) == {}
    assert not any(settings.images_dir.iterdir())


def test_upload_size_limit(client, settings):
    a = register(client, settings, "maman")
    settings.backup_max_mb = 0  # read when the app starts
    client = TestClient(create_app(settings))
    big = client.post("/import/backup", files={"file": ("r.json", b" " * (2 * 1024 * 1024))}, headers=a)
    assert big.status_code == 413
