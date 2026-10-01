"""Phase 4.3 concurrency proof (runs in the normal suite).

Max 10 concurrent actors so the default async pool (15) is never
exhausted. Wall time is logged for information only -- no timing
assertions, no flaky gates.
"""

import asyncio
import logging
import time

from tests.conftest import (
    auth_header,
    make_booking,
    make_resource,
    user_token,
    window,
)

log = logging.getLogger(__name__)

ACTORS = 10


async def _setup(client, db_session):
    admin = await user_token(client, db_session, email="admin@ex.com", role="admin")
    staff = await user_token(client, db_session, email="staff@ex.com", role="staff")
    customer_a = await user_token(client, db_session, email="a@ex.com")
    customer_b = await user_token(client, db_session, email="b@ex.com")
    resource = await make_resource(client, admin)
    return staff, customer_a, customer_b, resource


async def test_concurrent_confirms_single_winner(client, db_session):
    started = time.perf_counter()
    staff, customer_a, customer_b, resource = await _setup(client, db_session)
    rid = resource["id"]
    start, end = window()

    # 2 users, same resource, 10 overlapping pending bookings.
    customers = [customer_a, customer_b] * (ACTORS // 2)
    created = await asyncio.gather(
        *[
            make_booking(client, token, rid, start=start, end=end)
            for token in customers
        ]
    )
    assert all(r.status_code == 201 for r in created), [
        (r.status_code, r.text) for r in created
    ]
    bids = [r.json()["id"] for r in created]

    # Confirm all 10 concurrently: exactly one may win.
    results = await asyncio.gather(
        *[
            client.post(
                f"/api/v1/bookings/{bid}/confirm", headers=auth_header(staff)
            )
            for bid in bids
        ]
    )
    oks = [r for r in results if r.status_code == 200]
    conflicts = [r for r in results if r.status_code == 409]
    assert len(oks) == 1, [(r.status_code, r.text) for r in results]
    assert len(conflicts) == ACTORS - 1, [
        (r.status_code, r.text) for r in results
    ]
    assert oks[0].json()["status"] == "confirmed"

    log.info(
        "test_concurrent_confirms_single_winner wall time: %.2fs",
        time.perf_counter() - started,
    )


async def test_concurrent_same_key_single_booking(client, db_session):
    started = time.perf_counter()
    staff, customer_a, _, resource = await _setup(client, db_session)
    rid = resource["id"]
    start, end = window()
    headers = {"Idempotency-Key": "conc-key-1"}

    # 10 concurrent identical creates under one key: one row, all 201.
    results = await asyncio.gather(
        *[
            make_booking(
                client, customer_a, rid, start=start, end=end, headers=headers
            )
            for _ in range(ACTORS)
        ]
    )
    assert all(r.status_code == 201 for r in results), [
        (r.status_code, r.text) for r in results
    ]
    ids = {r.json()["id"] for r in results}
    assert len(ids) == 1, ids

    r = await client.get("/api/v1/bookings", headers=auth_header(customer_a))
    assert r.status_code == 200, r.text
    assert r.headers["x-total-count"] == "1", r.headers["x-total-count"]

    log.info(
        "test_concurrent_same_key_single_booking wall time: %.2fs",
        time.perf_counter() - started,
    )
