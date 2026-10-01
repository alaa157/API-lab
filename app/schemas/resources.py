import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.resource import ResourceType


class ResourceCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    type: ResourceType
    capacity: int = Field(ge=1)
    is_active: bool = True


class ResourceUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=200)
    type: ResourceType | None = None
    capacity: int | None = Field(default=None, ge=1)
    is_active: bool | None = None


class ResourceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    type: ResourceType
    capacity: int
    is_active: bool
    created_at: datetime
