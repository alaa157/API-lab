"""Phase 4.2 rate-limit storage tests (no Redis server needed)."""

from limits.storage.memory import MemoryStorage
from limits.storage.redis import RedisStorage

from app.core.config import Settings
from app.core.rate import build_limiter


def test_testing_env_uses_memory_storage():
    settings = Settings(redis_url=None, environment="testing")
    limiter = build_limiter(settings)
    assert limiter._storage_uri == "memory://"
    assert isinstance(limiter._storage, MemoryStorage)


def test_redis_url_selects_redis_storage():
    url = "redis://localhost:6379/0"
    settings = Settings(redis_url=url, environment="staging")
    limiter = build_limiter(settings)
    assert limiter._storage_uri == url
    assert isinstance(limiter._storage, RedisStorage)
