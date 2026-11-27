"""Server-backed recipe store with an offline cache and an outbox.

``RemoteStore`` has the same public methods as ``storage.RecipeStore`` so the
UI can use either. Every change is applied to the local cache at once and
queued in ``outbox.json``; ``sync()`` (call it from a background thread)
pushes the queue, then pulls what changed on the server.

Cache layout, in ``<data dir>/remote/<username>/``::

    cache.json    recipes + server meta (version, access, owner, images), last seq, conflicts
    outbox.json   pending operations, in order
    images/<recipe id>/<file>
"""

from __future__ import annotations

import json
import logging
import shutil
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

from mamarmiton import config
from mamarmiton.models import Recipe
from mamarmiton.remote.client import ApiClient, ApiError, AuthError, ConflictError, OfflineError
from mamarmiton.storage import RecipeStore, _atomic_write_json

log = logging.getLogger(__name__)

READ_ONLY_MSG = "Cette recette est en lecture seule : vous ne pouvez pas la modifier."
NOT_OWNER_MSG = ("Seul le propriétaire peut supprimer cette recette. "
                 "Vous pouvez arrêter de voir ce carnet à la place.")
EXTENSIONS = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp", "image/gif": "gif"}


@dataclass
class Conflict:
    """Two versions of one recipe: ``mine`` (local edit) and ``theirs`` (the server's, kept as the recipe)."""

    recipe_id: str
    mine: Recipe
    theirs: Recipe


@dataclass
class SyncResult:
    ok: bool = True  # False when stopped early (offline, signed out, error)
    skipped: bool = False  # another sync was already running
    pushed: int = 0  # outbox operations delivered
    pulled: int = 0  # recipes received from the server
    removed: int = 0  # recipes that disappeared (deleted / share revoked)
    conflicts: int = 0  # new conflicts
    images: int = 0  # photos downloaded
    changed: bool = False  # what the UI shows has changed
    error: str = ""


class RemoteStore:
    """Drop-in replacement for ``RecipeStore`` backed by the server.

    Extra attributes: ``client``, ``username``, ``status`` ("ok" | "offline" |
    "syncing" | "auth" | "error"), ``last_sync`` (datetime | None), ``last_error``,
    ``pending_count``, ``conflicts`` (list of ``Conflict``) and ``on_change``
    (called from the sync thread when visible data changed).
    """

    def __init__(self, client: ApiClient, cache_dir: Path | None = None, username: str | None = None):
        self.client = client
        self.username = username or client.username
        if not self.username and cache_dir is None:
            from mamarmiton.remote.account import load_account
            account = load_account()
            self.username = account.username if account else ""
        self.cache_dir = Path(cache_dir) if cache_dir else config.data_dir() / "remote" / (self.username or "_inconnu")
        self.images_root = self.cache_dir / "images"
        self.warnings: list[str] = []
        self.status = "ok"
        self.last_sync: datetime | None = None
        self.last_error = ""
        self.on_change: Callable[[], None] | None = None
        self._lock = threading.RLock()
        self._sync_lock = threading.Lock()
        self._recipes: dict[str, Recipe] = {}
        self._meta: dict[str, dict] = {}
        self._outbox: list[dict] = []
        self._conflicts: list[Conflict] = []
        self._seq = 0
        self._full_pull = False
        self._op_error = ""  # message of an operation the server refused during this sync
        self._load()

    # ------------------------------------------------------------------ io
    @property
    def _cache_path(self) -> Path:
        return self.cache_dir / "cache.json"

    @property
    def _outbox_path(self) -> Path:
        return self.cache_dir / "outbox.json"

    def _read_json(self, path: Path, default):
        if not path.is_file() or path.stat().st_size == 0:
            return default
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            aside = path.with_name(f"{path.stem}.corrompu-{datetime.now():%Y%m%d-%H%M%S}.json")
            shutil.copy2(path, aside)
            self.warnings.append(f"Un fichier du cache était illisible (copie : {aside.name}).")
            return default

    def _load(self) -> None:
        cache = self._read_json(self._cache_path, {})
        self._seq = int(cache.get("seq", 0) or 0)
        for rid, entry in (cache.get("recipes") or {}).items():
            try:
                self._recipes[rid] = Recipe.from_dict(entry["recipe"])
                self._meta[rid] = entry["meta"]
            except (ValueError, KeyError, TypeError):
                log.warning("Skipping unreadable cached recipe %s", rid)
        for c in cache.get("conflicts") or []:
            try:
                self._conflicts.append(Conflict(c["recipe_id"], Recipe.from_dict(c["mine"]),
                                                Recipe.from_dict(c["theirs"])))
            except (ValueError, KeyError, TypeError):
                pass
        outbox = self._read_json(self._outbox_path, [])
        self._outbox = [op for op in outbox if isinstance(op, dict) and "op" in op]

    def _save(self) -> None:
        with self._lock:
            _atomic_write_json(self._cache_path, {
                "seq": self._seq,
                "recipes": {rid: {"recipe": r.to_dict(), "meta": self._meta[rid]}
                            for rid, r in self._recipes.items()},
                "conflicts": [{"recipe_id": c.recipe_id, "mine": c.mine.to_dict(), "theirs": c.theirs.to_dict()}
                              for c in self._conflicts],
            })
            _atomic_write_json(self._outbox_path, self._outbox)

    def save(self) -> None:
        """Persist the cache; also queues favourite changes made directly on a recipe object."""
        with self._lock:
            for rid, recipe in self._recipes.items():
                self._sync_fav(rid, recipe)
            self._save()

    # ------------------------------------------------------- status for UI
    @property
    def pending_count(self) -> int:
        """Number of changes not yet delivered to the server."""
        with self._lock:
            return len(self._outbox)

    @property
    def conflicts(self) -> list[Conflict]:
        """Edits that clashed with someone else's; settle each with ``resolve_conflict``."""
        with self._lock:
            return list(self._conflicts)

    # ---------------------------------------------------------- permissions
    def access(self, recipe_id: str) -> str:
        """"owner", "edit" or "read" (unknown ids count as "owner": a new recipe)."""
        with self._lock:
            return self._meta.get(recipe_id, {}).get("access", "owner")

    def owner(self, recipe_id: str) -> str:
        """Username of the recipe's owner."""
        with self._lock:
            return self._meta.get(recipe_id, {}).get("owner") or self.username

    def can_edit(self, recipe_id: str) -> bool:
        return self.access(recipe_id) in ("owner", "edit")

    def can_delete(self, recipe_id: str) -> bool:
        return self.access(recipe_id) == "owner"

    def _require_edit(self, recipe_id: str) -> None:
        if recipe_id in self._recipes and not self.can_edit(recipe_id):
            raise PermissionError(READ_ONLY_MSG)

    # ---------------------------------------------------------------- crud
    def all(self) -> list[Recipe]:
        with self._lock:
            return list(self._recipes.values())

    def __len__(self) -> int:
        with self._lock:
            return len(self._recipes)

    def get(self, recipe_id: str) -> Recipe | None:
        with self._lock:
            return self._recipes.get(recipe_id)

    def add(self, recipe: Recipe) -> Recipe:
        with self._lock:
            while recipe.id in self._recipes:
                recipe.id = Recipe(nom="").id
            self._recipes[recipe.id] = recipe
            self._meta[recipe.id] = {"version": None, "access": "owner", "owner": self.username,
                                     "images": {}, "server_images": [], "fav": False}
            self._queue_put(recipe.id)
            self._sync_fav(recipe.id, recipe)
            self._reconcile_images(recipe)
            self._save()
        return recipe

    def update(self, recipe: Recipe) -> None:
        with self._lock:
            if recipe.id not in self._recipes:
                self.add(recipe)
                return
            self._require_edit(recipe.id)
            recipe.touch()
            self._recipes[recipe.id] = recipe
            self._queue_put(recipe.id)
            self._sync_fav(recipe.id, recipe)
            self._reconcile_images(recipe)
            self._save()

    def delete(self, recipe_id: str) -> None:
        """Delete a recipe you own (``PermissionError`` otherwise)."""
        with self._lock:
            if recipe_id not in self._recipes:
                return
            if not self.can_delete(recipe_id):
                raise PermissionError(NOT_OWNER_MSG)
            on_server = self._meta[recipe_id].get("version") is not None
            self._drop_ops(recipe_id)
            del self._recipes[recipe_id]
            del self._meta[recipe_id]
            self._conflicts = [c for c in self._conflicts if c.recipe_id != recipe_id]
            shutil.rmtree(self.images_root / recipe_id, ignore_errors=True)
            if on_server:
                self._push_op({"op": "delete", "id": recipe_id})
            self._save()

    def toggle_favorite(self, recipe_id: str) -> bool:
        with self._lock:
            recipe = self._recipes.get(recipe_id)
            if recipe is None:
                return False
            recipe.favori = not recipe.favori
            self._sync_fav(recipe_id, recipe)
            self._save()
            return recipe.favori

    # -------------------------------------------------------------- images
    def image_folder(self, recipe: Recipe) -> Path:
        return self.images_root / recipe.id

    def image_paths(self, recipe: Recipe) -> list[Path]:
        folder = self.image_folder(recipe)
        return [folder / name for name in recipe.images if (folder / name).is_file()]

    def add_image(self, recipe: Recipe, source: Path, save: bool = True) -> Path:
        with self._lock:
            self._require_edit(recipe.id)
            folder = self.image_folder(recipe)
            folder.mkdir(parents=True, exist_ok=True)
            target = folder / source.name
            n = 1
            while target.exists():
                target = folder / f"{source.stem}_{n}{source.suffix}"
                n += 1
            shutil.copy2(source, target)
            recipe.images.append(target.name)
            if save and recipe.id in self._recipes:
                self._reconcile_images(recipe)
                self._save()
            return target

    def remove_image(self, recipe: Recipe, name: str) -> None:
        with self._lock:
            self._require_edit(recipe.id)
            (self.image_folder(recipe) / name).unlink(missing_ok=True)
            if name in recipe.images:
                recipe.images.remove(name)
            if recipe.id in self._recipes:
                self._reconcile_images(recipe)
                self._save()

    # --------------------------------------------------------- import/export
    def export_to(self, path: Path) -> None:
        with self._lock:
            _atomic_write_json(path, [r.to_dict() for r in self._recipes.values()])

    def import_from(self, path: Path) -> int:
        """Add the recipes of another MAMARMITON JSON file; returns how many."""
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            data = data.get("recettes", [data])
        count = 0
        for item in data if isinstance(data, list) else []:
            try:
                recipe = Recipe.from_dict(item)
            except ValueError:
                continue
            if recipe.id in self._recipes:
                continue
            recipe.images = []
            self.add(recipe)
            count += 1
        return count

    # --------------------------------------------------------------- outbox
    def _push_op(self, op: dict) -> dict:
        op["uid"] = uuid.uuid4().hex
        self._outbox.append(op)
        return op

    def _find_op(self, kind: str, recipe_id: str) -> dict | None:
        return next((o for o in self._outbox if o["op"] == kind and o.get("id") == recipe_id), None)

    def _drop_ops(self, recipe_id: str) -> None:
        self._outbox = [o for o in self._outbox if o.get("id") != recipe_id]

    def _queue_put(self, recipe_id: str) -> None:
        op = self._find_op("put", recipe_id)
        if op:
            op["rev"] += 1
        else:
            self._push_op({"op": "put", "id": recipe_id, "base": self._meta[recipe_id].get("version"), "rev": 1})

    def _sync_fav(self, recipe_id: str, recipe: Recipe) -> None:
        meta = self._meta[recipe_id]
        if bool(recipe.favori) == bool(meta.get("fav")):
            return
        meta["fav"] = bool(recipe.favori)
        old = self._find_op("fav", recipe_id)
        if old:
            self._outbox.remove(old)
        self._push_op({"op": "fav", "id": recipe_id, "value": meta["fav"]})

    def _reconcile_images(self, recipe: Recipe) -> None:
        """Queue uploads for new photo files and deletions for removed ones."""
        meta = self._meta[recipe.id]
        known: dict[str, str] = meta["images"]
        folder = self.image_folder(recipe)
        for name in recipe.images:
            if name not in known and (folder / name).is_file():
                known[name] = ""  # "" = not uploaded yet
                self._push_op({"op": "img_add", "id": recipe.id, "file": name})
        for name in [n for n in known if n not in recipe.images]:
            image_id = known.pop(name)
            (folder / name).unlink(missing_ok=True)
            if image_id:
                self._push_op({"op": "img_del", "id": recipe.id, "image_id": image_id})
            else:
                self._outbox = [o for o in self._outbox
                                if not (o["op"] == "img_add" and o["id"] == recipe.id and o["file"] == name)]

    def _remove_op(self, uid: str) -> None:
        self._outbox = [o for o in self._outbox if o["uid"] != uid]

    # ---------------------------------------------------------- conflicts
    def resolve_conflict(self, recipe_id: str, choice: str) -> Recipe | None:
        """Settle a conflict: "theirs" (keep the server's), "mine" (overwrite with my edit) or
        "both" (keep theirs and save mine as a new recipe named "<nom> (ma version)").
        Returns the recipe now holding the result."""
        if choice not in ("mine", "theirs", "both"):
            raise ValueError(choice)
        with self._lock:
            conflict = next((c for c in self._conflicts if c.recipe_id == recipe_id), None)
            if conflict is None:
                return self._recipes.get(recipe_id)
            self._conflicts.remove(conflict)
            current = self._recipes.get(recipe_id)
            result = current
            if choice == "mine":
                mine = Recipe.from_dict(conflict.mine.to_dict())
                mine.id = recipe_id
                if current is not None:
                    mine.images, mine.favori = list(current.images), current.favori
                    self.update(mine)
                else:
                    mine.images = []
                    self.add(mine)
                result = mine
            elif choice == "both":
                copy = Recipe.from_dict(conflict.mine.to_dict())
                copy.id = uuid.uuid4().hex
                copy.nom = f"{conflict.mine.nom} (ma version)"
                copy.images, copy.favori = [], False
                self.add(copy)
                result = current
            self._save()
            return result

    # ----------------------------------------------------------------- sync
    def sync(self) -> SyncResult:
        """Push pending changes, then pull the server's. Safe to call from a background thread;
        returns at once (``skipped``) if a sync is already running."""
        if not self._sync_lock.acquire(blocking=False):
            return SyncResult(skipped=True)
        result = SyncResult()
        self.status = "syncing"
        self._op_error = ""
        try:
            if not self.username:
                self.username = self.client.me()["username"]
            self._push(result)
            self._pull(result)
            self._download_missing(result)
            self.status, self.last_sync = "ok", datetime.now()
            self.last_error = self._op_error
        except OfflineError as exc:
            self.status, self.last_error, result.ok, result.error = "offline", exc.message, False, exc.message
        except AuthError as exc:
            self.status, self.last_error, result.ok, result.error = "auth", exc.message, False, exc.message
        except ApiError as exc:
            self.status, self.last_error, result.ok, result.error = "error", exc.message, False, exc.message
        finally:
            self._sync_lock.release()
        if result.changed and self.on_change:
            try:
                self.on_change()
            except Exception:  # noqa: BLE001 - a UI callback must never break syncing
                log.exception("on_change failed")
        return result

    def _op_failed(self, op: dict, exc: ApiError) -> None:
        """A permanent refusal: forget the operation, keep the message."""
        with self._lock:
            self._remove_op(op["uid"])
            self._save()
        self._op_error = exc.message
        log.warning("Dropped %s for %s: %s", op["op"], op.get("id"), exc.message)

    # -- push
    def _push(self, result: SyncResult) -> None:
        while True:
            with self._lock:
                op = dict(self._outbox[0]) if self._outbox else None
            if op is None:
                return
            if self._run_op(op, result):
                result.pushed += 1

    def _run_op(self, op: dict, result: SyncResult) -> bool:
        """Deliver one operation. Returns True when it was delivered."""
        rid = op["id"]
        kind = op["op"]
        try:
            if kind == "put":
                return self._run_put(op, result)
            if kind == "delete":
                try:
                    self.client.delete_recipe(rid)
                except ApiError as exc:
                    if exc.status != 404:
                        raise
                self._finish(op)
            elif kind == "fav":
                self.client.set_favorite(rid, op["value"])
                self._finish(op)
            elif kind == "img_add":
                return self._run_img_add(op, result)
            elif kind == "img_del":
                try:
                    self.client.delete_image(rid, op["image_id"])
                except ApiError as exc:
                    if exc.status != 404:
                        raise
                self._finish(op)
            else:
                self._finish(op)
            return True
        except (OfflineError, AuthError):
            raise
        except ConflictError as exc:  # unexpected for non-put ops
            self._op_failed(op, exc)
        except ApiError as exc:
            self._op_failed(op, exc)
        return False

    def _finish(self, op: dict) -> None:
        with self._lock:
            self._remove_op(op["uid"])
            self._save()

    def _run_put(self, op: dict, result: SyncResult) -> bool:
        rid = op["id"]
        with self._lock:
            recipe = self._recipes.get(rid)
            if recipe is None:
                self._remove_op(op["uid"])
                self._save()
                return False
            data = recipe.to_dict()
        try:
            out = self.client.put_recipe(rid, data, op["base"])
        except ConflictError as exc:
            if exc.current:
                self._conflict(op, exc.current, result)
            else:
                self._create_exists(op)
            return False
        except OfflineError:
            raise
        except AuthError:
            raise
        except ApiError as exc:
            if exc.status in (403, 404) and op["base"] is None:
                with self._lock:  # that id belongs to someone else: use a fresh one
                    if rid in self._recipes:
                        self._reid(rid)
                        self._save()
            elif exc.status in (403, 404):
                self._rescue_copy(op, exc)
            else:
                self._op_failed(op, exc)
            return False
        with self._lock:
            live = next((o for o in self._outbox if o["uid"] == op["uid"]), None)
            meta = self._meta.get(rid)
            if meta is not None:
                meta.update(version=out["version"], access=out["access"], owner=out["owner"])
            if live is not None:
                if live["rev"] == op["rev"]:
                    self._outbox.remove(live)
                else:  # edited again while uploading: send that too, on top of this version
                    live["base"] = out["version"]
            self._save()
        return True

    def _conflict(self, op: dict, current: dict, result: SyncResult) -> None:
        """Someone else saved first: the server's version wins, my edit becomes a Conflict entry."""
        rid = op["id"]
        with self._lock:
            mine = self._recipes.get(rid)
            self._remove_op(op["uid"])
            if mine is not None:
                mine = Recipe.from_dict(mine.to_dict())
            self._apply_item(current, force=True)
            theirs = self._recipes.get(rid)
            if mine is not None and theirs is not None:
                self._conflicts = [c for c in self._conflicts if c.recipe_id != rid]
                self._conflicts.append(Conflict(rid, mine, Recipe.from_dict(theirs.to_dict())))
                result.conflicts += 1
            result.changed = True
            self._save()

    def _create_exists(self, op: dict) -> None:
        """409 on create: either our earlier upload (answer lost) or another user's recipe id."""
        rid = op["id"]
        try:
            current = self.client.get_recipe(rid)
        except OfflineError:
            raise
        except ApiError:
            current = None
        with self._lock:
            live = next((o for o in self._outbox if o["uid"] == op["uid"]), None)
            if live is None or rid not in self._recipes:
                return
            if current and current["owner"] == self.username:
                live["base"] = current["version"]
                self._meta[rid]["version"] = current["version"]
            else:
                self._reid(rid)
            self._save()

    def _rescue_copy(self, op: dict, exc: ApiError) -> None:
        """The server no longer lets me edit this recipe: keep my edit as a new recipe of mine."""
        rid = op["id"]
        with self._lock:
            self._remove_op(op["uid"])
            recipe = self._recipes.get(rid)
            if recipe is not None:
                copy = Recipe.from_dict(recipe.to_dict())
                copy.id = uuid.uuid4().hex
                copy.nom = f"{recipe.nom} (ma version)"
                copy.images, copy.favori = [], False
                self.add(copy)
            self._full_pull = True  # let the server's state replace the stale local copy
            self._save()
        self._op_error = exc.message

    def _reid(self, old: str) -> str:
        """Give a local recipe a new id (and move its photos, queued operations and conflicts)."""
        new = uuid.uuid4().hex
        recipe = self._recipes.pop(old)
        recipe.id = new
        self._recipes[new] = recipe
        self._meta[new] = self._meta.pop(old)
        for op in self._outbox:
            if op.get("id") == old:
                op["id"] = new
        for c in self._conflicts:
            if c.recipe_id == old:
                c.recipe_id = new
        if (self.images_root / old).is_dir():
            (self.images_root / old).rename(self.images_root / new)
        return new

    def _run_img_add(self, op: dict, result: SyncResult) -> bool:
        rid, name = op["id"], op["file"]
        with self._lock:
            path = self.images_root / rid / name
            meta = self._meta.get(rid)
            gone = meta is None or name not in meta["images"] or not path.is_file()
            if gone:
                self._remove_op(op["uid"])
                self._save()
                return False
        try:
            out = self.client.upload_image(rid, path)
        except (OfflineError, AuthError):
            raise
        except ApiError as exc:
            with self._lock:  # cannot be stored: forget this photo
                recipe = self._recipes.get(rid)
                if recipe is not None and name in recipe.images:
                    recipe.images.remove(name)
                if meta is not None:
                    meta["images"].pop(name, None)
                path.unlink(missing_ok=True)
                result.changed = True
            self._op_failed(op, exc)
            return False
        with self._lock:
            if name in meta["images"]:
                meta["images"][name] = out["id"]
            else:  # removed while uploading
                self._push_op({"op": "img_del", "id": rid, "image_id": out["id"]})
            self._remove_op(op["uid"])
            self._save()
        return True

    # -- pull
    def _pull(self, result: SyncResult) -> None:
        since = 0 if self._full_pull else self._seq
        data = self.client.list_recipes(since)
        with self._lock:
            self._full_pull = False
            for item in data["recipes"]:
                if self._apply_item(item):
                    result.pulled += 1
                    result.changed = True
            visible = set(data["visible_ids"])
            for rid in [r for r in self._recipes if r not in visible
                        and self._meta[r].get("version") is not None]:
                self._drop_local(rid)
                result.removed += 1
                result.changed = True
            favs = set(data["favorites"])
            for op in self._outbox:  # my unsent favourite changes still count
                if op["op"] == "fav":
                    (favs.add if op["value"] else favs.discard)(op["id"])
            for rid, recipe in self._recipes.items():
                value = rid in favs
                if recipe.favori != value:
                    recipe.favori = value
                    result.changed = True
                self._meta[rid]["fav"] = value
            self._seq = data["seq"]
            self._save()

    def _drop_local(self, rid: str) -> None:
        self._drop_ops(rid)
        self._recipes.pop(rid, None)
        self._meta.pop(rid, None)
        shutil.rmtree(self.images_root / rid, ignore_errors=True)

    def _apply_item(self, item: dict, force: bool = False) -> bool:
        """Put one server recipe into the cache. Returns True if something was applied."""
        rid = item["id"]
        if any(o["op"] == "delete" and o["id"] == rid for o in self._outbox):
            return False
        if item["deleted"]:
            if rid in self._recipes:
                self._drop_local(rid)
                return True
            return False
        meta = self._meta.get(rid)
        if meta is not None and not force and self._find_op("put", rid):
            # unsent local edits: keep them, learn only the new access rights
            meta.update(access=item["access"], owner=item["owner"])
            return False
        data = dict(item["recipe"])
        data["id"] = rid
        recipe = Recipe.from_dict(data)
        if meta is None:
            meta = self._meta[rid] = {"images": {}, "fav": False}
        meta.update(version=item["version"], access=item["access"], owner=item["owner"],
                    server_images=item["images"])
        recipe.favori = bool(meta.get("fav"))
        old = self._recipes.get(rid)
        self._merge_images(rid, recipe, meta, item["images"], old)
        self._recipes[rid] = recipe
        return True

    def _merge_images(self, rid: str, recipe: Recipe, meta: dict, server_images: list[dict],
                      old: Recipe | None) -> None:
        known: dict[str, str] = meta["images"]
        by_id = {image_id: name for name, image_id in known.items() if image_id}
        deleting = {o["image_id"] for o in self._outbox if o["op"] == "img_del" and o["id"] == rid}
        names: dict[str, str] = {}
        for img in server_images:
            if img["id"] in deleting:
                continue
            name = by_id.get(img["id"]) or f"{img['id']}.{EXTENSIONS.get(img['content_type'], 'jpg')}"
            names[name] = img["id"]
        folder = self.images_root / rid
        for name, image_id in known.items():
            if image_id and name not in names:  # removed on another device
                (folder / name).unlink(missing_ok=True)
        pending = {name: "" for name, image_id in known.items() if not image_id}
        meta["images"] = {**names, **pending}
        recipe.images = list(meta["images"])

    def _download_missing(self, result: SyncResult) -> None:
        with self._lock:
            todo = [(rid, name, image_id) for rid, meta in self._meta.items()
                    for name, image_id in meta["images"].items()
                    if image_id and not (self.images_root / rid / name).is_file()]
        for rid, name, image_id in todo:
            try:
                blob = self.client.download_image(rid, image_id)
            except (OfflineError, AuthError):
                raise
            except ApiError:
                continue  # photo gone meanwhile; the next pull will tell
            with self._lock:
                if rid not in self._recipes:
                    continue
                folder = self.images_root / rid
                folder.mkdir(parents=True, exist_ok=True)
                tmp = folder / f".{name}.tmp"
                tmp.write_bytes(blob)
                tmp.replace(folder / name)
            result.images += 1
            result.changed = True

    # ------------------------------------------------------ first-login upload
    def upload_local(self, local_store: RecipeStore,
                     progress: Callable[[int, int], None] | None = None) -> int:
        """Upload every recipe of the local book (ids kept, favourites and photos included).

        Safe to interrupt and rerun: progress is kept in ``upload_state.json``.
        The local ``recette.json`` is never modified. Call ``sync()`` afterwards to
        pull the uploaded recipes into the cache. Returns how many recipes were uploaded now.
        """
        state_path = self.cache_dir / "upload_state.json"
        state: dict[str, dict] = self._read_json(state_path, {})
        recipes = local_store.all()
        if not self.username:
            self.username = self.client.me()["username"]
        count = 0
        for i, recipe in enumerate(recipes):
            if progress:
                progress(i, len(recipes))
            entry = state.get(recipe.id)
            if entry and entry.get("done"):
                continue
            if entry is None:
                entry = {"id": recipe.id, "images": [], "done": False}
                try:
                    existing = self.client.get_recipe(recipe.id)
                except OfflineError:
                    raise
                except AuthError:
                    raise
                except ApiError:
                    existing = None
                if existing is not None and existing["owner"] == self.username:
                    entry["done"] = True  # already uploaded earlier
                    state[recipe.id] = entry
                    _atomic_write_json(state_path, state)
                    continue
                if existing is not None:  # someone else's recipe has this id
                    entry["id"] = uuid.uuid4().hex
                state[recipe.id] = entry
                _atomic_write_json(state_path, state)
            server_id = entry["id"]
            try:
                self.client.get_recipe(server_id)
            except ApiError as exc:
                if exc.status != 404:
                    raise
                data = recipe.to_dict()
                data["id"] = server_id
                self.client.put_recipe(server_id, data, None)
            for name in recipe.images:
                path = local_store.image_folder(recipe) / name
                if name in entry["images"] or not path.is_file():
                    continue
                try:
                    self.client.upload_image(server_id, path)
                except ConflictError:  # too many photos
                    continue
                entry["images"].append(name)
                _atomic_write_json(state_path, state)
            if recipe.favori:
                self.client.set_favorite(server_id, True)
            entry["done"] = True
            _atomic_write_json(state_path, state)
            count += 1
        if progress:
            progress(len(recipes), len(recipes))
        return count
