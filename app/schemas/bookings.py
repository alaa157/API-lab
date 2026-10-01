import uuid
from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.booking import BookingStatus

MAX_BOOKING_HOURS = 8


class BookingCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    resource_id: uuid.UUID
    user_id: uuid.UUID | None = None
    start_at: datetime
    end_at: datetime

    @model_validator(mode="after")
    def _check_window(self):
        for label in ("start_at", "end_at"):
            value = getattr(self, label)
            if value.tzinfo is None:
                raise ValueError(f"{label} must be timezone-aware (ISO-8601 UTC)")
        if self.end_at <= self.start_at:
            raise ValueError("end_at must be after start_at")
        duration_hours = (self.end_at - self.start_at).total_seconds() / 3600
        if duration_hours > MAX_BOOKING_HOURS:
            raise ValueError(f"booking may not exceed {MAX_BOOKING_HOURS} hours")
        if self.start_at < datetime.now(timezone.utc):
            raise ValueError("start_at may not be in the past")
        return self


class BookingRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID
    resource_id: uuid.UUID
    start_at: datetime
    end_at: datetime
    status: BookingStatus
    created_at: datetime


class Pagination(BaseModel):
    page: int = Field(default=1, ge=1)
    per_page: int = Field(default=20, ge=1, le=100)
