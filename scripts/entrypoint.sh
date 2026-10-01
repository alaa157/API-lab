#!/bin/sh
# Boot: migrations, then exec the API.
# Dev/test: best-effort (warn and continue). Production: fail fast — a prod
# API must never serve against a stale schema.
set -e
MIGRATE_STATUS=0
if [ -f alembic.ini ]; then
  echo "running migrations..."
  if timeout 30 alembic upgrade head; then
    echo "migrations done"
  else
    MIGRATE_STATUS=$?
    echo "WARNING: migrations failed or timed out (status $MIGRATE_STATUS)"
  fi
fi
if [ "$ENVIRONMENT" = "production" ] && [ "$MIGRATE_STATUS" -ne 0 ]; then
  echo "ERROR: refusing to boot production with an unmigrated schema" >&2
  exit 1
fi
exec "$@"
