#!/usr/bin/env bash
set -euo pipefail

ROOT="/home/ubuntu/projects/marawa-ai"
UV="/home/ubuntu/.hermes/bin/uv"
LOCK="/tmp/marawa-bps-webapi-update.lock"
LOG_DIR="$ROOT/data/reports"
mkdir -p "$LOG_DIR"

exec 9>"$LOCK"
if ! flock -n 9; then
  printf '%s\n' "BPS WebAPI update already running; skipped."
  exit 0
fi

MODE="${1:-full}"
case "$MODE" in
  full) RESUME_ARGS=() ;;
  resume) RESUME_ARGS=(--resume) ;;
  *) printf '%s\n' "Usage: $0 [full|resume]" >&2; exit 64 ;;
esac

cd "$ROOT"
if [[ "$MODE" == "full" ]]; then
  scripts/backup_bps_database.sh
fi
"$UV" run python scripts/ingest_bps_webapi.py \
  --families simdasi,dynamic,census,publication,glossary "${RESUME_ARGS[@]}"
"$UV" run python scripts/build_simdasi_registry.py
"$UV" run python scripts/backfill_dynamic_units.py
"$UV" run python scripts/validate_bps_database.py
"$UV" run python scripts/analyze_bps_database.py
# PDF binary mirror is optional and intentionally skipped (metadata-only per Tah).
"$UV" run python scripts/update_bps_checklist.py --force
printf '%s\n' "BPS WebAPI update and analysis completed."
