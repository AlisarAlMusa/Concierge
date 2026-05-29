"""PublicSiteRepository — read-only queries for the public tenant website.

All queries use explicit tenant_id filters. No RLS context is set on public
routes (the session has no authenticated user), so repository-layer isolation
is the only guard here — consistent with constitution Principle I.
"""

from __future__ import annotations

import asyncio
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.cms import CmsPage, CmsPageStatus
from app.models.tenant import Tenant
from app.models.tenant_config import TenantConfig
from app.models.widget import Widget


async def get_tenant_by_slug(session: AsyncSession, slug: str) -> Tenant | None:
    result = await session.execute(select(Tenant).where(Tenant.slug == slug))
    return result.scalar_one_or_none()


async def get_tenant_config(session: AsyncSession, tenant_id: UUID) -> TenantConfig | None:
    result = await session.execute(select(TenantConfig).where(TenantConfig.tenant_id == tenant_id))
    return result.scalar_one_or_none()


async def get_published_pages(session: AsyncSession, tenant_id: UUID) -> list[CmsPage]:
    result = await session.execute(
        select(CmsPage)
        .where(CmsPage.tenant_id == tenant_id, CmsPage.status == CmsPageStatus.published)
        .order_by(CmsPage.created_at)
    )
    return list(result.scalars().all())


async def get_widget(session: AsyncSession, tenant_id: UUID) -> Widget | None:
    result = await session.execute(
        select(Widget).where(Widget.tenant_id == tenant_id, Widget.enabled.is_(True)).limit(1)
    )
    return result.scalar_one_or_none()


async def get_site_data(
    session: AsyncSession, tenant_id: UUID
) -> tuple[TenantConfig | None, list[CmsPage], Widget | None]:
    """Fetch config, published pages, and widget in parallel."""
    config, pages, widget = await asyncio.gather(
        get_tenant_config(session, tenant_id),
        get_published_pages(session, tenant_id),
        get_widget(session, tenant_id),
    )
    return config, pages, widget
