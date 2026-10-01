"""RFC 7807 problem+json error model and domain exceptions (plan sec 3.2)."""

import logging
from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException

PROBLEM_BASE = "https://api.booking.local/problems"
logger = logging.getLogger(__name__)


class ProblemDetail(BaseModel):
    type: str
    title: str
    status: int
    detail: str
    errors: list[Any] = []


def problem(
    status_code: int, title: str, detail: str, slug: str,
    errors: list[Any] | None = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        media_type="application/problem+json",
        content=ProblemDetail(
            type=f"{PROBLEM_BASE}/{slug}",
            title=title,
            status=status_code,
            detail=detail,
            errors=errors or [],
        ).model_dump(),
    )


class DomainError(Exception):
    status_code: int = 500
    title: str = "Internal Server Error"
    slug: str = "internal-error"

    def __init__(self, detail: str = "", errors: list[Any] | None = None):
        super().__init__(detail)
        self.detail = detail or self.title
        self.errors = errors or []


class NotFoundError(DomainError):
    status_code = status.HTTP_404_NOT_FOUND
    title = "Not Found"
    slug = "not-found"


class ConflictError(DomainError):
    status_code = status.HTTP_409_CONFLICT
    title = "Conflict"
    slug = "conflict"


class ForbiddenError(DomainError):
    status_code = status.HTTP_403_FORBIDDEN
    title = "Forbidden"
    slug = "forbidden"


class UnauthorizedError(DomainError):
    status_code = status.HTTP_401_UNAUTHORIZED
    title = "Unauthorized"
    slug = "unauthorized"


class ValidationError(DomainError):
    status_code = status.HTTP_422_UNPROCESSABLE_CONTENT
    title = "Unprocessable Entity"
    slug = "validation-error"


async def _domain_handler(request: Request, exc: DomainError) -> JSONResponse:
    return problem(exc.status_code, exc.title, exc.detail, exc.slug, exc.errors)


async def _http_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    title = "Not Found" if exc.status_code == 404 else "HTTP Error"
    slug = "not-found" if exc.status_code == 404 else "http-error"
    return problem(exc.status_code, title, str(exc.detail), slug)


async def _validation_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    return problem(
        status.HTTP_422_UNPROCESSABLE_CONTENT,
        "Unprocessable Entity",
        "Request validation failed",
        "validation-error",
        jsonable_encoder(exc.errors()),
    )


async def _internal_error_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.error("Unhandled server error")
    return problem(
        status.HTTP_500_INTERNAL_SERVER_ERROR,
        "Internal Server Error",
        "An unexpected error occurred",
        "internal-error",
    )


def register_handlers(app: FastAPI) -> None:
    app.add_exception_handler(DomainError, _domain_handler)  # type: ignore[arg-type]
    app.add_exception_handler(StarletteHTTPException, _http_handler)  # type: ignore[arg-type]
    app.add_exception_handler(RequestValidationError, _validation_handler)  # type: ignore[arg-type]
    app.add_exception_handler(Exception, _internal_error_handler)
