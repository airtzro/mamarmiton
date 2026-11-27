"""Persistence: one JSON file of recipes plus an images folder.

* Writes are atomic (temp file + ``os.replace``) so a crash or power cut
  never leaves a half-written ``recette.json``.
* The previous version is kept as ``recette.json.bak``.
* A corrupt file is set aside, never overwritten.
* v3 data (no ids, separate ``recette_fav.json``, images in ``./img/<nom>``)
  is migrated once, after a full backup.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from mamarmiton import config
from mamarmiton.models import Recipe, fold

log = logging.getLogger(__name__)

LEGACY_IMAGE_LIMIT = 12
IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp")


def _fsync_dir(folder: Path) -> None:
    """Make a rename durable (POSIX); a no-op where directories cannot be opened (Windows)."""
    try:
        fd = os.open(folder, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def _atomic_write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name, suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        _fsync_dir(path.parent)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _safe_folder_name(name: str) -> str:
    return "".join(c for c in name if c not in '<>:"/\\|?*').strip() or "recette"


@dataclass
class Trashed:
    """A deleted recipe that can still be brought back with ``store.restore``.

    Its photo files stay on disk until ``store.purge`` (the UI calls it when the
    "Annuler" toast expires). The other fields are only used by RemoteStore.
    """

    recipe: Recipe
    index: int = 0
    meta: dict | None = None
    server_delete_pending: bool = False
    conflicts: list = field(default_factory=list)
    ops: list = field(default_factory=list)  # queued favourite / photo-delete operations dropped by delete


def copy_as_variant(store, recipe: Recipe) -> Recipe:
    """Add "<nom> (variante)" to ``store``: new id, photos copied into the copy's own folder."""
    copy = Recipe.from_dict(recipe.to_dict())
    copy.id = Recipe(nom="").id
    copy.nom = f"{recipe.nom} (variante)"
    copy.favori = False
    copy.cree_le = datetime.now().isoformat(timespec="seconds")
    copy.modifie_le = ""
    sources = store.image_paths(recipe)
    copy.images = []
    store.add(copy)
    for source in sources:
        store.add_image(copy, source)
    return copy


class RecipeStore:
    def __init__(self, path: Path | None = None, images_root: Path | None = None):
        self.path = path or config.recipes_file()
        self.images_root = images_root or config.images_dir()
        self.warnings: list[str] = []
        self._recipes: list[Recipe] = []
        self.load()

    # ------------------------------------------------------------------ io
    def load(self) -> None:
        raw = self._read_raw()
        needs_migration = any(isinstance(r, dict) and "id" not in r for r in raw)
        if needs_migration and self.path.exists():
            backup = self.path.with_name(f"recette.v3-backup-{datetime.now():%Y%m%d-%H%M%S}.json")
            shutil.copy2(self.path, backup)
            log.info("Backed up v3 data to %s", backup)

        recipes: list[Recipe] = []
        for item in raw:
            try:
                recipes.append(Recipe.from_dict(item))
            except ValueError as exc:
                log.warning("Skipping unreadable recipe %r: %s", item, exc)
        self._recipes = recipes

        changed = needs_migration
        changed |= self._migrate_favorites()
        if needs_migration:
            changed |= self._migrate_legacy_images()
        if changed:
            self.save()
        fav_path = self.path.with_name("recette_fav.json")
        if fav_path.exists():  # only now that the migrated book is safely on disk
            fav_path.replace(fav_path.with_suffix(".json.migre"))

    def _read_raw(self) -> list:
        if not self.path.exists() or self.path.stat().st_size == 0:
            return []
        try:
            with open(self.path, encoding="utf-8-sig") as f:
                data = json.load(f)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            aside = self.path.with_name(f"recette.corrompu-{datetime.now():%Y%m%d-%H%M%S}.json")
            shutil.copy2(self.path, aside)
            msg = (
                f"Le fichier de recettes était illisible ({exc}). "
                f"Une copie a été mise de côté : {aside.name}"
            )
            log.error(msg)
            self.warnings.append(msg)
            # try the last good backup
            bak = self.path.with_suffix(".json.bak")
            if bak.exists():
                try:
                    with open(bak, encoding="utf-8-sig") as f:
                        data = json.load(f)
                    self.warnings.append("La dernière sauvegarde automatique a été restaurée.")
                except (json.JSONDecodeError, UnicodeDecodeError):
                    return []
            else:
                return []
        if isinstance(data, dict):  # tolerate {"recettes": [...]}
            data = data.get("recettes", [])
        return data if isinstance(data, list) else []

    def save(self) -> None:
        self._rotate_backup()
        _atomic_write_json(self.path, [r.to_dict() for r in self._recipes])

    def _rotate_backup(self) -> None:
        """Keep the current file as ``.bak``, but only if it is itself readable (a torn file must never
        replace the last good backup), and never by writing into the old ``.bak`` in place."""
        try:
            if not self.path.exists() or self.path.stat().st_size == 0:
                return
            raw = self.path.read_bytes()
            if not isinstance(json.loads(raw.decode("utf-8")), (list, dict)):
                return
        except (OSError, ValueError):
            return
        bak = self.path.with_suffix(".json.bak")
        tmp = bak.with_name(bak.name + ".tmp")
        try:
            tmp.write_bytes(raw)
            os.replace(tmp, bak)
        except OSError:
            tmp.unlink(missing_ok=True)
            log.warning("Could not refresh %s", bak, exc_info=True)

    # ----------------------------------------------------------- migration
    def _migrate_favorites(self) -> bool:
        fav_path = self.path.with_name("recette_fav.json")
        if not fav_path.exists():
            return False
        try:
            with open(fav_path, encoding="utf-8-sig") as f:
                favs = json.load(f)
        except (OSError, json.JSONDecodeError):
            favs = []
        names = {fold(f.get("nom", "")) for f in favs if isinstance(f, dict)}
        for recipe in self._recipes:
            if fold(recipe.nom) in names:
                recipe.favori = True
        return bool(names)

    def _migrate_legacy_images(self) -> bool:
        changed = False
        for legacy_root in config.legacy_image_dirs():
            if legacy_root.resolve() == self.images_root.resolve():
                continue
            folders = {fold(p.name): p for p in legacy_root.iterdir() if p.is_dir()}
            for recipe in self._recipes:
                if recipe.images:
                    continue
                folder = folders.get(fold(_safe_folder_name(recipe.nom)))
                if not folder:
                    continue
                for img in sorted(folder.iterdir())[:LEGACY_IMAGE_LIMIT]:
                    if img.suffix.lower() in IMAGE_EXTENSIONS:
                        self.add_image(recipe, img, save=False)
                        changed = True
        return changed

    # ---------------------------------------------------------------- crud
    def all(self) -> list[Recipe]:
        return list(self._recipes)

    def __len__(self) -> int:
        return len(self._recipes)

    def get(self, recipe_id: str) -> Recipe | None:
        return next((r for r in self._recipes if r.id == recipe_id), None)

    def add(self, recipe: Recipe) -> Recipe:
        while self.get(recipe.id):  # imported file may reuse an id
            recipe.id = Recipe(nom="").id
        self._recipes.append(recipe)
        self.save()
        return recipe

    def update(self, recipe: Recipe) -> None:
        recipe.touch()
        for i, existing in enumerate(self._recipes):
            if existing.id == recipe.id:
                self._recipes[i] = recipe
                break
        else:
            self._recipes.append(recipe)
        self.save()

    def duplicate(self, recipe_id: str) -> Recipe | None:
        """Copy a recipe as "<nom> (variante)" (own id, own photo files)."""
        recipe = self.get(recipe_id)
        return copy_as_variant(self, recipe) if recipe else None

    def delete(self, recipe_id: str, keep_files: bool = False) -> Trashed | None:
        """Delete a recipe. With ``keep_files`` the photos stay until ``purge``."""
        index = next((i for i, r in enumerate(self._recipes) if r.id == recipe_id), None)
        if index is None:
            return None
        trashed = Trashed(self._recipes.pop(index), index)
        if not keep_files:
            shutil.rmtree(self.images_root / recipe_id, ignore_errors=True)
        self.save()
        return trashed

    def restore(self, trashed: Trashed) -> Recipe:
        """Undo ``delete(keep_files=True)``: same id, photos, favourite flag and position."""
        recipe = trashed.recipe
        if self.get(recipe.id) is None:
            self._recipes.insert(min(trashed.index, len(self._recipes)), recipe)
            self.save()
        return recipe

    def purge(self, trashed: Trashed) -> None:
        """Remove the photo files of a deleted recipe for good (no-op if it was restored)."""
        if self.get(trashed.recipe.id) is None:
            shutil.rmtree(self.images_root / trashed.recipe.id, ignore_errors=True)

    def toggle_favorite(self, recipe_id: str) -> bool:
        recipe = self.get(recipe_id)
        if recipe is None:
            return False
        recipe.favori = not recipe.favori
        self.save()
        return recipe.favori

    # -------------------------------------------------------------- images
    def image_folder(self, recipe: Recipe) -> Path:
        return self.images_root / recipe.id

    def image_paths(self, recipe: Recipe) -> list[Path]:
        folder = self.image_folder(recipe)
        return [folder / name for name in recipe.images if (folder / name).is_file()]

    def add_image(self, recipe: Recipe, source: Path, save: bool = True) -> Path:
        folder = self.image_folder(recipe)
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / source.name
        n = 1
        while target.exists():
            target = folder / f"{source.stem}_{n}{source.suffix}"
            n += 1
        shutil.copy2(source, target)
        recipe.images.append(target.name)
        if save and self.get(recipe.id):
            self.save()
        return target

    def remove_image(self, recipe: Recipe, name: str) -> None:
        (self.image_folder(recipe) / name).unlink(missing_ok=True)
        if name in recipe.images:
            recipe.images.remove(name)
        if self.get(recipe.id):
            self.save()

    # --------------------------------------------------------- import/export
    def export_to(self, path: Path) -> None:
        _atomic_write_json(path, [r.to_dict() for r in self._recipes])

    def import_from(self, path: Path) -> int:
        """Add the recipes of another MAMARMITON JSON file; returns how many."""
        with open(path, encoding="utf-8-sig") as f:
            data = json.load(f)
        if isinstance(data, dict):
            data = data.get("recettes", [data])
        known = {r.id for r in self._recipes}
        count = 0
        for item in data if isinstance(data, list) else []:
            try:
                recipe = Recipe.from_dict(item)
            except ValueError:
                continue
            if recipe.id in known:
                continue
            recipe.images = []  # image files are not part of the export
            self._recipes.append(recipe)
            known.add(recipe.id)
            count += 1
        if count:
            self.save()
        return count
