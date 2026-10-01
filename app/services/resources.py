"""Business rules and transactions; raises domain errors (plan sec 3.1)."""

import uuid
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError
from app.models.resource import Resource
from app.repos import bookings as booking_repo
from app.repos import resources as repo
from app.schemas.resources import ResourceCreate, ResourceUpdate


async def get_or_404(session: AsyncSession, resource_id: uuid.UUID) -> Resource:
    resource = await repo.get(session, resource_id)
    if resource is None:
        raise NotFoundError("resource not found")
    return resource


async def create(session: AsyncSession, data: ResourceCreate) -> Resource:
    resource = await repo.create(
        session,
        name=data.name,
        type=data.type,
        capacity=data.capacity,
        is_active=data.is_active,
    )
    await session.commit()
    return resource


async def _guard_deactivation(session: AsyncSession, resource: Resource) -> None:
    now = datetime.now(timezone.utc)
    count = await booking_repo.future_confirmed_count(
        session, resource.id, now
    )
    if count:
        raise ConflictError(
            "resource has future confirmed bookings and cannot be deactivated"
        )


async def update(
    session: AsyncSession, resource: Resource, data: ResourceUpdate
) -> Resource:
    changes = data.model_dump(exclude_unset=True)
    if changes.get("is_active") is False and resource.is_active:
        await _guard_deactivation(session, resource)
    for field, value in changes.items():
        setattr(resource, field, value)
    await session.commit()
    await session.refresh(resource)
    return resource


async def deactivate(session: AsyncSession, resource: Resource) -> Resource:
    if not resource.is_active:
        return resource
    await _guard_deactivation(session, resource)
    resource.is_active = False
    await session.commit()
    await session.refresh(resource)
    return resource
