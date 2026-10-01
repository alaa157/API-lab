"""SQL only, no business logic (plan sec 3.1)."""

import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import RefreshToken, Role, User


async def get_by_email(session: AsyncSession, email: str) -> User | None:
    result = await session.execute(select(User).where(User.email == email))
    return result.scalar_one_or_none()


async def get_by_id(session: AsyncSession, user_id: uuid.UUID) -> User | None:
    return await session.get(User, user_id)


async def create_user(
    session: AsyncSession, *, email: str, password_hash: str, role: Role
) -> User:
    user = User(email=email, password_hash=password_hash, role=role)
    session.add(user)
    await session.flush()
    await session.refresh(user)
    return user


async def store_refresh_token(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    token_hash: str,
    expires_at: datetime,
) -> RefreshToken:
    record = RefreshToken(
        user_id=user_id, token_hash=token_hash, expires_at=expires_at
    )
    session.add(record)
    await session.flush()
    return record


async def get_refresh_token(
    session: AsyncSession, token_hash: str
) -> RefreshToken | None:
    result = await session.execute(
        select(RefreshToken).where(RefreshToken.token_hash == token_hash)
    )
    return result.scalar_one_or_none()


async def revoke_user_tokens(session: AsyncSession, user_id: uuid.UUID) -> None:
    result = await session.execute(
        select(RefreshToken).where(
            RefreshToken.user_id == user_id, RefreshToken.revoked.is_(False)
        )
    )
    for record in result.scalars():
        record.revoked = True
    await session.flush()
