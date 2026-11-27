"""Snapshots, restore and the restore drill.

A snapshot is one ``mamarmiton-YYYYMMDD-HHMMSS.tar.gz`` (UTC) holding::

    manifest.json          counts, sha256 of the database copy, app version, photo list
    mamarmiton.sqlite3     consistent copy made with SQLite's online backup API
    images/<recipe>/<file> every photo the database copy refers to

Exit codes used by the command line (for cron and monitoring):
0 = fine, 1 = failed (nothing usable was written / the check failed),
2 = a snapshot was written but is incomplete (a photo was missing on disk).
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import sqlite3
import tarfile
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from mamarmiton_server import __version__
from mamarmiton_server.config import Settings

DB_NAME = "mamarmiton.sqlite3"
MANIFEST = "manifest.json"
NAME_RE = re.compile(r"^mamarmiton-(\d{8})-(\d{6})\.tar\.gz$")
FORMAT = 1


class BackupError(Exception):
    pass


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _counts(conn: sqlite3.Connection) -> dict[str, int]:
    one = lambda sql: conn.execute(sql).fetchone()[0]  # noqa: E731
    return {"users": one("SELECT COUNT(*) FROM users"),
            "recipes": one("SELECT COUNT(*) FROM recipes WHERE deleted = 0"),
            "recipes_total": one("SELECT COUNT(*) FROM recipes"),
            "images": one("SELECT COUNT(*) FROM images")}


def _image_rows(conn: sqlite3.Connection) -> list[tuple[str, str]]:
    return [(r[0], r[1]) for r in conn.execute("SELECT recipe_id, filename FROM images ORDER BY recipe_id, filename")]


def _safe_part(name: str) -> bool:
    return bool(name) and name not in (".", "..") and "/" not in name and "\\" not in name and "\0" not in name


# ---------------------------------------------------------------- backup

def _build(settings: Settings, work: Path) -> tuple[dict, list[str]]:
    """Fill ``work`` with the database copy, photos and manifest. Returns (manifest, missing photos)."""
    db_copy = work / DB_NAME
    src = sqlite3.connect(settings.db_path, timeout=30)
    try:
        dst = sqlite3.connect(db_copy)
        try:
            src.backup(dst)  # consistent, even while the server writes
            dst.execute("PRAGMA journal_mode = DELETE")  # one self-contained file
        finally:
            dst.close()
    finally:
        src.close()
    conn = sqlite3.connect(db_copy)
    try:
        if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise BackupError("la copie de la base échoue à integrity_check")
        counts = _counts(conn)
        rows = _image_rows(conn)
    finally:
        conn.close()
    photos, missing = [], []
    for recipe_id, filename in rows:
        rel = f"images/{recipe_id}/{filename}"
        if not (_safe_part(recipe_id) and _safe_part(filename)):
            raise BackupError(f"nom de photo suspect dans la base : {rel!r}")
        src_file = settings.images_dir / recipe_id / filename
        if not src_file.is_file():
            missing.append(rel)
            continue
        dst_file = work / rel
        dst_file.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src_file, dst_file)
        photos.append({"path": rel, "size": dst_file.stat().st_size, "sha256": _sha256(dst_file)})
    manifest = {"format": FORMAT, "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "app_version": __version__, "counts": counts, "db_sha256": _sha256(db_copy),
                "photos": photos, "missing_photos": missing}
    (work / MANIFEST).write_text(json.dumps(manifest, indent=1, ensure_ascii=False), encoding="utf-8")
    return manifest, missing


def create_snapshot(settings: Settings, dest_dir: Path, when: datetime | None = None,
                    attempts: int = 3) -> tuple[Path, dict, list[str]]:
    """Write a snapshot into ``dest_dir``. Returns (archive, manifest, missing photos).

    A photo that vanishes between the database copy and the file copy (deleted by a user
    meanwhile) makes it retry, so only a photo that is really absent ends up in ``missing``.
    """
    if not settings.db_path.is_file():
        raise BackupError(f"base introuvable : {settings.db_path}")
    dest_dir.mkdir(parents=True, exist_ok=True)
    when = (when or datetime.now(timezone.utc)).astimezone(timezone.utc)
    archive = dest_dir / f"mamarmiton-{when:%Y%m%d-%H%M%S}.tar.gz"
    with tempfile.TemporaryDirectory(prefix="mm-backup-", dir=dest_dir) as tmp:
        work = Path(tmp) / "snap"
        for attempt in range(attempts):
            if work.exists():
                shutil.rmtree(work)
            work.mkdir()
            manifest, missing = _build(settings, work)
            if not missing:
                break
        part = Path(tmp) / "out.tar.gz"
        with tarfile.open(part, "w:gz") as tar:
            for item in sorted(work.rglob("*")):
                tar.add(item, arcname=str(item.relative_to(work)), recursive=False)
        # light check of what was just written, before it replaces anything
        problems = check_snapshot(part, deep=False).problems
        if problems:
            raise BackupError("archive écrite mais invalide : " + "; ".join(problems))
        part.replace(archive)  # atomic: a half-written file never carries the final name
    return archive, manifest, missing


def snapshot_time(name: str) -> datetime | None:
    m = NAME_RE.match(name)
    if not m:
        return None
    return datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)


def apply_retention(dest_dir: Path, keep_daily: int, keep_weekly: int) -> list[Path]:
    """Keep the newest snapshot of each of the last ``keep_daily`` days and of the last
    ``keep_weekly`` ISO weeks (days/weeks that have snapshots). Delete the rest.
    Only files named like our snapshots are ever touched. Returns the deleted paths."""
    found = sorted(((snapshot_time(p.name), p) for p in dest_dir.iterdir() if snapshot_time(p.name)),
                   key=lambda t: t[0], reverse=True)
    keep: set[Path] = set()
    days: dict = {}
    weeks: dict = {}
    for t, p in found:  # newest first, so setdefault keeps the newest of each bucket
        days.setdefault(t.date(), p)
        weeks.setdefault(tuple(t.isocalendar())[:2], p)
    keep.update(list(days.values())[:keep_daily])
    keep.update(list(weeks.values())[:keep_weekly])
    if found:
        keep.add(found[0][1])  # never delete the newest one
    deleted = []
    for _, p in found:
        if p not in keep:
            p.unlink()
            deleted.append(p)
    return deleted


# ----------------------------------------------------------------- check

@dataclass
class Report:
    problems: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    info: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems


def extract(snapshot: Path, into: Path) -> Path:
    """Unpack an archive (refusing unsafe members) or return the directory as is."""
    if snapshot.is_dir():
        return snapshot
    into.mkdir(parents=True, exist_ok=True)
    root = into.resolve()
    with tarfile.open(snapshot, "r:*") as tar:
        for m in tar.getmembers():
            target = (into / m.name).resolve()
            if not (m.isfile() or m.isdir()) or not target.is_relative_to(root):
                raise BackupError(f"élément d'archive refusé : {m.name!r}")
        tar.extractall(into, filter="data")
    return into


def _verify_dir(root: Path, rep: Report, deep: bool) -> dict | None:
    try:
        manifest = json.loads((root / MANIFEST).read_text(encoding="utf-8"))
        counts = manifest["counts"]
        photos = {p["path"]: p for p in manifest["photos"]}
        expected_sha = manifest["db_sha256"]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        rep.problems.append(f"manifeste illisible : {exc}")
        return None
    db_file = root / DB_NAME
    if not db_file.is_file():
        rep.problems.append("base absente de la sauvegarde")
        return None
    if _sha256(db_file) != expected_sha:
        rep.problems.append("sha256 de la base différent du manifeste (copie altérée)")
        return manifest
    try:
        conn = sqlite3.connect(f"file:{db_file}?mode=ro", uri=True)
        try:
            result = conn.execute("PRAGMA integrity_check").fetchone()[0]
            if result != "ok":
                rep.problems.append(f"integrity_check : {result}")
            actual = _counts(conn)
            rows = {f"images/{r}/{f}" for r, f in _image_rows(conn)}
        finally:
            conn.close()
    except sqlite3.DatabaseError as exc:
        rep.problems.append(f"base illisible : {exc}")
        return manifest
    for key, value in counts.items():
        if actual.get(key) != value:
            rep.problems.append(f"{key} : manifeste {value}, base {actual.get(key)}")
    rep.info.append(f"{actual['users']} comptes, {actual['recipes']} recettes, {actual['images']} photos")
    declared_missing = set(manifest.get("missing_photos", []))
    for rel in sorted(rows):
        f = root / rel
        if rel in declared_missing:
            rep.warnings.append(f"photo déjà absente lors de la sauvegarde : {rel}")
        elif not f.is_file():
            rep.problems.append(f"photo manquante : {rel}")
        elif rel in photos and deep and _sha256(f) != photos[rel]["sha256"]:
            rep.problems.append(f"photo altérée : {rel}")
    on_disk = {p.relative_to(root).as_posix() for p in (root / "images").rglob("*") if p.is_file()}
    for rel in sorted(on_disk - rows):
        rep.problems.append(f"fichier photo sans ligne en base : {rel}")
    return manifest


def _drill_app(root: Path, rep: Report, tmp: Path) -> None:
    """Open the restored data with the real app and read a few recipes and a photo."""
    from fastapi.testclient import TestClient

    from mamarmiton_server import db, security
    from mamarmiton_server.api import create_app

    data = tmp / "drill-data"
    shutil.copytree(root, data, ignore=shutil.ignore_patterns(MANIFEST))
    client = TestClient(create_app(Settings(data_dir=data)))
    health = client.get("/health")
    if health.status_code != 200:
        rep.problems.append(f"/health : {health.status_code}")
        return
    conn = db.connect(data / DB_NAME)
    try:
        owners = conn.execute("SELECT DISTINCT owner_id FROM recipes WHERE deleted = 0 ORDER BY owner_id").fetchall()
        tested = 0
        for owner in owners:
            token = "drill-" + security.new_invite_code()
            now = db.now()
            conn.execute("INSERT INTO sessions (token_hash, user_id, device, created_at, last_used) "
                         "VALUES (?, ?, 'drill', ?, ?)", (security.sha256(token), owner[0], now, now))
            headers = {"Authorization": f"Bearer {token}"}
            ids = [r[0] for r in conn.execute("SELECT id FROM recipes WHERE owner_id = ? AND deleted = 0 LIMIT 3",
                                              (owner[0],))]
            for rid in ids:
                r = client.get(f"/recipes/{rid}", headers=headers)
                if r.status_code != 200 or r.json().get("id") != rid or not r.json().get("recipe"):
                    rep.problems.append(f"GET /recipes/{rid} : {r.status_code} (contenu inattendu)")
                tested += 1
            img = conn.execute("SELECT i.id, i.recipe_id FROM images i JOIN recipes r ON r.id = i.recipe_id "
                               "WHERE r.owner_id = ? AND r.deleted = 0 LIMIT 1", (owner[0],)).fetchone()
            if img:
                r = client.get(f"/recipes/{img['recipe_id']}/images/{img['id']}", headers=headers)
                if r.status_code != 200 or not r.content:
                    rep.problems.append(f"GET photo {img['id']} : {r.status_code}")
            if tested >= 3:
                break
    finally:
        conn.close()
    rep.info.append(f"{tested} recettes lues via l'API")


def check_snapshot(snapshot: Path, deep: bool = True) -> Report:
    """The restore drill. ``deep`` also opens the restored data with the real app."""
    rep = Report()
    with tempfile.TemporaryDirectory(prefix="mm-drill-") as tmp:
        try:
            root = extract(snapshot, Path(tmp) / "snap")
        except (BackupError, tarfile.TarError, OSError, EOFError) as exc:
            rep.problems.append(f"archive illisible : {exc}")
            return rep
        manifest = _verify_dir(root, rep, deep)
        if manifest:
            rep.info.append(f"sauvegarde du {manifest.get('created_at')}, version {manifest.get('app_version')}")
        if deep and rep.ok:
            try:
                _drill_app(root, rep, Path(tmp))
            except Exception as exc:  # the drill must report, not crash
                rep.problems.append(f"l'application n'a pas pu ouvrir les données : {exc!r}")
    return rep


# --------------------------------------------------------------- restore

def restore(snapshot: Path, target: Path, force: bool = False) -> Path | None:
    """Put a snapshot into ``target``. Refuses a non-empty target without ``force``; with it,
    the current content is moved into ``target/replaced-<time>`` (never deleted).
    ``backups/`` and earlier ``replaced-*`` folders stay where they are. Returns the aside folder."""
    target = Path(target)
    existing = [p for p in target.iterdir()] if target.is_dir() else []
    if existing and not force:
        raise BackupError(f"{target} n'est pas vide ; arrêtez le serveur et relancez avec --force "
                          "(l'ancien contenu sera gardé à part)")
    target.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".mm-restore-", dir=target))
    aside = None
    try:
        root = extract(snapshot, staging / "snap")
        rep = Report()
        _verify_dir(root, rep, deep=True)
        if not rep.ok:
            raise BackupError("sauvegarde invalide, rien n'a été touché : " + "; ".join(rep.problems))
        keep = {"backups"}
        old = [p for p in existing if p.name not in keep and not p.name.startswith("replaced-")]
        if old:
            aside = target / f"replaced-{datetime.now(timezone.utc):%Y%m%d-%H%M%S}"
            aside.mkdir()
            for p in old:
                p.rename(aside / p.name)
        (root / MANIFEST).unlink()
        for item in root.iterdir():
            item.rename(target / item.name)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return aside


# ------------------------------------------------------------------- CLI

def cmd_backup(settings: Settings, dest_dir: Path, keep_daily: int, keep_weekly: int) -> int:
    try:
        archive, manifest, missing = create_snapshot(settings, dest_dir)
    except (BackupError, sqlite3.Error, OSError) as exc:
        print(f"ECHEC de la sauvegarde : {exc}")
        return 1
    c = manifest["counts"]
    print(f"Sauvegarde : {archive} ({archive.stat().st_size} octets) - {c['users']} comptes, "
          f"{c['recipes']} recettes, {c['images']} photos, sha256 base {manifest['db_sha256'][:12]}")
    code = 0
    for rel in missing:
        print(f"ATTENTION photo absente du disque : {rel}")
        code = 2
    try:
        for p in apply_retention(dest_dir, keep_daily, keep_weekly):
            print(f"Ancienne sauvegarde supprimée : {p.name}")
    except OSError as exc:
        print(f"ATTENTION rotation impossible : {exc}")
        code = code or 2
    return code


def cmd_restore_check(snapshot: Path) -> int:
    rep = check_snapshot(snapshot)
    print(f"Restore drill : {snapshot}")
    for line in rep.info:
        print(f"  ok   {line}")
    for line in rep.warnings:
        print(f"  warn {line}")
    for line in rep.problems:
        print(f"  FAIL {line}")
    print("RESULTAT : " + ("OK" if rep.ok else f"ECHEC ({len(rep.problems)} problème(s))"))
    return 0 if rep.ok else 1


def cmd_restore(snapshot: Path, target: Path, force: bool) -> int:
    try:
        aside = restore(snapshot, target, force)
    except (BackupError, tarfile.TarError, OSError) as exc:
        print(f"ECHEC : {exc}")
        return 1
    print(f"Restauré dans {target}." + (f" Ancien contenu gardé dans {aside}." if aside else ""))
    return 0


def _main(argv: list[str] | None = None) -> int:
    """``python -m mamarmiton_server.backup prune DIR``: retention only, needs nothing but Python
    (the cron script uses it on the NAS host to prune the USB disk copy)."""
    import argparse

    parser = argparse.ArgumentParser(prog="mamarmiton_server.backup")
    sub = parser.add_subparsers(dest="command", required=True)
    prune = sub.add_parser("prune", help="apply the retention rule to a folder of snapshots")
    prune.add_argument("directory", type=Path)
    prune.add_argument("--keep-daily", type=int, default=7)
    prune.add_argument("--keep-weekly", type=int, default=8)
    args = parser.parse_args(argv)
    for p in apply_retention(args.directory, args.keep_daily, args.keep_weekly):
        print(f"Supprimée : {p.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
