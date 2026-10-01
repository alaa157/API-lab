from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession


async def paginate(
    session: AsyncSession, stmt, *, page: int, per_page: int
) -> tuple[list, int]:
    page = max(page, 1)
    per_page = min(max(per_page, 1), 100)
    total = (
        await session.execute(
            select(func.count()).select_from(stmt.order_by(None).subquery())
        )
    ).scalar_one()
    items = (
        await session.execute(stmt.offset((page - 1) * per_page).limit(per_page))
    ).scalars()
    return list(items), total
