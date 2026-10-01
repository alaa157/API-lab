from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from slowapi.errors import RateLimitExceeded

from app.api.health import router as health_router
from app.api.v1.auth import router as auth_router
from app.core.config import get_settings
from app.core.errors import problem, register_handlers
from app.core.rate import limiter


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title=settings.app_name)

    app.state.limiter = limiter

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.backend_cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

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

    @app.get("/", include_in_schema=False)
    async def root() -> dict[str, str]:
        return {"service": settings.app_name, "docs": "/docs"}

    return app


app = create_app()
