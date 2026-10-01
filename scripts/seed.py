"""Dev seed: admin + staff + demo customer, 3 resources.

Gated to ENVIRONMENT=development. Idempotent (skips existing rows).
Usage: make seed  (needs Postgres reachable via SYNC/DATABASE_URL)
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.core.security import hash_password
from app.models.resource import Resource, ResourceType
from app.models.user import Role, User

SEED_USERS = [
    ("admin@booking.local", "admin12345", Role.admin),
    ("staff@booking.local", "staff12345", Role.staff),
    ("customer@booking.local", "customer123", Role.customer),
]

SEED_RESOURCES = [
    ("Red Room", ResourceType.room, 6),
    ("Desk A1", ResourceType.desk, 1),
    ("Projector X", ResourceType.equipment, 1),
]


async def _ensure_user(session: AsyncSession, email: str, password: str, role: Role):
    existing = (
        await session.execute(select(User).where(User.email == email))
    ).scalar_one_or_none()
    if existing is not None:
        print(f"user exists: {email}")
        return existing
    user = User(email=email, password_hash=hash_password(password), role=role)
    session.add(user)
    await session.flush()
    print(f"user created: {email} ({role.value})")
    return user


async def _ensure_resource(
    session: AsyncSession, name: str, type: ResourceType, capacity: int
):
    existing = (
        await session.execute(select(Resource).where(Resource.name == name))
    ).scalar_one_or_none()
    if existing is not None:
        print(f"resource exists: {name}")
        return existing
    resource = Resource(name=name, type=type, capacity=capacity)
    session.add(resource)
    await session.flush()
    print(f"resource created: {name}")
    return resource


async def main() -> None:
    settings = get_settings()
    if settings.environment != "development":
        print("seed refused: ENVIRONMENT is not 'development'", file=sys.stderr)
        raise SystemExit(1)
    async with SessionLocal() as session:
        for email, password, role in SEED_USERS:
            await _ensure_user(session, email, password, role)
        for name, type, capacity in SEED_RESOURCES:
            await _ensure_resource(session, name, type, capacity)
        await session.commit()
    print("seed done")


if __name__ == "__main__":
    asyncio.run(main())
