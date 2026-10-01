#!/bin/sh
# Phase 0 entrypoint: run migrations when alembic lands (Phase 1), then exec.
set -e
if [ -f alembic.ini ]; then
  alembic upgrade head
fi
exec "$@"
