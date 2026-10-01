from tests.conftest import (
    auth_header,
    make_booking,
    make_resource,
    user_token,
)

UNKNOWN_ID = "00000000-0000-0000-0000-000000000000"


async def _setup(client, db_session):
    admin = await user_token(client, db_session, email="admin@ex.com", role="admin")
    staff = await user_token(client, db_session, email="staff@ex.com", role="staff")
    customer = await user_token(client, db_session, email="cust@ex.com")
    resource = await make_resource(client, admin)
    return staff, customer, resource


async def _confirmed(client, customer, staff, resource_id):
    r = await make_booking(client, customer, resource_id)
    assert r.status_code == 201, r.text
    bid = r.json()["id"]
    r = await client.post(
        f"/api/v1/bookings/{bid}/confirm", headers=auth_header(staff)
    )
    assert r.status_code == 200, r.text
    return bid


async def test_complete_confirmed_idempotent(client, db_session):
    staff, customer, resource = await _setup(client, db_session)
    bid = await _confirmed(client, customer, staff, resource["id"])

    r = await client.post(
        f"/api/v1/bookings/{bid}/complete", headers=auth_header(staff)
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "completed"

    # repeat -> 200, still completed (idempotent)
    r = await client.post(
        f"/api/v1/bookings/{bid}/complete", headers=auth_header(staff)
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "completed"


async def test_complete_rbac(client, db_session):
    staff, customer, resource = await _setup(client, db_session)
    bid = await _confirmed(client, customer, staff, resource["id"])

    # customer attempts -> 403
    r = await client.post(
        f"/api/v1/bookings/{bid}/complete", headers=auth_header(customer)
    )
    assert r.status_code == 403, r.text

    # anon -> 401
    r = await client.post(f"/api/v1/bookings/{bid}/complete")
    assert r.status_code == 401, r.text

    # unknown id -> 404
    r = await client.post(
        f"/api/v1/bookings/{UNKNOWN_ID}/complete", headers=auth_header(staff)
    )
    assert r.status_code == 404, r.text


async def test_complete_pending_conflicts(client, db_session):
    staff, customer, resource = await _setup(client, db_session)
    r = await make_booking(client, customer, resource["id"])
    assert r.status_code == 201, r.text
    bid = r.json()["id"]

    r = await client.post(
        f"/api/v1/bookings/{bid}/complete", headers=auth_header(staff)
    )
    assert r.status_code == 409, r.text


async def test_complete_cancelled_conflicts(client, db_session):
    staff, customer, resource = await _setup(client, db_session)
    r = await make_booking(client, customer, resource["id"])
    assert r.status_code == 201, r.text
    bid = r.json()["id"]

    r = await client.post(
        f"/api/v1/bookings/{bid}/cancel", headers=auth_header(customer)
    )
    assert r.status_code == 200, r.text

    r = await client.post(
        f"/api/v1/bookings/{bid}/complete", headers=auth_header(staff)
    )
    assert r.status_code == 409, r.text


async def test_cancel_completed_conflicts(client, db_session):
    staff, customer, resource = await _setup(client, db_session)
    bid = await _confirmed(client, customer, staff, resource["id"])

    r = await client.post(
        f"/api/v1/bookings/{bid}/complete", headers=auth_header(staff)
    )
    assert r.status_code == 200, r.text

    r = await client.post(
        f"/api/v1/bookings/{bid}/cancel", headers=auth_header(customer)
    )
    assert r.status_code == 409, r.text
