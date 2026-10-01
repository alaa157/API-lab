"""SQL only, no business logic (plan sec 3.1)."""

import uuid
from datetime import datetime

from sqlalchemy import and_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.booking import Booking, BookingStatus
from app.repos.base import paginate


async def get(session: AsyncSession, booking_id: uuid.UUID) -> Booking | None:
    return await session.get(Booking, booking_id)


async def list(
    session: AsyncSession,
    *,
    user_id: uuid.UUID | None = None,
    resource_id: uuid.UUID | None = None,
    status: BookingStatus | None = None,
    start_from: datetime | None = None,
    start_to: datetime | None = None,
    page: int = 1,
    per_page: int = 20,
) -> tuple[list[Booking], int]:
    stmt = select(Booking).order_by(Booking.start_at)
    if user_id is not None:
        stmt = stmt.where(Booking.user_id == user_id)
    if resource_id is not None:
        stmt = stmt.where(Booking.resource_id == resource_id)
    if status is not None:
        stmt = stmt.where(Booking.status == status)
    if start_from is not None:
        stmt = stmt.where(Booking.start_at >= start_from)
    if start_to is not None:
        stmt = stmt.where(Booking.start_at < start_to)
    return await paginate(session, stmt, page=page, per_page=per_page)


async def has_confirmed_overlap(
    session: AsyncSession,
    *,
    resource_id: uuid.UUID,
    start_at: datetime,
    end_at: datetime,
    exclude_id: uuid.UUID | None = None,
) -> bool:
    stmt = select(Booking.id).where(
        Booking.resource_id == resource_id,
        Booking.status == BookingStatus.confirmed,
        Booking.start_at < end_at,
        Booking.end_at > start_at,
    )
    if exclude_id is not None:
        stmt = stmt.where(Booking.id != exclude_id)
    result = await session.execute(stmt.limit(1))
    return result.scalar_one_or_none() is not None


async def future_confirmed_count(
    session: AsyncSession, resource_id: uuid.UUID, now: datetime
) -> int:
    from sqlalchemy import func

    result = await session.execute(
        select(func.count())
        .select_from(Booking)
        .where(
            Booking.resource_id == resource_id,
            Booking.status == BookingStatus.confirmed,
            Booking.end_at > now,
        )
    )
    return result.scalar_one()


async def release_key(
    session: AsyncSession, user_id: uuid.UUID, key: str
) -> None:
    """Free an idempotency-key slot (used when its stored response expired).

    The original booking row keeps existing; a late retry is a new request
    and needs the (user_id, idempotency_key) slot back.
    """
    await session.execute(
        update(Booking)
        .where(Booking.user_id == user_id, Booking.idempotency_key == key)
        .values(idempotency_key=None)
    )
    await session.flush()


async def create(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    resource_id: uuid.UUID,
    start_at: datetime,
    end_at: datetime,
    idempotency_key: str | None = None,
) -> Booking:
    booking = Booking(
        user_id=user_id,
        resource_id=resource_id,
        start_at=start_at,
        end_at=end_at,
        status=BookingStatus.pending,
        idempotency_key=idempotency_key,
    )
    session.add(booking)
    await session.flush()
    await session.refresh(booking)
    return booking
