# Backups and monitoring

Two things protect the family's recipes: a **nightly backup you have proven you can restore**, and an
**alert when the server is down**. Both run without root.

## What a backup is

`python -m mamarmiton_server backup /data/backups` writes one file, `mamarmiton-YYYYMMDD-HHMMSS.tar.gz`
(UTC), made while the server keeps running:

- `mamarmiton.sqlite3`: a consistent copy made with SQLite's online backup API;
- `images/…`: every photo the database refers to;
- `manifest.json`: counts of accounts, recipes and photos, the sha256 of the database copy, the app version.

It re-reads the archive before giving it its final name, and prunes old snapshots in the folder
(`--keep-daily 7 --keep-weekly 8` by default: the newest snapshot of each of the last 7 days, plus the newest
of each of the last 8 weeks). Exit code `0` = fine, `1` = failed, `2` = written but a photo was missing on disk.

## Nightly job on the NAS

`ops/backup-cron.sh <usb-folder>` does the whole chain: snapshot inside the container (kept 3 days in
`DATA_DIR/backups`), copy to the USB disk with a checksum check, prune the USB folder, optional off-site copy
with rclone, optional ping to a monitoring URL, log in `/opt/mamarmiton/backup.log`.

One-time setup, as the NAS user:

1. Create the destination folder **by hand on the mounted disk** (the script refuses to create it, so that
   an unmounted disk never fills the system SSD):
   `mkdir /srv/dev-disk-by-uuid-XXXX/mamarmiton-backups`
2. Try it once: `/opt/mamarmiton/app/server/ops/backup-cron.sh /srv/dev-disk-by-uuid-XXXX/mamarmiton-backups`
   and check `/opt/mamarmiton/backup.log`.
3. Schedule it, either way (no sudo needed):
   - `crontab -e` as that user and paste the line from `ops/crontab.example`; or
   - in the OMV web interface: **System > Scheduled jobs > Add**, user = your user, time 03:15 daily,
     command = the same `backup-cron.sh …` line (untick "Send command output via email" if no mail is set up).
4. Optional settings go in `/opt/mamarmiton/ops.env` (plain `NAME=value` lines, `chmod 600`):

   | Variable | Default | Meaning |
   |---|---|---|
   | `HEALTHCHECK_URL` | empty | healthchecks.io-style ping URL (see below) |
   | `RCLONE_REMOTE` | empty | off-site folder, e.g. `offsite:mamarmiton-backups`; needs `rclone` configured by hand (`rclone config`) |
   | `OFFSITE_KEEP_DAYS` | 60 | snapshots older than this are deleted off-site |
   | `KEEP_DAILY` / `KEEP_WEEKLY` | 7 / 8 | retention on the USB disk |
   | `APP_DIR`, `DATA_DIR`, `COMPOSE_PROJECT` | `/opt/mamarmiton/app/server`, `/opt/mamarmiton/data`, `server` | where things are |

Off-site: any rclone remote works (a cheap object store, a friend's SFTP, a cloud drive). Backups hold
password hashes and every recipe, so prefer an `rclone crypt` remote.

Docker needs `export DOCKER_CONFIG=/tmp/mm-docker` on this NAS (no home directory); the script sets it, and so
must you for manual commands.

## The restore drill (every quarter)

A backup nobody has restored is a hope, not a backup. Every three months, and after any server upgrade:

```sh
export DOCKER_CONFIG=/tmp/mm-docker
cd /opt/mamarmiton/app/server
# copy a snapshot from the USB disk (the copy you would really use) into the container's view of /data
cp /srv/dev-disk-by-uuid-XXXX/mamarmiton-backups/mamarmiton-LATEST.tar.gz /opt/mamarmiton/data/backups/
docker compose -p server exec -T api python -m mamarmiton_server restore-check /data/backups/mamarmiton-LATEST.tar.gz
```

It restores into a temporary folder, opens it with the real application code, and checks: the database
`integrity_check`, the sha256 and the counts from the manifest, that every photo row has its file and every
photo file has a row, and that a few recipes (and a photo) can be read through the API. It prints a short
report and exits non-zero on any mismatch. Write the date of the last passed drill down; the product plan
asks for one per quarter. Do the same drill with the **off-site** copy once a year (download it first).

### Restoring for real (disaster)

```sh
cd /opt/mamarmiton/app/server
docker compose -p server stop api
docker compose -p server run --rm --no-deps api \
    python -m mamarmiton_server restore /data/backups/mamarmiton-LATEST.tar.gz --into /data --force
docker compose -p server start api
```

`restore` checks the snapshot first and changes nothing if it is bad. Without `--force` it refuses a
non-empty folder; with it, the current database and photos are moved to `DATA_DIR/replaced-<time>/` and
`DATA_DIR/backups` stays in place. Delete `replaced-*` yourself once you are happy.
(If `docker compose run` is refused by the read-only setup, use `docker compose -p server run --rm --no-deps
--entrypoint python api -m mamarmiton_server restore …` or restore into a new folder with `--into` and move it.)

## Knowing when it is down

Backups do not tell you the site is unreachable, and an unreachable NAS cannot tell you anything. Use two
independent outside checks, both free:

1. **Uptime check on `/health`.** A free external monitor requests `https://<domain>/health` every 5 minutes
   (it answers `{"ok":true,…}`) and emails or messages the developer when it fails twice in a row. Options:
   - **UptimeRobot**: free plan, 5-minute checks, e-mail/Telegram/push alerts. Simplest choice.
   - **healthchecks.io** (or Better Stack, Uptime Kuma hosted elsewhere): similar; good if you want a single
     account for both checks below.

   Create the account yourself, add an "HTTP(s)" monitor on the public URL (including the port if the router
   still forwards a non-443 port), alert contact = the developer. Test it once by stopping Caddy
   (`docker compose -p server stop caddy`) and starting it again. A monitor outside the house also notices
   power cuts, a changed IP and an expired certificate.
2. **Dead-man's switch for the backup.** In healthchecks.io create a check with period 1 day and grace 6
   hours, and put its ping URL in `HEALTHCHECK_URL` in `/opt/mamarmiton/ops.env`. The script pings `/start`,
   then success or `/fail`; no ping at all (NAS off, cron broken, disk unmounted) also raises an alert.

Never put these URLs or tokens in the git repository.
