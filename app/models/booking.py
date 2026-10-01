import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    DateTime,
    Enum,
    ForeignKey,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID, ExcludeConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class BookingStatus(str, enum.Enum):
    pending = "pending"
    confirmed = "confirmed"
    cancelled = "cancelled"
    completed = "completed"


class Booking(Base):
    __tablename__ = "bookings"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "idempotency_key", name="uq_booking_user_key"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    resource_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("resources.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    start_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    end_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[BookingStatus] = mapped_column(
        Enum(BookingStatus, name="booking_status"),
        default=BookingStatus.pending,
        nullable=False,
    )
    idempotency_key: Mapped[str | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


# Race safety behind the service-layer overlap check: no two CONFIRMED
# bookings for the same resource may overlap. '[)' range semantics mean
# back-to-back bookings (end == start) do not conflict.
Booking.__table__.append_constraint(
    ExcludeConstraint(
        (Booking.__table__.c.resource_id, "="),
        (
            func.tstzrange(
                Booking.__table__.c.start_at, Booking.__table__.c.end_at
            ),
            "&&",
        ),
        where=(Booking.__table__.c.status == BookingStatus.confirmed.value),
        name="no_overlapping_confirmed_bookings",
        using="gist",
    )
)
