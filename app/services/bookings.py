"""Business rules and transactions; raises domain errors (plan sec 3.1)."""

import uuid
from datetime import datetime

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import (
    ConflictError,
    ForbiddenError,
    NotFoundError,
    ValidationError,
)
from app.models.booking import Booking, BookingStatus
from app.models.user import Role, User
from app.repos import bookings as repo
from app.repos import resources as resource_repo
from app.repos import users as user_repo
from app.schemas.bookings import BookingCreate, BookingRead
from app.services import idempotency as idem
from app.services.idempotency import Replay


async def _resolve_target(
    session: AsyncSession, caller: User, user_id: uuid.UUID | None
) -> User:
    if caller.role == Role.customer:
        if user_id is not None and user_id != caller.id:
            raise ForbiddenError("customers may only book for themselves")
        return caller
    if user_id is None:
        return caller
    target = await user_repo.get_by_id(session, user_id)
    if target is None:
        raise NotFoundError("user not found")
    if not target.is_active:
        raise ValidationError("inactive users cannot hold bookings")
    return target


async def create_booking(
    session: AsyncSession,
    *,
    caller: User,
    data: BookingCreate,
    idem_key: str | None,
    method: str,
    path: str,
) -> Booking | Replay:
    target = await _resolve_target(session, caller, data.user_id)

    resource = await resource_repo.get(session, data.resource_id)
    if resource is None:
        raise NotFoundError("resource not found")
    if not resource.is_active:
        raise ValidationError("inactive resources cannot be booked")

    req_hash = idem.hash_request(data.model_dump(mode="json"))

    if idem_key is not None:
        record = await idem.lookup(session, key=idem_key, user_id=caller.id)
        if record is not None:
            return idem.check_payload(record, req_hash)

    if await repo.has_confirmed_overlap(
        session,
        resource_id=data.resource_id,
        start_at=data.start_at,
        end_at=data.end_at,
    ):
        raise ConflictError("resource already booked for that window")

    try:
        booking = await repo.create(
            session,
            user_id=target.id,
            resource_id=data.resource_id,
            start_at=data.start_at,
            end_at=data.end_at,
            idempotency_key=idem_key,
        )
        body = BookingRead.model_validate(booking).model_dump(mode="json")
        if idem_key is not None:
            await idem.store(
                session,
                key=idem_key,
                user_id=caller.id,
                method=method,
                path=path,
                request_hash=req_hash,
                status_code=201,
                response_body=body,
            )
        await session.commit()
    except IntegrityError:
        # Lost a race (duplicate key or overlapping confirm committed first).
        await session.rollback()
        if idem_key is not None:
            record = await idem.lookup(session, key=idem_key, user_id=caller.id)
            if record is not None:
                return idem.check_payload(record, req_hash)
        raise ConflictError("booking conflicts with an existing booking")

    return booking


async def get_visible(
    session: AsyncSession, booking_id: uuid.UUID, caller: User
) -> Booking:
    booking = await repo.get(session, booking_id)
    if booking is None:
        raise NotFoundError("booking not found")
    if caller.role == Role.customer and booking.user_id != caller.id:
        raise ForbiddenError("not your booking")
    return booking


async def list_visible(
    session: AsyncSession,
    *,
    caller: User,
    user_id: uuid.UUID | None,
    resource_id: uuid.UUID | None,
    status: BookingStatus | None,
    start_from: datetime | None,
    start_to: datetime | None,
    page: int,
    per_page: int,
) -> tuple[list[Booking], int]:
    if caller.role == Role.customer:
        if user_id is not None and user_id != caller.id:
            raise ForbiddenError("customers may only list their own bookings")
        user_id = caller.id
    return await repo.list(
        session,
        user_id=user_id,
        resource_id=resource_id,
        status=status,
        start_from=start_from,
        start_to=start_to,
        page=page,
        per_page=per_page,
    )


async def confirm(session: AsyncSession, booking: Booking) -> Booking:
    if booking.status == BookingStatus.confirmed:
        return booking  # idempotent
    if booking.status != BookingStatus.pending:
        raise ConflictError(f"cannot confirm a {booking.status.value} booking")
    if await repo.has_confirmed_overlap(
        session,
        resource_id=booking.resource_id,
        start_at=booking.start_at,
        end_at=booking.end_at,
        exclude_id=booking.id,
    ):
        raise ConflictError("resource already booked for that window")
    booking.status = BookingStatus.confirmed
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise ConflictError("booking conflicts with an existing booking")
    await session.refresh(booking)
    return booking


async def cancel(session: AsyncSession, booking: Booking, caller: User) -> Booking:
    if caller.role == Role.customer and booking.user_id != caller.id:
        raise ForbiddenError("not your booking")
    if booking.status == BookingStatus.completed:
        raise ConflictError("completed bookings cannot be cancelled")
    if booking.status == BookingStatus.cancelled:
        return booking  # idempotent
    booking.status = BookingStatus.cancelled
    await session.commit()
    await session.refresh(booking)
    return booking
