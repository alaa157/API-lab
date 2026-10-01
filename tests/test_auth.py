import asyncio
import time

import jwt

from app.core.config import get_settings
from tests.conftest import auth_header, login, register


async def test_register_ok_forces_customer(client):
    user = await register(client, role="admin")
    assert user["email"] == "user@example.com"
    assert user["role"] == "customer"


async def test_register_duplicate_409(client):
    await register(client)
    r = await client.post(
        "/api/v1/auth/register",
        json={"email": "user@example.com", "password": "password123"},
    )
    assert r.status_code == 409
    assert r.headers["content-type"].startswith("application/problem+json")


async def test_concurrent_duplicate_registration_returns_conflict(client):
    responses = await asyncio.gather(
        *(
            client.post(
                "/api/v1/auth/register",
                json={"email": "racing@example.com", "password": "password123"},
            )
            for _ in range(2)
        )
    )

    assert sorted(response.status_code for response in responses) == [201, 409]


async def test_register_weak_password_422(client):
    r = await client.post(
        "/api/v1/auth/register",
        json={"email": "user@example.com", "password": "short"},
    )
    assert r.status_code == 422
    body = r.json()
    assert body["type"].endswith("/validation-error")
    assert body["errors"]


async def test_register_unknown_field_rejected(client):
    r = await client.post(
        "/api/v1/auth/register",
        json={
            "email": "user@example.com",
            "password": "password123",
            "is_admin": True,
        },
    )
    assert r.status_code == 422


async def test_login_ok(client):
    await register(client)
    tokens = await login(client)
    assert tokens["token_type"] == "bearer"
    assert tokens["access_token"] and tokens["refresh_token"]


async def test_login_wrong_password_401(client):
    await register(client)
    r = await client.post(
        "/api/v1/auth/login",
        data={"username": "user@example.com", "password": "wrongpass1"},
    )
    assert r.status_code == 401


async def test_login_inactive_403(client, db_session):
    from app.models.user import User
    from sqlalchemy import select

    await register(client)
    result = await db_session.execute(
        select(User).where(User.email == "user@example.com")
    )
    user = result.scalar_one()
    user.is_active = False
    await db_session.commit()

    r = await client.post(
        "/api/v1/auth/login",
        data={"username": "user@example.com", "password": "password123"},
    )
    assert r.status_code == 403


async def test_me_ok_and_anon_401(client):
    await register(client)
    tokens = await login(client)

    r = await client.get("/api/v1/auth/me", headers=auth_header(tokens["access_token"]))
    assert r.status_code == 200
    assert r.json()["email"] == "user@example.com"

    r = await client.get("/api/v1/auth/me")
    assert r.status_code == 401


async def test_expired_access_token_401(client):
    await register(client)
    settings = get_settings()
    expired = jwt.encode(
        {
            "sub": "00000000-0000-0000-0000-000000000000",
            "type": "access",
            "exp": int(time.time()) - 10,
        },
        settings.jwt_secret_key,
        algorithm=settings.jwt_algorithm,
    )
    r = await client.get("/api/v1/auth/me", headers=auth_header(expired))
    assert r.status_code == 401


async def test_refresh_rotation_and_reuse_401(client):
    await register(client)
    tokens = await login(client)

    r = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
    )
    assert r.status_code == 200
    rotated = r.json()
    assert rotated["refresh_token"] != tokens["refresh_token"]

    # Old token reuse -> 401 and the whole chain is revoked.
    r = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
    )
    assert r.status_code == 401

    r = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": rotated["refresh_token"]}
    )
    assert r.status_code == 401


async def test_concurrent_refresh_reuse_revokes_token_chain(client):
    await register(client)
    tokens = await login(client)

    responses = await asyncio.gather(
        *(
            client.post(
                "/api/v1/auth/refresh",
                json={"refresh_token": tokens["refresh_token"]},
            )
            for _ in range(2)
        )
    )

    assert sorted(response.status_code for response in responses) == [200, 401]
    rotated = next(
        response.json() for response in responses if response.status_code == 200
    )
    reused = await client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": rotated["refresh_token"]},
    )
    assert reused.status_code == 401


async def test_logout_revokes(client):
    await register(client)
    tokens = await login(client)

    r = await client.post(
        "/api/v1/auth/logout",
        json={"refresh_token": tokens["refresh_token"]},
        headers=auth_header(tokens["access_token"]),
    )
    assert r.status_code == 204

    r = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
    )
    assert r.status_code == 401


async def test_logout_cannot_revoke_another_users_refresh_token(client):
    await register(client, email="owner@example.com")
    owner_tokens = await login(client, email="owner@example.com")
    await register(client, email="other@example.com")
    other_tokens = await login(client, email="other@example.com")

    r = await client.post(
        "/api/v1/auth/logout",
        json={"refresh_token": owner_tokens["refresh_token"]},
        headers=auth_header(other_tokens["access_token"]),
    )
    assert r.status_code == 403

    r = await client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": owner_tokens["refresh_token"]},
    )
    assert r.status_code == 200


async def test_admin_can_assign_role(client, db_session):
    from app.models.user import Role, User
    from sqlalchemy import select

    await register(client, email="admin@example.com")
    result = await db_session.execute(
        select(User).where(User.email == "admin@example.com")
    )
    admin = result.scalar_one()
    admin.role = Role.admin
    await db_session.commit()
    admin_tokens = await login(client, email="admin@example.com")

    r = await client.post(
        "/api/v1/auth/register",
        json={
            "email": "staff@example.com",
            "password": "password123",
            "role": "staff",
        },
        headers=auth_header(admin_tokens["access_token"]),
    )
    assert r.status_code == 201
    assert r.json()["role"] == "staff"
