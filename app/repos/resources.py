"""SQL only, no business logic (plan sec 3.1)."""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.resource import Resource, ResourceType
from app.repos.base import paginate


async def get(session: AsyncSession, resource_id: uuid.UUID) -> Resource | None:
    return await session.get(Resource, resource_id)


async def list(
    session: AsyncSession,
    *,
    type: ResourceType | None = None,
    is_active: bool | None = None,
    q: str | None = None,
    page: int = 1,
    per_page: int = 20,
) -> tuple[list[Resource], int]:
    stmt = select(Resource).order_by(Resource.name)
    if type is not None:
        stmt = stmt.where(Resource.type == type)
    if is_active is not None:
        stmt = stmt.where(Resource.is_active == is_active)
    if q:
        stmt = stmt.where(Resource.name.ilike(f"%{q}%"))
    return await paginate(session, stmt, page=page, per_page=per_page)


async def create(
    session: AsyncSession,
    *,
    name: str,
    type: ResourceType,
    capacity: int,
    is_active: bool = True,
) -> Resource:
    resource = Resource(
        name=name, type=type, capacity=capacity, is_active=is_active
    )
    session.add(resource)
    await session.flush()
    await session.refresh(resource)
    return resource
