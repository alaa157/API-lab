# Booking API — Implementation Plan

> Goal: a production-quality REST API (auth, validation, tests, docs) for a
> bookings domain that a hiring team could take over. Stack: **FastAPI**,
> **Postgres (Docker) + Alembic**, **JWT + RBAC**.

## 0. Decisions (locked)

| Area | Choice | Why |
|---|---|---|
| Framework | FastAPI + Pydantic v2 | Hiring velocity, auto OpenAPI |
| ORM / driver | SQLAlchemy 2.0 + asyncpg | Async, standard for FastAPI |
| Migrations | Alembic (sync `psycopg` URL) | Async engine can't run Alembic directly |
| Auth | JWT access (15m) + rotating refresh (7d), argon2 hashing via `pwdlib` | Stateless API standard; argon2 avoids bcrypt 72-byte issues on Py 3.14 |
| Roles | `admin`, `staff`, `customer` | Minimal matrix that still shows permission edge cases |
| Errors | RFC 7807 `application/problem+json` | Consistent, team-takeover-ready |
| Pagination | `?page&per_page`, cap 100, `X-Total-Count` header | Simple, reviewable |
| Idempotency | `Idempotency-Key` header on POST writes, DB table, 24h TTL, replay | Proves safe retries |
| Rate limiting | `slowapi`, 100/min global, 10/min on login | Cheap, Redis-ready later |
| Tests | `pytest + httpx + testcontainers[postgres]` vs real DB | Brief requires real DB |
| Dev setup | `docker compose up --build` + `Makefile` + `.devcontainer` | One-command + Codespaces port-forward 8000 |

## 1. Domain model (bookings)

```
User(id UUID, email UNIQUE, password_hash, role, is_active, created_at)
Resource(id UUID, name, type[room|desk|equipment], capacity, is_active)
Booking(id UUID, user_id FK, resource_id FK, start_at, end_at,
        status[pending|confirmed|cancelled|completed],
        idempotency_key UNIQUE NULL, created_at)
```

Business rules:
1. No overlapping `confirmed` bookings for the same `resource_id`.
   Enforced in service layer + Postgres exclusion constraint (GiST).
2. `end_at > start_at`, max duration 8h, no past `start_at` (validated in schema).
3. State machine: `pending -> confirmed -> completed`, `pending|confirmed -> cancelled`.
   Only `staff`/`admin` can confirm; owners can cancel own; staff/admin can cancel any.
4. Soft rules: inactive users/resources can't book.

## 2. API surface (v1, prefix `/api/v1`)

### Auth
| Method + path | Auth | Body | Success |
|---|---|---|---|
| `POST /auth/register` | public, rate-limited | `{email, password>=8, role?=customer}` | `201 {user}` (role forced to `customer` unless admin token) |
| `POST /auth/login` | public, rate-limited 10/min | `{username=email, password}` (OAuth2 form) | `200 {access_token, refresh_token, token_type}` |
| `POST /auth/refresh` | refresh token | `{refresh_token}` | `200 {access_token, refresh_token}` (rotation; old revoked) |
| `POST /auth/logout` | access token | `{refresh_token}` | `204` (revoke) |
| `GET /me` | access token | — | `200 {user}` |

### Resources
| Method + path | Auth | Notes |
|---|---|---|
| `GET /resources` | any authed user | paginated, `?type&is_active&q` |
| `GET /resources/{id}` | any authed user | 404 problem+json |
| `POST /resources` | `admin` | 403 otherwise |
| `PATCH /resources/{id}` | `admin` | partial update |
| `DELETE /resources/{id}` | `admin` | soft-deactivate (`is_active=false`), 409 if future confirmed bookings |

### Bookings
| Method + path | Auth | Notes |
|---|---|---|
| `POST /bookings` | authed; `Idempotency-Key` optional | customers book for self; staff/admin may pass `user_id`; 409 on overlap; replay on same key |
| `GET /bookings?resource_id&from&to&status&page&per_page` | authed | customers see own only; staff/admin see all + `?user_id` filter |
| `GET /bookings/{id}` | owner/staff/admin | 403 otherwise |
| `POST /bookings/{id}/confirm` | staff/admin | 409 if overlapping confirmed meanwhile |
| `POST /bookings/{id}/cancel` | owner/staff/admin | idempotent cancel; 409 if already completed |
| `GET /healthz` | public | `{status, db}` |
| `GET /openapi.json`, `/docs` | public | generated spec, committed to repo |

### Permission matrix (tests must prove each cell)
| Action | customer | staff | admin | anon |
|---|---|---|---|---|
| register/login | ✅ | ✅ | ✅ | ✅ |
| `GET /me` | ✅ | ✅ | ✅ | 401 |
| create booking for self | ✅ | ✅ | ✅ | 401 |
| create booking for others | 403 | ✅ | ✅ | 401 |
| confirm any booking | 403 | ✅ | ✅ | 401 |
| cancel own booking | ✅ | ✅ | ✅ | 401 |
| cancel others booking | 403 | ✅ | ✅ | 401 |
| list all bookings | own only | ✅ | ✅ | 401 |
| manage resources | 403 | 403 | ✅ | 401 |

## 3. Cross-cutting design

### 3.1 Layering
```
router (thin: parse auth, validate, status codes)
  -> service (business rules, transactions, raises domain errors)
    -> repository (SQL only, no business logic)
```
- Routers never touch the DB session directly except via dependency.
- Services own transactions (`async with session.begin()` where multi-step).
- Repositories expose `get/list/create/update` per aggregate.

### 3.2 Error handling
- Single `problem+json` shape: `{type, title, status, detail, errors[]}`.
- Domain exceptions (`NotFoundError`, `ConflictError`, `ForbiddenError`,
  `UnauthorizedError`, `ValidationError`) mapped by one global handler in `app/main.py`.
- Pydantic 422s reshaped into the same envelope.
- `type` values are stable URIs, e.g. `https://api.booking.local/problems/booking-conflict`.

### 3.3 Validation
- Strict split: `*Create/*Update` (input) vs `*Read` (output); `model_config = from_attributes=True`.
- Email: `EmailStr`; password min 8; `end_at > start_at` via `model_validator`.
- Unknown fields rejected where security-relevant (`extra="forbid"` on auth inputs).

### 3.4 Pagination / filtering
- Helper `paginate(query, page, per_page)` returns `(items, total)`; sets `X-Total-Count`.
- `per_page` clamped 1–100, default 20. Datetime filters are ISO-8601 UTC.

### 3.5 Idempotency
- Table `idempotency_keys(key PK, user_id, method, path, status_code, body, created_at)`.
- Middleware/handler in booking creation: if key seen + same user → return stored
  response with `Idempotent-Replayed: true`; different payload → `422`.
- TTL cleanup: nightly delete or `pg_cron`-style; v1 = lazy expiry on read + startup sweep.

### 3.6 Rate limiting
- `slowapi` limiter on app state; in-memory for dev; key = client IP + route.
- Login/refresh/register: 10/min; global default 100/min. 429 uses problem+json.

### 3.7 Security checklist
- argon2id hashing; never log passwords/tokens; JWT secret from env, HS256.
- CORS locked to configured origins; security headers middleware.
- Refresh tokens stored hashed + revoked on reuse (reuse = revoke chain, 401).
- SQL injection: ORM-bound params only; mass-assignment guarded by explicit schemas.

## 4. Data + migrations

- `DATABASE_URL` (async, `postgresql+asyncpg://…`) for app; `SYNC_DATABASE_URL`
  (`postgresql+psycopg://…`) for Alembic.
- Alembic autogenerate reviewed by hand; each migration has upgrade+ downgrade.
- Seed script (`scripts/seed.py`): admin + staff + demo customer, 3 resources.
  Only for dev, gated by `ENVIRONMENT=development`.

## 5. One-command dev setup

```
cp .env.example .env
docker compose up --build     # api :8000, db :5432, runs migrations on boot
make migrate | make seed | make test | make docs
```

- `Dockerfile`: `python:3.12-slim`, installs `requirements.txt`, runs uvicorn.
- `.devcontainer/devcontainer.json`: Python + Docker features, forward 8000, `postCreateCommand: pip install -r requirements.txt`.
- `openapi.json` regenerated via `make docs` (`python scripts/export_openapi.py`) and committed.

## 6. Test strategy (real DB)

- `tests/conftest.py`: session-scoped `testcontainers.postgres.PostgresContainer`,
  runs Alembic `upgrade head`, yields `AsyncSession` + `httpx.ASGITransport` client.
  Fallback: `TEST_DATABASE_URL` env (Compose DB) when Docker unavailable.
- Suites:
  - `test_health.py`: `/healthz`, openapi served.
  - `test_auth.py`: register ok/duplicate 409/weak-pw 422; login ok/wrong-pw 401/
    inactive 403; refresh rotation + reuse 401; logout revokes; expired token 401.
  - `test_permissions.py`: full RBAC matrix (§2), incl. anon 401s.
  - `test_bookings.py`: create ok; overlap 409; end<start 422; past start 422;
    cancel own 200 / others 403 / double-cancel idempotent; confirm RBAC;
    pagination headers + filters.
  - `test_idempotency.py`: same key → same id + `Idempotent-Replayed`; different
    payload same key → 422; different key → two rows.
- Target: >85% coverage on `services/` + `api/`; `pytest --cov` enforced in CI.
- Load note (not load test): overlap check benchmarked with 1k conflicting inserts in dev.

## 7. Docs deliverables

1. `README.md`: 60-second quickstart, endpoints table, auth example with curl,
   test + migrate commands, Codespaces button/forward note.
2. `ARCHITECTURE.md`: layering diagram, why problem+json, why DB idempotency keys,
   why exclusion constraint + service check (race safety), auth/token rationale.
3. `openapi.json`: committed generated spec; `/docs` screenshot optional.
4. This file (`IMPLEMENTATION_PLAN.md`): the build contract.

## 8. Build phases + acceptance

- **Phase 0 — Skeleton (done when `docker compose up` serves `/healthz`):**
  compose, Dockerfile, Makefile, devcontainer, `.env.example`, `.gitignore`,
  `requirements.txt`, app factory, health route, global error handler, config.
- **Phase 1 — Auth + users (done when matrix auth rows pass):**
  User model + migration, register/login/refresh/logout, `get_current_user`,
  `require_role`, password hashing, rate limits on auth, `test_auth.py` green.
- **Phase 2 — Resources + bookings (done when overlap + state machine proven):**
  models + migrations + exclusion constraint, repositories/services/routers,
  idempotency, pagination, `test_bookings + test_permissions + test_idempotency` green.
- **Phase 3 — Hardening + docs (done when deliverables checklist ticks):**
  seed script, `make docs`, README/ARCHITECTURE, coverage gate, `openapi.json`
  committed, Codespaces forward verified.

## 9. Risks / open questions

1. Exclusion constraint needs `btree_gist` extension — migration must `CREATE EXTENSION IF NOT EXISTS btree_gist`. Fallback: service-only check + unique partial index (weaker, documented).
2. Testcontainers needs Docker in CI — fallback to service-container Postgres via `TEST_DATABASE_URL`.
3. Pydantic `EmailStr` needs `email-validator` — pinned in requirements.
4. Clock skew on JWT expiry in tests — use freezegun-style tolerances, short leeway 10s.

## 10. Definition of done

- [ ] `docker compose up --build` → `/docs` renders, `/healthz` 200.
- [ ] `make test` green against real Postgres, incl. all §6 suites.
- [ ] `openapi.json` committed and matches served spec.
- [ ] README + ARCHITECTURE explain setup, layering, error decisions.
- [ ] No secrets committed; `.env` gitignored; CI runs migrate + tests.
