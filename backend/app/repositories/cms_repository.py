from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.cms import CmsPage, CmsPageStatus


async def list_published_pages(session: AsyncSession, tenant_id: UUID) -> list[CmsPage]:
    """Return all published CMS pages for a tenant, ordered by creation date.

    Uses ix_cms_pages_tenant_status composite index. Explicit tenant_id filter
    is the primary isolation layer; RLS is defense-in-depth on authenticated routes.
    """
    result = await session.execute(
        select(CmsPage)
        .where(CmsPage.tenant_id == tenant_id, CmsPage.status == CmsPageStatus.published)
        .order_by(CmsPage.created_at)
    )
    return list(result.scalars().all())
