# Architecture

Sources for every diagram below live in [`docs/diagrams/`](docs/diagrams/)
as `.mmd` files; GitHub renders them in place here.

## Layering

```
router (thin: auth, validation, status codes, headers)
  -> service (business rules, transactions, raises domain errors)
    -> repository (SQL only, no business logic)
```

- Routers (`app/api/`) never own transactions; they parse auth, validate
  input via Pydantic schemas, and map results to status codes/headers
  (`X-Total-Count`, `Idempotent-Replayed`).
- Services (`app/services/`) own the business rules and commit once per
  operation. Multi-step writes (refresh rotation, idempotent create) commit
  once at the end so partial state is never persisted.
- Repositories (`app/repos/`) are straight SQLAlchemy queries. A shared
  `paginate()` helper clamps `per_page` to 1–100 and returns `(items, total)`.
- Schemas are split input vs output: `*Create/*Update` reject unknown fields
  (`extra="forbid"`) where security-relevant; `*Read` uses
  `from_attributes=True`.

```
client -> FastAPI routers -> services -> repos -> Postgres (asyncpg)
              |-> deps (get_current_user, require_role)
              |-> problem+json handlers (DomainError, 422, 429, 404)
```

One write end to end (idempotent booking creation):

```mermaid
flowchart TD
    subgraph Router["Router (status codes, headers)"]
        A["POST /bookings<br/>+ Idempotency-Key?"]
        H1["201 + body"]
        H2["201 replay<br/>Idempotent-Replayed: true"]
        H3["422 / 409 problem+json"]
    end
    subgraph Service["Service (rules, one txn)"]
        B{"stored response<br/>for key + user?"}
        C["validate target user<br/>+ resource active"]
        D{"confirmed overlap?"}
        E["insert pending booking<br/>+ store response"]
    end
    subgraph RepoDB["Repo + Postgres"]
        F[("bookings<br/>idempotency_keys")]
    end

    A --> B
    B -->|same payload| H2
    B -->|different payload| H3
    B -->|miss or expired| C
    C --> D
    D -->|yes| H3
    D -->|no| E
    E <--> F
    E --> H1
```

## Booking lifecycle

State machine enforced by the booking service (`confirm` / `cancel`).
`confirm` and `cancel` are idempotent; confirming a terminal state is a
`409`, and cancelling a `completed` booking is a `409`. There is no
complete endpoint in v1; `completed` is set out of band.

```mermaid
stateDiagram-v2
    [*] --> pending : POST /bookings
    pending --> confirmed : staff/admin confirm
    pending --> cancelled : owner/staff/admin cancel
    confirmed --> cancelled : owner/staff/admin cancel
    confirmed --> completed : elapsed (no endpoint in v1)
    confirmed --> confirmed : confirm (idempotent)
    cancelled --> cancelled : cancel (idempotent)
    cancelled --> [*]
    completed --> [*]
```

## Why problem+json

Every error — domain, validation, auth, rate limit, 404 — returns
`application/problem+json` with a stable shape
`{type, title, status, detail, errors[]}` and stable `type` URIs
(`https://api.booking.local/problems/<slug>`). One envelope means clients
write one error path, and reshaped Pydantic 422s look like everything else.

## Why DB-backed idempotency keys

`POST /bookings` accepts `Idempotency-Key`. The first request stores
`(key, user_id) -> (request_hash, status_code, response_body)` in the
`idempotency_keys` table; a retry with the same key + same payload replays
the stored response with `Idempotent-Replayed: true`, and the same key with
a different payload is a `422` (likely a client bug, surfaced loudly).

Keys live 24h (lazy expiry on read + a startup sweep). The table is the
source of truth for *responses*; the `bookings.idempotency_key` column
(unique per user) is a backstop so two requests racing past the lookup still
collide on exactly one row instead of double-booking.

## Why exclusion constraint + service check

Overlap is checked in the service (`has_confirmed_overlap`) for a clear
`409` with a readable message — but application checks race. The GiST
exclusion constraint `no_overlapping_confirmed_bookings`
(`resource_id =` AND `tstzrange(start_at, end_at) &&`, partial to
`status = 'confirmed'`) makes the database the final arbiter: concurrent
confirms serialize on exactly one winner, and the loser is mapped from
`IntegrityError` to the same `409`. `[)` range semantics mean back-to-back
bookings don't conflict. Requires the `btree_gist` extension (created in the
migration; without it, the fallback is the service-layer check plus a unique
partial index).

## Auth and tokens

- argon2id hashing via `pwdlib` (no bcrypt 72-byte truncation issues).
- Short-lived access JWT (15m, HS256, secret ≥ 32 chars from env, fail-fast
  at startup) + rotating refresh JWT (7d).
- Only the SHA-256 hash of a refresh token is stored. Rotation revokes the
  presented token and issues a fresh pair; presenting an already-rotated
  token signals theft, so the whole chain is revoked and the caller gets
  `401`. Logout revokes a single token (idempotent, `204`).
- Roles are `admin | staff | customer`, enforced by `require_role` and
  owner checks in the booking service. Registration forces `customer`
  unless the caller presents a valid admin access token.
- Rate limits: 100/min global, 10/min on auth endpoints (in-memory via
  `slowapi`; raised under `ENVIRONMENT=testing` so the suite can't flake).

```mermaid
sequenceDiagram
    participant C as Client
    participant API as API
    participant DB as Postgres

    C ->> API: POST /auth/refresh {refresh_token}
    API ->> API: verify signature + expiry + type=refresh
    alt invalid or expired
        API -->> C: 401 problem+json
    else valid JWT
        API ->> DB: lookup token hash
        alt unknown hash
            API -->> C: 401 unknown refresh token
        else revoked (reuse detected)
            API ->> DB: revoke whole chain (one txn)
            API -->> C: 401 reuse detected
        else active
            API ->> DB: revoke old + store new hash (one txn)
            API -->> C: 200 new access + refresh pair
        end
    end
```

## Migrations and data

- App uses the async URL (`asyncpg`); Alembic uses the sync URL (`psycopg`)
  because Alembic can't run on an async engine. Every migration has a
  downgrade (enum types dropped explicitly on Postgres).
- Tests run against real Postgres (testcontainers, or `TEST_DATABASE_URL`
  in CI), with Alembic `upgrade head` at session start and table truncation
  between tests.

## Domain model

```mermaid
erDiagram
    users ||--o{ refresh_tokens : holds
    users ||--o{ bookings : owns
    users ||--o{ idempotency_keys : scopes
    resources ||--o{ bookings : hosts

    users {
        uuid id PK
        string email UK
        string password_hash
        string role
        boolean is_active
        timestamptz created_at
    }

    refresh_tokens {
        uuid id PK
        uuid user_id FK
        string token_hash UK
        timestamptz expires_at
        boolean revoked
        timestamptz created_at
    }

    resources {
        uuid id PK
        string name
        string type
        integer capacity
        boolean is_active
        timestamptz created_at
    }

    bookings {
        uuid id PK
        uuid user_id FK
        uuid resource_id FK
        timestamptz start_at
        timestamptz end_at
        string status
        string idempotency_key "UK per user"
        timestamptz created_at
    }

    idempotency_keys {
        string key "PK + user_id"
        uuid user_id "PK + key"
        string method
        string path
        string request_hash
        integer status_code
        jsonb response_body
        timestamptz created_at
    }
```

Enforced in the schema, not just the service: the GiST exclusion
constraint on confirmed bookings per resource, and the per-user unique key
slot on bookings. See `alembic/versions/` for the exact DDL.
