"""Phase 1+ test setup: real Postgres (testcontainers or TEST_DATABASE_URL).

The container is started at import time so DATABASE_URL/SYNC_DATABASE_URL
are set before any app module (engine is created at import).
"""

import os
import subprocess
from pathlib import Path

import pytest

os.environ.setdefault("ENVIRONMENT", "testing")
os.environ.setdefault(
    "JWT_SECRET_KEY", "test-secret-key-32-bytes-minimum-ok"
)

ROOT = Path(__file__).resolve().parent.parent


def _derive_urls(base: str) -> tuple[str, str]:
    if "://" in base and "+" not in base.split("://")[0]:
        scheme, rest = base.split("://", 1)
        assert scheme in ("postgres", "postgresql"), f"unsupported scheme {scheme}"
        return (
            f"postgresql+asyncpg://{rest}",
            f"postgresql+psycopg://{rest}",
        )
    if base.startswith("postgresql+psycopg2://"):
        rest = base.split("://", 1)[1]
        return f"postgresql+asyncpg://{rest}", f"postgresql+psycopg://{rest}"
    if base.startswith("postgresql+asyncpg://"):
        rest = base.split("://", 1)[1]
        return base, f"postgresql+psycopg://{rest}"
    if base.startswith("postgresql+psycopg://"):
        rest = base.split("://", 1)[1]
        return f"postgresql+asyncpg://{rest}", base
    raise ValueError(f"unsupported TEST_DATABASE_URL scheme: {base!r}")


def _ensure_urls() -> None:
    if "TEST_DATABASE_URL" in os.environ:
        async_url, sync_url = _derive_urls(os.environ["TEST_DATABASE_URL"])
    else:
        from testcontainers.postgres import PostgresContainer

        container = PostgresContainer("postgres:16-alpine")
        container.start()
        async_url, sync_url = _derive_urls(container.get_connection_url())
    os.environ["DATABASE_URL"] = async_url
    os.environ["SYNC_DATABASE_URL"] = sync_url


_ensure_urls()
subprocess.run(["alembic", "upgrade", "head"], check=True, cwd=ROOT)

from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.core.database import SessionLocal  # noqa: E402
from app.main import create_app  # noqa: E402

_app = create_app()


@pytest.fixture
async def client():
    async with AsyncClient(
        transport=ASGITransport(app=_app), base_url="http://test"
    ) as c:
        yield c


@pytest.fixture(autouse=True)
async def clean_db():
    yield
    async with SessionLocal() as session:
        await session.execute(
            text(
                "TRUNCATE refresh_tokens, idempotency_keys, bookings, "
                "resources, users"
            )
        )
        await session.commit()


@pytest.fixture
async def db_session():
    async with SessionLocal() as session:
        yield session
        await session.commit()


async def register(client, email="user@example.com", password="password123", **kw):
    r = await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": password, **kw},
    )
    assert r.status_code == 201, r.text
    return r.json()


async def login(client, email="user@example.com", password="password123"):
    r = await client.post(
        "/api/v1/auth/login",
        data={"username": email, "password": password},
    )
    assert r.status_code == 200, r.text
    return r.json()


def auth_header(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}
