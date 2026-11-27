import json
import shutil
import sqlite3
import tarfile
import threading
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from mamarmiton_server import backup, db
from mamarmiton_server.__main__ import main
from mamarmiton_server.api import create_app
from mamarmiton_server.config import Settings

from test_api import RID, RID2, jpeg_bytes, put, register


@pytest.fixture
def settings(tmp_path):
    return Settings(data_dir=tmp_path / "data")


@pytest.fixture
def populated(settings):
    """A data dir with 2 users, 2 recipes and 2 photos, made through the real API."""
    client = TestClient(create_app(settings))
    maman = register(client, settings, "maman")
    eve = register(client, settings, "eve")
    for rid, name, headers in ((RID, "Gâteau", maman), (RID2, "Tarte", eve)):
        assert put(client, headers, rid=rid, name=name).status_code in (200, 201)
        r = client.post(f"/recipes/{rid}/images", files={"file": ("p.jpg", jpeg_bytes(), "image/jpeg")},
                        headers=headers)
        assert r.status_code == 201, r.text
    return settings


def snapshot(settings, tmp_path):
    archive, manifest, missing = backup.create_snapshot(settings, tmp_path / "bk")
    assert not missing
    return archive, manifest


def unpack(archive, tmp_path, name="x"):
    root = tmp_path / name
    with tarfile.open(archive) as tar:
        tar.extractall(root, filter="data")
    return root


def repack(root, out):
    with tarfile.open(out, "w:gz") as tar:
        for item in sorted(root.rglob("*")):
            tar.add(item, arcname=str(item.relative_to(root)), recursive=False)
    return out


def test_backup_while_another_connection_writes(populated, tmp_path):
    stop = threading.Event()
    errors = []

    def writer():
        conn = db.connect(populated.db_path)
        i = 0
        try:
            while not stop.is_set():
                with db.transaction(conn):
                    conn.execute("UPDATE counters SET value = value + 1 WHERE name = 'changes'")
                i += 1
        except Exception as exc:  # pragma: no cover
            errors.append(exc)
        finally:
            conn.close()

    t = threading.Thread(target=writer)
    t.start()
    try:
        archive, manifest = snapshot(populated, tmp_path)
    finally:
        stop.set()
        t.join()
    assert not errors
    assert manifest["counts"] == {"users": 3, "recipes": 2, "recipes_total": 2, "images": 2}  # admin + 2
    assert backup.check_snapshot(archive).ok


def test_restore_check_passes_on_good_snapshot(populated, tmp_path):
    archive, manifest = snapshot(populated, tmp_path)
    rep = backup.check_snapshot(archive)
    assert rep.ok, rep.problems
    assert any("recettes lues" in line for line in rep.info)
    assert manifest["app_version"] and len(manifest["db_sha256"]) == 64
    assert main(["restore-check", str(archive)]) == 0


def test_detects_missing_photo(populated, tmp_path):
    archive, _ = snapshot(populated, tmp_path)
    root = unpack(archive, tmp_path)
    next((root / "images").rglob("*.jpg")).unlink()
    bad = repack(root, tmp_path / "bad.tar.gz")
    rep = backup.check_snapshot(bad)
    assert not rep.ok and any("photo manquante" in p for p in rep.problems)
    assert main(["restore-check", str(bad)]) == 1


def test_detects_orphan_photo_file(populated, tmp_path):
    archive, _ = snapshot(populated, tmp_path)
    root = unpack(archive, tmp_path)
    (root / "images" / RID / "stray.jpg").write_bytes(b"x")
    assert any("sans ligne" in p for p in backup.check_snapshot(repack(root, tmp_path / "b.tar.gz")).problems)


def test_detects_tampered_db(populated, tmp_path):
    archive, _ = snapshot(populated, tmp_path)
    root = unpack(archive, tmp_path)
    with open(root / backup.DB_NAME, "r+b") as f:
        f.seek(5000)
        f.write(b"\xff" * 64)
    rep = backup.check_snapshot(repack(root, tmp_path / "bad.tar.gz"))
    assert not rep.ok and any("sha256" in p for p in rep.problems)


def test_detects_corrupt_db_even_with_matching_manifest(populated, tmp_path):
    archive, _ = snapshot(populated, tmp_path)
    root = unpack(archive, tmp_path)
    with open(root / backup.DB_NAME, "r+b") as f:
        f.seek(100)
        f.write(b"\xff" * 3000)
    manifest = json.loads((root / backup.MANIFEST).read_text())
    manifest["db_sha256"] = backup._sha256(root / backup.DB_NAME)
    (root / backup.MANIFEST).write_text(json.dumps(manifest))
    assert not backup.check_snapshot(repack(root, tmp_path / "bad.tar.gz")).ok


def test_detects_count_mismatch(populated, tmp_path):
    archive, _ = snapshot(populated, tmp_path)
    root = unpack(archive, tmp_path)
    manifest = json.loads((root / backup.MANIFEST).read_text())
    manifest["counts"]["recipes"] += 1
    (root / backup.MANIFEST).write_text(json.dumps(manifest))
    rep = backup.check_snapshot(repack(root, tmp_path / "bad.tar.gz"))
    assert not rep.ok and any(p.startswith("recipes") for p in rep.problems)


def test_backup_reports_photo_missing_on_disk(populated, tmp_path):
    next(populated.images_dir.rglob("*.jpg")).unlink()
    assert main_backup(populated, tmp_path) == 2
    archive = next((tmp_path / "bk").glob("*.tar.gz"))
    assert backup.check_snapshot(archive).warnings


def main_backup(settings, tmp_path, *extra):
    return backup.cmd_backup(settings, tmp_path / "bk", 7, 8)


def test_cli_backup_exit_codes(populated, tmp_path, monkeypatch):
    monkeypatch.setenv("MAMARMITON_SERVER_DATA", str(populated.data_dir))
    assert main(["backup", str(tmp_path / "bk")]) == 0
    assert len(list((tmp_path / "bk").glob("*.tar.gz"))) == 1
    monkeypatch.setenv("MAMARMITON_SERVER_DATA", str(tmp_path / "nothing"))
    assert backup.cmd_backup(Settings(data_dir=tmp_path / "nothing"), tmp_path / "bk", 7, 8) == 1


def test_restore_roundtrip_and_refuses_non_empty(populated, tmp_path):
    archive, manifest = snapshot(populated, tmp_path)
    target = tmp_path / "new"
    assert main(["restore", str(archive), "--into", str(target)]) == 0
    conn = sqlite3.connect(target / backup.DB_NAME)
    assert conn.execute("SELECT COUNT(*) FROM recipes").fetchone()[0] == 2
    conn.close()
    assert len(list((target / "images").rglob("*.jpg"))) == 2
    assert not (target / backup.MANIFEST).exists()
    # the target now has data: refused without --force, and nothing changes
    before = (target / backup.DB_NAME).read_bytes()
    assert main(["restore", str(archive), "--into", str(target)]) == 1
    assert (target / backup.DB_NAME).read_bytes() == before
    assert populated.db_path.is_file()


def test_restore_force_keeps_old_data_aside(populated, tmp_path):
    archive, _ = snapshot(populated, tmp_path)
    target = tmp_path / "live"
    target.mkdir()
    (target / backup.DB_NAME).write_text("old")
    (target / "backups").mkdir()
    (target / "backups" / "keep.txt").write_text("k")
    assert main(["restore", str(archive), "--into", str(target), "--force"]) == 0
    aside = next(target.glob("replaced-*"))
    assert (aside / backup.DB_NAME).read_text() == "old"
    assert (target / "backups" / "keep.txt").exists()
    assert sqlite3.connect(target / backup.DB_NAME).execute("SELECT COUNT(*) FROM users").fetchone()[0] == 3


def test_restore_refuses_bad_snapshot_and_touches_nothing(populated, tmp_path):
    archive, _ = snapshot(populated, tmp_path)
    root = unpack(archive, tmp_path)
    (root / backup.DB_NAME).write_bytes(b"garbage")
    target = tmp_path / "live"
    target.mkdir()
    (target / "keep").write_text("1")
    assert main(["restore", str(repack(root, tmp_path / "b.tar.gz")), "--into", str(target), "--force"]) == 1
    assert (target / "keep").read_text() == "1"


def test_extract_refuses_path_traversal(tmp_path):
    evil = tmp_path / "evil.tar.gz"
    src = tmp_path / "f"
    src.write_text("x")
    with tarfile.open(evil, "w:gz") as tar:
        tar.add(src, arcname="../../escape.txt")
    assert not backup.check_snapshot(evil).ok
    assert not (tmp_path.parent / "escape.txt").exists()


def test_retention_keeps_right_ones(tmp_path):
    d = tmp_path / "bk"
    d.mkdir()
    now = datetime(2026, 11, 27, 3, 0, tzinfo=timezone.utc)  # a Friday
    names = []
    for days in range(0, 40):
        for hour in (3, 15) if days < 2 else (3,):
            t = (now - timedelta(days=days)).replace(hour=hour)
            n = f"mamarmiton-{t:%Y%m%d-%H%M%S}.tar.gz"
            (d / n).write_text("x")
            names.append(n)
    (d / "notes.txt").write_text("not ours")
    (d / "mamarmiton-bad.tar.gz").write_text("not ours either")
    deleted = backup.apply_retention(d, keep_daily=7, keep_weekly=4)
    left = {p.name for p in d.iterdir()}
    assert {"notes.txt", "mamarmiton-bad.tar.gz"} <= left
    snaps = sorted(n for n in left if backup.snapshot_time(n))
    days_kept = {backup.snapshot_time(n).date() for n in snaps}
    # the last 7 days are all there, once each (the 15:00 one is the day's newest)
    for k in range(7):
        assert (now - timedelta(days=k)).date() in days_kept
    # older than 7 days: only weekly ones (newest of each of the 4 most recent ISO weeks)
    old = [backup.snapshot_time(n) for n in snaps if backup.snapshot_time(n).date() < (now - timedelta(days=6)).date()]
    assert len(old) <= 4
    assert len(snaps) <= 7 + 4
    assert len(deleted) + len(snaps) == len(names)
    # the newest of the day is kept, not the earlier one
    assert not (d / "mamarmiton-20261127-030000.tar.gz").exists()
    assert (d / "mamarmiton-20261127-150000.tar.gz").exists()


def test_restore_check_works_without_httpx(populated, tmp_path):
    """The production image has no httpx (TestClient needs it): the drill must not depend on it."""
    import subprocess
    import sys

    archive, _ = snapshot(populated, tmp_path)
    code = ("import sys; sys.modules['httpx'] = None; sys.modules['httpx2'] = None\n"
            "from mamarmiton_server.__main__ import main\n"
            f"sys.exit(main(['restore-check', {str(archive)!r}]))")
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "recettes lues" in result.stdout
