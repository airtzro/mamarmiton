# Mamarmiton server

Stores recipes and photos on the NAS so several people can use Mamarmiton,
each with their own account and recipe book, and share books or single
recipes with each other.

- **API**: FastAPI, SQLite, photos as files. Everything lives in one folder (`DATA_DIR`).
- **HTTPS**: Caddy, with a free Let's Encrypt certificate for a DuckDNS name.
- **Accounts**: invite-only. Nobody can sign up without a code from the admin.

```
Internet ──443──▶ router ──8443──▶ NAS: caddy (HTTPS) ──▶ api (not reachable from outside)
                                                          └── DATA_DIR/ mamarmiton.sqlite3, images/
```

## Before you start: can the NAS be reached from outside?

Some internet providers put home connections behind CGNAT (a shared public
address), and then port forwarding cannot work. Compare the **WAN IP** on your
router's status page with what https://ifconfig.me shows. If they differ, or the
router's address starts with `100.64.`–`100.127.`, `10.` or `192.168.`, you are
behind CGNAT. Ask your provider for a "full-stack" public IPv4 (often free in
France, e.g. Free and SFR offer it), or use Tailscale instead of DuckDNS.

## Setup on OpenMediaVault

1. **DuckDNS**: sign in at https://www.duckdns.org, create a name (e.g.
   `mamarmiton-famille`), and copy your **token**.
2. **Shared folder**: in OMV, create a shared folder for the data (e.g.
   `mamarmiton`). Note its path (`/srv/dev-disk-by-uuid-…/mamarmiton`) and
   run `id <your user>` over SSH for its uid (PUID) and group (PGID, usually 100).
3. **Files**: copy this repository to the NAS (e.g. `git clone` into
   `/srv/…/apps/mamarmiton`), then:
   ```sh
   cd server
   cp .env.example .env
   nano .env            # DUCKDNS_SUBDOMAIN, DOMAIN, DUCKDNS_TOKEN, DATA_DIR, PUID, PGID
   chmod 600 .env
   ```
   With OMV's compose plugin you can instead paste `docker-compose.yml` and the
   `.env` values into a new compose file in the web interface, but keep the
   repository on the NAS: the `api` image is built from it.
4. **Start**:
   ```sh
   docker compose up -d --build
   docker compose logs -f caddy     # wait for "certificate obtained successfully"
   ```
5. **Router**: forward **TCP port 443** (public) to the NAS's **port 8443**
   (`HTTPS_PORT`). No other port is needed. If the router cannot change the
   port, forward 8443→8443 and use `https://<name>.duckdns.org:8443` in the app.
6. **Check** from a phone on 4G (not Wi-Fi):
   `https://<name>.duckdns.org/health` should show `{"ok":true,…}`.
7. **Your admin account**:
   ```sh
   docker compose exec api python -m mamarmiton_server create-admin tristan
   ```

## Inviting someone

```sh
docker compose exec api python -m mamarmiton_server invite --note "pour maman"
```

This prints a code like `HB3J-24PD-KENJ-S45G`. Send it to the person; it works
once and expires after 7 days. They type it into the app with the name and
password they choose.

## Admin dashboard

`https://<name>.duckdns.org:51820/admin` (any address of the server, followed by `/admin`). Sign in with an
**admin** account (made with `create-admin`); other accounts are refused. It replaces most of the commands below:

| Section | What you can do |
|---|---|
| Vue d'ensemble | what needs attention (unread messages, old backup, no restore drill this quarter, full disk, lockouts), counts, storage, server version and settings |
| Comptes | every account with recipe/photo counts, devices, last activity and shares; rename, sign out one device or all, temporary password for someone who forgot theirs, grant/remove admin, delete an account |
| Invitations | create a code with a ready-to-send message, see which codes were used and by whom, cancel a code |
| Messages | the "Signaler un problème" reports with their log; mark read, delete |
| Utilisation | the anonymous usage counters per week, least used first |
| Système | last nightly backup (USB copy, off-site), snapshots on the server, "back up now", restore drill, lifting a login lockout |

Recipe and photo content is never shown, only counts. Resetting a password, deleting an account and granting
admin ask for your own password again. The nightly backup status comes from `DATA_DIR/backups/last-run.json`,
written by `ops/backup-cron.sh`.

## Web version

The server also serves the web version of the app at its root address, for example `https://<name>.duckdns.org/` (its files are under `/app/`). Nothing to install: open the address in a phone browser, log in, and use "Add to home screen" for an icon. It has the same accounts, sharing and conflict handling as the desktop app, but no PDF import and no offline use.

With the router forwarding public port 51820 to the NAS (as in the current setup, and the desktop app's default URL), the address is `https://<name>.duckdns.org:51820/`.

The web page is part of the `api` image, so the update command below also updates it.

## Day-to-day

| Task | Command (in `server/`) |
|---|---|
| List accounts | `docker compose exec api python -m mamarmiton_server users` |
| Forgotten password | `docker compose exec -it api python -m mamarmiton_server reset-password maman` (signs out all of her devices) |
| Back up | `docker compose exec api python -m mamarmiton_server backup /data/backups` (database + photos, one `.tar.gz`); nightly job, restore drill and uptime monitoring: see [`ops/README.md`](ops/README.md) |
| Update | `git pull && docker compose up -d --build` |
| Logs | `docker compose logs --tail 100 api caddy` |

## Schema migrations and minimum client version

The database carries `PRAGMA user_version`. At startup the server applies the
missing steps of `MIGRATIONS` (in `mamarmiton_server/db.py`) in one transaction;
a database that already holds tables is first copied to
`mamarmiton.sqlite3.pre-v<N>-<time>.bak` next to it. A database from a *newer*
server is refused with a clear error instead of being damaged. Manual run:
`python -m mamarmiton_server migrate [--dry-run]`.

**To add a migration:** write `def _m2_something(conn): conn.execute("ALTER TABLE ...")`
(plain SQL, never commit), append `(2, _m2_something)` to `MIGRATIONS`, and add a
test in `tests/test_migrations.py` that upgrades a database built at the
previous version. Never edit or reorder a released migration, and never change
`SCHEMA` (it is migration 1, frozen).

Set `MIN_DESKTOP_VERSION=4.1.0` in `.env` to make the server answer `426` (in
French, with `min_version`) to desktop apps older than that. Clients announce
themselves with `X-Mamarmiton-Client: desktop/<version>`; requests without the
header are allowed. `GET /version` returns `{"server", "min_desktop"}`.

## Security notes

- Only Caddy is exposed. The API container has no published port, runs as
  your NAS user, on a read-only filesystem, with no Linux capabilities.
- Passwords are hashed with Argon2id. Session tokens and invite codes are
  stored as SHA-256 hashes, so a stolen database copy cannot be used to log in.
- Failed logins: after 5 wrong passwords for one account from one address,
  that address waits 15 minutes (20 failures from one address block all
  attempts from it). Invite-code guesses count too.
- Every photo is decoded and re-encoded (JPEG/PNG, at most 2560 px): the
  server never stores the uploaded bytes, and phone metadata such as GPS
  position is removed. Uploads over 15 MB are refused.
- Users only ever see their own recipes and what was shared with them.
  Recipes they cannot see answer "not found", so ids leak nothing.
- Keep the NAS and Docker images up to date (`docker compose pull` for Caddy
  and DuckDNS, `--build` for the API).

## Development

```sh
cd server
uv venv .venv && uv pip install --python .venv -e ".[dev]" && uv pip install --python .venv --no-deps -e ..
.venv/bin/python -m pytest          # API tests (test_api.py) and web page tests (test_web.py)
MAMARMITON_SERVER_DATA=./data .venv/bin/python -m mamarmiton_server serve --port 8000
```

## Usage counters and problem reports

`POST /usage` takes anonymous daily counts (`usage_daily`: day, client kind, event, count; no user id,
no recipe id, no text) so that after 8 weeks `python -m mamarmiton_server usage [--days 56]` shows
which features are used (least used first). The allowed events are listed once per client
(`mamarmiton_server/usage.py`, `mamarmiton/usage.py`, `web/usage.js`; a test keeps them equal);
unknown ones are ignored. Clients have an opt-out switch. "Signaler un problème" posts to
`POST /reports` (tied to the account so you can reply); read them with
`python -m mamarmiton_server reports [--all]` (shown ones are marked read). Tokens and passwords
are masked in the log on the client and again on the server.
