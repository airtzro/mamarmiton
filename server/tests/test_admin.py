"""The /admin dashboard and its /admin/api routes."""

import json
import os
import re
import shutil
import sqlite3
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from mamarmiton_server import admin, db, security
from mamarmiton_server.api import WEB_DIR, create_app
from mamarmiton_server.config import Settings
from test_api import PASSWORD, RID, RID2, jpeg_bytes, put, register

ADMIN_DIR = WEB_DIR / "admin"


@pytest.fixture
def settings(tmp_path):
    return Settings(data_dir=tmp_path, login_attempts=3, login_window_s=60)


@pytest.fixture
def client(settings):
    return TestClient(create_app(settings))


@pytest.fixture
def admin_headers(client, settings):
    register(client, settings, "bootstrap")  # creates the 'admin' row through make_invite
    r = client.post("/auth/login", json={"username": "admin", "password": PASSWORD, "device": "Tableau de bord admin"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


@pytest.fixture
def maman(client, settings):
    return register(client, settings, "maman")


def rows(settings, sql, *params):
    conn = sqlite3.connect(settings.db_path)
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


# ------------------------------------------------------------------- access
ADMIN_ROUTES = [
    ("GET", "/admin/api/overview"), ("GET", "/admin/api/users"), ("GET", "/admin/api/invites"),
    ("GET", "/admin/api/reports"), ("GET", "/admin/api/usage"), ("GET", "/admin/api/security"),
    ("GET", "/admin/api/backups"), ("POST", "/admin/api/backups"), ("POST", "/admin/api/backups/drill"),
    ("GET", "/admin/api/users/maman/sessions"), ("POST", "/admin/api/users/maman/signout"),
    ("POST", "/admin/api/invites/cleanup"), ("POST", "/admin/api/reports/read-all"),
]


@pytest.mark.parametrize("method,path", ADMIN_ROUTES)
def test_admin_routes_refuse_anonymous_and_members(client, maman, method, path):
    assert client.request(method, path).status_code == 401
    assert client.request(method, path, headers=maman).status_code == 403


def test_admin_page_is_served_with_strict_csp_and_noindex(client):
    r = client.get("/admin")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    csp = r.headers["content-security-policy"]
    assert "script-src 'self'" in csp and "unsafe-inline" not in csp
    assert "noindex" in r.headers["x-robots-tag"]
    assert "<style" not in r.text and " style=" not in r.text
    assert '<script type="module" src="/app/admin/main.js">' in r.text


def test_admin_modules_and_their_imports_are_served(client):
    for js in ADMIN_DIR.glob("*.js"):
        assert client.get(f"/app/admin/{js.name}").status_code == 200
        for target in re.findall(r'from "(\.\.?/[\w./-]+)"', js.read_text(encoding="utf-8")):
            path = (ADMIN_DIR / target).resolve()
            assert path.is_file(), f"{js.name} imports missing {target}"
            assert client.get("/app/" + str(path.relative_to(WEB_DIR.resolve()))).status_code == 200
    assert client.get("/app/admin/admin.css").headers["content-type"].startswith("text/css")


def test_admin_code_never_parses_html():
    for js in ADMIN_DIR.glob("*.js"):
        code = js.read_text(encoding="utf-8")
        for danger in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval("):
            assert danger not in code, f"{js.name} uses {danger}"


def test_admin_files_are_packaged():
    text = (Path(__file__).parents[1] / "pyproject.toml").read_text()
    assert '"web/admin/*"' in text


# ----------------------------------------------------------------- overview
def test_overview_counts_and_alerts(client, admin_headers, maman, settings):
    put(client, maman, RID)
    client.post(f"/recipes/{RID}/images", files={"file": ("a.jpg", jpeg_bytes(), "image/jpeg")}, headers=maman)
    client.post("/reports", json={"message": "ça plante"}, headers=maman)
    data = client.get("/admin/api/overview", headers=admin_headers).json()
    c = data["counts"]
    assert c["users"] == 3 and c["admins"] == 1 and c["recipes"] == 1 and c["photos"] == 1
    assert c["photo_bytes"] > 0 and c["unread_reports"] == 1 and c["active_users_7d"] >= 2
    assert data["server"]["schema"] == data["server"]["schema_latest"] == db.latest_version()
    views = {a["view"] for a in data["alerts"]}
    assert {"reports", "backups"} <= views  # unread report, no backup yet
    assert data["storage"]["db_bytes"] > 0
    # nothing in the overview reveals a recipe's content
    assert "Gâteau" not in json.dumps(data)


# ------------------------------------------------------------------- users
def test_users_list_has_counts_but_no_recipe_content(client, admin_headers, maman):
    put(client, maman, RID)
    put(client, maman, RID2, name="Tarte")
    client.put("/shares/book/bootstrap", json={"can_edit": False}, headers=maman)
    users = {u["username"]: u for u in client.get("/admin/api/users", headers=admin_headers).json()}
    assert users["maman"]["recipes"] == 2 and users["maman"]["devices"] == 1
    assert users["maman"]["shares_given"] == 1 and users["bootstrap"]["shares_received"] == 1
    assert users["admin"]["is_admin"] and users["admin"]["is_me"] and not users["maman"]["is_me"]
    assert "password_hash" not in users["maman"] and "id" not in users["maman"]
    text = json.dumps(users)
    assert "Tarte" not in text and "farine" not in text


def test_rename(client, admin_headers, maman):
    r = client.patch("/admin/api/users/maman", json={"display_name": "  Maman chérie "}, headers=admin_headers)
    assert r.json()["display_name"] == "Maman chérie"
    assert client.patch("/admin/api/users/maman", json={"display_name": " "}, headers=admin_headers).status_code == 422
    assert client.patch("/admin/api/users/nobody", json={"display_name": "x"}, headers=admin_headers).status_code == 404


def test_grant_and_revoke_admin_needs_password_and_keeps_one_admin(client, admin_headers, maman):
    r = client.patch("/admin/api/users/maman", json={"is_admin": True, "admin_password": "wrong"}, headers=admin_headers)
    assert r.status_code == 403
    r = client.patch("/admin/api/users/maman", json={"is_admin": True, "admin_password": PASSWORD}, headers=admin_headers)
    assert r.json()["is_admin"] is True
    assert client.get("/admin/api/users", headers=maman).status_code == 200
    r = client.patch("/admin/api/users/maman", json={"is_admin": False, "admin_password": PASSWORD}, headers=admin_headers)
    assert r.json()["is_admin"] is False
    r = client.patch("/admin/api/users/admin", json={"is_admin": False, "admin_password": PASSWORD}, headers=admin_headers)
    assert r.status_code == 409  # last admin


def test_sessions_list_and_revoke(client, admin_headers, maman):
    client.post("/auth/login", json={"username": "maman", "password": PASSWORD, "device": "PC cuisine"})
    sessions = client.get("/admin/api/users/maman/sessions", headers=admin_headers).json()
    assert len(sessions) == 2 and {s["device"] for s in sessions} >= {"PC cuisine"}
    assert all(len(s["id"]) == 16 and not s["current"] for s in sessions)
    target = next(s for s in sessions if s["device"] == "PC cuisine")
    assert client.delete(f"/admin/api/users/maman/sessions/{target['id']}", headers=admin_headers).status_code == 204
    assert len(client.get("/admin/api/users/maman/sessions", headers=admin_headers).json()) == 1
    assert client.get("/me", headers=maman).status_code == 200  # the other device is untouched
    assert client.delete(f"/admin/api/users/maman/sessions/{target['id']}", headers=admin_headers).status_code == 404
    assert client.delete("/admin/api/users/maman/sessions/' OR 1=1 --", headers=admin_headers).status_code == 404
    mine = client.get("/admin/api/users/admin/sessions", headers=admin_headers).json()
    assert [s["current"] for s in mine] == [True]


def test_sign_out_everywhere_keeps_the_admins_own_session(client, admin_headers, maman):
    assert client.post("/admin/api/users/maman/signout", headers=admin_headers).json() == {"signed_out": 1}
    assert client.get("/me", headers=maman).status_code == 401
    client.post("/admin/api/users/admin/signout", headers=admin_headers)
    assert client.get("/me", headers=admin_headers).status_code == 200


def test_reset_password(client, admin_headers, maman):
    assert client.post("/admin/api/users/maman/password", json={"admin_password": "nope"},
                       headers=admin_headers).status_code == 403
    r = client.post("/admin/api/users/maman/password", json={"admin_password": PASSWORD}, headers=admin_headers)
    temp = r.json()["password"]
    assert re.fullmatch(r"[a-z2-9]{4}-[a-z2-9]{4}-[a-z2-9]{4}", temp)
    assert security.password_problem(temp, "maman") is None
    assert client.get("/me", headers=maman).status_code == 401  # devices signed out
    assert client.post("/auth/login", json={"username": "maman", "password": PASSWORD}).status_code == 401
    assert client.post("/auth/login", json={"username": "maman", "password": temp}).status_code == 200
    assert client.post("/admin/api/users/admin/password", json={"admin_password": PASSWORD},
                       headers=admin_headers).status_code == 409


def test_password_confirmation_is_rate_limited(client, admin_headers, maman):
    codes = [client.post("/admin/api/users/maman/password", json={"admin_password": "bad"},
                         headers=admin_headers).status_code for _ in range(4)]
    assert codes == [403, 403, 403, 429]
    # and it shows up as a lockout the admin can lift
    blocked = client.get("/admin/api/security", headers=admin_headers).json()["blocked"]
    assert any(b["username"] == "pw:admin" for b in blocked)
    b = next(b for b in blocked if b["username"] == "pw:admin")
    client.post("/admin/api/security/unblock", json={"ip": b["ip"], "username": b["username"]}, headers=admin_headers)
    assert client.post("/admin/api/users/maman/password", json={"admin_password": PASSWORD},
                       headers=admin_headers).status_code == 200


def test_reset_password_lifts_the_users_lockout(client, admin_headers, maman):
    for _ in range(3):
        client.post("/auth/login", json={"username": "maman", "password": "wrong password"})
    assert client.post("/auth/login", json={"username": "maman", "password": PASSWORD}).status_code == 429
    temp = client.post("/admin/api/users/maman/password", json={"admin_password": PASSWORD},
                       headers=admin_headers).json()["password"]
    assert client.post("/auth/login", json={"username": "maman", "password": temp}).status_code == 200


def test_delete_user_removes_recipes_photos_and_shares(client, admin_headers, maman, settings):
    put(client, maman, RID)
    client.post(f"/recipes/{RID}/images", files={"file": ("a.jpg", jpeg_bytes(), "image/jpeg")}, headers=maman)
    client.put("/shares/book/bootstrap", json={"can_edit": False}, headers=maman)
    assert (settings.images_dir / RID).is_dir()
    url = "/admin/api/users/maman/delete"
    assert client.post(url, json={"admin_password": PASSWORD, "confirm_username": "mama"},
                       headers=admin_headers).status_code == 422
    assert client.post(url, json={"admin_password": "nope", "confirm_username": "maman"},
                       headers=admin_headers).status_code == 403
    r = client.post(url, json={"admin_password": PASSWORD, "confirm_username": "Maman"}, headers=admin_headers)
    assert r.status_code == 200 and r.json() == {"username": "maman", "recipes": 1, "photos": 1}
    assert not (settings.images_dir / RID).exists()
    assert rows(settings, "SELECT COUNT(*) FROM recipes")[0][0] == 0
    assert rows(settings, "SELECT COUNT(*) FROM images")[0][0] == 0
    assert rows(settings, "SELECT COUNT(*) FROM book_shares")[0][0] == 0
    assert client.get("/me", headers=maman).status_code == 401
    assert client.post("/admin/api/users/admin/delete", json={"admin_password": PASSWORD, "confirm_username": "admin"},
                       headers=admin_headers).status_code == 409


def test_deleted_users_recipes_leave_the_people_they_were_shared_with(client, admin_headers, maman, settings):
    other = register(client, settings, "fille")
    put(client, maman, RID)
    client.put("/shares/book/fille", json={"can_edit": False}, headers=maman)
    first = client.get("/recipes", headers=other).json()
    assert RID in json.dumps(first)
    client.post("/admin/api/users/maman/delete", json={"admin_password": PASSWORD, "confirm_username": "maman"},
                headers=admin_headers)
    later = client.get(f"/recipes?since={first['seq']}", headers=other).json()
    assert RID not in json.dumps(later)


# ------------------------------------------------------------------ invites
def test_invites_list_revoke_and_cleanup(client, admin_headers, maman, settings):
    code = client.post("/invites", json={"note": "tante Marie"}, headers=admin_headers).json()["code"]
    conn = db.connect(settings.db_path)
    conn.execute("INSERT INTO invites (code_hash, created_by, note, created_at, expires_at) "
                 "VALUES ('deadbeef' || hex(randomblob(28)), 1, 'old', '2020-01-01', '2020-01-08')")
    conn.close()
    invites = client.get("/admin/api/invites", headers=admin_headers).json()
    by_note = {i["note"]: i for i in invites}
    assert by_note["tante Marie"]["status"] == "open"
    assert by_note["old"]["status"] == "expired"
    assert any(i["status"] == "used" and i["used_by"] == "maman" for i in invites)
    assert code not in json.dumps(invites) and security.sha256(code) not in json.dumps(invites)
    used = next(i for i in invites if i["status"] == "used")
    assert client.delete(f"/admin/api/invites/{used['id']}", headers=admin_headers).status_code == 409
    assert client.delete(f"/admin/api/invites/{by_note['tante Marie']['id']}", headers=admin_headers).status_code == 204
    r = client.post("/auth/register", json={"invite": code, "username": "marie", "password": PASSWORD})
    assert r.status_code == 403  # the revoked code no longer works
    assert client.post("/admin/api/invites/cleanup", headers=admin_headers).json() == {"deleted": 1}
    assert client.delete("/admin/api/invites/zz", headers=admin_headers).status_code == 404


def test_invite_with_a_member_account_is_refused(client, maman):
    assert client.post("/invites", json={"note": "x"}, headers=maman).status_code == 403


# ------------------------------------------------------------------ reports
def test_reports_read_unread_delete(client, admin_headers, maman):
    a = client.post("/reports", json={"message": "premier", "log_tail": "ligne 1"}, headers=maman).json()["id"]
    b = client.post("/reports", json={"message": "second"}, headers=maman).json()["id"]
    unread = client.get("/admin/api/reports?status=unread", headers=admin_headers).json()
    assert [r["id"] for r in unread] == [b, a] and unread[1]["username"] == "maman" and unread[1]["log_tail"] == "ligne 1"
    assert client.post(f"/admin/api/reports/{a}/read", json={"read": True}, headers=admin_headers).status_code == 204
    assert [r["id"] for r in client.get("/admin/api/reports?status=unread", headers=admin_headers).json()] == [b]
    client.post(f"/admin/api/reports/{a}/read", json={"read": False}, headers=admin_headers)
    assert client.post("/admin/api/reports/read-all", headers=admin_headers).json() == {"marked": 2}
    assert client.delete(f"/admin/api/reports/{a}", headers=admin_headers).status_code == 204
    assert [r["id"] for r in client.get("/admin/api/reports", headers=admin_headers).json()] == [b]
    assert client.post("/admin/api/reports/999/read", json={"read": True}, headers=admin_headers).status_code == 404
    assert client.get("/admin/api/reports?status=evil", headers=admin_headers).status_code == 422


# -------------------------------------------------------------------- usage
def test_usage_summary(client, admin_headers, maman):
    from datetime import date
    client.post("/usage", json={"client": "web/1", "days": {date.today().isoformat(): {"search": 2, "active": 1}}},
                headers=maman)
    data = client.get("/admin/api/usage?days=28", headers=admin_headers).json()
    assert len(data["weeks"]) == 4
    search = next(e for e in data["events"] if e["event"] == "search")
    assert search["counts"][-1] == 2 and search["total"] == 2 and search["days"] == 1
    assert data["events"][-1]["event"] == "search"  # least used first
    assert data["active"]["web"][-1] == 1
    assert client.get("/admin/api/usage?days=3", headers=admin_headers).status_code == 422


# ------------------------------------------------------------------ backups
def test_backup_now_drill_and_status(client, admin_headers, maman, settings):
    put(client, maman, RID)
    status = client.get("/admin/api/backups", headers=admin_headers).json()
    assert status["snapshots"] == [] and status["stale"] and status["drill_due"] and status["nightly"] is None
    r = client.post("/admin/api/backups", headers=admin_headers)
    assert r.status_code == 201 and r.json()["counts"]["recipes"] == 1
    assert client.post("/admin/api/backups", headers=admin_headers).status_code == 429  # one per minute
    status = client.get("/admin/api/backups", headers=admin_headers).json()
    assert len(status["snapshots"]) == 1 and not status["stale"]
    drill = client.post("/admin/api/backups/drill", headers=admin_headers).json()
    assert drill["ok"] is True and drill["snapshot"] == r.json()["name"]
    status = client.get("/admin/api/backups", headers=admin_headers).json()
    assert status["drill"]["ok"] and not status["drill_due"]
    assert client.post("/admin/api/backups/drill?name=../../etc/passwd", headers=admin_headers).status_code == 404
    overview = client.get("/admin/api/overview", headers=admin_headers).json()
    assert "backups" not in {a["view"] for a in overview["alerts"]}


def test_nightly_status_file_is_reported(client, admin_headers, settings):
    folder = settings.data_dir / "backups"
    folder.mkdir()
    (folder / "last-run.json").write_text(json.dumps({"finished_at": db.now(), "exit": 1, "message": "USB absent",
                                                      "snapshot": "", "usb": False, "offsite": "off"}))
    assert client.get("/admin/api/backups", headers=admin_headers).json()["nightly"]["message"] == "USB absent"
    alerts = client.get("/admin/api/overview", headers=admin_headers).json()["alerts"]
    assert any("USB absent" in a["text"] for a in alerts)
    (folder / "last-run.json").write_text("not json")
    assert client.get("/admin/api/backups", headers=admin_headers).json()["nightly"] is None


@pytest.mark.skipif(shutil.which("bash") is None or shutil.which("sha256sum") is None, reason="needs bash")
def test_backup_cron_script_writes_the_status_file(tmp_path):
    """Run ops/backup-cron.sh with a fake `docker` that drops a snapshot, and read the status it writes."""
    data, usb, bin_dir = tmp_path / "data", tmp_path / "usb", tmp_path / "bin"
    for d in (data / "backups", usb, bin_dir):
        d.mkdir(parents=True)
    (bin_dir / "docker").write_text(
        "#!/bin/sh\n"
        f"printf 'x' > '{data}/backups/mamarmiton-20261127-031500.tar.gz'\n"
        "echo 'Sauvegarde : ok'\n")
    (bin_dir / "docker").chmod(0o755)
    server = Path(__file__).parents[1]
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "DATA_DIR": str(data), "APP_DIR": str(server),
           "LOG_FILE": str(tmp_path / "backup.log"), "MAMARMITON_OPS_ENV": str(tmp_path / "none.env"),
           "DOCKER_CONFIG": str(tmp_path / "docker-config")}
    r = subprocess.run(["bash", str(server / "ops" / "backup-cron.sh"), str(usb)], env=env,
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr
    status = json.loads((data / "backups" / "last-run.json").read_text())
    assert status["exit"] == 0 and status["usb"] is True and status["offsite"] == "off"
    assert status["snapshot"] == "mamarmiton-20261127-031500.tar.gz"

    # a missing USB folder is reported too
    r = subprocess.run(["bash", str(server / "ops" / "backup-cron.sh"), str(tmp_path / "unplugged")], env=env,
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 1
    status = json.loads((data / "backups" / "last-run.json").read_text())
    assert status["exit"] == 1 and "does not exist" in status["message"]


def test_temporary_passwords_are_unique_and_readable():
    seen = {admin.new_temporary_password() for _ in range(200)}
    assert len(seen) == 200
    assert all(not set(p) & set("01ilo") for p in seen)
