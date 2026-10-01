async def test_healthz_shape(client):
    r = await client.get("/healthz")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] in ("ok", "degraded")
    assert body["db"] in ("up", "down")


async def test_openapi_served(client):
    r = await client.get("/openapi.json")
    assert r.status_code == 200
    assert "/healthz" in r.json()["paths"]
    assert "/api/v1/auth/login" in r.json()["paths"]


async def test_unknown_route_is_problem_json(client):
    r = await client.get("/nope")
    assert r.status_code == 404
    assert "application/problem+json" in r.headers["content-type"]
    assert r.json()["type"].endswith("/not-found")
