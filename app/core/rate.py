"""Rate limiting helpers (plan sec 3.6).

Auth routes are limited to 10/min in real environments. Under the test
environment the budget is raised so the suite can't flake on 429s.
"""

from slowapi import Limiter
from slowapi.util import get_remote_address

from app.core.config import Settings, get_settings


def build_limiter(settings: Settings) -> Limiter:
    return Limiter(
        key_func=get_remote_address,
        default_limits=["100/minute"],
        storage_uri=settings.redis_url or "memory://",
    )


limiter = build_limiter(get_settings())


def auth_limit():
    spec = "1000/minute" if get_settings().environment == "testing" else "10/minute"
    return limiter.limit(spec)
