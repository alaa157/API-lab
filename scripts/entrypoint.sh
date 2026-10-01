#!/bin/sh
# Boot: best-effort migrations, then exec the API.
# Migrations are bounded by a timeout and never block boot: if the DB is
# unreachable the API still starts and reports it honestly via /healthz.
set -e
if [ -f alembic.ini ]; then
  echo "running migrations..."
  if timeout 30 alembic upgrade head; then
    echo "migrations done"
  else
    echo "WARNING: migrations failed or timed out; starting anyway (see /healthz)"
  fi
fi
exec "$@"
