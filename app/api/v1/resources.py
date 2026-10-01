import uuid

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, require_role
from app.core.database import get_session
from app.models.resource import ResourceType
from app.models.user import Role, User
from app.repos import resources as repo
from app.schemas.resources import ResourceCreate, ResourceRead, ResourceUpdate
from app.services import resources as service

router = APIRouter(prefix="/resources", tags=["resources"])


@router.get("", response_model=list[ResourceRead])
async def list_resources(
    response: Response,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
    type: ResourceType | None = Query(default=None),
    is_active: bool | None = None,
    q: str | None = None,
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=20, ge=1, le=100),
) -> list:
    items, total = await repo.list(
        session, type=type, is_active=is_active, q=q, page=page, per_page=per_page
    )
    response.headers["X-Total-Count"] = str(total)
    return items


@router.get("/{resource_id}", response_model=ResourceRead)
async def get_resource(
    resource_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    return await service.get_or_404(session, resource_id)


@router.post("", response_model=ResourceRead, status_code=status.HTTP_201_CREATED)
async def create_resource(
    body: ResourceCreate,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(require_role(Role.admin)),
):
    return await service.create(session, body)


@router.patch("/{resource_id}", response_model=ResourceRead)
async def update_resource(
    resource_id: uuid.UUID,
    body: ResourceUpdate,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(require_role(Role.admin)),
):
    resource = await service.get_or_404(session, resource_id)
    return await service.update(session, resource, body)


@router.delete("/{resource_id}", response_model=ResourceRead)
async def delete_resource(
    resource_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(require_role(Role.admin)),
):
    resource = await service.get_or_404(session, resource_id)
    return await service.deactivate(session, resource)
