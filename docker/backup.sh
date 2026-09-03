#!/bin/sh
# Daily PostgreSQL backup with retention. Runs in a loop inside the
# `backup` service (see docker-compose.yml) rather than as a host cron
# job, so it works identically regardless of host OS and travels with
# the rest of the stack.
#
# Neo4j and ChromaDB are deliberately NOT backed up here: both are fully
# regenerable from the KBV catalog PDF via the admin import feature
# (POST /admin/import/kbv-fetch + kbv-commit). PostgreSQL holds the only
# irreplaceable data — patient records, case files, users, the audit
# log — and is the only store where "restore from backup" is the actual
# recovery path rather than "re-run the import".
set -eu

BACKUP_DIR="/backups"
RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-14}"
INTERVAL_SECONDS="${BACKUP_INTERVAL_SECONDS:-86400}"

mkdir -p "$BACKUP_DIR"

run_backup() {
  timestamp=$(date -u +%Y%m%dT%H%M%SZ)
  out_file="$BACKUP_DIR/ebm_db_${timestamp}.dump"
  echo "[$(date -u -Iseconds)] Starting backup -> $out_file"

  if PGPASSWORD="$POSTGRES_PASSWORD" pg_dump \
      -h postgres -U "$POSTGRES_USER" -d ebm_db \
      -Fc --no-owner --no-privileges \
      -f "${out_file}.tmp"; then
    mv "${out_file}.tmp" "$out_file"
    echo "[$(date -u -Iseconds)] Backup complete: $(du -h "$out_file" | cut -f1)"
  else
    echo "[$(date -u -Iseconds)] Backup FAILED — leaving no partial file" >&2
    rm -f "${out_file}.tmp"
    return 1
  fi

  echo "[$(date -u -Iseconds)] Pruning backups older than ${RETENTION_DAYS} days"
  find "$BACKUP_DIR" -name 'ebm_db_*.dump' -mtime "+${RETENTION_DAYS}" -print -delete
}

echo "Backup service started. Interval: ${INTERVAL_SECONDS}s, retention: ${RETENTION_DAYS}d"
while true; do
  run_backup || echo "[$(date -u -Iseconds)] Backup run failed, will retry next interval" >&2
  sleep "$INTERVAL_SECONDS"
done
