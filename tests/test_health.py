def test_healthz_shape(client):
    r = client.get("/healthz")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] in ("ok", "degraded")
    assert body["db"] in ("up", "down")


def test_openapi_served(client):
    r = client.get("/openapi.json")
    assert r.status_code == 200
    assert "/healthz" in r.json()["paths"]


def test_unknown_route_is_problem_json(client):
    r = client.get("/nope")
    assert r.status_code == 404
    assert r.headers["content-type"].startswith("application/problem+json")
    assert r.json()["type"].endswith("/not-found")
