# Booking API — Design Spec

- **Date:** 2026-10-01
- **Status:** Awaiting user review
- **Approach:** A — Portfolio-vertical
- **Stack:** FastAPI + Pydantic v2, SQLAlchemy 2.0 (asyncpg), Alembic, PyJWT, pwdlib[argon2], slowapi, pytest + httpx + testcontainers, Postgres 16, Docker Compose
- **Related plan (premature, needs re-issue after spec approval):** `docs/superpowers/plans/2026-10-01-booking-api.md`

## 1. Outcome and success criteria

Build a production-quality bookings REST API in `alaa157/API-lab` that a hiring
reviewer can take over: one-command boot, consistent errors, real JWT auth with
role-based permissions, validated inputs, pagination, idempotency keys on writes,
rate limiting, integration tests against a real database, and generated OpenAPI
docs. Success means `docker compose up --build` serves `/docs`, `make test` is
green on real Postgres (including auth and permission edge cases), `openapi.json`
is committed and matches the served spec, and README + architecture note explain
setup, layering, and error-handling decisions.

## 2. Constraints

- Python 3.12+, pinned dependencies; Postgres 16 in Docker; tests never use
  sqlite as a Postgres stand-in (testcontainers locally, `TEST_DATABASE_URL`
  fallback in CI).
- JWT access tokens (15 min) + rotating refresh tokens (7 days); argon2id
  password hashing; secrets from environment only; passwords and tokens never
  logged.
- All error responses use `application/problem+json` with
  `{type, title, status, detail, errors[]}`; Pydantic validation failures are
  reshaped into the same envelope.
- List endpoints paginate with `?page&per_page` (default 20, cap 100) and
  expose `X-Total-Count`.
- `POST /bookings` honors `Idempotency-Key`: same key + user + payload replays
  the stored response with `Idempotent-Replayed: true`; same key with a
  different payload returns 422.
- Rate limits: 100/min global, 10/min on auth routes, 429 in problem+json.
- TDD: no production code without a failing test watched failing first; each
  task ends with a testable deliverable and a commit.
- Out of scope for this spec: Redis-backed limits, CI pipeline, log
  aggregation, load testing (deferred by Approach A, not abandoned).

## 3. Architecture and components

Layered FastAPI app. Thin routers (`app/api/v1/*.py`) parse auth and validate
I/O; services (`app/services/*.py`) own business rules and transactions;
repositories (`app/repositories/*.py`) do SQL only — routers never touch the
database session directly. Shared kernel in `app/core/` (settings, security,
errors, pagination, idempotency, rate limiting); storage models in
`app/models/`; strict input/output schemas in `app/schemas/` (`*Create` /
`*Update` vs `*Read`). Auth plumbing (`get_current_user`, `require_role`)
lives in `app/api/deps.py`. Database evolution via Alembic migrations; one
command (`docker compose up --build` plus `Makefile` targets) boots API +
Postgres, runs migrations, seeds dev data, runs tests, and exports the spec.

## 4. Domain and data flow

Aggregates: `User` (email unique, password hash, role
`admin|staff|customer`, active flag), `Resource` (name, type
`room|desk|equipment`, capacity, active flag), `Booking` (user, resource,
`start_at`/`end_at`, status `pending|confirmed|cancelled|completed`,
idempotency key unique). Core invariant: no overlapping `confirmed` bookings
per resource, enforced by a service check plus a Postgres exclusion constraint
(`btree_gist`) so concurrent confirms cannot double-book. Booking validation:
`end_at > start_at`, no past starts, maximum duration 8 hours.

Auth flow: register forces role `customer` unless the caller is admin; login
issues access + refresh; refresh rotates (old token revoked, reuse revokes the
chain with 401); logout revokes. Booking flow: create validates times, checks
the idempotency key, checks overlap, and inserts `pending`; confirm
(staff/admin only) re-checks overlap inside the transaction; cancel
(owner/staff/admin) is idempotent; `completed` is terminal. Reads are
owner-scoped for customers and full (plus `?user_id`) for staff/admin.

## 5. Error handling

A single global handler maps domain errors (`NotFoundError`, `ConflictError`,
`ForbiddenError`, `UnauthorizedError`) to problem+json with stable `type`
URIs (for example `https://api.booking.local/problems/booking-conflict` on overlap). Unknown routes,
auth failures, rate-limit hits, and validation failures all share the envelope;
validation failures carry per-field entries. Responses and logs never contain
passwords, tokens, or tracebacks.

## 6. Testing and documentation

Suite layout: `test_health`, `test_auth` (register, duplicate 409, weak
password 422, wrong-password 401, anonymous 401, refresh rotation and
reuse-revocation, logout), `test_permissions` (every cell of the RBAC matrix,
including anonymous callers), `test_bookings` (overlap 409, time-rule 422s,
pagination headers and filters), `test_idempotency` (replay, mismatch 422,
distinct keys), `test_openapi` (served spec equals committed file, every route
documented). Coverage gate: at least 85% over services and API layers.
Deliverables: quickstart README with curl auth examples, an architecture note
covering layering, error, idempotency, and race-safety decisions, and a
committed `openapi.json` behind `make docs`.
