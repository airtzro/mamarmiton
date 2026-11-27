import json

from mamarmiton.models import Recipe
from mamarmiton.storage import RecipeStore

V3 = [
    {"nom": "GÂTEAU AU CHOCOLAT", "temps_cuisson": 25, "temps": "20", "pour": 6, "type_plat": "Dessert",
     "type_cuisson": "Four", "ingredients": [{"nom": "chocolat", "quantité": "200 g"}], "etapes": []},
    {"nom": "Quiche", "temps_cuisson": 35, "temps": "15", "pour": 4, "type_plat": "Plat",
     "type_cuisson": "Four", "ingredients": [], "etapes": []},
]


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def test_empty_store(isolated_data_dir):
    store = RecipeStore()
    assert len(store) == 0
    assert not (isolated_data_dir / "recette.json").exists() or json.loads(
        (isolated_data_dir / "recette.json").read_text()) == []


def test_v3_migration(isolated_data_dir):
    write(isolated_data_dir / "recette.json", V3)
    write(isolated_data_dir / "recette_fav.json", [{"nom": "quiche"}])
    store = RecipeStore()
    assert len(store) == 2
    assert all(r.id for r in store.all())
    quiche = next(r for r in store.all() if r.nom == "Quiche")
    assert quiche.favori
    assert list(isolated_data_dir.glob("recette.v3-backup-*.json"))
    assert (isolated_data_dir / "recette_fav.json.migre").exists()
    saved = json.loads((isolated_data_dir / "recette.json").read_text(encoding="utf-8"))
    assert saved[0]["ingredients"][0]["quantité"] == "200 g"
    # second load: ids are stable, no new backup
    ids = [r.id for r in store.all()]
    assert [r.id for r in RecipeStore().all()] == ids
    assert len(list(isolated_data_dir.glob("recette.v3-backup-*.json"))) == 1


def test_legacy_images_migrated(isolated_data_dir, tmp_path):
    from PIL import Image

    folder = tmp_path / "img" / "Gâteau Au Chocolat"
    folder.mkdir(parents=True)
    Image.new("RGB", (10, 10)).save(folder / "a.png")
    write(isolated_data_dir / "recette.json", V3)
    store = RecipeStore()
    cake = next(r for r in store.all() if r.nom.startswith("G"))
    assert cake.images == ["a.png"]
    assert store.image_paths(cake)[0].is_file()


def test_crud_and_backup(isolated_data_dir):
    store = RecipeStore()
    r = store.add(Recipe(nom="Crêpes"))
    store.add(Recipe(nom="Gaufres"))
    assert (isolated_data_dir / "recette.json.bak").exists()
    r.nom = "Crêpes de maman"
    store.update(r)
    assert RecipeStore().get(r.id).nom == "Crêpes de maman"
    assert store.toggle_favorite(r.id) is True
    store.delete(r.id)
    assert [x.nom for x in RecipeStore().all()] == ["Gaufres"]


def test_corrupt_file_is_kept_aside_and_backup_restored(isolated_data_dir):
    store = RecipeStore()
    store.add(Recipe(nom="A"))
    store.add(Recipe(nom="B"))  # .bak now holds [A]
    (isolated_data_dir / "recette.json").write_text("{ pas du json", encoding="utf-8")
    store = RecipeStore()
    assert [r.nom for r in store.all()] == ["A"]
    assert store.warnings
    assert list(isolated_data_dir.glob("recette.corrompu-*.json"))


def test_images(isolated_data_dir, tmp_path):
    from PIL import Image

    src = tmp_path / "photo.jpg"
    Image.new("RGB", (10, 10)).save(src)
    store = RecipeStore()
    r = store.add(Recipe(nom="A"))
    store.add_image(r, src)
    store.add_image(r, src)
    assert r.images == ["photo.jpg", "photo_1.jpg"]
    store.remove_image(r, "photo.jpg")
    assert [p.name for p in store.image_paths(r)] == ["photo_1.jpg"]
    store.delete(r.id)
    assert not (isolated_data_dir / "images" / r.id).exists()


def test_export_import(isolated_data_dir, tmp_path):
    store = RecipeStore()
    store.add(Recipe(nom="A"))
    out = tmp_path / "export.json"
    store.export_to(out)
    assert store.import_from(out) == 0  # same ids -> skipped
    data = json.loads(out.read_text(encoding="utf-8"))
    data[0]["id"] = "other"
    out.write_text(json.dumps(data), encoding="utf-8")
    assert store.import_from(out) == 1
    assert len(store) == 2


# ------------------------------------------------- variante / annuler la suppression
def _store_with_photo(tmp_path):
    from PIL import Image

    store = RecipeStore(tmp_path / "r.json", tmp_path / "imgs")
    r = store.add(Recipe(nom="Tarte", etapes=["Cuire"], favori=True))
    src = tmp_path / "p.png"
    Image.new("RGB", (8, 8), "red").save(src)
    store.add_image(r, src)
    return store, r


def test_duplicate_gives_independent_copy(tmp_path):
    store, r = _store_with_photo(tmp_path)
    copy = store.duplicate(r.id)
    assert copy.id != r.id and copy.nom == "Tarte (variante)" and not copy.favori
    assert copy.etapes == ["Cuire"] and len(store) == 2
    assert store.image_folder(copy) != store.image_folder(r)
    assert len(store.image_paths(copy)) == 1
    store.delete(r.id)  # deleting the original keeps the copy's photo
    assert len(store.image_paths(copy)) == 1
    assert RecipeStore(tmp_path / "r.json", tmp_path / "imgs").get(copy.id).images == copy.images
    assert store.duplicate("nope") is None


def test_delete_restore_keeps_everything(tmp_path):
    store, r = _store_with_photo(tmp_path)
    other = store.add(Recipe(nom="Autre"))
    trashed = store.delete(r.id, keep_files=True)
    assert store.get(r.id) is None and store.image_paths(r) != []  # photo still on disk
    store.restore(trashed)
    assert [x.id for x in store.all()] == [r.id, other.id]
    again = RecipeStore(tmp_path / "r.json", tmp_path / "imgs").get(r.id)
    assert again.favori and again.images == r.images and len(store.image_paths(again)) == 1
    store.purge(trashed)  # restored: purge must not touch the photos
    assert len(store.image_paths(r)) == 1


def test_purge_removes_photos_after_delete(tmp_path):
    store, r = _store_with_photo(tmp_path)
    trashed = store.delete(r.id, keep_files=True)
    store.purge(trashed)
    assert not store.image_folder(r).exists()
