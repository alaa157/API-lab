# Deployment

Single-host Docker Compose is the deploy target. `docker-compose.yml` is the
dev base; `docker-compose.prod.yml` is a production overlay (no bind mount,
no `--reload`, `restart: unless-stopped`, `ENVIRONMENT=production`,
Redis-backed rate limits, Postgres port unpublished):

![Production deploy topology](diagrams/deploy.svg)

## Release flow

```bash
# 1. build + push (or build on the host)
docker compose -f docker-compose.yml -f docker-compose.prod.yml build

# 2. one-off migrate (never rely on boot-time migrate in prod)
docker compose -f docker-compose.yml -f docker-compose.prod.yml run --rm api \
  alembic upgrade head

# 3. deploy
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d

# 4. verify
curl localhost:8000/healthz   # {"status":"ok","db":"up"}
curl localhost:8000/metrics   # http_requests_total, ...
```

Boot-time migrate still runs on container start, but in production it is
fail-fast: if migrations fail, the container exits `1` before serving
(`scripts/entrypoint.sh`). In dev it warns and continues, and `/healthz`
reports `{"status":"degraded","db":"down"}`.

## Secrets

| Secret | Generate | Notes |
|---|---|---|
| `JWT_SECRET_KEY` | `openssl rand -hex 32` | ≥32 chars; missing value fails fast at startup |
| `REDIS_PASSWORD` | `openssl rand -hex 16` | Redis `--requirepass`; interpolated into `REDIS_URL` by compose |

Never commit `.env` (gitignored). Both variables use `:?` interpolation, so
compose refuses to start without them.

## Backup / restore

Postgres data lives in the `postgres-data` volume; Redis AOF in `redis-data`.
Back up Postgres with `pg_dump` (custom format) and test restores:

```bash
# backup (run on the host, db container must be up)
docker compose exec db pg_dump -U booking -Fc bookingdb > backup-$(date +%F).dump

# restore into a scratch database to verify
docker compose exec db createdb -U booking restore_check
cat backup-*.dump | docker compose exec -T db pg_restore -U booking -d restore_check
docker compose exec db psql -U booking -c "select count(*) from bookings;" restore_check
docker compose exec db dropdb -U booking restore_check
```

Nightly cron example (host crontab, keeps 7 days):

```cron
0 3 * * * cd /srv/api-lab && docker compose exec -T db pg_dump -U booking -Fc bookingdb > /var/backups/api-lab/backup-$(date +\%F).dump && find /var/backups/api-lab -name 'backup-*.dump' -mtime +7 -delete
```

## Rollback

1. Redeploy the previous image (re-tag or `git checkout` the previous compose files and `up -d --build`).
2. Downgrade the schema **one revision**: `docker compose run --rm api alembic downgrade -1`.

Warning: downgrades after data-migrating revisions need review — a
downgrade can drop columns/tables holding live data. When in doubt, roll the
code forward instead.
