# Booking API Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a production-quality FastAPI bookings REST API with JWT+RBAC auth, validation, pagination, idempotency, rate limiting, real-DB tests, and generated OpenAPI docs, runnable via one command in Codespaces.

**Architecture:** Thin routers → services (business rules + transactions) → repositories (SQL only). One global handler renders domain errors as RFC 7807 problem+json. No overlapping confirmed bookings enforced by service check + Postgres exclusion constraint.

**Tech Stack:** FastAPI + Pydantic v2 + SQLAlchemy 2.0 (asyncpg) + Alembic (psycopg sync URL) + PyJWT + pwdlib[argon2] + slowapi + pytest + httpx + testcontainers[postgres] + Docker Compose + Postgres 16.

**Spec:** `docs/superpowers/specs/2026-10-01-booking-api-design.md` (approved 2026-10-01; Approach A). Supplemental notes in `IMPLEMENTATION_PLAN.md`. Executors read both. Traceability IDs (REQ-/TASK-/TEST-/FILE-) are borrowed from `create-implementation-plan` for machine readability.

## Global Constraints

- **REQ-001:** Python >= 3.12; pinned requirements, no unpinned deps.
- **REQ-002:** Postgres 16 only; tests run against a REAL database (testcontainers or `TEST_DATABASE_URL`), never sqlite-as-postgres.
- **REQ-003 (TDD Iron Law):** No production code without a failing test first; every test must be watched failing for the expected reason before GREEN.
- **REQ-004:** JWT access 15 min + rotating refresh 7 days; argon2id hashing; secrets from env only; never log passwords/tokens.
- **REQ-005:** All errors returned as `application/problem+json` `{type, title, status, detail, errors[]}`; Pydantic 422s reshaped into the same envelope.
- **REQ-006:** `per_page` clamped 1–100 default 20; `X-Total-Count` header on all list endpoints.
- **REQ-007:** `Idempotency-Key` on `POST /bookings`; same key+user+payload replays stored response with `Idempotent-Replayed: true`; same key different payload → 422.
- **REQ-008:** Rate limits: 100/min global, 10/min on auth routes; 429 uses problem+json.
- **REQ-009:** Each task ends with an independently testable deliverable + a commit (`feat:`/`test:`/`chore:` prefix).
- **REQ-010:** No secrets committed; `.env` gitignored; `openapi.json` regenerated via `make docs` and committed.

## Review Focus

1. Overlapping-booking race: two concurrent confirms for the same resource/time — second must 409, not double-book (service check + exclusion constraint).
2. Refresh-token reuse after rotation — must revoke the chain and return 401, not issue new tokens.
3. Idempotency-key mismatch (same key, different payload/user) — must 422, never silently create a second booking.
4. Customer enumerating others' bookings via `GET /bookings/{id}` or list filters — must 403/own-only, not leak.
5. Timezone-naive datetimes or `end_at <= start_at` slipping past validation — must 422 with field-level `errors[]`.

---

### Task 1: Phase 0 skeleton — Compose, Dockerfile, Makefile, config, health, errors

**Files:**
- Create: `requirements.txt`, `Dockerfile`, `docker-compose.yml`, `Makefile`, `.devcontainer/devcontainer.json`, `.env.example`, `app/__init__.py`, `app/main.py`, `app/core/config.py`, `app/core/errors.py`, `app/api/v1/health.py`, `tests/test_health.py`
- Test: `tests/test_health.py`

**Interfaces:**
- Consumes: nothing (first task).
- Produces: `create_app() -> FastAPI` in `app/main.py`; `get_settings() -> Settings` in `app/core/config.py`; problem+json handler `problem_handler(request, exc)`; `GET /healthz -> {status, db}` (db check stubbed to `{"db": "ok"}` until Task 4 wires Postgres).

- [ ] **Step 1: Write the failing test** — health + error envelope shape:

```python
def test_healthz_returns_ok(client):
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"

def test_unknown_route_uses_problem_json(client):
    r = client.get("/nope")
    assert r.status_code == 404
    body = r.json()
    assert set(["type", "title", "status", "detail"]).issubset(body)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_health.py -v`
Expected: FAIL with "app.main not defined / 404 plain detail" (feature missing, not typo).

- [ ] **Step 3: Implement `create_app()` in `app/main.py` + `Settings` in `app/core/config.py` + `problem_handler` in `app/core/errors.py` + `GET /healthz`**

Minimal: FastAPI factory, CORS from settings, global handler mapping `DomainError` + 404/422 to problem+json, health router. Docker files per Spec §5.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_health.py -v` then full `pytest -q`
Expected: PASS, no other tests exist yet.

- [ ] **Step 5: Commit**

```bash
git add requirements.txt Dockerfile docker-compose.yml Makefile .devcontainer/devcontainer.json .env.example app tests/test_health.py
git commit -m "feat: phase 0 skeleton with health and problem+json errors"
```

---

### Task 2: Auth — register/login/me/refresh/logout + RBAC deps

**Files:**
- Create: `app/models/user.py`, `app/schemas/auth.py`, `app/schemas/user.py`, `app/core/security.py`, `app/api/deps.py`, `app/api/v1/auth.py`, `app/services/auth_service.py`, `alembic/versions/0001_users.py`, `tests/test_auth.py`
- Modify: `app/main.py` (mount auth router, rate limiter), `requirements.txt` (PyJWT, pwdlib, slowapi, email-validator)
- Test: `tests/test_auth.py`

**Interfaces:**
- Consumes: `get_settings()`, `problem_handler`, `GET /healthz` from Task 1.
- Produces: `hash_password(pw: str) -> str`, `verify_password(pw, hash) -> bool`, `create_tokens(user) -> TokenPair`, `get_current_user() -> UserRead`, `require_role(*roles)` dependency; `POST /auth/register|login|refresh|logout`, `GET /me`.

- [ ] **Step 1: Write the failing tests** (one behavior each; register ok + duplicate 409 + weak-pw 422 + login ok + wrong-pw 401 + me 401-anon + refresh rotation + refresh-reuse 401 + logout revokes):

```python
def test_register_and_login_flow(client): ...
def test_register_duplicate_email_returns_409(client): ...
def test_register_weak_password_returns_422(client): ...
def test_login_wrong_password_returns_401(client): ...
def test_me_without_token_returns_401(client): ...
def test_refresh_rotation_and_reuse_revokes(client): ...
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_auth.py -v`
Expected: FAIL with "404 /auth/register" (routes missing).

- [ ] **Step 3: Implement `User` model + migration + `security.py` (argon2, HS256 JWT, hashed refresh store) + `auth_service.py` + auth router + `deps.py`**

Role on register forced to `customer` unless caller is admin. Refresh tokens stored hashed with `revoked_at`; reuse revokes chain.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_auth.py -v` then `pytest -q`
Expected: PASS, health still green.

- [ ] **Step 5: Commit**

```bash
git add app tests/test_auth.py alembic/versions/0001_users.py
git commit -m "feat: JWT auth with refresh rotation and RBAC deps"
```

---

### Task 3: Resources CRUD (admin-only writes)

**Files:**
- Create: `app/models/resource.py`, `app/schemas/resource.py`, `app/repositories/resource_repo.py`, `app/services/resource_service.py`, `app/api/v1/resources.py`, `alembic/versions/0002_resources.py`, `tests/test_resources.py`
- Modify: `app/main.py` (mount resources router)
- Test: `tests/test_resources.py`

**Interfaces:**
- Consumes: `get_current_user()`, `require_role()` from Task 2.
- Produces: `ResourceService.list/create/update/deactivate()`; `GET/POST /resources`, `GET/PATCH/DELETE /resources/{id}` (DELETE = soft-deactivate, 409 if future confirmed bookings exist — checked against bookings table stubbed until Task 4, then enforced).

- [ ] **Step 1: Write the failing tests**

```python
def test_admin_can_create_resource(admin_client): ...
def test_customer_create_resource_returns_403(customer_client): ...
def test_anon_list_resources_returns_401(client): ...
def test_delete_deactivates_not_hard_deletes(admin_client): ...
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_resources.py -v`
Expected: FAIL with "404 /resources".

- [ ] **Step 3: Implement model + migration + repo + service + router** with `require_role("admin")` on writes, paginated list with `X-Total-Count`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_resources.py -v` then `pytest -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app tests/test_resources.py alembic/versions/0002_resources.py
git commit -m "feat: resources CRUD with admin-only writes"
```

---

### Task 4: Bookings — create/list/get with overlap guard + pagination

**Files:**
- Create: `app/models/booking.py`, `app/schemas/booking.py`, `app/repositories/booking_repo.py`, `app/services/booking_service.py`, `app/api/v1/bookings.py`, `app/core/pagination.py`, `alembic/versions/0003_bookings.py`, `tests/test_bookings.py`
- Modify: `app/main.py` (mount bookings router), `app/services/resource_service.py` (DELETE 409 check now live)
- Test: `tests/test_bookings.py`

**Interfaces:**
- Consumes: auth deps (Task 2), pagination helper new here.
- Produces: `BookingService.create/list/get()`; `POST /bookings`, `GET /bookings`, `GET /bookings/{id}`; `paginate(query, page, per_page) -> (items, total)`; exclusion constraint `no_overlap` (btree_gist) + service overlap check raising `ConflictError(type=.../booking-conflict)`.

- [ ] **Step 1: Write the failing tests**

```python
def test_create_booking_ok(customer_client): ...
def test_overlapping_booking_returns_409(customer_client): ...
def test_end_before_start_returns_422(customer_client): ...
def test_customer_cannot_read_others_booking(customer_client): ...
def test_list_pagination_headers_and_filters(staff_client): ...
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_bookings.py -v`
Expected: FAIL with "404 /bookings".

- [ ] **Step 3: Implement booking model (status enum, FKs) + migration with `CREATE EXTENSION IF NOT EXISTS btree_gist` + repo + service (past-start 422, max 8h, overlap 409) + routers** (customers see own only; staff/admin all + `?user_id`).

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_bookings.py -v` then `pytest -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app tests/test_bookings.py alembic/versions/0003_bookings.py
git commit -m "feat: bookings with overlap guard and pagination"
```

---

### Task 5: Booking state machine + permission matrix

**Files:**
- Modify: `app/services/booking_service.py` (confirm/cancel), `app/api/v1/bookings.py` (`POST /{id}/confirm`, `POST /{id}/cancel`)
- Create: `tests/test_permissions.py`
- Test: `tests/test_permissions.py`

**Interfaces:**
- Consumes: booking create/get from Task 4.
- Produces: `BookingService.confirm/cancel()`; `POST /bookings/{id}/confirm` (staff/admin), `POST /bookings/{id}/cancel` (owner/staff/admin, idempotent).

- [ ] **Step 1: Write the failing tests** — every cell of Spec §2 matrix:

```python
def test_customer_cannot_confirm_returns_403(customer_client): ...
def test_staff_can_confirm(staff_client): ...
def test_customer_cannot_cancel_others_returns_403(customer_client): ...
def test_double_cancel_is_idempotent(customer_client): ...
def test_anon_all_endpoints_401(client): ...
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_permissions.py -v`
Expected: FAIL with "404 confirm/cancel" or "403 expected".

- [ ] **Step 3: Implement confirm/cancel transitions** (`pending→confirmed→completed`, `pending|confirmed→cancelled`; confirm re-checks overlap inside transaction; completed is terminal 409).

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_permissions.py -v` then `pytest -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app tests/test_permissions.py
git commit -m "feat: booking confirm/cancel with RBAC matrix"
```

---

### Task 6: Idempotency keys on booking creation

**Files:**
- Create: `app/models/idempotency.py`, `app/core/idempotency.py`, `alembic/versions/0004_idempotency.py`, `tests/test_idempotency.py`
- Modify: `app/services/booking_service.py`, `app/api/v1/bookings.py` (key handling)
- Test: `tests/test_idempotency.py`

**Interfaces:**
- Consumes: `BookingService.create()` from Task 4.
- Produces: `get_or_replay_key(key, user_id, payload_hash) -> stored response | None`; `Idempotent-Replayed` header behavior.

- [ ] **Step 1: Write the failing tests**

```python
def test_same_key_replays_same_booking(customer_client): ...
def test_same_key_different_payload_returns_422(customer_client): ...
def test_different_keys_create_two_bookings(customer_client): ...
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_idempotency.py -v`
Expected: FAIL (no replay header / duplicate rows created).

- [ ] **Step 3: Implement idempotency table + handler** (store method+path+status+body hash; same user+payload replays; TTL sweep on startup).

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_idempotency.py -v` then `pytest -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app tests/test_idempotency.py alembic/versions/0004_idempotency.py
git commit -m "feat: idempotency keys on booking creation"
```

---

### Task 7: Hardening + docs — seed, OpenAPI export, README/ARCHITECTURE, coverage gate

**Files:**
- Create: `scripts/seed.py`, `scripts/export_openapi.py`, `openapi.json`, `ARCHITECTURE.md`, `tests/test_openapi.py`
- Modify: `README.md`, `Makefile` (test/migrate/seed/docs targets), `.devcontainer/devcontainer.json` (verify forward)
- Test: `tests/test_openapi.py` (spec served == committed file; every route has summary + example)

**Interfaces:**
- Consumes: all routes from Tasks 1–6.
- Produces: `make dev|test|migrate|seed|docs`; dev seed (admin/staff/customer + 3 resources); committed `openapi.json`.

- [ ] **Step 1: Write the failing tests**

```python
def test_openapi_matches_committed_spec(client): ...
def test_every_route_has_summary(client): ...
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_openapi.py -v`
Expected: FAIL (scripts/export_openapi.py missing / openapi.json stale).

- [ ] **Step 3: Implement seed + export scripts + docs** (README 60-second quickstart + curl auth example; ARCHITECTURE layering diagram + why problem+json + why DB keys + race-safety note).

- [ ] **Step 4: Run full verification**

Run: `make test` (must be green on real Postgres) then `pytest --cov=app --cov-fail-under=85 -q`
Expected: PASS; `docker compose up --build` serves `/docs`; forwarded port 8000 verified in Codespaces.

- [ ] **Step 5: Commit**

```bash
git add scripts openapi.json ARCHITECTURE.md README.md Makefile tests/test_openapi.py
git commit -m "chore: seed, openapi export, docs, coverage gate"
```

---

## Appendix A — Traceability (create-implementation-plan IDs)

- **REQ-001..010**: Global Constraints above.
- **TASK-001..007**: Task 1..7 in order. **GOAL-001:** skeleton serves health. **GOAL-002:** auth works. **GOAL-003:** resources managed. **GOAL-004:** bookings created safely. **GOAL-005:** permissions proven. **GOAL-006:** retries safe. **GOAL-007:** shippable docs.
- **FILE:** every Create/Modify path listed per task. **TEST:** `TEST-001` health … `TEST-007` openapi (one per task's test file).
- **DEP-001:** Postgres 16 + `btree_gist`. **DEP-002:** Docker/Compose for dev+tests. **DEP-003:** env secrets (`JWT_SECRET_KEY`, `DATABASE_URL`).
- **RISK-001:** exclusion constraint needs `btree_gist` → migration creates it, fallback documented. **RISK-002:** testcontainers needs Docker in CI → `TEST_DATABASE_URL` fallback. **ASSUMPTION-001:** single-region UTC datetimes; single API replica for in-memory rate limits in dev.
- **ALT-001 (rejected):** session cookies — chosen JWT for stateless REST demo. **ALT-002 (rejected):** Redis idempotency — chosen DB table for durability + test simplicity.

## Appendix B — Verification checklist (TDD)

- [ ] Every new function has a test written first and watched failing for the expected reason.
- [ ] Minimal code to GREEN; full `pytest -q` green after every task (report any red by name).
- [ ] Coverage `>= 85%` on `services/` + `api/` before Done.
- [ ] `docker compose up --build` → `/healthz` 200 + `/docs` renders.
