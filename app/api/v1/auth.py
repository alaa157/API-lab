from fastapi import APIRouter, Depends, Header, Request, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core import security
from app.core.database import get_session
from app.core.rate import auth_limit
from app.models.user import Role, User
from app.schemas.auth import RefreshIn, RegisterIn, TokenPair, UserRead
from app.services import auth as service

router = APIRouter(prefix="/auth", tags=["auth"])


async def _is_admin_token(session: AsyncSession, authorization: str | None) -> bool:
    """True only when the caller presents a valid access token of an active admin."""
    if not authorization or not authorization.lower().startswith("bearer "):
        return False
    try:
        payload = security.decode_token(
            authorization.split(" ", 1)[1], expected_type="access"
        )
        import uuid

        caller = await session.get(User, uuid.UUID(payload["sub"]))
    except Exception:
        return False
    return caller is not None and caller.is_active and caller.role == Role.admin


@router.post("/register", response_model=UserRead, status_code=status.HTTP_201_CREATED)
@auth_limit()
async def register(
    request: Request,
    body: RegisterIn,
    session: AsyncSession = Depends(get_session),
    authorization: str | None = Header(default=None),
) -> User:
    return await service.register(
        session,
        email=body.email,
        password=body.password,
        requested_role=body.role,
        admin_override=await _is_admin_token(session, authorization),
    )


@router.post("/login", response_model=TokenPair)
@auth_limit()
async def login(
    request: Request,
    form: OAuth2PasswordRequestForm = Depends(),
    session: AsyncSession = Depends(get_session),
) -> TokenPair:
    user = await service.authenticate(
        session, email=form.username, password=form.password
    )
    return await service.issue_pair(session, user)


@router.post("/refresh", response_model=TokenPair)
@auth_limit()
async def refresh(
    request: Request,
    body: RefreshIn,
    session: AsyncSession = Depends(get_session),
) -> TokenPair:
    return await service.rotate(session, body.refresh_token)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    body: RefreshIn,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
) -> None:
    await service.logout(session, body.refresh_token)


@router.get("/me", response_model=UserRead)
async def me(user: User = Depends(get_current_user)) -> User:
    return user
