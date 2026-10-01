import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Header, Query, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, require_role
from app.core.database import get_session
from app.models.booking import Booking, BookingStatus
from app.models.user import Role, User
from app.schemas.bookings import BookingCreate, BookingRead
from app.services import bookings as service
from app.services import idempotency as idem

router = APIRouter(prefix="/bookings", tags=["bookings"])


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    responses={201: {"model": BookingRead}},
)
async def create_booking(
    request: Request,
    body: BookingCreate,
    session: AsyncSession = Depends(get_session),
    caller: User = Depends(get_current_user),
    idempotency_key: str | None = Header(default=None),
):
    key = idem.normalize_key(idempotency_key)
    result = await service.create_booking(
        session,
        caller=caller,
        data=body,
        idem_key=key,
        method=request.method,
        path=request.url.path,
    )
    if isinstance(result, idem.Replay):
        return idem.to_response(result)
    return BookingRead.model_validate(result)


@router.get("", response_model=list[BookingRead])
async def list_bookings(
    response: Response,
    session: AsyncSession = Depends(get_session),
    caller: User = Depends(get_current_user),
    resource_id: uuid.UUID | None = None,
    user_id: uuid.UUID | None = None,
    status: BookingStatus | None = None,
    from_: datetime | None = Query(default=None, alias="from"),
    to: datetime | None = Query(default=None, alias="to"),
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=20, ge=1, le=100),
) -> list:
    items, total = await service.list_visible(
        session,
        caller=caller,
        user_id=user_id,
        resource_id=resource_id,
        status=status,
        start_from=from_,
        start_to=to,
        page=page,
        per_page=per_page,
    )
    response.headers["X-Total-Count"] = str(total)
    return items


@router.get("/{booking_id}", response_model=BookingRead)
async def get_booking(
    booking_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    caller: User = Depends(get_current_user),
) -> Booking:
    return await service.get_visible(session, booking_id, caller)


@router.post("/{booking_id}/confirm", response_model=BookingRead)
async def confirm_booking(
    booking_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    caller: User = Depends(require_role(Role.staff, Role.admin)),
) -> Booking:
    booking = await service.get_visible(session, booking_id, caller)
    return await service.confirm(session, booking)


@router.post("/{booking_id}/cancel", response_model=BookingRead)
async def cancel_booking(
    booking_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    caller: User = Depends(get_current_user),
) -> Booking:
    booking = await service.get_visible(session, booking_id, caller)
    return await service.cancel(session, booking, caller)
