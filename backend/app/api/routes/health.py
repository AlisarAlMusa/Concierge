from fastapi import APIRouter
from sqlalchemy import text

from app.db.session import get_session_factory

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@router.get("/ready")
async def ready() -> dict:
    """Check that the database is reachable."""
    factory = get_session_factory()
    async with factory() as session:
        await session.execute(text("SELECT 1"))
    return {"status": "ready"}
