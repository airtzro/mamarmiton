#!/usr/bin/env bash
# Nightly backup of MAMARMITON on the NAS. Run it as the NAS user (no root needed).
#
#   backup-cron.sh /srv/dev-disk-by-uuid-XXXX/mamarmiton-backups
#
# 1. makes a snapshot (database + photos) inside the running container,
#    into DATA_DIR/backups (kept 3 days locally, the USB disk is the real copy);
# 2. copies it to the USB disk folder given as argument and checks the copy;
# 3. prunes the USB folder (KEEP_DAILY / KEEP_WEEKLY);
# 4. if RCLONE_REMOTE is set, copies it off-site too;
# 5. if HEALTHCHECK_URL is set, pings it (/start, then success or /fail).
#
# Settings are plain environment variables, or lines in /opt/mamarmiton/ops.env
# (a file you create yourself; this script never reads the app's .env).
# Exit code 0 = everything fine, 1 = a step failed, 2 = backup written but a photo was missing.

set -uo pipefail

[ -r "${MAMARMITON_OPS_ENV:-/opt/mamarmiton/ops.env}" ] && . "${MAMARMITON_OPS_ENV:-/opt/mamarmiton/ops.env}"

USB_DIR="${1:-${USB_DIR:-}}"
APP_DIR="${APP_DIR:-/opt/mamarmiton/app/server}"      # where docker-compose.yml is
DATA_DIR="${DATA_DIR:-/opt/mamarmiton/data}"          # host side of the container's /data
COMPOSE_PROJECT="${COMPOSE_PROJECT:-server}"
KEEP_DAILY="${KEEP_DAILY:-7}"
KEEP_WEEKLY="${KEEP_WEEKLY:-8}"
RCLONE_REMOTE="${RCLONE_REMOTE:-}"                    # e.g. "offsite:mamarmiton-backups"; empty = no off-site copy
OFFSITE_KEEP_DAYS="${OFFSITE_KEEP_DAYS:-60}"
HEALTHCHECK_URL="${HEALTHCHECK_URL:-}"                # e.g. https://hc-ping.com/<uuid>
LOG_FILE="${LOG_FILE:-/opt/mamarmiton/backup.log}"

# the user account has no home directory, so Docker needs a writable config dir
export DOCKER_CONFIG="${DOCKER_CONFIG:-/tmp/mm-docker}"
mkdir -p "$DOCKER_CONFIG"

log() { printf '%s %s\n' "$(date '+%F %T')" "$*" | tee -a "$LOG_FILE"; }

ping_hc() {  # ping_hc [suffix]  (suffix: /start, /fail, or nothing for success)
  [ -n "$HEALTHCHECK_URL" ] || return 0
  curl -fsS -m 15 --retry 3 -o /dev/null "${HEALTHCHECK_URL}${1:-}" || log "WARN: healthcheck ping failed"
}

# the admin dashboard (/admin) reads this file to show how the last night went
STATUS_FILE="$DATA_DIR/backups/last-run.json"
name=""
offsite="off"
write_status() {  # write_status <exit code> <message>
  local msg=${2//\\/\\\\}; msg=${msg//\"/\\\"}
  mkdir -p "$DATA_DIR/backups" 2>/dev/null
  printf '{"finished_at": "%s", "exit": %d, "message": "%s", "snapshot": "%s", "usb": %s, "offsite": "%s"}\n' \
    "$(date -u +%Y-%m-%dT%H:%M:%S+00:00)" "$1" "$msg" "$name" \
    "$( [ -n "$name" ] && [ -f "$USB_DIR/$name" ] && echo true || echo false )" "$offsite" \
    > "$STATUS_FILE.tmp" 2>/dev/null && mv "$STATUS_FILE.tmp" "$STATUS_FILE" 2>/dev/null
  return 0
}

fail() { log "ERROR: $*"; write_status 1 "$*"; ping_hc /fail; exit 1; }

# keep the log small
if [ -f "$LOG_FILE" ] && [ "$(stat -c %s "$LOG_FILE")" -gt 1048576 ]; then
  tail -n 500 "$LOG_FILE" > "$LOG_FILE.tmp" && mv "$LOG_FILE.tmp" "$LOG_FILE"
fi

[ -n "$USB_DIR" ] || { echo "usage: $0 /srv/dev-disk-by-uuid-XXXX/mamarmiton-backups" >&2; exit 1; }
ping_hc /start
log "=== backup start (usb: $USB_DIR) ==="

# never write into an unmounted USB mount point (that would fill the system SSD)
[ -d "$USB_DIR" ] || fail "USB folder $USB_DIR does not exist (disk not mounted? create the folder once by hand)"
[ -w "$USB_DIR" ] || fail "USB folder $USB_DIR is not writable"

status=0
cd "$APP_DIR" || fail "cannot cd to $APP_DIR"
out=$(docker compose -p "$COMPOSE_PROJECT" exec -T api python -m mamarmiton_server backup /data/backups \
        --keep-daily 3 --keep-weekly 0 2>&1)
rc=$?
log "$out"
case $rc in
  0) ;;
  2) log "WARN: snapshot written but incomplete (see above)"; status=2 ;;
  *) fail "backup command failed (exit $rc)" ;;
esac

latest=$(ls -1 "$DATA_DIR"/backups/mamarmiton-*.tar.gz 2>/dev/null | sort | tail -n 1)
[ -n "$latest" ] && [ -f "$latest" ] || fail "no snapshot found in $DATA_DIR/backups"
name=$(basename "$latest")

cp "$latest" "$USB_DIR/.$name.part" || fail "copy to USB failed (disk full?)"
want=$(sha256sum "$latest" | cut -d' ' -f1)
got=$(sha256sum "$USB_DIR/.$name.part" | cut -d' ' -f1)
[ "$want" = "$got" ] || { rm -f "$USB_DIR/.$name.part"; fail "USB copy differs from the original"; }
mv "$USB_DIR/.$name.part" "$USB_DIR/$name" || fail "cannot finalize USB copy"
log "USB copy ok: $USB_DIR/$name"

# prune the USB folder; the helper only needs python3 on the host
if command -v python3 >/dev/null; then
  PYTHONPATH="$APP_DIR" python3 -m mamarmiton_server.backup prune "$USB_DIR" \
      --keep-daily "$KEEP_DAILY" --keep-weekly "$KEEP_WEEKLY" 2>&1 | while read -r l; do log "$l"; done
else
  log "WARN: python3 not found on the host, USB folder not pruned"; [ $status -eq 0 ] && status=2
fi

if [ -n "$RCLONE_REMOTE" ]; then
  if ! command -v rclone >/dev/null; then
    log "ERROR: RCLONE_REMOTE is set but rclone is not installed"; ping_hc /fail; status=1; offsite="failed"
  elif rclone copy --immutable "$USB_DIR/$name" "$RCLONE_REMOTE" 2>&1 | while read -r l; do log "rclone: $l"; done; then
    # (pipefail: the status above is rclone's)
    rclone delete --min-age "${OFFSITE_KEEP_DAYS}d" --include 'mamarmiton-*.tar.gz' "$RCLONE_REMOTE" 2>&1 \
      | while read -r l; do log "rclone: $l"; done
    log "off-site copy ok: $RCLONE_REMOTE/$name"; offsite="ok"
  else
    log "ERROR: off-site copy failed"; ping_hc /fail; status=1; offsite="failed"
  fi
fi

if [ $status -eq 0 ]; then
  log "=== backup ok ==="; write_status 0 "ok"; ping_hc
else
  write_status "$status" "$( [ $status -eq 2 ] && echo "sauvegarde incomplète (voir backup.log)" || echo "copie hors site en échec (voir backup.log)" )"
  log "=== backup finished with problems (exit $status) ==="; [ $status -eq 2 ] && ping_hc /fail
fi
exit $status
