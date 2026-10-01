# Phase 4 — Production readiness (8.5 → 9.5)

> Goal: close the production gaps without changing the foundation.
> Five workstreams, each independently shippable, each with acceptance
> criteria a fresh agent can verify with commands. Non-goals at the bottom.

## 0. Locked decisions

| Area | Choice | Why |
|---|---|---|
| Completion | `POST /bookings/{id}/complete`, staff/admin, idempotent | Endpoint (not worker): synchronous, testable, no new infra; no schema change needed (`completed` already in the enum) |
| Rate limits | `slowapi` + Redis backend, memory fallback | Keeps the existing decorator API; memory when `REDIS_URL` unset (dev/test) |
| Logging | `structlog`, JSON in production, console in dev | One structured record per request; no new service to run |
| Request ID | middleware generates/propagates `X-Request-ID` | Correlates logs; echoed on responses |
| Metrics | `prometheus-fastapi-instrumentator`, public `GET /metrics` | Zero-config RED method metrics; tracing deferred (see non-goals) |
| Lifespan | replace `@app.on_event("startup")` with `lifespan=` | `on_event` is deprecated in current FastAPI |
| Migrations on boot | dev: warn-and-continue (current); prod: fail fast | A prod API must never serve against a stale schema |
| Concurrency proof | `tests/test_concurrency.py`, 10 concurrent actors | Proves the exclusion constraint + idempotency under race; doubles as load evidence |
| Backups | documented `pg_dump`/`pg_restore` runbook only | No automation to operate yet; cron example included |
| Secrets | env-only, `openssl rand -hex 32` (JWT), `-hex 16` (redis pw) | Same fail-fast pattern as `JWT_SECRET_KEY` |

## 1. Config additions (`app/core/config.py`)

Add fields (all optional except where noted):

| Field | Default | Notes |
|---|---|---|
| `redis_url: str \| None` | `None` | When set, rate-limit storage moves to Redis |
| `redis_password: str \| None` | `None` | Interpolated into prod compose only |
| `log_level: str` | `"INFO"` | Passed to structlog + uvicorn |
| `log_format: str` | `"auto"` | `auto` = JSON unless `environment == "development"` |

Rules: `redis_url` unset → memory storage + a startup log line saying so.
`ENVIRONMENT=production` + missing `JWT_SECRET_KEY` already fails fast (keep).

## 2. Workstream 4.0 — Lifespan, logging, request ID, metrics

**Files:**
- EDIT `app/main.py`: replace `@app.on_event("startup")` with
  `@asynccontextmanager async def lifespan(app)` passed as `lifespan=lifespan`
  to `FastAPI(...)`. Lifespan body = current `_sweep_idempotency_keys()` call.
- NEW `app/core/logging.py`:
  - `configure_logging(settings) -> None` — structlog with JSON renderer
    unless dev (console renderer), level from `settings.log_level`.
  - `bind_request_id` middleware (pure Starlette): reuse incoming
    `X-Request-ID` or generate `uuid4().hex`; set on `request.state`,
    echo on response, `structlog.contextvars.bind_contextvars(request_id=...)`,
    clear after response.
- EDIT `app/main.py`: call `configure_logging()` first line of `create_app()`;
  add the request-ID middleware; add
  `Instrumentator().instrument(app).expose(app, endpoint="/metrics",
  include_in_schema=False)`.
- EDIT `requirements.txt`: add `structlog==<pin-at-implementation>` and
  `prometheus-fastapi-instrumentator==<pin-at-implementation>` (install,
  then `pip freeze` the exact versions; repo pins `==`).

**Tests** (`tests/test_observability.py`):
- `GET /` echoes the incoming `X-Request-ID`; generates one when absent.
- `GET /metrics` returns 200 with `http_requests_total` after one API call.
- structlog emits one JSON line per request containing `request_id`, `method`,
  `path`, `status_code`, `duration_ms` (assert via `caplog` JSON parsing —
  configure structlog to stdlib in the test process if needed).

**Accept:** new tests green; `make test` still ≥85%; `/metrics` visible in
compose `/docs` is untouched (endpoint excluded from schema).

## 3. Workstream 4.1 — Completion endpoint

No migration (enum already has `completed`).

**Files:**
- EDIT `app/services/bookings.py`: add
  `async def complete(session, booking) -> Booking`:
  - `cancelled` → `ConflictError("cancelled bookings cannot be completed")`
  - `completed` → return as-is (idempotent)
  - `pending` → `ConflictError("only confirmed bookings can be completed")`
  - `confirmed` → set `completed`, commit, refresh. (No overlap check:
    the booking already holds its window.)
- EDIT `app/api/v1/bookings.py`: add
  `POST /{booking_id}/complete` → `require_role(staff, admin)`,
  `get_visible` then `service.complete`, response `BookingRead`.
- `openapi.json` regen via `make docs`.

**Tests** (`tests/test_completion.py`):
- staff completes a confirmed booking → 200 `completed`; repeat → 200
  (idempotent).
- customer attempts → 403; anon → 401; unknown id → 404.
- completing `pending` → 409; completing `cancelled` → 409.
- cancelling a `completed` booking → 409 (already covered; keep as regression).

**Accept:** matrix green; permission table in README gains a `complete` row.

## 4. Workstream 4.2 — Redis-backed rate limits

**Files:**
- EDIT `app/core/rate.py`: add
  `def build_limiter(settings) -> Limiter` returning
  `Limiter(key_func=get_remote_address, default_limits=["100/minute"],
  storage_uri=settings.redis_url or "memory://")`.
  Module-level `limiter = build_limiter(get_settings())` stays (same import
  surface for routers/`main.py`).
- EDIT `app/main.py`: on startup, log one line naming the storage backend
  (`memory` vs `redis`) at INFO.
- EDIT `docker-compose.yml`: add `redis` service (`redis:7-alpine`,
  `command: ["redis-server", "--appendonly", "yes"]`, no published port by
  default); api `depends_on` unchanged (Redis absence must not block boot —
  memory fallback covers it).
- EDIT `.env.example`: add commented
  `# REDIS_URL=redis://redis:6379/0` (commented = memory fallback in dev).

**Tests** (`tests/test_rate_storage.py`, no Redis needed):
- testing env → `build_limiter` uses memory storage.
- production-like settings object with `redis_url` set → storage URI is
  the Redis URL (construct `Settings(redis_url=..., environment="staging")`
  directly; do not mutate the cached global).

**Accept:** suite green with no Redis running (proves fallback); manual
check with Redis up shows shared counting across two workers
(`docker compose up --scale api=2` is out of scope — note as manual).

## 5. Workstream 4.3 — Concurrency proof

**File:** NEW `tests/test_concurrency.py` (runs in the normal suite; 10
actors max so the default async pool of 15 is never exhausted):

- `test_concurrent_confirms_single_winner`: 2 users, same resource,
  10 overlapping pending bookings via `asyncio.gather`; confirm all 10
  concurrently as staff → exactly 1 `confirmed`, 9 `409`. Asserts the
  exclusion constraint decides, not the service check.
- `test_concurrent_same_key_single_booking`: 10 concurrent identical
  `POST /bookings` with one `Idempotency-Key` → exactly 1 booking row;
  all responses 201 with the same `id` (replay or winner — either is
  correct, count rows via list endpoint `X-Total-Count == 1`).
- Both tests log wall time; keep an eye on duration (informational only,
  no timing assertions — no flaky gates).

**Accept:** green three consecutive runs (`pytest tests/test_concurrency.py`
× 3); total coverage stays ≥85% (these paths may incidentally cover the
`IntegrityError` branches — accept either way, do not chase 100%).

## 6. Workstream 4.4 — Prod deploy story

**Files:**
- NEW `docker-compose.prod.yml` (extends base, no rebuild of app logic):
  api without bind mount / `--reload`, `restart: unless-stopped`,
  `ENVIRONMENT=production`, Redis with
  `command: ["redis-server", "--appendonly", "yes",
  "--requirepass", "${REDIS_PASSWORD:?Set REDIS_PASSWORD}"]`,
  no published Postgres port.
- EDIT `scripts/entrypoint.sh`: after the migrate attempt,
  `if [ "$ENVIRONMENT" = "production" ]` and migrate failed → `exit 1`
  (fail fast). Dev behavior unchanged. Track migrate exit code in a var.
- NEW `docs/DEPLOYMENT.md`:
  - release flow: `build → push image → run one-off migrate →
    deploy → verify /healthz + /metrics`;
  - the exact one-off migrate command
    (`docker compose run --rm api alembic upgrade head`);
  - secrets table (JWT ≥32 chars, redis password, never commit `.env`);
  - backup/restore runbook (`pg_dump -Fc` / `pg_restore`) + cron example;
  - rollback = redeploy previous image + `alembic downgrade -1` (with the
    warning that downgrades after data-migrating revisions need review).
- EDIT `README.md`: ops paragraph linking `docs/DEPLOYMENT.md`
  (2–3 lines max; README stays quickstart-focused).

**Accept:** `docker compose -f docker-compose.yml -f docker-compose.prod.yml
config` validates; boot with `ENVIRONMENT=production` and a bad DB URL exits
non-zero before serving (verify once manually against a stopped db).

## 7. Order of work

4.0 → 4.1 → 4.2 → 4.3 (needs 4.1) → 4.4. Each lands as its own commit
(`feat: ...`), each followed by `make test`, `make docs`, and an
`openapi.json` + spec-diff check (`git diff --exit-code openapi.json`
must only show intended route additions).

## 8. Definition of done

- [ ] `make test` green, coverage gate ≥85% holds
- [ ] `openapi.json` regenerated, committed, matches served spec
- [ ] README permission matrix + ops paragraph current
- [ ] ARCHITECTURE.md gains: logging/metrics paragraph, Redis paragraph,
      completion in the lifecycle diagram + text
- [ ] `docs/diagrams/*.d2` updated (`booking-state` shows complete path
      from confirmed; add `redis` + `prometheus` to a deploy diagram —
      NEW `docs/diagrams/deploy.d2`/`.svg`, embedded in DEPLOYMENT.md)
- [ ] CI green on the branch (migrate + tests + spec check)
- [ ] No secrets committed; `.env` still gitignored

## 9. Non-goals (with reasons)

- OpenTelemetry tracing — needs a collector to run; metrics + request IDs
  cover portfolio-grade observability without new infra.
- Automated backup sidecars — runbook first; automate when a real host exists.
- Admin user-management endpoints — valuable but a separate feature phase
  (new RBAC surface, new tests), not production-readiness.
- Email/password-reset flows — out of scope for a bookings portfolio API.
- Multi-instance deploy target (K8s/fly.io) — document the single-host
  compose path; revisit when traffic demands it.
