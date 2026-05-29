from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.widget import Widget


async def get_widget_by_tenant(session: AsyncSession, tenant_id: UUID) -> Widget | None:
    """Return the first enabled widget for a tenant, or None if none configured."""
    result = await session.execute(
        select(Widget).where(Widget.tenant_id == tenant_id, Widget.enabled.is_(True)).limit(1)
    )
    return result.scalar_one_or_none()
