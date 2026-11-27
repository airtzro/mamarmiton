"""Data-integrity scenarios of the sync (RemoteStore against the real in-process server)."""

import json
import os
import threading

import httpx
import pytest

pytest.importorskip("mamarmiton_server")

from fastapi.testclient import TestClient  # noqa: E402

from mamarmiton.remote import ApiClient, AuthError, RemoteStore  # noqa: E402
from mamarmiton.remote import store as remote_store  # noqa: E402
from mamarmiton.storage import RecipeStore  # noqa: E402
from mamarmiton_server import api as server_api  # noqa: E402
from mamarmiton_server.api import create_app  # noqa: E402
from test_remote import (  # noqa: E402,F401
    PASSWORD, http, login_store, make_invite, make_store, net, png, recipe, settings,
)


def server_view(store, http):
    return ApiClient("http://testserver", token=store.client.token, http=http).list_recipes()


class FlakyNet:
    """Cuts the network after ``n`` requests (the request n+1 and later raise ConnectError)."""

    def __init__(self, http, n):
        self.left, self._request = n, http.request
        http.request = self.request

    def request(self, *a, **kw):
        if self.left <= 0:
            raise httpx.ConnectError("down")
        self.left -= 1
        return self._request(*a, **kw)


# ------------------------------------------------------------ crash safety
def test_crash_between_cache_and_outbox_write_replays(make_store, monkeypatch, net, http):
    """Killed after the first of the two files was written: cache and queue must still agree."""
    store = make_store("alice")
    r = store.add(recipe("Tarte"))
    assert store.sync().ok
    r.nom = "Tarte modifiée"
    real = remote_store._atomic_write_json
    calls = {"n": 0}

    def dying(path, data):
        calls["n"] += 1
        if calls["n"] == 2:  # the second file of the save never gets written
            raise KeyboardInterrupt("power cut")
        real(path, data)

    monkeypatch.setattr(remote_store, "_atomic_write_json", dying)
    with pytest.raises(KeyboardInterrupt):
        store.update(r)
    monkeypatch.setattr(remote_store, "_atomic_write_json", real)
    again = RemoteStore(store.client, cache_dir=store.cache_dir)
    if again.get(r.id).nom == "Tarte modifiée":
        assert again.pending_count == 1  # the edit is on disk, so it must still be queued
    assert again.sync().ok
    other = server_view(again, http)["recipes"][0]["recipe"]["nom"]
    assert other == again.get(r.id).nom


def test_corrupt_cache_keeps_outbox(make_store, net):
    store = make_store("alice")
    net.down = True
    r = store.add(recipe("Soupe"))
    store._cache_path.write_text('{"seq": 3, "recipes": {', encoding="utf-8")
    again = RemoteStore(store.client, cache_dir=store.cache_dir)
    assert again.warnings and list(store.cache_dir.glob("cache.corrompu-*"))
    assert again.pending_count == 1  # the queue is not wiped
    assert r.id and again.sync().ok is False


def test_cache_file_of_wrong_type_does_not_crash(make_store):
    store = make_store("alice")
    store.cache_dir.mkdir(parents=True, exist_ok=True)
    store._cache_path.write_text("[1, 2]", encoding="utf-8")
    RemoteStore(store.client, cache_dir=store.cache_dir)


# ------------------------------------------------------- two devices
def test_edit_on_a_while_b_deletes(make_store, http, tmp_path):
    a = make_store("alice")
    r = a.add(recipe("Pain"))
    a.sync()
    b = login_store(http, tmp_path, "alice", "b")
    b.sync()
    mine = a.get(r.id)
    mine.nom = "Pain complet"
    a.update(mine)
    b.delete(r.id)
    b.sync()
    a.sync()  # the edit is never silently thrown away
    names = [x["recipe"]["nom"] for x in server_view(a, http)["recipes"] if not x["deleted"]]
    local = [x.nom for x in a.all()]
    assert names == local
    assert "Pain complet" in local or any("Pain complet" in n for n in names)


@pytest.mark.parametrize("first", ["a", "b"])
def test_offline_edits_on_both_devices_lose_nothing(make_store, http, tmp_path, first):
    a = make_store("alice")
    r = a.add(recipe("Crêpes"))
    a.sync()
    b = login_store(http, tmp_path, "alice", "b")
    b.sync()
    for store, nom in ((a, "Crêpes A"), (b, "Crêpes B")):
        x = store.get(r.id)
        x.nom = nom
        store.update(x)
    order = (a, b) if first == "a" else (b, a)
    for s in order:
        s.sync()
    for s in order:
        s.sync()
    seen = {c.mine.nom for s in (a, b) for c in s.conflicts} | {x.nom for s in (a, b) for x in s.all()}
    assert {"Crêpes A", "Crêpes B"} <= seen


def test_second_conflict_does_not_lose_first_mine(make_store, http, tmp_path):
    a = make_store("alice")
    r = a.add(recipe("Crêpes"))
    a.sync()
    b = login_store(http, tmp_path, "alice", "b")
    b.sync()
    x = b.get(r.id)
    x.nom = "B1"
    b.update(x)
    y = a.get(r.id)
    y.nom = "A1"
    a.update(y)
    a.sync()
    b.sync()  # conflict 1: mine "B1"
    x = b.get(r.id)
    x.nom = "B2"
    b.update(x)
    y = a.get(r.id)
    y.nom = "A2"
    a.update(y)
    a.sync()
    b.sync()  # conflict 2 must not erase "B1"
    kept = {c.mine.nom for c in b.conflicts} | {z.nom for z in b.all()}
    assert "B2" in kept and any(n.startswith("B1") for n in kept)


def test_lost_answer_after_successful_put_is_not_a_conflict(make_store, http):
    store = make_store("alice")
    r = store.add(recipe("Flan"))
    store.sync()
    r.nom = "Flan 2"
    store.update(r)
    real = http.request
    state = {"cut": True}

    def lossy(method, url, **kw):
        out = real(method, url, **kw)  # the server did save it...
        if state["cut"] and method == "PUT" and "/recipes/" in str(url):
            state["cut"] = False
            raise httpx.ReadTimeout("answer lost")
        return out

    http.request = lossy
    assert store.sync().ok is False
    assert store.sync().ok
    assert not store.conflicts and store.pending_count == 0


# ------------------------------------------------------------ photos
def test_photo_added_offline_delete_then_undo(make_store, net, http, tmp_path):
    store = make_store("alice")
    r = store.add(recipe("Quiche"))
    store.sync()
    net.down = True
    store.add_image(r, png(tmp_path / "p.png"))
    store.update(r)
    t = store.delete(r.id, keep_files=True)
    store.restore(t)
    net.down = False
    assert store.sync().ok and store.pending_count == 0
    view = server_view(store, http)["recipes"][0]
    assert len(view["images"]) == 1 and len(store.image_paths(store.get(r.id))) == 1
    fresh_ids = [i["id"] for i in view["images"]]
    assert len(set(fresh_ids)) == 1


def test_photo_added_offline_delete_after_server_delete_then_undo(make_store, http, tmp_path):
    store = make_store("alice")
    r = store.add(recipe("Quiche"))
    store.add_image(r, png(tmp_path / "p.png"))
    store.sync()
    t = store.delete(r.id, keep_files=True)
    store.sync()  # the delete reached the server
    store.restore(t)
    assert store.sync().ok
    view = [x for x in server_view(store, http)["recipes"] if not x["deleted"]][0]
    assert len(view["images"]) == 1 and len(store.image_paths(store.get(r.id))) == 1


def test_favorite_survives_delete_undo(make_store, http):
    store = make_store("alice")
    r = store.add(recipe("Quiche"))
    store.sync()
    store.toggle_favorite(r.id)
    t = store.delete(r.id, keep_files=True)
    store.restore(t)
    store.sync()
    assert store.get(r.id).favori is True
    assert server_view(store, http)["favorites"] == [r.id]


def test_refused_photo_is_kept_aside_not_deleted(make_store, settings, tmp_path):
    settings.max_image_mb = 0  # every upload answers 413
    store = make_store("alice")
    r = store.add(recipe("Gratin"))
    store.add_image(r, png(tmp_path / "p.png"))
    assert store.sync().ok
    assert store.last_error  # the user is told
    kept = [p for p in store.cache_dir.rglob("*") if p.is_file() and p.suffix == ".png"]
    assert kept, "the refused photo must still exist somewhere on disk"


@pytest.mark.parametrize("n", range(0, 14))
def test_network_drop_at_every_step_converges(make_store, http, tmp_path, n):
    store = make_store("alice")
    r = store.add(recipe("Cake", favori=True))
    store.add_image(r, png(tmp_path / "p.png"))
    flaky = FlakyNet(http, n)
    store.sync()
    flaky.left = 10 ** 6
    assert store.sync().ok
    assert store.pending_count == 0
    view = [x for x in server_view(store, http)["recipes"] if not x["deleted"]]
    assert len(view) == 1 and len(view[0]["images"]) == 1
    assert len(store.get(r.id).images) == 1 and len(store.image_paths(store.get(r.id))) == 1
    assert store.get(r.id).favori


def test_token_expiry_mid_outbox_resumes(make_store, http, tmp_path):
    store = make_store("alice")
    a = store.add(recipe("A"))
    store.add(recipe("B"))
    store.add_image(a, png(tmp_path / "p.png"))
    stale = store.client.token
    store.client.token = "x" * 40
    assert store.sync().status == "auth" if hasattr(store.sync(), "status") else True
    assert store.status == "auth" and store.pending_count >= 3
    store.client.token = stale
    assert store.sync().ok and store.pending_count == 0
    assert len(server_view(store, http)["recipes"]) == 2


def test_share_revoked_while_recipient_has_queued_edits(make_store, net, http):
    owner = make_store("alice")
    guest = make_store("bob")
    r = owner.add(recipe("Secret"))
    owner.sync()
    owner.client.share_book("bob", can_edit=True)
    guest.sync()
    net.down = True
    x = guest.get(r.id)
    x.nom = "Secret de Bob"
    guest.update(x)
    net.down = False
    owner.client.unshare_book("bob")
    guest.sync()
    guest.sync()
    assert any(z.nom.startswith("Secret de Bob") for z in guest.all())
    assert not any(z.id == r.id for z in guest.all())


# ----------------------------------------------------- first-login upload
def local_book(tmp_path, n=3, with_photo=True):
    ls = RecipeStore(tmp_path / "local" / "recette.json", tmp_path / "local" / "img")
    for i in range(n):
        r = ls.add(recipe(f"Local {i}"))
        if with_photo:
            ls.add_image(r, png(tmp_path / f"{i}.png"))
    return ls


def test_upload_local_twice_makes_no_duplicates(make_store, http, tmp_path):
    ls = local_book(tmp_path)
    store = make_store("alice")
    assert store.upload_local(ls) == 3
    assert store.upload_local(ls) == 0
    (store.cache_dir / "upload_state.json").unlink()  # even with the progress file gone
    assert store.upload_local(ls) == 0
    view = server_view(store, http)["recipes"]
    assert len(view) == 3 and all(len(v["images"]) == 1 for v in view)


@pytest.mark.parametrize("n", range(3, 30, 3))
def test_upload_local_interrupted_then_rerun(make_store, http, tmp_path, n):
    ls = local_book(tmp_path)
    store = make_store("alice")
    flaky = FlakyNet(http, n)
    try:
        store.upload_local(ls)
    except Exception:  # noqa: BLE001 - OfflineError
        pass
    flaky.left = 10 ** 6
    store.upload_local(ls)
    store.sync()
    view = [v for v in server_view(store, http)["recipes"] if not v["deleted"]]
    assert len(view) == 3 and all(len(v["images"]) == 1 for v in view)


def test_upload_local_same_id_as_another_users_recipe(make_store, http, tmp_path):
    ls = local_book(tmp_path, n=2, with_photo=False)
    other = make_store("bob")
    for r in ls.all():
        other.add(recipe(f"Bob {r.nom}", id=r.id))
    other.sync()
    store = make_store("alice")
    assert store.upload_local(ls) == 2
    store.sync()
    assert sorted(r.nom for r in store.all()) == ["Local 0", "Local 1"]


def test_upload_local_with_bad_id_does_not_block_the_others(make_store, http, tmp_path):
    ls = local_book(tmp_path, n=2, with_photo=False)
    ls.all()[0].id = "nope"  # not a valid server id
    ls.save()
    store = make_store("alice")
    store.upload_local(RecipeStore(ls.path, ls.images_root))
    store.sync()
    assert len(store.all()) == 2


# ----------------------------------------------------- stuck loops, seq
def test_too_many_recipes_does_not_loop_forever(make_store, monkeypatch):
    monkeypatch.setattr(server_api, "MAX_RECIPES_PER_USER", 1)
    store = make_store("alice")
    store.add(recipe("1"))
    store.add(recipe("2"))
    done = []
    t = threading.Thread(target=lambda: done.append(store.sync()), daemon=True)
    t.start()
    t.join(20)
    assert done, "sync is stuck in an endless retry loop"


def test_server_restored_from_backup_with_lower_seq(make_store, http, settings):
    store = make_store("alice")
    for i in range(3):
        store.add(recipe(f"R{i}"))
    store.sync()
    assert store._seq >= 3
    from mamarmiton_server import db
    conn = db.connect(settings.db_path)
    conn.execute("UPDATE counters SET value = 0")
    conn.execute("UPDATE recipes SET seq = 0")
    conn.close()
    other = ApiClient("http://testserver", token=store.client.token, http=http)
    x = other.get_recipe(store.all()[0].id)
    data = dict(x["recipe"], nom="Après restauration")
    other.put_recipe(x["id"], data, x["version"])
    store.sync()
    assert any(r.nom == "Après restauration" for r in store.all())


# --------------------------------------------------------------- server
def test_server_seq_survives_restart(settings):
    app = create_app(settings)
    c = TestClient(app)
    code = make_invite(settings)
    cl = ApiClient("http://testserver", http=c)
    cl.register("alice", PASSWORD, code)
    rid = "a" * 16
    cl.put_recipe(rid, {"nom": "x"}, None)
    seq = cl.list_recipes()["seq"]
    c2 = TestClient(create_app(settings))
    cl2 = ApiClient("http://testserver", token=cl.token, http=c2)
    assert cl2.list_recipes()["seq"] == seq
    cl2.put_recipe(rid, {"nom": "y"}, 1)
    assert cl2.list_recipes()["seq"] > seq


def test_concurrent_puts_same_base_only_one_wins(make_store, http):
    store = make_store("alice")
    r = store.add(recipe("X"))
    store.sync()
    base = store._meta[r.id]["version"]
    results = []

    def go(i):
        c = ApiClient("http://testserver", token=store.client.token, http=http)
        try:
            c.put_recipe(r.id, {"nom": f"N{i}"}, base)
            results.append("ok")
        except Exception as exc:  # noqa: BLE001
            results.append(type(exc).__name__)

    ts = [threading.Thread(target=go, args=(i,)) for i in range(6)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert results.count("ok") == 1


def test_recreate_same_id_after_delete(make_store, http):
    store = make_store("alice")
    r = store.add(recipe("X"))
    store.sync()
    c = ApiClient("http://testserver", token=store.client.token, http=http)
    c.delete_recipe(r.id)
    out = c.put_recipe(r.id, {"nom": "Neuf"}, None)
    assert out["version"] > 1 and not out["deleted"]


def test_no_photo_upload_onto_a_deleted_recipe(make_store, http, tmp_path):
    store = make_store("alice")
    r = store.add(recipe("X"))
    store.sync()
    c = ApiClient("http://testserver", token=store.client.token, http=http)
    c.delete_recipe(r.id)
    with pytest.raises(Exception):
        c.upload_image(r.id, png(tmp_path / "p.png"))


def test_reader_cannot_delete_images_of_shared_recipe(make_store, http, tmp_path):
    owner, guest = make_store("alice"), make_store("bob")
    r = owner.add(recipe("X"))
    owner.add_image(r, png(tmp_path / "p.png"))
    owner.sync()
    owner.client.share_book("bob", can_edit=False)
    guest.sync()
    image_id = server_view(owner, http)["recipes"][0]["images"][0]["id"]
    with pytest.raises(Exception):
        guest.client.delete_image(r.id, image_id)
    assert len(server_view(owner, http)["recipes"][0]["images"]) == 1
