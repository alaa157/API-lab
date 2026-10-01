from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import get_settings


def _engine():
    # Bounded connects: a blackholed DB fails fast (healthz/sweep degrade
    # in seconds) instead of hanging workers.
    return create_async_engine(
        get_settings().database_url,
        pool_pre_ping=True,
        connect_args={"timeout": 5},
    )


engine = _engine()
SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def get_session() -> AsyncSession:  # FastAPI dependency
    async with SessionLocal() as session:
        yield session


async def check_db() -> bool:
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False
