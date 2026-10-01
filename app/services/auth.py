"""Business rules and transactions; raises domain errors (plan sec 3.1)."""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core import security
from app.core.errors import ConflictError, ForbiddenError, UnauthorizedError
from app.models.user import Role, User
from app.repos import users as repo
from app.schemas.auth import TokenPair


async def register(
    session: AsyncSession,
    *,
    email: str,
    password: str,
    requested_role: Role,
    admin_override: bool,
) -> User:
    if await repo.get_by_email(session, email) is not None:
        raise ConflictError("email already registered")
    role = requested_role if admin_override else Role.customer
    user = await repo.create_user(
        session,
        email=email,
        password_hash=security.hash_password(password),
        role=role,
    )
    await session.commit()
    return user


async def authenticate(session: AsyncSession, *, email: str, password: str) -> User:
    user = await repo.get_by_email(session, email)
    if user is None or not security.verify_password(password, user.password_hash):
        raise UnauthorizedError("invalid credentials")
    if not user.is_active:
        raise ForbiddenError("account inactive")
    return user


async def issue_pair(session: AsyncSession, user: User) -> TokenPair:
    refresh, expires_at = security.create_refresh_token(user.id)
    await repo.store_refresh_token(
        session,
        user_id=user.id,
        token_hash=security.hash_refresh_token(refresh),
        expires_at=expires_at,
    )
    await session.commit()
    return TokenPair(
        access_token=security.create_access_token(user.id),
        refresh_token=refresh,
    )


async def rotate(session: AsyncSession, refresh_token: str) -> TokenPair:
    payload = security.decode_token(refresh_token, expected_type="refresh")
    record = await repo.get_refresh_token(
        session, security.hash_refresh_token(refresh_token)
    )
    if record is None:
        raise UnauthorizedError("unknown refresh token")
    if record.revoked:
        # Reuse of a rotated token: revoke the whole chain.
        await repo.revoke_user_tokens(session, record.user_id)
        await session.commit()
        raise UnauthorizedError("refresh token reuse detected")

    user = await repo.get_by_id(session, uuid.UUID(payload["sub"]))
    if user is None:
        raise UnauthorizedError("user no longer exists")
    if not user.is_active:
        raise ForbiddenError("account inactive")

    record.revoked = True
    new_refresh, expires_at = security.create_refresh_token(user.id)
    await repo.store_refresh_token(
        session,
        user_id=user.id,
        token_hash=security.hash_refresh_token(new_refresh),
        expires_at=expires_at,
    )
    await session.commit()
    return TokenPair(
        access_token=security.create_access_token(user.id),
        refresh_token=new_refresh,
    )


async def logout(session: AsyncSession, refresh_token: str) -> None:
    record = await repo.get_refresh_token(
        session, security.hash_refresh_token(refresh_token)
    )
    if record is not None and not record.revoked:
        record.revoked = True
    await session.commit()
