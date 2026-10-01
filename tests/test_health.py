from httpx import ASGITransport, AsyncClient

from app.main import create_app


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


async def test_unhandled_error_is_problem_json():
    app = create_app()

    @app.get("/test-error")
    async def raise_error():
        raise RuntimeError("sensitive internal detail")

    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        response = await client.get("/test-error")

    assert response.status_code == 500
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json()["type"].endswith("/internal-error")
    assert "sensitive internal detail" not in response.text
