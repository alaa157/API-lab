from datetime import timedelta

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
    admin = await user_token(client, db_session, email="admin@ex.com", role="admin")
    staff = await user_token(client, db_session, email="staff@ex.com", role="staff")
    customer = await user_token(client, db_session, email="cust@ex.com")
    resource = await make_resource(client, admin)
    return admin, staff, customer, resource


async def test_create_ok_pending(client, db_session):
    admin, _, customer, resource = await _setup(client, db_session)
    r = await make_booking(client, customer, resource["id"])
    assert r.status_code == 201, r.text
    assert r.json()["status"] == "pending"


async def test_pending_overlap_allowed_but_confirmed_409(client, db_session):
    admin, staff, customer, resource = await _setup(client, db_session)
    s, e = window()
    r1 = await make_booking(client, customer, resource["id"], start=s, end=e)
    assert r1.status_code == 201
    r2 = await make_booking(client, customer, resource["id"], start=s, end=e)
    assert r2.status_code == 201  # pending+pending is fine

    r = await client.post(
        f"/api/v1/bookings/{r1.json()['id']}/confirm",
        headers=auth_header(staff),
    )
    assert r.status_code == 200

    # Overlap against a CONFIRMED booking -> 409, even from another user.
    other = await user_token(client, db_session, email="other@ex.com")
    r = await make_booking(client, other, resource["id"], start=s, end=e)
    assert r.status_code == 409

    # Adjacent window (end == start) does not overlap.
    from datetime import datetime

    end_dt = datetime.fromisoformat(e)
    s2 = end_dt.isoformat()
    e2 = (end_dt + timedelta(hours=1)).isoformat()
    r = await make_booking(client, other, resource["id"], start=s2, end=e2)
    assert r.status_code == 201, r.text


async def test_create_validation(client, db_session):
    _, _, customer, resource = await _setup(client, db_session)
    rid = resource["id"]

    # end before start
    s, e = window()
    r = await make_booking(client, customer, rid, start=e, end=s)
    assert r.status_code == 422

    # past start
    from datetime import datetime, timezone

    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    r = await make_booking(client, customer, rid, start=past, end=future)
    assert r.status_code == 422

    # over 8h
    s, _ = window()
    e8 = (datetime.fromisoformat(s) + timedelta(hours=9)).isoformat()
    r = await make_booking(client, customer, rid, start=s, end=e8)
    assert r.status_code == 422

    # unknown resource
    r = await make_booking(
        client, customer, "00000000-0000-0000-0000-000000000000"
    )
    assert r.status_code == 404


async def test_cancel_flow(client, db_session):
    from sqlalchemy import select

    from app.models.booking import BookingStatus
    from app.models.user import User

    admin, _, customer, resource = await _setup(client, db_session)
    other = await user_token(client, db_session, email="other@ex.com")

    # cancel own
    r = await make_booking(client, customer, resource["id"])
    bid = r.json()["id"]
    r = await client.post(
        f"/api/v1/bookings/{bid}/cancel", headers=auth_header(customer)
    )
    assert r.status_code == 200
    assert r.json()["status"] == "cancelled"

    # double cancel is idempotent
    r = await client.post(
        f"/api/v1/bookings/{bid}/cancel", headers=auth_header(customer)
    )
    assert r.status_code == 200

    # cancel others -> 403
    r = await make_booking(client, customer, resource["id"])
    bid2 = r.json()["id"]
    r = await client.post(
        f"/api/v1/bookings/{bid2}/cancel", headers=auth_header(other)
    )
    assert r.status_code == 403

    # completed -> 409 (status set directly; no complete endpoint in v1)
    result = await db_session.execute(select(User).where(User.email == "cust@ex.com"))
    user = result.scalar_one()
    tokens = await login(client, email="cust@ex.com")
    from app.models.booking import Booking

    result = await db_session.execute(
        select(Booking).where(Booking.id == bid2)
    )
    booking = result.scalar_one()
    booking.status = BookingStatus.completed
    await db_session.commit()
    r = await client.post(
        f"/api/v1/bookings/{bid2}/cancel",
        headers=auth_header(tokens["access_token"]),
    )
    assert r.status_code == 409


async def test_confirm_rbac(client, db_session):
    admin, staff, customer, resource = await _setup(client, db_session)
    r = await make_booking(client, customer, resource["id"])
    bid = r.json()["id"]

    r = await client.post(
        f"/api/v1/bookings/{bid}/confirm", headers=auth_header(customer)
    )
    assert r.status_code == 403

    r = await client.post(
        f"/api/v1/bookings/{bid}/confirm", headers=auth_header(staff)
    )
    assert r.status_code == 200
    assert r.json()["status"] == "confirmed"

    # double confirm is idempotent
    r = await client.post(
        f"/api/v1/bookings/{bid}/confirm", headers=auth_header(admin)
    )
    assert r.status_code == 200


async def test_pagination_and_filters(client, db_session):
    admin, _, customer, resource = await _setup(client, db_session)
    for i in range(3):
        s, e = window(hours_from_now=1 + i * 2)
        r = await make_booking(client, customer, resource["id"], start=s, end=e)
        assert r.status_code == 201, r.text

    r = await client.get(
        "/api/v1/bookings?page=1&per_page=2", headers=auth_header(customer)
    )
    assert r.status_code == 200
    assert r.headers["x-total-count"] == "3"
    assert len(r.json()) == 2

    r = await client.get(
        f"/api/v1/bookings?resource_id={resource['id']}&status=pending",
        headers=auth_header(customer),
    )
    assert r.headers["x-total-count"] == "3"

    r = await client.get(
        "/api/v1/bookings?status=confirmed", headers=auth_header(customer)
    )
    assert r.headers["x-total-count"] == "0"
