#!/bin/sh
# Runs schema migrations exactly once before uvicorn spawns its worker
# processes — running `alembic upgrade head` from each of the 2 workers
# independently would race on the alembic_version table.
set -e

echo "Running database migrations..."
alembic upgrade head

# The restricted ebm_app role (see .../create_restricted_app_role.py) is
# created by that migration without a password — set/rotate it here from
# an env var rather than embedding a secret in a version-controlled
# migration file. Idempotent: safe to run on every container start.
if [ -n "$APP_DB_PASSWORD" ]; then
  echo "Setting app role password..."
  PGPASSWORD="$POSTGRES_PASSWORD" psql -h postgres -U "$POSTGRES_USER" -d ebm_db \
    -c "ALTER ROLE ebm_app WITH PASSWORD '$APP_DB_PASSWORD'"
else
  echo "WARNING: APP_DB_PASSWORD not set — ebm_app role password unchanged." >&2
fi

echo "Starting application server..."
exec uvicorn src.app.main:app --host 0.0.0.0 --port 8000 --workers 2 --log-level info
