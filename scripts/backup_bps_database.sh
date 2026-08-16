#!/usr/bin/env bash
set -euo pipefail

ENV_FILE="/home/ubuntu/.config/marawa-ai/postgres.env"
BACKUP_DIR="/home/ubuntu/projects/marawa-ai/data/backups"
KEEP="${BPS_BACKUP_KEEP:-7}"
mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"

set -a
# shellcheck disable=SC1090
. "$ENV_FILE"
set +a

stamp="$(date -u +%Y%m%dT%H%M%SZ)"
target="$BACKUP_DIR/marawa-bps-$stamp.dump"
temporary="$target.part"
umask 077

PGPASSWORD="$POSTGRES_PASSWORD" pg_dump \
  --host "$POSTGRES_HOST" --port "$POSTGRES_PORT" \
  --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  --format custom --compress 6 --file "$temporary"

mv "$temporary" "$target"
sha256sum "$target" > "$target.sha256"
chmod 600 "$target" "$target.sha256"

shopt -s nullglob
backups=("$BACKUP_DIR"/*.dump)
if (( ${#backups[@]} > KEEP )); then
  printf '%s\n' "${backups[@]}" | sort | head -n "$(( ${#backups[@]} - KEEP ))" | while IFS= read -r old; do
    rm -f -- "$old" "$old.sha256"
  done
fi

printf '%s\n' "$target"
