"""SQL only, no business logic (plan sec 3.1)."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.idempotency import IdempotencyKey


async def get(
    session: AsyncSession, key: str, user_id: uuid.UUID
) -> IdempotencyKey | None:
    result = await session.execute(
        select(IdempotencyKey).where(
            IdempotencyKey.key == key, IdempotencyKey.user_id == user_id
        )
    )
    return result.scalar_one_or_none()


async def store(
    session: AsyncSession,
    *,
    key: str,
    user_id: uuid.UUID,
    method: str,
    path: str,
    request_hash: str,
    status_code: int,
    response_body: dict[str, Any],
) -> None:
    session.add(
        IdempotencyKey(
            key=key,
            user_id=user_id,
            method=method,
            path=path,
            request_hash=request_hash,
            status_code=status_code,
            response_body=response_body,
        )
    )
    await session.flush()


async def sweep_expired(session: AsyncSession, cutoff: datetime) -> int:
    result = await session.execute(
        delete(IdempotencyKey).where(IdempotencyKey.created_at < cutoff)
    )
    await session.commit()
    return result.rowcount or 0
