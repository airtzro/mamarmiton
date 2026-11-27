"""Command line: run the server and manage accounts.

    python -m mamarmiton_server serve
    python -m mamarmiton_server create-admin <username>
    python -m mamarmiton_server invite [--note "pour ma soeur"]
    python -m mamarmiton_server reset-password <username>
    python -m mamarmiton_server users
    python -m mamarmiton_server backup /data/backups          # snapshot (db + photos), see ops/README.md
    python -m mamarmiton_server restore-check <snapshot>
    python -m mamarmiton_server restore <snapshot> --into /data [--force]
    python -m mamarmiton_server migrate [--dry-run]

In Docker: ``docker compose exec api python -m mamarmiton_server invite``.
"""

from __future__ import annotations

import argparse
import getpass
import sys
from datetime import datetime, timedelta, timezone

from mamarmiton_server import db, security
from mamarmiton_server.config import Settings


def _ask_password(username: str, from_stdin: bool = False) -> str:
    if from_stdin:  # for scripts: the password is the first line of standard input
        password = sys.stdin.readline().rstrip("\n")
        problem = security.password_problem(password, username)
        if problem:
            raise SystemExit(problem)
        return password
    while True:
        first = getpass.getpass("Mot de passe : ")
        problem = security.password_problem(first, username)
        if problem:
            print(problem)
            continue
        if getpass.getpass("Encore une fois : ") != first:
            print("Les deux mots de passe sont différents.")
            continue
        return first


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="mamarmiton_server")
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve", help="run the HTTP server")
    serve.add_argument("--host", default="0.0.0.0")
    serve.add_argument("--port", type=int, default=8000)
    admin = sub.add_parser("create-admin", help="create an administrator account")
    admin.add_argument("username")
    admin.add_argument("--display-name", default="")
    admin.add_argument("--password-stdin", action="store_true", help="read the password from standard input")
    invite = sub.add_parser("invite", help="print a new invite code")
    invite.add_argument("--note", default="")
    reset = sub.add_parser("reset-password", help="set a new password and sign out all devices")
    reset.add_argument("username")
    reset.add_argument("--password-stdin", action="store_true", help="read the password from standard input")
    sub.add_parser("users", help="list accounts")
    backup = sub.add_parser("backup", help="snapshot of database + photos while the server runs")
    backup.add_argument("destination", help="folder for mamarmiton-DATE.tar.gz snapshots (or a *.sqlite3 file: "
                                            "database copy only, old behaviour)")
    backup.add_argument("--keep-daily", type=int, default=7, help="days to keep one snapshot of (default 7)")
    backup.add_argument("--keep-weekly", type=int, default=8, help="weeks to keep one snapshot of (default 8)")
    check = sub.add_parser("restore-check", help="restore drill: restore a snapshot to a temp dir and verify it")
    check.add_argument("snapshot")
    rest = sub.add_parser("restore", help="restore a snapshot into a data directory")
    rest.add_argument("snapshot")
    rest.add_argument("--into", required=True, metavar="DATA_DIR")
    rest.add_argument("--force", action="store_true", help="replace a non-empty directory (old content is kept aside)")
    mig = sub.add_parser("migrate", help="upgrade the database schema (done automatically at startup)")
    mig.add_argument("--dry-run", action="store_true", help="only say what would be done")
    args = parser.parse_args(argv)

    import logging

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        return _run(args)
    except (db.SchemaTooNew, db.MigrationError) as exc:
        logging.getLogger("mamarmiton_server").error("%s", exc)
        print(f"Erreur : {exc}", file=sys.stderr)
        return 1


def _run(args) -> int:
    from pathlib import Path

    from mamarmiton_server import backup as snap

    if args.command == "restore-check":
        return snap.cmd_restore_check(Path(args.snapshot))
    if args.command == "restore":
        return snap.cmd_restore(Path(args.snapshot), Path(args.into), args.force)
    settings = Settings()
    if args.command == "migrate":
        settings.ensure_dirs()
        start, target = db.migrate(settings.db_path, dry_run=args.dry_run)
        if start == target:
            print(f"Base déjà à jour (version {target}).")
        elif args.dry_run:
            print(f"Migration nécessaire : version {start} vers {target} (rien n'a été modifié).")
        else:
            print(f"Base migrée : version {start} vers {target}. Une copie a été faite à côté de la base.")
        return 0
    if args.command == "backup" and not args.destination.endswith(".sqlite3"):
        return snap.cmd_backup(settings, Path(args.destination), args.keep_daily, args.keep_weekly)
    if args.command == "serve":
        import uvicorn

        from mamarmiton_server.api import create_app

        # one worker: the login rate limiter lives in memory.
        # Only the reverse proxy can reach this port, so trust its X-Forwarded-For.
        uvicorn.run(create_app(settings), host=args.host, port=args.port, proxy_headers=True,
                    forwarded_allow_ips="*", server_header=False)
        return 0

    settings.ensure_dirs()
    db.init(settings.db_path)
    conn = db.connect(settings.db_path)
    try:
        if args.command == "create-admin":
            username = security.normalize_username(args.username)
            if not security.valid_username(username):
                print("Nom d'utilisateur invalide (2 à 32 caractères : a-z, 0-9, . _ -).")
                return 1
            if conn.execute("SELECT 1 FROM users WHERE username = ?", (username,)).fetchone():
                print("Ce nom d'utilisateur existe déjà.")
                return 1
            password = _ask_password(username, args.password_stdin)
            conn.execute("INSERT INTO users (username, display_name, password_hash, is_admin, created_at) "
                         "VALUES (?, ?, ?, 1, ?)",
                         (username, args.display_name or username, security.hash_password(password), db.now()))
            print(f"Administrateur « {username} » créé.")
        elif args.command == "invite":
            admin_row = conn.execute("SELECT id FROM users WHERE is_admin = 1 ORDER BY id LIMIT 1").fetchone()
            if admin_row is None:
                print("Créez d'abord un administrateur : create-admin <nom>")
                return 1
            code = security.new_invite_code()
            expires = datetime.now(timezone.utc) + timedelta(days=settings.invite_days)
            conn.execute("INSERT INTO invites (code_hash, created_by, note, created_at, expires_at) "
                         "VALUES (?, ?, ?, ?, ?)", (security.sha256(code), admin_row["id"], args.note, db.now(),
                                                    expires.isoformat(timespec="microseconds")))
            print(f"Code d'invitation : {code}")
            print(f"Valable jusqu'au {expires.astimezone().strftime('%d/%m/%Y %H:%M')}, une seule fois.")
        elif args.command == "reset-password":
            username = security.normalize_username(args.username)
            row = conn.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()
            if row is None:
                print("Utilisateur introuvable.")
                return 1
            password = _ask_password(username, args.password_stdin)
            with db.transaction(conn):
                conn.execute("UPDATE users SET password_hash = ? WHERE id = ?",
                             (security.hash_password(password), row["id"]))
                conn.execute("DELETE FROM sessions WHERE user_id = ?", (row["id"],))
            print("Mot de passe changé ; tous les appareils de ce compte sont déconnectés.")
        elif args.command == "backup":
            dest = Path(args.destination)
            db.backup_to(conn, dest)
            print(f"Base copiée dans {dest}. Les photos sont dans {settings.images_dir}.")
        elif args.command == "users":
            for row in conn.execute("SELECT u.username, u.display_name, u.is_admin, u.created_at, "
                                    "(SELECT COUNT(*) FROM recipes r WHERE r.owner_id = u.id AND r.deleted = 0) AS n "
                                    "FROM users u ORDER BY u.id"):
                flag = " (admin)" if row["is_admin"] else ""
                print(f"{row['username']}{flag}  « {row['display_name']} »  {row['n']} recettes  "
                      f"depuis {row['created_at'][:10]}")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
