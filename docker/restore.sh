#!/bin/sh
# Restores a PostgreSQL backup produced by backup.sh. Run manually, e.g.:
#   docker compose exec backup /restore.sh /backups/ebm_db_20260707T120000Z.dump
#
# --clean --if-exists drops existing objects first, so this OVERWRITES the
# current database with the backup's contents. Confirm before running
# against a database with data you want to keep.
set -eu

DUMP_FILE="${1:?Usage: restore.sh <path-to-dump-file>}"

if [ ! -f "$DUMP_FILE" ]; then
  echo "File not found: $DUMP_FILE" >&2
  exit 1
fi

echo "About to restore $DUMP_FILE into database 'ebm_db' on host 'postgres'."
echo "This will DROP and recreate all objects currently in that database."
printf "Type 'yes' to continue: "
read -r confirm
if [ "$confirm" != "yes" ]; then
  echo "Aborted."
  exit 1
fi

PGPASSWORD="$POSTGRES_PASSWORD" pg_restore \
  -h postgres -U "$POSTGRES_USER" -d ebm_db \
  --clean --if-exists --no-owner --no-privileges \
  "$DUMP_FILE"

echo "Restore complete."
