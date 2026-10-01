from tests.conftest import (
    auth_header,
    make_booking,
    make_resource,
    user_token,
    window,
)
from app.models.user import Role


async def _setup(client, db_session):
    admin = await user_token(client, db_session, "admin@ex.com", Role.admin)
    customer = await user_token(client, db_session, "cust@ex.com")
    resource = await make_resource(client, admin)
    return admin, customer, resource


async def test_same_key_replays(client, db_session):
    _, customer, resource = await _setup(client, db_session)
    s, e = window()
    headers = {"Idempotency-Key": "key-123"}
    r1 = await make_booking(
        client, customer, resource["id"], start=s, end=e, headers=headers
    )
    assert r1.status_code == 201, r1.text
    assert "idempotent-replayed" not in {k.lower() for k in r1.headers}

    r2 = await make_booking(
        client, customer, resource["id"], start=s, end=e, headers=headers
    )
    assert r2.status_code == 201
    assert r2.headers["idempotent-replayed"] == "true"
    assert r2.json()["id"] == r1.json()["id"]

    # Only one booking row exists.
    r = await client.get("/api/v1/bookings", headers=auth_header(customer))
    assert r.headers["x-total-count"] == "1"


async def test_same_key_different_payload_422(client, db_session):
    _, customer, resource = await _setup(client, db_session)
    s, e = window()
    headers = {"Idempotency-Key": "key-abc"}
    r1 = await make_booking(
        client, customer, resource["id"], start=s, end=e, headers=headers
    )
    assert r1.status_code == 201

    s2, e2 = window(hours_from_now=5)
    r2 = await make_booking(
        client, customer, resource["id"], start=s2, end=e2, headers=headers
    )
    assert r2.status_code == 422


async def test_different_keys_create_two(client, db_session):
    _, customer, resource = await _setup(client, db_session)
    s, e = window()
    for key in ("k1", "k2"):
        r = await make_booking(
            client,
            customer,
            resource["id"],
            start=s,
            end=e,
            headers={"Idempotency-Key": key},
        )
        assert r.status_code == 201, r.text
    r = await client.get("/api/v1/bookings", headers=auth_header(customer))
    assert r.headers["x-total-count"] == "2"


async def test_key_scoped_per_user(client, db_session):
    admin, customer, resource = await _setup(client, db_session)
    other = await user_token(client, db_session, "other@ex.com")
    s, e = window()
    headers = {"Idempotency-Key": "shared-key"}
    r1 = await make_booking(
        client, customer, resource["id"], start=s, end=e, headers=headers
    )
    r2 = await make_booking(
        client, other, resource["id"], start=s, end=e, headers=headers
    )
    assert r1.status_code == 201
    assert r2.status_code == 201
    assert r1.json()["id"] != r2.json()["id"]
