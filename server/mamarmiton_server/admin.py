"""Administration API behind the /admin dashboard (admin accounts only).

What an admin can see: accounts, devices, invites, problem reports, anonymous usage counters,
lockouts, backups and the server's health. What an admin can NOT see: the content of anyone's
recipes or photos (only how many there are).

Destructive or sensitive actions (deleting an account, resetting a password, granting admin)
ask for the admin's own password again, with the same rate limit as a password change.
"""

# No "from __future__ import annotations": FastAPI must see the real Conn/Admin types passed to register().

import json
import platform
import secrets
import shutil
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Annotated, Any, Callable, Literal

from fastapi import FastAPI, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field

from mamarmiton_server import __version__, db, security, usage
from mamarmiton_server.config import Settings

PREFIX = "/admin/api"
BACKUPS = "backups"                 # folder inside the data dir, written by ops/backup-cron.sh
LAST_RUN = "last-run.json"          # status of the last nightly job, written by ops/backup-cron.sh
LAST_DRILL = "last-drill.json"      # result of the last restore drill run from the dashboard
BACKUP_STALE_HOURS = 30             # the nightly job runs every 24 h
DRILL_STALE_DAYS = 92               # the product plan asks for one drill per quarter
LOW_DISK_RATIO = 0.10
MIN_SECONDS_BETWEEN_SNAPSHOTS = 60


class PasswordConfirm(BaseModel):
    admin_password: str = Field(max_length=security.MAX_PASSWORD)


class DeleteAccount(PasswordConfirm):
    confirm_username: str = Field(max_length=64)


class AccountChange(BaseModel):
    display_name: str | None = Field(default=None, max_length=60)
    is_admin: bool | None = None
    admin_password: str = Field(default="", max_length=security.MAX_PASSWORD)


class ReportCategory(BaseModel):
    category: Literal["bug", "idea", "other"]
    category_other: str = Field(default="", max_length=80)


class ReadFlag(BaseModel):
    read: bool = True


class Unblock(BaseModel):
    ip: str = Field(max_length=100)
    username: str | None = Field(default=None, max_length=80)


def _error(code: int, message: str) -> HTTPException:
    return HTTPException(status_code=code, detail=message)


def _parse(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        when = datetime.fromisoformat(ts)
    except ValueError:
        return None
    return when if when.tzinfo else when.replace(tzinfo=timezone.utc)


def _file_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _read_json(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def new_temporary_password() -> str:
    """Easy to read on the phone and type once: 3 groups of 4, no look-alikes (~80 bits)."""
    alphabet = "abcdefghjkmnpqrstuvwxyz23456789"
    raw = "".join(secrets.choice(alphabet) for _ in range(12))
    return "-".join(raw[i:i + 4] for i in range(0, 12, 4))


def session_id(token_hash: str) -> str:
    """A short handle for a session row. It is a prefix of sha256(token): useless to log in with."""
    return token_hash[:16]


def invite_id(code_hash: str) -> str:
    return code_hash[:16]


def backup_status(settings: Settings, now: datetime | None = None) -> dict:
    """Snapshots in DATA/backups, the nightly job's last report and the last restore drill."""
    now = now or datetime.now(timezone.utc)
    from mamarmiton_server import backup as snap

    folder = settings.data_dir / BACKUPS
    snapshots = []
    if folder.is_dir():
        for p in folder.iterdir():
            when = snap.snapshot_time(p.name)
            if when is not None and p.is_file():
                snapshots.append({"name": p.name, "created_at": when.isoformat(timespec="seconds"),
                                  "size": _file_size(p)})
    snapshots.sort(key=lambda s: s["created_at"], reverse=True)
    latest = _parse(snapshots[0]["created_at"]) if snapshots else None
    last_run = _read_json(folder / LAST_RUN)
    drill = _read_json(folder / LAST_DRILL)
    drill_at = _parse(drill.get("finished_at")) if drill else None
    return {
        "snapshots": snapshots,
        "latest_age_hours": round((now - latest).total_seconds() / 3600, 1) if latest else None,
        "stale": latest is None or now - latest > timedelta(hours=BACKUP_STALE_HOURS),
        "nightly": last_run,  # None until ops/backup-cron.sh writes it
        "drill": drill,
        "drill_due": drill_at is None or now - drill_at > timedelta(days=DRILL_STALE_DAYS),
    }


def register(app: FastAPI, settings: Settings, limiter: security.LoginLimiter,
             Conn: Any, Admin: Any, client_ip: Callable[[Request], str]) -> None:
    """Add the /admin page and the /admin/api routes to ``app``."""
    started = time.time()

    def confirm_password(request: Request, admin: sqlite3.Row, password: str) -> None:
        key = (client_ip(request), "pw:" + admin["username"])
        wait = limiter.retry_after(*key)
        if wait:
            raise HTTPException(429, "Trop d'essais. Réessayez plus tard.", headers={"Retry-After": str(wait)})
        if not security.verify_password(admin["password_hash"], password):
            limiter.failed(*key)
            raise _error(403, "Votre mot de passe (administrateur) est incorrect.")

    def account(conn: sqlite3.Connection, username: str) -> sqlite3.Row:
        row = conn.execute("SELECT * FROM users WHERE username = ?",
                           (security.normalize_username(username),)).fetchone()
        if row is None:
            raise _error(404, "Compte introuvable.")
        return row

    def admin_count(conn: sqlite3.Connection) -> int:
        return conn.execute("SELECT COUNT(*) FROM users WHERE is_admin = 1").fetchone()[0]

    # ---------------------------------------------------------- overview
    @app.get(f"{PREFIX}/overview")
    def overview(admin: Admin, conn: Conn) -> dict:
        now = datetime.now(timezone.utc)
        week_ago = (now - timedelta(days=7)).isoformat(timespec="microseconds")
        one = lambda sql, *p: conn.execute(sql, p).fetchone()[0]  # noqa: E731
        counts = {
            "users": one("SELECT COUNT(*) FROM users"),
            "admins": one("SELECT COUNT(*) FROM users WHERE is_admin = 1"),
            "recipes": one("SELECT COUNT(*) FROM recipes WHERE deleted = 0"),
            "deleted_recipes": one("SELECT COUNT(*) FROM recipes WHERE deleted = 1"),
            "photos": one("SELECT COUNT(*) FROM images"),
            "photo_bytes": one("SELECT COALESCE(SUM(size), 0) FROM images"),
            "book_shares": one("SELECT COUNT(*) FROM book_shares"),
            "recipe_shares": one("SELECT COUNT(*) FROM recipe_shares"),
            "sessions": one("SELECT COUNT(*) FROM sessions"),
            "active_users_7d": one("SELECT COUNT(DISTINCT user_id) FROM sessions WHERE last_used > ?", week_ago),
            "recipes_changed_7d": one("SELECT COUNT(*) FROM recipes WHERE updated_at > ?", week_ago),
            "unread_reports": one("SELECT COUNT(*) FROM reports WHERE read_at IS NULL"),
            "open_invites": one("SELECT COUNT(*) FROM invites WHERE used_by IS NULL AND expires_at > ?", db.now()),
        }
        try:
            disk = shutil.disk_usage(settings.data_dir)
            disk_info = {"total": disk.total, "free": disk.free, "low": disk.free < disk.total * LOW_DISK_RATIO}
        except OSError:
            disk_info = None
        db_bytes = sum(_file_size(settings.db_path.with_name(settings.db_path.name + s)) for s in ("", "-wal", "-shm"))
        backups = backup_status(settings, now)
        blocked = limiter.blocked()

        alerts: list[dict] = []
        if counts["unread_reports"]:
            n = counts["unread_reports"]
            alerts.append({"level": "warn", "view": "reports",
                           "text": f"{n} message{'s' if n > 1 else ''} « Signaler un problème » non lu{'s' if n > 1 else ''}."})
        if backups["stale"]:
            age = backups["latest_age_hours"]
            alerts.append({"level": "bad", "view": "backups",
                           "text": "Aucune sauvegarde trouvée." if age is None
                           else f"Dernière sauvegarde il y a {age:.0f} h (la tâche de nuit ne tourne plus ?)."})
        nightly = backups["nightly"]
        if nightly and nightly.get("exit") not in (0, None):
            alerts.append({"level": "bad", "view": "backups",
                           "text": "La dernière sauvegarde de nuit a signalé un problème : "
                                   + str(nightly.get("message") or f"code {nightly.get('exit')}")})
        if backups["drill_due"]:
            alerts.append({"level": "warn", "view": "backups",
                           "text": "Aucun test de restauration depuis plus de 3 mois."})
        if disk_info and disk_info["low"]:
            alerts.append({"level": "bad", "view": "overview", "text": "Le disque des données est presque plein."})
        if blocked:
            alerts.append({"level": "warn", "view": "security",
                           "text": f"{len(blocked)} blocage{'s' if len(blocked) > 1 else ''} après des mots de passe faux."})

        return {
            "server": {
                "version": __version__,
                "schema": db.schema_version(conn),
                "schema_latest": db.latest_version(),
                "python": platform.python_version(),
                "started_at": datetime.fromtimestamp(started, timezone.utc).isoformat(timespec="seconds"),
                "uptime_s": int(time.time() - started),
                "min_desktop_version": settings.min_desktop_version or None,
                "invite_days": settings.invite_days,
                "token_idle_days": settings.token_idle_days,
                "max_image_mb": settings.max_image_mb,
            },
            "counts": counts,
            "storage": {"db_bytes": db_bytes, "photo_bytes": counts["photo_bytes"], "disk": disk_info},
            "backups": {k: backups[k] for k in ("latest_age_hours", "stale", "drill_due")}
                       | {"latest": backups["snapshots"][0] if backups["snapshots"] else None},
            "blocked": len(blocked),
            "alerts": alerts,
        }

    # ---------------------------------------------------------- accounts
    @app.get(f"{PREFIX}/users")
    def users(admin: Admin, conn: Conn) -> list[dict]:
        rows = conn.execute("""
            SELECT u.id, u.username, u.display_name, u.is_admin, u.created_at,
              (SELECT COUNT(*) FROM recipes r WHERE r.owner_id = u.id AND r.deleted = 0) AS recipes,
              (SELECT COUNT(*) FROM images i JOIN recipes r ON r.id = i.recipe_id WHERE r.owner_id = u.id) AS photos,
              (SELECT COALESCE(SUM(i.size), 0) FROM images i JOIN recipes r ON r.id = i.recipe_id
                 WHERE r.owner_id = u.id) AS photo_bytes,
              (SELECT COUNT(*) FROM sessions s WHERE s.user_id = u.id) AS devices,
              (SELECT MAX(s.last_used) FROM sessions s WHERE s.user_id = u.id) AS last_seen,
              (SELECT MAX(r.updated_at) FROM recipes r WHERE r.owner_id = u.id) AS last_change,
              (SELECT COUNT(*) FROM book_shares b WHERE b.owner_id = u.id)
                + (SELECT COUNT(*) FROM recipe_shares s JOIN recipes r ON r.id = s.recipe_id
                     WHERE r.owner_id = u.id) AS shares_given,
              (SELECT COUNT(*) FROM book_shares b WHERE b.grantee_id = u.id)
                + (SELECT COUNT(*) FROM recipe_shares s WHERE s.grantee_id = u.id) AS shares_received,
              (SELECT i.note FROM invites i WHERE i.used_by = u.id LIMIT 1) AS invite_note
            FROM users u ORDER BY u.is_admin DESC, u.display_name COLLATE NOCASE""").fetchall()
        return [{**{k: r[k] for k in r.keys() if k != "id"}, "is_admin": bool(r["is_admin"]),
                 "is_me": r["id"] == admin["id"]} for r in rows]

    @app.patch(f"{PREFIX}/users/{{username}}")
    def change_user(username: str, body: AccountChange, request: Request, admin: Admin, conn: Conn) -> dict:
        target = account(conn, username)
        if body.display_name is not None:
            name = body.display_name.strip()
            if not name:
                raise _error(422, "Le prénom ne peut pas être vide.")
            conn.execute("UPDATE users SET display_name = ? WHERE id = ?", (name, target["id"]))
        if body.is_admin is not None and body.is_admin != bool(target["is_admin"]):
            confirm_password(request, admin, body.admin_password)
            with db.transaction(conn):
                if not body.is_admin and admin_count(conn) <= 1:
                    raise _error(409, "Il faut garder au moins un administrateur.")
                conn.execute("UPDATE users SET is_admin = ? WHERE id = ?", (1 if body.is_admin else 0, target["id"]))
        row = account(conn, username)
        return {"username": row["username"], "display_name": row["display_name"], "is_admin": bool(row["is_admin"])}

    @app.get(f"{PREFIX}/users/{{username}}/sessions")
    def sessions(username: str, request: Request, admin: Admin, conn: Conn) -> list[dict]:
        target = account(conn, username)
        mine = security.sha256(request.headers.get("authorization", "").partition(" ")[2])
        rows = conn.execute("SELECT token_hash, device, created_at, last_used FROM sessions WHERE user_id = ? "
                            "ORDER BY last_used DESC", (target["id"],)).fetchall()
        return [{"id": session_id(r["token_hash"]), "device": r["device"] or "Appareil inconnu",
                 "created_at": r["created_at"], "last_used": r["last_used"], "current": r["token_hash"] == mine}
                for r in rows]

    @app.delete(f"{PREFIX}/users/{{username}}/sessions/{{sid}}", status_code=204)
    def revoke_session(username: str, sid: str, admin: Admin, conn: Conn) -> Response:
        target = account(conn, username)
        if len(sid) != 16 or any(c not in "0123456789abcdef" for c in sid):
            raise _error(404, "Appareil introuvable.")
        cur = conn.execute("DELETE FROM sessions WHERE user_id = ? AND substr(token_hash, 1, 16) = ?",
                           (target["id"], sid))
        if not cur.rowcount:
            raise _error(404, "Appareil introuvable.")
        return Response(status_code=204)

    @app.post(f"{PREFIX}/users/{{username}}/signout")
    def sign_out_everywhere(username: str, request: Request, admin: Admin, conn: Conn) -> dict:
        target = account(conn, username)
        mine = security.sha256(request.headers.get("authorization", "").partition(" ")[2])
        cur = conn.execute("DELETE FROM sessions WHERE user_id = ? AND token_hash != ?", (target["id"], mine))
        return {"signed_out": cur.rowcount}

    @app.post(f"{PREFIX}/users/{{username}}/password")
    def reset_password(username: str, body: PasswordConfirm, request: Request, admin: Admin, conn: Conn) -> dict:
        """A temporary password, shown once, for someone who forgot theirs. Signs out all their devices."""
        confirm_password(request, admin, body.admin_password)
        target = account(conn, username)
        if target["id"] == admin["id"]:
            raise _error(409, "Pour votre propre compte, changez le mot de passe depuis « Mon compte ».")
        password = new_temporary_password()
        with db.transaction(conn):
            conn.execute("UPDATE users SET password_hash = ? WHERE id = ?",
                         (security.hash_password(password), target["id"]))
            conn.execute("DELETE FROM sessions WHERE user_id = ?", (target["id"],))
        limiter.clear_user(target["username"])
        return {"username": target["username"], "password": password}

    @app.post(f"{PREFIX}/users/{{username}}/delete")
    def delete_user(username: str, body: DeleteAccount, request: Request, admin: Admin, conn: Conn) -> dict:
        """Delete an account with its recipes and photos. Cannot be undone (except from a backup)."""
        confirm_password(request, admin, body.admin_password)
        target = account(conn, username)
        if security.normalize_username(body.confirm_username) != target["username"]:
            raise _error(422, "Le nom d'utilisateur tapé ne correspond pas.")
        if target["id"] == admin["id"]:
            raise _error(409, "Vous ne pouvez pas supprimer votre propre compte.")
        with db.transaction(conn):
            recipe_ids = [r[0] for r in conn.execute("SELECT id FROM recipes WHERE owner_id = ?", (target["id"],))]
            n_recipes = conn.execute("SELECT COUNT(*) FROM recipes WHERE owner_id = ? AND deleted = 0",
                                     (target["id"],)).fetchone()[0]
            # devices of people it shared with must learn that these recipes are gone
            db.next_seq(conn)
            conn.execute("DELETE FROM users WHERE id = ?", (target["id"],))  # cascades: recipes, photos rows, shares…
        removed_photos = 0
        for rid in recipe_ids:
            folder = settings.images_dir / rid
            if folder.is_dir() and folder.parent == settings.images_dir:
                removed_photos += sum(1 for p in folder.iterdir() if p.is_file())
                shutil.rmtree(folder, ignore_errors=True)
        limiter.clear_user(target["username"])
        return {"username": target["username"], "recipes": n_recipes, "photos": removed_photos}

    # ----------------------------------------------------------- invites
    @app.get(f"{PREFIX}/invites")
    def invites(admin: Admin, conn: Conn) -> list[dict]:
        now = db.now()
        rows = conn.execute("""
            SELECT i.code_hash, i.note, i.created_at, i.expires_at, c.username AS created_by,
                   u.username AS used_by, u.display_name AS used_by_name
            FROM invites i LEFT JOIN users c ON c.id = i.created_by LEFT JOIN users u ON u.id = i.used_by
            ORDER BY i.created_at DESC""").fetchall()
        out = []
        for r in rows:
            status = "used" if r["used_by"] else ("expired" if r["expires_at"] <= now else "open")
            out.append({"id": invite_id(r["code_hash"]), "note": r["note"], "created_at": r["created_at"],
                        "expires_at": r["expires_at"], "created_by": r["created_by"], "status": status,
                        "used_by": r["used_by"], "used_by_name": r["used_by_name"]})
        return out

    @app.delete(f"{PREFIX}/invites/{{iid}}", status_code=204)
    def revoke_invite(iid: str, admin: Admin, conn: Conn) -> Response:
        if len(iid) != 16 or any(c not in "0123456789abcdef" for c in iid):
            raise _error(404, "Invitation introuvable.")
        row = conn.execute("SELECT used_by FROM invites WHERE substr(code_hash, 1, 16) = ?", (iid,)).fetchone()
        if row is None:
            raise _error(404, "Invitation introuvable.")
        if row["used_by"] is not None:
            raise _error(409, "Cette invitation a déjà servi à créer un compte.")
        conn.execute("DELETE FROM invites WHERE substr(code_hash, 1, 16) = ? AND used_by IS NULL", (iid,))
        return Response(status_code=204)

    @app.post(f"{PREFIX}/invites/cleanup")
    def cleanup_invites(admin: Admin, conn: Conn) -> dict:
        cur = conn.execute("DELETE FROM invites WHERE used_by IS NULL AND expires_at <= ?", (db.now(),))
        return {"deleted": cur.rowcount}

    # ----------------------------------------------------------- reports
    @app.get(f"{PREFIX}/reports")
    def reports(admin: Admin, conn: Conn, status: Annotated[str, Query(pattern="^(unread|all)$")] = "all",
                category: Annotated[str, Query(pattern="^(all|bug|idea|other)$")] = "all") -> list[dict]:
        clauses, params = [], []
        if status == "unread":
            clauses.append("r.read_at IS NULL")
        if category != "all":
            clauses.append("r.category = ?")
            params.append(category)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = conn.execute("SELECT r.*, u.username, u.display_name FROM reports r JOIN users u ON u.id = r.user_id "
                            f"{where} ORDER BY r.id DESC LIMIT 500", params).fetchall()
        return [{k: r[k] for k in ("id", "created_at", "message", "category", "category_other", "client",
                                   "app_version", "os", "log_tail", "read_at", "username", "display_name")}
                for r in rows]

    @app.get(f"{PREFIX}/reports/counts")
    def report_counts(admin: Admin, conn: Conn, status: Annotated[str, Query(pattern="^(unread|all)$")] = "all") -> dict:
        """How many messages per label, for the filter chips."""
        where = "WHERE read_at IS NULL" if status == "unread" else ""
        counts = {"bug": 0, "idea": 0, "other": 0}
        for row in conn.execute(f"SELECT category, COUNT(*) AS n FROM reports {where} GROUP BY category"):
            counts[row["category"]] = row["n"]
        return counts

    @app.post(f"{PREFIX}/reports/{{rid}}/category", status_code=204)
    def set_report_category(rid: int, body: ReportCategory, admin: Admin, conn: Conn) -> Response:
        """The admin moves a message to another label (people do not always pick the right one)."""
        other = " ".join(body.category_other.split()) if body.category == "other" else ""
        if body.category == "other" and not other:
            raise _error(422, "Dites en quelques mots de quoi il s'agit.")
        cur = conn.execute("UPDATE reports SET category = ?, category_other = ? WHERE id = ?",
                           (body.category, other, rid))
        if not cur.rowcount:
            raise _error(404, "Message introuvable.")
        return Response(status_code=204)

    @app.post(f"{PREFIX}/reports/{{rid}}/read", status_code=204)
    def mark_report(rid: int, body: ReadFlag, admin: Admin, conn: Conn) -> Response:
        cur = conn.execute("UPDATE reports SET read_at = ? WHERE id = ?", (db.now() if body.read else None, rid))
        if not cur.rowcount:
            raise _error(404, "Message introuvable.")
        return Response(status_code=204)

    @app.post(f"{PREFIX}/reports/read-all")
    def mark_all_reports(admin: Admin, conn: Conn) -> dict:
        cur = conn.execute("UPDATE reports SET read_at = ? WHERE read_at IS NULL", (db.now(),))
        return {"marked": cur.rowcount}

    @app.delete(f"{PREFIX}/reports/{{rid}}", status_code=204)
    def delete_report(rid: int, admin: Admin, conn: Conn) -> Response:
        if not conn.execute("DELETE FROM reports WHERE id = ?", (rid,)).rowcount:
            raise _error(404, "Message introuvable.")
        return Response(status_code=204)

    # ------------------------------------------------------------- usage
    @app.get(f"{PREFIX}/usage")
    def usage_summary(admin: Admin, conn: Conn, days: Annotated[int, Query(ge=7, le=364)] = 56) -> dict:
        return usage.weekly_summary(conn, days)

    # ---------------------------------------------------------- security
    @app.get(f"{PREFIX}/security")
    def blocks(admin: Admin) -> dict:
        return {"blocked": limiter.blocked(), "attempts": settings.login_attempts,
                "window_s": settings.login_window_s}

    @app.post(f"{PREFIX}/security/unblock", status_code=204)
    def unblock(body: Unblock, admin: Admin) -> Response:
        limiter.clear(body.ip, body.username)
        return Response(status_code=204)

    # ----------------------------------------------------------- backups
    @app.get(f"{PREFIX}/backups")
    def backups(admin: Admin) -> dict:
        return backup_status(settings)

    @app.post(f"{PREFIX}/backups", status_code=201)
    def backup_now(admin: Admin) -> dict:
        """A snapshot into DATA/backups now (e.g. before an upgrade). The nightly job copies it to the USB disk."""
        from mamarmiton_server import backup as snap

        status = backup_status(settings)
        if status["snapshots"]:
            newest = _parse(status["snapshots"][0]["created_at"])
            if newest and (datetime.now(timezone.utc) - newest).total_seconds() < MIN_SECONDS_BETWEEN_SNAPSHOTS:
                raise _error(429, "Une sauvegarde vient d'être faite. Attendez une minute.")
        try:
            archive, manifest, missing = snap.create_snapshot(settings, settings.data_dir / BACKUPS)
        except (snap.BackupError, sqlite3.Error, OSError) as exc:
            raise _error(500, f"La sauvegarde a échoué : {exc}") from exc
        return {"name": archive.name, "size": _file_size(archive), "counts": manifest["counts"],
                "missing_photos": len(missing)}

    @app.post(f"{PREFIX}/backups/drill")
    def restore_drill(admin: Admin, name: Annotated[str | None, Query(max_length=80)] = None) -> dict:
        """The restore drill on a snapshot (the newest by default): restore it to a temporary folder and
        check it with the real code. The result is kept, so the dashboard can say when it was last done."""
        from mamarmiton_server import backup as snap

        folder = settings.data_dir / BACKUPS
        status = backup_status(settings)
        if not status["snapshots"]:
            raise _error(404, "Aucune sauvegarde à tester.")
        chosen = name or status["snapshots"][0]["name"]
        if snap.snapshot_time(chosen) is None or not (folder / chosen).is_file():
            raise _error(404, "Sauvegarde introuvable.")
        rep = snap.check_snapshot(folder / chosen)
        result = {"finished_at": db.now(), "snapshot": chosen, "ok": rep.ok, "problems": rep.problems,
                  "warnings": rep.warnings, "info": rep.info}
        try:
            tmp = folder / f".{LAST_DRILL}.tmp"
            tmp.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
            tmp.replace(folder / LAST_DRILL)
        except OSError:
            pass  # the drill still ran; the dashboard just will not remember it
        return result
