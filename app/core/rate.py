"""Rate limiting helpers (plan sec 3.6).

Auth routes are limited to 10/min in real environments. Under the test
environment the budget is raised so the suite can't flake on 429s.
"""

from slowapi import Limiter
from slowapi.util import get_remote_address

from app.core.config import get_settings

limiter = Limiter(key_func=get_remote_address, default_limits=["100/minute"])


def auth_limit():
    spec = "1000/minute" if get_settings().environment == "testing" else "10/minute"
    return limiter.limit(spec)
