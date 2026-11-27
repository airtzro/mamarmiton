"""RemoteStore duplicate / undo-delete logic with a stub client (no server needed)."""

from unittest.mock import MagicMock

from PIL import Image

from mamarmiton.models import Recipe
from mamarmiton.remote import RemoteStore


def make(tmp_path) -> RemoteStore:
    return RemoteStore(MagicMock(username="u"), cache_dir=tmp_path / "cache", username="u")


def synced(store: RemoteStore, tmp_path, favori=True) -> Recipe:
    """A recipe with a photo that the server already knows (outbox emptied)."""
    r = store.add(Recipe(nom="Soupe", favori=favori))
    src = tmp_path / "a.png"
    Image.new("RGB", (8, 8)).save(src)
    store.add_image(r, src)
    store._outbox.clear()
    store._meta[r.id].update(version=3, fav=favori, images={r.images[0]: "img-1"})
    return r


def kinds(store):
    return sorted(o["op"] for o in store._outbox)


def test_duplicate_queues_copy_with_own_photos(tmp_path):
    store = make(tmp_path)
    r = synced(store, tmp_path)
    copy = store.duplicate(r.id)
    assert copy.id != r.id and copy.nom == "Soupe (variante)" and not copy.favori
    assert store._meta[copy.id]["version"] is None  # a new recipe, created on next sync
    assert kinds(store) == ["img_add", "put"]
    assert store.image_folder(copy) != store.image_folder(r) and len(store.image_paths(copy)) == 1
    store.delete(r.id)
    assert len(store.image_paths(copy)) == 1


def test_restore_cancels_pending_server_delete(tmp_path):
    store = make(tmp_path)
    r = synced(store, tmp_path)
    trashed = store.delete(r.id, keep_files=True)
    assert kinds(store) == ["delete"] and len(store.image_paths(r)) == 1
    store.restore(trashed)
    assert "delete" not in kinds(store)
    assert store.get(r.id).favori and store._meta[r.id]["version"] == 3
    assert store._meta[r.id]["images"] == {r.images[0]: "img-1"}


def test_restore_after_delete_reached_server_recreates(tmp_path):
    store = make(tmp_path)
    r = synced(store, tmp_path)
    trashed = store.delete(r.id, keep_files=True)
    store._outbox.clear()  # the delete was delivered
    store.restore(trashed)
    assert store._meta[r.id]["version"] is None
    assert kinds(store) == ["fav", "img_add", "put"]
    assert all(o["id"] == r.id for o in store._outbox)


def test_restore_unsynced_recipe_and_purge(tmp_path):
    store = make(tmp_path)
    r = store.add(Recipe(nom="Flan"))
    trashed = store.delete(r.id, keep_files=True)
    assert store._outbox == []  # never on the server: nothing to delete
    store.restore(trashed)
    assert kinds(store) == ["put"]
    trashed = store.delete(r.id, keep_files=True)
    store.purge(trashed)
    assert not store.image_folder(r).exists() and store.get(r.id) is None
