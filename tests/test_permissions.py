"""Full RBAC matrix from the plan (sec 2, permission table)."""

from app.models.user import Role
from tests.conftest import (
    auth_header,
    make_booking,
    make_resource,
    user_token,
)


async def _tokens(client, db_session):
    admin = await user_token(client, db_session, "admin@ex.com", Role.admin)
    staff = await user_token(client, db_session, "staff@ex.com", Role.staff)
    customer = await user_token(client, db_session, "cust@ex.com", Role.customer)
    return admin, staff, customer


async def test_me_matrix(client, db_session):
    admin, staff, customer = await _tokens(client, db_session)
    for token in (admin, staff, customer):
        r = await client.get("/api/v1/auth/me", headers=auth_header(token))
        assert r.status_code == 200
    r = await client.get("/api/v1/auth/me")
    assert r.status_code == 401


async def test_create_booking_matrix(client, db_session):
    from tests.conftest import login, register

    admin, staff, customer = await _tokens(client, db_session)
    resource = await make_resource(client, admin)

    # create for self: all roles ok
    for token in (admin, staff, customer):
        r = await make_booking(client, token, resource["id"])
        assert r.status_code == 201, r.text
    # anon: 401
    r = await make_booking(client, "bogus", resource["id"])
    assert r.status_code == 401

    # create for others: customer 403, staff/admin ok
    await register(client, email="target@ex.com")
    from sqlalchemy import select

    from app.core.database import SessionLocal
    from app.models.user import User

    async with SessionLocal() as s:
        target = (
            await s.execute(select(User).where(User.email == "target@ex.com"))
        ).scalar_one()
        target_id = str(target.id)

    r = await make_booking(
        client, customer, resource["id"], user_id=target_id
    )
    assert r.status_code == 403
    for token in (staff, admin):
        r = await make_booking(
            client, token, resource["id"], user_id=target_id
        )
        assert r.status_code == 201, r.text


async def test_confirm_matrix(client, db_session):
    from tests.conftest import window

    admin, staff, customer = await _tokens(client, db_session)
    resource = await make_resource(client, admin)
    bids = []
    for i in range(4):
        s, e = window(hours_from_now=1 + i * 2)
        r = await make_booking(client, customer, resource["id"], start=s, end=e)
        assert r.status_code == 201, r.text
        bids.append(r.json()["id"])

    r = await client.post(
        f"/api/v1/bookings/{bids[0]}/confirm", headers=auth_header(customer)
    )
    assert r.status_code == 403
    for bid, token in ((bids[1], staff), (bids[2], admin)):
        r = await client.post(
            f"/api/v1/bookings/{bid}/confirm", headers=auth_header(token)
        )
        assert r.status_code == 200
    r = await client.post(f"/api/v1/bookings/{bids[3]}/confirm")
    assert r.status_code == 401


async def test_cancel_and_read_matrix(client, db_session):
    admin, staff, customer = await _tokens(client, db_session)
    other = await user_token(client, db_session, "other@ex.com")
    resource = await make_resource(client, admin)

    mine = (await make_booking(client, customer, resource["id"])).json()

    # read: owner/staff/admin ok, other customer 403, anon 401
    for token, expected in (
        (customer, 200),
        (staff, 200),
        (admin, 200),
        (other, 403),
    ):
        r = await client.get(
            f"/api/v1/bookings/{mine['id']}", headers=auth_header(token)
        )
        assert r.status_code == expected, (token[:8], r.text)
    r = await client.get(f"/api/v1/bookings/{mine['id']}")
    assert r.status_code == 401

    # cancel others: customer 403, staff/admin ok
    r = await client.post(
        f"/api/v1/bookings/{mine['id']}/cancel", headers=auth_header(other)
    )
    assert r.status_code == 403
    for token in (staff, admin):
        fresh = (await make_booking(client, customer, resource["id"])).json()
        r = await client.post(
            f"/api/v1/bookings/{fresh['id']}/cancel", headers=auth_header(token)
        )
        assert r.status_code == 200


async def test_list_scoping(client, db_session):
    admin, staff, customer = await _tokens(client, db_session)
    other = await user_token(client, db_session, "other@ex.com")
    resource = await make_resource(client, admin)
    await make_booking(client, customer, resource["id"])
    await make_booking(client, other, resource["id"])

    r = await client.get("/api/v1/bookings", headers=auth_header(customer))
    assert r.headers["x-total-count"] == "1"
    other_id = await _uid("other@ex.com")
    assert all(b["user_id"] != other_id for b in r.json())

    for token in (staff, admin):
        r = await client.get("/api/v1/bookings", headers=auth_header(token))
        assert r.headers["x-total-count"] == "2", r.text

    # customer filtering by someone else -> 403
    r = await client.get(
        f"/api/v1/bookings?user_id={other_id}", headers=auth_header(customer)
    )
    assert r.status_code == 403

    r = await client.get("/api/v1/bookings")
    assert r.status_code == 401


async def _uid(email: str):
    from sqlalchemy import select

    from app.core.database import SessionLocal
    from app.models.user import User

    async with SessionLocal() as s:
        user = (
            await s.execute(select(User).where(User.email == email))
        ).scalar_one()
        return str(user.id)


async def test_resource_management_matrix(client, db_session):
    admin, staff, customer = await _tokens(client, db_session)
    payload = {"name": "R", "type": "desk", "capacity": 2}

    for token, expected in ((customer, 403), (staff, 403)):
        r = await client.post(
            "/api/v1/resources", json=payload, headers=auth_header(token)
        )
        assert r.status_code == expected
    r = await client.post("/api/v1/resources", json=payload)
    assert r.status_code == 401

    r = await client.post(
        "/api/v1/resources", json=payload, headers=auth_header(admin)
    )
    assert r.status_code == 201
    rid = r.json()["id"]

    for token, expected in ((customer, 403), (staff, 403)):
        r = await client.patch(
            f"/api/v1/resources/{rid}",
            json={"capacity": 5},
            headers=auth_header(token),
        )
        assert r.status_code == expected
    r = await client.patch(
        f"/api/v1/resources/{rid}",
        json={"capacity": 5},
        headers=auth_header(admin),
    )
    assert r.status_code == 200
    assert r.json()["capacity"] == 5

    for token, expected in ((customer, 403), (staff, 403)):
        r = await client.delete(
            f"/api/v1/resources/{rid}", headers=auth_header(token)
        )
        assert r.status_code == expected
    r = await client.delete(
        f"/api/v1/resources/{rid}", headers=auth_header(admin)
    )
    assert r.status_code == 200
    assert r.json()["is_active"] is False
