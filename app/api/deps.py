import uuid

from fastapi import Depends
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import security
from app.core.database import get_session
from app.core.errors import ForbiddenError, UnauthorizedError
from app.models.user import Role, User
from app.repos import users as repo

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")


async def get_current_user(
    session: AsyncSession = Depends(get_session),
    token: str = Depends(oauth2_scheme),
) -> User:
    payload = security.decode_token(token, expected_type="access")
    user = await repo.get_by_id(session, uuid.UUID(payload["sub"]))
    if user is None:
        raise UnauthorizedError("user no longer exists")
    if not user.is_active:
        raise ForbiddenError("account inactive")
    return user


def require_role(*roles: Role):
    async def checker(user: User = Depends(get_current_user)) -> User:
        if user.role not in roles:
            raise ForbiddenError("insufficient role")
        return user

    return checker
