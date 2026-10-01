"""Structured logging + request-ID middleware (phase 4.0).

One JSON record per request on the ``app.access`` logger with
``request_id``/``method``/``path``/``status_code``/``duration_ms`` fields.
Console rendering in development, JSON everywhere else.
"""

from __future__ import annotations

import logging
import sys
import time
import uuid
from typing import TYPE_CHECKING

import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

if TYPE_CHECKING:  # pragma: no cover
    from app.core.config import Settings

REQUEST_ID_HEADER = "X-Request-ID"


def _use_json(log_format: str, environment: str) -> bool:
    fmt = log_format.lower()
    if fmt == "json":
        return True
    if fmt == "console":
        return False
    # "auto": human-readable console in dev, JSON everywhere else.
    return environment != "development"


def configure_logging(settings: Settings) -> None:
    """Configure structlog + stdlib root handler. Idempotent per call."""
    level = getattr(logging, settings.log_level.upper(), logging.INFO)
    renderer = (
        structlog.processors.JSONRenderer()
        if _use_json(settings.log_format, settings.environment)
        else structlog.dev.ConsoleRenderer()
    )
    shared_processors = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
    ]
    structlog.configure(
        processors=shared_processors
        + [structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )
    formatter = structlog.stdlib.ProcessorFormatter(
        processor=renderer,
        foreign_pre_chain=shared_processors,
    )
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Propagate/issue ``X-Request-ID`` and emit one access log per request."""

    async def dispatch(self, request: Request, call_next) -> Response:
        request_id = request.headers.get(REQUEST_ID_HEADER) or uuid.uuid4().hex
        request.state.request_id = request_id
        structlog.contextvars.bind_contextvars(request_id=request_id)
        start = time.perf_counter()
        log = structlog.get_logger("app.access")
        try:
            response = await call_next(request)
            duration_ms = round((time.perf_counter() - start) * 1000, 2)
            response.headers[REQUEST_ID_HEADER] = request_id
            log.info(
                "request",
                request_id=request_id,
                method=request.method,
                path=request.url.path,
                status_code=response.status_code,
                duration_ms=duration_ms,
            )
            return response
        finally:
            structlog.contextvars.clear_contextvars()
