from fastapi import APIRouter, status

from app.core.database import check_db

router = APIRouter(tags=["health"])


@router.get("/healthz", status_code=status.HTTP_200_OK)
async def healthz() -> dict[str, str]:
    db_up = await check_db()
    if db_up:
        return {"status": "ok", "db": "up"}
    return {"status": "degraded", "db": "down"}
