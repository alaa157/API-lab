from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from prometheus_fastapi_instrumentator import Instrumentator
from slowapi.errors import RateLimitExceeded
import structlog

from app.api.health import router as health_router
from app.api.v1.auth import router as auth_router
from app.api.v1.bookings import router as bookings_router
from app.api.v1.resources import router as resources_router
from app.core.config import get_settings
from app.core.database import SessionLocal
from app.core.errors import problem, register_handlers
from app.core.logging import RequestIdMiddleware, configure_logging
from app.core.rate import limiter


async def _sweep_idempotency_keys() -> None:
    try:
        from app.services.idempotency import sweep

        async with SessionLocal() as session:
            await sweep(session)
    except Exception:
        # DB may be unreachable at boot; lazy expiry on read covers it.
        pass


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    backend = "redis" if settings.redis_url else "memory"
    structlog.get_logger("app.startup").info(
        "rate limit storage", backend=backend
    )
    await _sweep_idempotency_keys()
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings)
    app = FastAPI(title=settings.app_name, lifespan=lifespan)

    app.state.limiter = limiter

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.backend_cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(RequestIdMiddleware)

    @app.middleware("http")
    async def security_headers(request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        return response

    register_handlers(app)

    async def _rate_limit_handler(request: Request, exc: RateLimitExceeded):
        return problem(429, "Too Many Requests", str(exc), "rate-limited")

    app.add_exception_handler(RateLimitExceeded, _rate_limit_handler)  # type: ignore[arg-type]

    app.include_router(health_router)
    app.include_router(auth_router, prefix="/api/v1")
    app.include_router(resources_router, prefix="/api/v1")
    app.include_router(bookings_router, prefix="/api/v1")

    @app.get("/", include_in_schema=False)
    async def root() -> dict[str, str]:
        return {"service": settings.app_name, "docs": "/docs"}

    Instrumentator().instrument(app).expose(
        app, endpoint="/metrics", include_in_schema=False
    )

    return app


app = create_app()
