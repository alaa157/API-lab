"""Phase 4.0 observability tests: request ID, access logs, metrics."""

import json
import logging

import structlog


def _configure_test_logging() -> None:
    # Plain JSONRenderer chain so structlog renders to a JSON string that
    # lands in caplog records via the stdlib logger factory.
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )


async def test_request_id_echoed(client):
    r = await client.get("/", headers={"X-Request-ID": "abc123"})
    assert r.status_code == 200
    assert r.headers["X-Request-ID"] == "abc123"


async def test_request_id_generated_when_absent(client):
    r = await client.get("/")
    assert r.status_code == 200
    generated = r.headers.get("X-Request-ID")
    assert generated, "expected a generated X-Request-ID"


async def test_metrics_endpoint(client):
    await client.get("/")
    r = await client.get("/metrics")
    assert r.status_code == 200
    assert "http_requests_total" in r.text


async def test_access_log_json_line(client, caplog):
    _configure_test_logging()
    with caplog.at_level(logging.INFO, logger="app.access"):
        r = await client.get("/", headers={"X-Request-ID": "log-test-1"})
    assert r.status_code == 200
    records = [rec for rec in caplog.records if rec.name == "app.access"]
    assert len(records) == 1
    payload = json.loads(records[0].getMessage())
    assert payload["request_id"] == "log-test-1"
    assert payload["method"] == "GET"
    assert payload["path"] == "/"
    assert payload["status_code"] == 200
    assert isinstance(payload["duration_ms"], (int, float))
