"""RemoteStore against the real server app, in-process (no network)."""

import httpx
import pytest

pytest.importorskip("mamarmiton_server")

from fastapi.testclient import TestClient  # noqa: E402
from PIL import Image  # noqa: E402

from mamarmiton.models import Ingredient, Recipe  # noqa: E402
from mamarmiton.remote import (  # noqa: E402
    Account, ApiClient, AuthError, OfflineError, RemoteStore, clear_account, load_account, open_store, save_account,
)
from mamarmiton.storage import RecipeStore  # noqa: E402
from mamarmiton_server import db, security  # noqa: E402
from mamarmiton_server.api import create_app  # noqa: E402
from mamarmiton_server.config import Settings  # noqa: E402

PASSWORD = "correct horse battery"


class Net:
    """Wraps an httpx client so tests can cut the network."""

    def __init__(self, http):
        self.http, self.down, self._request = http, False, http.request
        http.request = self.request

    def request(self, *args, **kwargs):
        if self.down:
            raise httpx.ConnectError("down")
        return self._request(*args, **kwargs)


@pytest.fixture
def settings(tmp_path):
    return Settings(data_dir=tmp_path / "server")


@pytest.fixture
def http(settings):
    return TestClient(create_app(settings))


@pytest.fixture
def net(http):
    return Net(http)


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


@pytest.fixture
def make_store(http, settings, tmp_path):
    def make(username: str, device: str = "") -> RemoteStore:
        client = ApiClient("http://testserver", http=http)
        client.register(username, PASSWORD, make_invite(settings), display_name=username.title())
        return RemoteStore(client, cache_dir=tmp_path / "cache" / f"{username}{device}")
    return make


def login_store(http, tmp_path, username: str, device: str) -> RemoteStore:
    client = ApiClient("http://testserver", http=http)
    client.login(username, PASSWORD)
    return RemoteStore(client, cache_dir=tmp_path / "cache" / f"{username}-{device}")


def png(path, color="red"):
    Image.new("RGB", (40, 30), color).save(path)
    return path


def recipe(name="Gâteau", **kw) -> Recipe:
    return Recipe(nom=name, ingredients=[Ingredient("farine", "200 g")], etapes=["Mélanger"], **kw)


# ------------------------------------------------------------------ client
def test_register_login_and_errors(http, settings):
    client = ApiClient("http://testserver", http=http)
    data = client.register("alice", PASSWORD, make_invite(settings), display_name="Alice")
    assert data["user"]["username"] == "alice" and client.username == "alice"
    other = ApiClient("http://testserver", http=http)
    other.login("alice", PASSWORD)
    assert other.me()["display_name"] == "Alice"
    with pytest.raises(AuthError) as err:
        ApiClient("http://testserver", http=http).login("alice", "nope nope nope")
    assert "incorrect" in err.value.message
    assert any(u["username"] == "alice" for u in ApiClient("http://testserver", token=other.token, http=http).users()
               ) is False  # users() excludes yourself


def test_offline_error(http, net):
    net.down = True
    with pytest.raises(OfflineError):
        ApiClient("http://testserver", http=http).login("a", "b")


# ------------------------------------------------------------- local ops
def test_offline_create_update_delete_then_sync(make_store, net, http):
    store = make_store("alice")
    net.down = True
    r = store.add(recipe("Tarte", favori=True))
    r.pour = 6
    store.update(r)
    assert store.pending_count == 2  # one put (coalesced) + one favourite
    assert store.sync().ok is False and store.status == "offline" and store.pending_count == 2
    net.down = False
    result = store.sync()
    assert result.ok and store.status == "ok" and store.pending_count == 0 and store.last_sync
    other = ApiClient("http://testserver", token=store.client.token, http=http).list_recipes()
    assert [x["recipe"]["pour"] for x in other["recipes"]] == [6] and other["favorites"] == [r.id]
    store.delete(r.id)
    assert store.pending_count == 1 and len(store) == 0
    store.sync()
    assert ApiClient("http://testserver", token=store.client.token, http=http).list_recipes()["visible_ids"] == []


def test_create_then_delete_queues_nothing(make_store):
    store = make_store("alice")
    r = store.add(recipe())
    store.delete(r.id)
    assert store.pending_count == 0


def test_toggle_favorite_and_pull_on_second_device(make_store, http, tmp_path):
    a = make_store("alice")
    r = a.add(recipe())
    assert a.toggle_favorite(r.id) is True
    a.sync()
    b = login_store(http, tmp_path, "alice", "b")
    b.sync()
    assert b.get(r.id).favori is True and b.owner(r.id) == "alice" and b.access(r.id) == "owner"
    b.toggle_favorite(r.id)
    b.sync()
    a.sync()
    assert a.get(r.id).favori is False


def test_outbox_persists_across_restart(make_store, net, tmp_path, http):
    store = make_store("alice")
    net.down = True
    r = store.add(recipe("Soupe"))
    again = RemoteStore(store.client, cache_dir=store.cache_dir)
    assert again.pending_count == 1 and again.get(r.id).nom == "Soupe"
    net.down = False
    assert again.sync().ok and again.pending_count == 0
    fresh = login_store(http, tmp_path, "alice", "z")
    fresh.sync()
    assert fresh.get(r.id).nom == "Soupe"


# ---------------------------------------------------------------- conflicts
def conflict_setup(make_store, http, tmp_path):
    a = make_store("alice")
    r = a.add(recipe("Crêpes"))
    a.sync()
    b = login_store(http, tmp_path, "alice", "b")
    b.sync()
    mine = b.get(r.id)
    mine.nom = "Crêpes de B"
    b.update(mine)
    theirs = a.get(r.id)
    theirs.nom = "Crêpes de A"
    a.update(theirs)
    a.sync()
    result = b.sync()
    return a, b, r.id, result


def test_conflict_theirs(make_store, http, tmp_path):
    a, b, rid, result = conflict_setup(make_store, http, tmp_path)
    assert result.conflicts == 1 and b.pending_count == 0
    assert b.get(rid).nom == "Crêpes de A"
    c = b.conflicts[0]
    assert (c.mine.nom, c.theirs.nom) == ("Crêpes de B", "Crêpes de A")
    b.resolve_conflict(rid, "theirs")
    assert not b.conflicts and b.get(rid).nom == "Crêpes de A" and b.pending_count == 0


def test_conflict_mine(make_store, http, tmp_path):
    a, b, rid, _ = conflict_setup(make_store, http, tmp_path)
    b.resolve_conflict(rid, "mine")
    assert b.get(rid).nom == "Crêpes de B"
    assert b.sync().ok
    a.sync()
    assert a.get(rid).nom == "Crêpes de B" and not a.conflicts


def test_conflict_both(make_store, http, tmp_path):
    a, b, rid, _ = conflict_setup(make_store, http, tmp_path)
    b.resolve_conflict(rid, "both")
    assert sorted(r.nom for r in b.all()) == ["Crêpes de A", "Crêpes de B (ma version)"]
    b.sync()
    a.sync()
    assert len(a) == 2


def test_conflicts_survive_restart(make_store, http, tmp_path):
    a, b, rid, _ = conflict_setup(make_store, http, tmp_path)
    again = RemoteStore(b.client, cache_dir=b.cache_dir)
    assert [c.recipe_id for c in again.conflicts] == [rid]


# ------------------------------------------------------------------ sharing
def test_read_only_share_and_revoke(make_store, http, tmp_path):
    a, b = make_store("alice"), make_store("bob")
    r = a.add(recipe("Secret"))
    a.sync()
    a.client.share_book("bob", can_edit=False)
    b.on_change = lambda: calls.append(1)
    calls: list[int] = []
    b.sync()
    shared = b.get(r.id)
    assert shared and b.access(r.id) == "read" and b.owner(r.id) == "alice" and calls
    assert not b.can_edit(r.id) and not b.can_delete(r.id)
    with pytest.raises(PermissionError):
        b.update(shared)
    with pytest.raises(PermissionError):
        b.delete(r.id)
    with pytest.raises(PermissionError):
        b.add_image(shared, png(tmp_path / "x.png"))
    assert b.toggle_favorite(r.id) is True  # favourites are personal
    b.sync()
    a.client.share_book("bob", can_edit=True)
    b.sync()
    assert b.access(r.id) == "edit"
    edited = b.get(r.id)
    edited.nom = "Modifiée par Bob"
    b.update(edited)
    b.sync()
    a.sync()
    assert a.get(r.id).nom == "Modifiée par Bob"
    with pytest.raises(PermissionError):
        b.delete(r.id)
    a.client.unshare_book("bob")
    b.sync()
    assert b.get(r.id) is None and len(b) == 0


def test_revoked_share_takes_its_tags_away(make_store):
    from mamarmiton.search import tag_counts
    a, b = make_store("alice"), make_store("bob")
    theirs = recipe("Bûche")
    theirs.tags = ["Noël", "Commun"]
    a.add(theirs)
    a.sync()
    mine = recipe("Crêpes")
    mine.tags = ["commun", "Rapide"]
    b.add(mine)
    b.sync()
    a.client.share_book("bob")
    b.sync()
    assert {t for t, _n in tag_counts(b.all())} >= {"Noël", "Rapide"}
    a.client.unshare_book("bob")
    b.sync()
    assert dict(tag_counts(b.all())) == {"commun": 1, "Rapide": 1}
    # and not from the offline copy either
    reopened = RemoteStore(b.client, cache_dir=b.cache_dir)
    assert dict(tag_counts(reopened.all())) == {"commun": 1, "Rapide": 1}


def test_leave_book_and_single_recipe_share(make_store):
    a, b = make_store("alice"), make_store("bob")
    r1, r2 = a.add(recipe("Un")), a.add(recipe("Deux"))
    a.sync()
    a.client.share_recipe(r1.id, "bob")
    b.sync()
    assert [x.nom for x in b.all()] == ["Un"]
    a.client.unshare_recipe(r1.id, "bob")
    a.client.share_book("bob")
    b.sync()
    assert len(b) == 2
    b.client.leave_book("alice")
    b.sync()
    assert len(b) == 0 and r2.id


def test_hide_one_recipe_of_a_shared_book(make_store, http, tmp_path):
    a, b = make_store("alice"), make_store("bob")
    r1, r2 = a.add(recipe("Un")), a.add(recipe("Deux"))
    a.sync()
    a.client.share_book("bob")
    b.sync()
    mine = b.add(recipe("À moi"))
    assert b.can_hide(r1.id) and not b.can_hide(mine.id)
    with pytest.raises(PermissionError):
        b.hide(mine.id)  # own recipes are deleted, not hidden
    trashed = b.hide(r1.id)
    assert b.get(r1.id) is None
    assert b.sync().ok
    assert sorted(x.nom for x in b.all()) == ["Deux", "À moi"]  # stays gone after the pull
    a.sync()
    assert a.get(r1.id) is not None  # the owner keeps it
    other_device = login_store(http, tmp_path, "bob", "phone")
    other_device.sync()
    assert r1.id not in {x.id for x in other_device.all()}

    b.restore(trashed)  # undo after the hide reached the server
    assert b.get(r1.id) is not None
    b.sync()
    assert b.get(r1.id) is not None and b.client.shares()["hidden"] == []
    other_device.sync()
    assert r1.id in {x.id for x in other_device.all()}


def test_hide_undone_before_sync_sends_nothing(make_store):
    a, b = make_store("alice"), make_store("bob")
    r1 = a.add(recipe("Un"))
    a.sync()
    a.client.share_book("bob")
    b.sync()
    b.restore(b.hide(r1.id))
    assert b.pending_count == 0
    b.sync()
    assert b.get(r1.id) is not None and b.client.shares()["hidden"] == []


# ------------------------------------------------------------------- images
def test_image_roundtrip(make_store, http, tmp_path):
    a = make_store("alice")
    r = a.add(recipe("Photo"))
    a.add_image(r, png(tmp_path / "red.png"))
    a.add_image(r, png(tmp_path / "blue.png", "blue"))
    assert a.pending_count == 3 and len(r.images) == 2
    assert a.sync().ok
    assert len(a.image_paths(a.get(r.id))) == 2
    b = login_store(http, tmp_path, "alice", "b")
    res = b.sync()
    assert res.images == 2
    got = b.get(r.id)
    assert len(b.image_paths(got)) == 2
    Image.open(b.image_paths(got)[0]).verify()
    b.remove_image(got, got.images[0])
    assert b.pending_count == 1
    b.sync()
    a.sync()
    assert len(a.get(r.id).images) == 1 and len(a.image_paths(a.get(r.id))) == 1


def test_image_added_then_removed_before_sync(make_store, tmp_path):
    a = make_store("alice")
    r = a.add(recipe())
    a.sync()
    path = a.add_image(r, png(tmp_path / "p.png"))
    a.remove_image(r, path.name)
    assert a.pending_count == 0


# -------------------------------------------------------------- first login
def test_upload_local_idempotent(make_store, net, tmp_path, monkeypatch):
    local = RecipeStore()
    r1 = local.add(recipe("Locale 1", favori=True))
    r2 = local.add(recipe("Locale 2"))
    local.add_image(r1, png(tmp_path / "l.png"))
    before = local.path.read_bytes()
    store = make_store("alice")
    calls = {"n": 0}
    real = store.client.upload_image

    def flaky(*a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OfflineError()
        return real(*a, **k)

    monkeypatch.setattr(store.client, "upload_image", flaky)
    with pytest.raises(OfflineError):
        store.upload_local(local)
    assert store.upload_local(local) >= 1
    assert store.upload_local(local) == 0
    store.sync()
    assert sorted(r.nom for r in store.all()) == ["Locale 1", "Locale 2"]
    assert store.get(r1.id).favori and len(store.image_paths(store.get(r1.id))) == 1
    assert r2.id in {r.id for r in store.all()}
    assert local.path.read_bytes() == before and len(local) == 2


def test_upload_local_foreign_id_gets_new_id(make_store):
    a, b = make_store("alice"), make_store("bob")
    r = a.add(recipe("A moi"))
    a.sync()
    a.client.share_book("bob")
    local = RecipeStore()
    clash = Recipe.from_dict({**r.to_dict(), "nom": "Celle de Bob"})
    local.add(clash)
    assert b.upload_local(local) == 1
    b.sync()
    names = sorted(x.nom for x in b.all())
    assert names == ["A moi", "Celle de Bob"]
    assert {x.id for x in b.all() if x.nom == "Celle de Bob"} != {r.id}


# --------------------------------------------------------------------- auth
def test_auth_expiry(make_store):
    store = make_store("alice")
    store.add(recipe())
    store.client.logout()  # the server forgets this token
    store.client.token = None
    result = store.sync()
    assert store.status == "auth" and not result.ok and store.pending_count == 1


def test_create_with_existing_foreign_id_is_renamed(make_store):
    a, b = make_store("alice"), make_store("bob")
    r = a.add(recipe("Alice"))
    a.sync()
    clash = Recipe.from_dict({**recipe("Bob").to_dict(), "id": r.id})
    b.add(clash)
    assert b.sync().ok
    assert b.pending_count == 0 and len(b) == 1 and b.all()[0].id != r.id


# ------------------------------------------------------------------ account
def test_account_roundtrip_and_open_store():
    store, account = open_store()
    assert isinstance(store, RecipeStore) and account is None
    assert open_store(local_fallback=False) == (None, None)
    save_account(Account("http://x", "tok", "alice", "Alice", False))
    got = load_account()
    assert got.token == "tok" and got.username == "alice"
    store, account = open_store()
    assert isinstance(store, RemoteStore) and store.username == "alice"
    import os
    from mamarmiton import config
    if os.name == "posix":
        assert (config.data_dir() / "account.json").stat().st_mode & 0o777 == 0o600
    clear_account()
    assert load_account() is None


# ------------------------------------------------- variante / annuler la suppression
def test_remote_duplicate_syncs_with_own_photos(make_store, http, tmp_path):
    store = make_store("dupli")
    r = store.add(recipe("Quiche"))
    store.add_image(r, png(tmp_path / "a.png"))
    store.sync()
    copy = store.duplicate(r.id)
    assert copy.id != r.id and copy.nom == "Quiche (variante)" and store.can_edit(copy.id)
    assert len(store.image_paths(copy)) == 1 and store.image_folder(copy) != store.image_folder(r)
    store.sync()
    other = login_store(http, tmp_path, "dupli", "b")
    other.sync()
    assert other.get(copy.id).nom == "Quiche (variante)" and len(other.get(copy.id).images) == 1
    store.delete(r.id)
    assert len(store.image_paths(copy)) == 1


def test_remote_restore_before_sync_cancels_delete(make_store, http, tmp_path):
    store = make_store("undo1")
    r = store.add(recipe("Soupe", favori=True))
    store.add_image(r, png(tmp_path / "a.png"))
    store.sync()
    trashed = store.delete(r.id, keep_files=True)
    assert any(o["op"] == "delete" for o in store._outbox)
    store.restore(trashed)
    assert not any(o["op"] == "delete" for o in store._outbox)
    store.sync()
    fresh = login_store(http, tmp_path, "undo1", "b")
    fresh.sync()
    got = fresh.get(r.id)
    assert got and got.favori and len(got.images) == 1


def test_remote_restore_after_delete_was_synced_recreates(make_store, http, tmp_path):
    store = make_store("undo2")
    r = store.add(recipe("Crêpes", favori=True))
    store.add_image(r, png(tmp_path / "a.png"))
    store.sync()
    trashed = store.delete(r.id, keep_files=True)
    store.sync()  # the delete reached the server
    store.restore(trashed)
    store.sync()
    assert store.pending_count == 0
    fresh = login_store(http, tmp_path, "undo2", "b")
    fresh.sync()
    got = fresh.get(r.id)
    assert got and got.favori and len(got.images) == 1


def test_remote_restore_unsynced_recipe_and_purge(make_store, tmp_path):
    store = make_store("undo3")
    r = store.add(recipe("Flan"))
    store.add_image(r, png(tmp_path / "a.png"))
    trashed = store.delete(r.id, keep_files=True)
    assert store.pending_count == 0
    store.restore(trashed)
    store.sync()
    assert store.pending_count == 0 and store.get(r.id)
    trashed = store.delete(r.id, keep_files=True)
    store.sync()
    store.purge(trashed)
    assert not store.image_folder(r).exists()


def test_tags_round_trip_through_the_server(make_store, http, tmp_path):
    a = make_store("alice")
    r = recipe()
    r.tags = ["Noël", "sans gluten"]
    a.add(r)
    a.sync()
    b = login_store(http, tmp_path, "alice", "b")
    b.sync()
    assert b.get(r.id).tags == ["Noël", "sans gluten"]
    edited = b.get(r.id)
    edited.tags = ["rapide"]
    b.update(edited)
    b.sync()
    a.sync()
    assert a.get(r.id).tags == ["rapide"]


# ------------------------------------------------------- minimum client version
def test_client_sends_its_version_header():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(request.headers)
        return httpx.Response(200, json={})

    from mamarmiton import __version__
    ApiClient("http://x", http=httpx.Client(transport=httpx.MockTransport(handler))).me()
    assert seen["x-mamarmiton-client"] == f"desktop/{__version__}"


def test_426_from_stub_server_is_upgrade_required_and_not_a_sign_out():
    from mamarmiton.remote import UpgradeRequired

    def handler(request):
        return httpx.Response(426, json={"detail": "Cette version de Mamarmiton est trop ancienne. Mettez-la à jour.",
                                         "min_version": "4.1.0"})

    client = ApiClient("http://x", token="t", http=httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(UpgradeRequired) as err:
        client.me()
    assert err.value.status == 426 and err.value.min_version == "4.1.0" and "trop ancienne" in err.value.message
    assert isinstance(err.value, OfflineError) and not isinstance(err.value, AuthError)


def test_outdated_app_stops_syncing_but_keeps_local_data_and_queue(make_store, settings, http):
    store = make_store("alice")
    r = store.add(recipe("Tarte"))
    assert store.sync().ok
    strict = TestClient(create_app(Settings(data_dir=settings.data_dir, min_desktop_version="99.0.0")))
    store.client._http = strict
    store.update(recipe_with(r, pour=8))
    result = store.sync()
    assert result.outdated and not result.ok and store.status == "outdated"
    assert "trop ancienne" in result.error
    assert store.pending_count == 1  # the edit waits in the outbox
    assert store.get(r.id).pour == 8 and len(store) == 1  # the local cache still works
    # once the app is updated (or the minimum lowered) the queue is delivered
    store.client._http = http
    assert store.sync().ok and store.pending_count == 0 and store.status == "ok"


def recipe_with(r, **kw):
    for k, v in kw.items():
        setattr(r, k, v)
    return r


# ------------------------------------------------------- credit and variants
def test_variant_rights_and_credit_follow_the_share_level(make_store, tmp_path):
    from mamarmiton.ui import remote_logic as rl

    a, b = make_store("alice"), make_store("bob")
    r = a.add(recipe("Tarte"))
    a.add_image(r, png(tmp_path / "t.png"))
    a.sync()
    assert a.credit(r.id)["author"] == "alice"
    assert rl.credit_line(a, r.id, r.nom, {}, me="alice") == ""  # my own original: no signature on screen
    assert rl.credit_line(a, r.id, r.nom, {"alice": "Alice"}) == "Recette de Alice"  # printed: always
    a.client.share_recipe(r.id, "bob", can_edit=False, can_copy=False)
    b.sync()
    assert not b.can_copy(r.id)
    assert rl.credit_line(b, r.id, "Tarte", {"alice": "Alice"}, me="bob") == "Recette de Alice"
    with pytest.raises(PermissionError):
        b.duplicate(r.id)
    a.client.share_recipe(r.id, "bob", can_edit=False, can_copy=True)
    b.sync()
    assert b.can_copy(r.id)
    copy = b.duplicate(r.id)
    # credited straight away, before it reaches the server
    line = rl.credit_line(b, copy.id, copy.nom, {"alice": "Alice"}, me="bob")
    assert line == "Variante de « Tarte » de Alice"
    b.sync()
    assert b.access(copy.id) == "owner" and b.credit(copy.id)["author"] == "alice"
    assert b.credit(copy.id)["variant_of"] == r.id and "variant_of" not in b._meta[copy.id]
    server = b.client.get_recipe(copy.id)
    assert server["credit"]["author"] == "alice" and server["owner"] == "bob" and len(server["images"]) == 1
    copy.nom = "Ma tarte"
    b.update(copy)
    b.sync()
    assert b.client.get_recipe(copy.id)["credit"]["variant_of_nom"] == "Tarte"


def test_cache_from_before_credits_fetches_everything_once(make_store, tmp_path):
    a = make_store("alice")
    r = a.add(recipe("Soupe"))
    a.sync()
    a._meta[r.id].pop("credit")
    a._save()
    again = RemoteStore(a.client, cache_dir=a.cache_dir)
    assert again._full_pull
    again.sync()
    assert again.credit(r.id)["author"] == "alice"
    assert not RemoteStore(a.client, cache_dir=a.cache_dir)._full_pull
