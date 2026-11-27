"""Backup files: the zip written by ``GET /export`` and the readers behind ``POST /import/backup``.

Zip layout (also what the desktop data folder looks like once zipped)::

    recettes.json                       list of recipes, the desktop JSON format
    images/<recipe id>/<file name>      the photos named in each recipe's "images"

The importer also takes a plain ``recette.json`` and a zipped desktop ``recette_app`` folder
(``recette.json`` + ``images/<recipe id>/...``, possibly inside one top folder). Nothing from an
archive is ever written to disk under the name it carries; names are only looked up.
"""

from __future__ import annotations

import json
import stat
import zipfile
from pathlib import PurePosixPath
from typing import IO

JSON_NAMES = ("recettes.json", "recette.json")
MAX_ZIP_FILES = 20_000  # entries in the archive
MAX_ZIP_TOTAL = 2 * 1024**3  # uncompressed bytes in all
MAX_JSON_BYTES = 64 * 1024 * 1024  # the recipes file itself (also the limit for a bare .json upload)


class BadBackup(ValueError):
    """The file cannot be used as a backup; the message is shown to the user."""


def safe_name(name: str) -> str | None:
    """A normalised relative path, or None for an absolute path, drive letter or ``..`` (ignored)."""
    name = name.replace("\\", "/")
    if not name or name.startswith("/") or "\x00" in name or (len(name) > 1 and name[1] == ":"):
        return None
    parts = [p for p in name.split("/") if p not in ("", ".")]
    if not parts or ".." in parts:
        return None
    return "/".join(parts)


def decode_recipes(raw: bytes) -> list:
    """The recipes in a ``recette.json`` (BOM tolerated; a list, or ``{"recettes": [...]}`` like the desktop)."""
    try:
        data = json.loads(raw.decode("utf-8-sig"))
    except (ValueError, UnicodeDecodeError) as exc:  # JSONDecodeError is a ValueError
        raise BadBackup("Ce fichier n'est pas une sauvegarde Mamarmiton lisible.") from exc
    if isinstance(data, dict):
        data = data.get("recettes", [data])
    if not isinstance(data, list):
        raise BadBackup("Ce fichier ne contient pas de recettes.")
    return data


class BackupZip:
    """A checked view of an uploaded zip: counts and sizes are capped, odd entries are ignored."""

    def __init__(self, fileobj: IO[bytes], max_file: int, max_files: int | None = None, max_total: int | None = None):
        max_files = MAX_ZIP_FILES if max_files is None else max_files
        max_total = MAX_ZIP_TOTAL if max_total is None else max_total
        try:
            self.zip = zipfile.ZipFile(fileobj)
            infos = self.zip.infolist()
        except (zipfile.BadZipFile, OSError, ValueError, NotImplementedError) as exc:
            raise BadBackup("Ce fichier zip est illisible.") from exc
        if len(infos) > max_files:
            raise BadBackup("Cette archive contient trop de fichiers.")
        self.max_file = max_file
        self.max_total = max_total
        self.read_total = 0
        self.files: dict[str, zipfile.ZipInfo] = {}
        declared = 0
        for info in infos:
            name = safe_name(info.filename)
            if name is None or info.is_dir() or info.flag_bits & 0x1:  # encrypted: cannot be read anyway
                continue
            if stat.S_ISLNK(info.external_attr >> 16):  # a symlink's content is a target path, not a file
                continue
            declared += info.file_size
            self.files.setdefault(name, info)
        if declared > max_total:
            raise BadBackup("Cette archive est trop volumineuse une fois dézippée.")

    def read(self, name: str, limit: int) -> bytes | None:
        """The content of an entry, or None when absent, over ``limit`` or past the total budget.
        Sizes are enforced while reading: the sizes an archive declares can lie."""
        info = self.files.get(name)
        if info is None or info.file_size > limit:
            return None
        try:
            with self.zip.open(info) as f:
                data = f.read(limit + 1)
        except (zipfile.BadZipFile, OSError, RuntimeError, NotImplementedError, EOFError, ValueError):
            return None
        self.read_total += len(data)
        if len(data) > limit or self.read_total > self.max_total:
            return None
        return data

    def locate(self) -> tuple[bytes, str]:
        """(the recipes file, the folder prefix it sits in, "" or "top/") using the shallowest candidate."""
        found = sorted((len(n.split("/")), n) for n in self.files if PurePosixPath(n).name.lower() in JSON_NAMES)
        if not found:
            raise BadBackup("Cette archive ne contient pas de fichier recettes.json ou recette.json.")
        name = found[0][1]
        raw = self.read(name, MAX_JSON_BYTES)
        if raw is None:
            raise BadBackup("Le fichier de recettes de l'archive est illisible ou trop gros.")
        return raw, name[: -len(PurePosixPath(name).name)]

    def photo(self, prefix: str, recipe_id: str, filename: str) -> bytes | None:
        name = safe_name(f"{prefix}images/{recipe_id}/{filename}")
        return None if name is None else self.read(name, self.max_file)

    def close(self) -> None:
        self.zip.close()
