"""Edge cases that close coverage gaps and lock in error behavior."""

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.core.database import SessionLocal
from app.models.booking import Booking, BookingStatus
from app.models.idempotency import IdempotencyKey
from app.models.user import Role, User
from tests.conftest import (
    auth_header,
    login,
    make_booking,
    make_resource,
    register,
    user_token,
    window,
)


async def _setup(client, db_session):
    admin = await user_token(client, db_session, "admin@ex.com", Role.admin)
    staff = await user_token(client, db_session, "staff@ex.com", Role.staff)
    customer = await user_token(client, db_session, "cust@ex.com")
    return admin, staff, customer


async def test_root_and_degraded_health(client, monkeypatch):
    r = await client.get("/")
    assert r.status_code == 200
    assert r.json()["docs"] == "/docs"

    from app.api import health as health_module

    async def _down():
        return False

    monkeypatch.setattr(health_module, "check_db", _down)
    r = await client.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "degraded", "db": "down"}


async def test_resource_read_and_list(client, db_session):
    admin, _, customer = await _setup(client, db_session)
    await make_resource(client, admin, name="Alpha Room")
    await make_resource(client, admin, name="Beta Desk", type="desk")

    r = await client.get("/api/v1/resources", headers=auth_header(customer))
    assert r.status_code == 200
    assert r.headers["x-total-count"] == "2"

    r = await client.get(
        "/api/v1/resources?q=alpha", headers=auth_header(customer)
    )
    assert r.headers["x-total-count"] == "1"

    r = await client.get(
        "/api/v1/resources?type=desk&is_active=true",
        headers=auth_header(customer),
    )
    assert r.headers["x-total-count"] == "1"

    rid = (
        await client.get("/api/v1/resources", headers=auth_header(customer))
    ).json()[0]["id"]
    r = await client.get(
        f"/api/v1/resources/{rid}", headers=auth_header(customer)
    )
    assert r.status_code == 200

    r = await client.get(
        "/api/v1/resources/00000000-0000-0000-0000-000000000000",
        headers=auth_header(customer),
    )
    assert r.status_code == 404


async def test_booking_404s(client, db_session):
    admin, staff, customer = await _setup(client, db_session)
    dead = "00000000-0000-0000-0000-000000000000"

    r = await client.get(
        f"/api/v1/bookings/{dead}", headers=auth_header(customer)
    )
    assert r.status_code == 404

    r = await client.post(
        f"/api/v1/bookings/{dead}/confirm", headers=auth_header(staff)
    )
    assert r.status_code == 404

    r = await client.post(
        f"/api/v1/bookings/{dead}/cancel", headers=auth_header(customer)
    )
    assert r.status_code == 404


async def test_staff_booking_target_edge_cases(client, db_session):
    admin, staff, _ = await _setup(client, db_session)
    resource = await make_resource(client, admin)
    rid = resource["id"]

    # unknown target user -> 404
    r = await make_booking(
        client,
        staff,
        rid,
        user_id="00000000-0000-0000-0000-000000000000",
    )
    assert r.status_code == 404

    # inactive target user -> 422
    await register(client, email="target@ex.com")
    async with SessionLocal() as s:
        target = (
            await s.execute(select(User).where(User.email == "target@ex.com"))
        ).scalar_one()
        target.is_active = False
        await s.commit()
        target_id = str(target.id)
    r = await make_booking(client, staff, rid, user_id=target_id)
    assert r.status_code == 422

    # inactive resource -> 422
    res = await make_resource(client, admin, name="Old Room")
    r = await client.delete(
        f"/api/v1/resources/{res['id']}", headers=auth_header(admin)
    )
    assert r.status_code == 200
    r = await make_booking(client, staff, res["id"])
    assert r.status_code == 422


async def test_confirm_terminal_states_409(client, db_session):
    admin, staff, customer = await _setup(client, db_session)
    resource = await make_resource(client, admin)

    # confirm a cancelled booking -> 409
    s, e = window()
    bid = (await make_booking(client, customer, resource["id"], start=s, end=e)).json()["id"]
    await client.post(
        f"/api/v1/bookings/{bid}/cancel", headers=auth_header(customer)
    )
    r = await client.post(
        f"/api/v1/bookings/{bid}/confirm", headers=auth_header(staff)
    )
    assert r.status_code == 409

    # confirm re-checks overlap at confirm time -> 409
    s2, e2 = window(hours_from_now=10)
    b1 = (await make_booking(client, customer, resource["id"], start=s2, end=e2)).json()
    other = await user_token(client, db_session, "other@ex.com")
    b2 = (await make_booking(client, other, resource["id"], start=s2, end=e2)).json()
    r = await client.post(
        f"/api/v1/bookings/{b1['id']}/confirm", headers=auth_header(staff)
    )
    assert r.status_code == 200
    r = await client.post(
        f"/api/v1/bookings/{b2['id']}/confirm", headers=auth_header(staff)
    )
    assert r.status_code == 409


async def test_deactivate_guarded_by_future_confirmed(client, db_session):
    admin, staff, customer = await _setup(client, db_session)
    resource = await make_resource(client, admin)
    s, e = window()
    bid = (await make_booking(client, customer, resource["id"], start=s, end=e)).json()["id"]
    await client.post(
        f"/api/v1/bookings/{bid}/confirm", headers=auth_header(staff)
    )

    r = await client.delete(
        f"/api/v1/resources/{resource['id']}", headers=auth_header(admin)
    )
    assert r.status_code == 409

    # deactivating an already-inactive resource is a no-op success
    res2 = await make_resource(client, admin, name="Second Room")
    r = await client.delete(
        f"/api/v1/resources/{res2['id']}", headers=auth_header(admin)
    )
    assert r.status_code == 200
    r = await client.delete(
        f"/api/v1/resources/{res2['id']}", headers=auth_header(admin)
    )
    assert r.status_code == 200
    assert r.json()["is_active"] is False

    r = await client.patch(
        f"/api/v1/resources/{resource['id']}",
        json={"is_active": False},
        headers=auth_header(admin),
    )
    assert r.status_code == 409


async def test_register_with_garbage_bearer_forces_customer(client, db_session):
    await _setup(client, db_session)
    r = await client.post(
        "/api/v1/auth/register",
        json={
            "email": "x@ex.com",
            "password": "password123",
            "role": "admin",
        },
        headers={"Authorization": "Bearer garbage-token"},
    )
    assert r.status_code == 201
    assert r.json()["role"] == "customer"


async def test_me_deleted_and_inactive(client, db_session):
    await register(client, email="gone@ex.com")
    tokens = await login(client, email="gone@ex.com")

    async with SessionLocal() as s:
        user = (
            await s.execute(select(User).where(User.email == "gone@ex.com"))
        ).scalar_one()
        await s.delete(user)
        await s.commit()

    r = await client.get(
        "/api/v1/auth/me", headers=auth_header(tokens["access_token"])
    )
    assert r.status_code == 401

    # inactive user with a pre-deactivation token -> 403
    await register(client, email="off2@ex.com")
    tokens2 = await login(client, email="off2@ex.com")
    async with SessionLocal() as s:
        user = (
            await s.execute(select(User).where(User.email == "off2@ex.com"))
        ).scalar_one()
        user.is_active = False
        await s.commit()
    r = await client.get(
        "/api/v1/auth/me", headers=auth_header(tokens2["access_token"])
    )
    assert r.status_code == 403


async def test_rotate_unknown_and_inactive(client, db_session):
    import uuid as uuid_lib

    from app.core import security

    await register(client, email="u@ex.com")
    stranger = security.create_refresh_token(uuid_lib.uuid4())[0]
    r = await client.post("/api/v1/auth/refresh", json={"refresh_token": stranger})
    assert r.status_code == 401

    await register(client, email="gone2@ex.com")
    tokens = await login(client, email="gone2@ex.com")
    async with SessionLocal() as s:
        user = (
            await s.execute(select(User).where(User.email == "gone2@ex.com"))
        ).scalar_one()
        user.is_active = False
        await s.commit()
    r = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
    )
    assert r.status_code == 403


async def test_idempotency_expiry_and_key_rules(client, db_session):
    _, customer, resource = await (
        lambda c, d: _setup_min(c, d)
    )(client, db_session)
    s, e = window()
    headers = {"Idempotency-Key": "expiring-key"}
    r1 = await make_booking(
        client, customer, resource["id"], start=s, end=e, headers=headers
    )
    assert r1.status_code == 201

    # age the record past the 24h TTL -> next request is a miss, creates anew
    async with SessionLocal() as sess:
        record = (
            await sess.execute(select(IdempotencyKey))
        ).scalar_one()
        record.created_at = datetime.now(timezone.utc) - timedelta(hours=25)
        await sess.commit()

    r2 = await make_booking(
        client, customer, resource["id"], start=s, end=e, headers=headers
    )
    assert r2.status_code == 201
    assert "idempotent-replayed" not in {k.lower() for k in r2.headers}
    assert r2.json()["id"] != r1.json()["id"]

    # whitespace-only key behaves as no key
    r = await make_booking(
        client, customer, resource["id"], headers={"Idempotency-Key": "   "}
    )
    assert r.status_code == 201

    # overlong key -> 422
    r = await make_booking(
        client, customer, resource["id"], headers={"Idempotency-Key": "k" * 256}
    )
    assert r.status_code == 422


async def _setup_min(client, db_session):
    from app.models.user import Role as R

    admin = await user_token(client, db_session, "admin@ex.com", R.admin)
    customer = await user_token(client, db_session, "cust@ex.com")
    resource = await make_resource(client, admin)
    return admin, customer, resource


async def test_sweep_reports_expired(db_session):
    from datetime import datetime as dt

    from app.services.idempotency import sweep

    assert await sweep(db_session) == 0
    async with SessionLocal() as s:
        user = (await s.execute(select(User).limit(1))).scalar_one_or_none()
        if user is None:
            user = User(
                email="sweep@ex.com",
                password_hash="x",
                role=Role.customer,
            )
            s.add(user)
            await s.flush()
        from app.models.idempotency import IdempotencyKey as K

        s.add(
            K(
                key="sweep-me",
                user_id=user.id,
                method="POST",
                path="/api/v1/bookings",
                request_hash="h",
                status_code=201,
                response_body={},
                created_at=dt.now(timezone.utc) - timedelta(hours=25),
            )
        )
        await s.commit()
    assert await sweep(db_session) == 1
