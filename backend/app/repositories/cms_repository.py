"""CMS page repository — tenant-scoped data access for ``cms_pages``.

Every read carries an explicit ``WHERE tenant_id = $1`` clause; RLS on
``cms_pages`` is the second wall. All writes flush once so the caller
(route → service → here) keeps a single transaction boundary owned by the
HTTP dependency.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.cms import CmsPage, CmsPageStatus


async def get_by_id(
    session: AsyncSession, *, tenant_id: UUID, page_id: UUID
) -> CmsPage | None:
    """Return one page or ``None`` if absent / cross-tenant."""
    stmt = select(CmsPage).where(
        CmsPage.tenant_id == tenant_id,
        CmsPage.id == page_id,
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_by_slug(
    session: AsyncSession, *, tenant_id: UUID, slug: str
) -> CmsPage | None:
    """Return the page matching ``(tenant_id, slug)`` or ``None``."""
    stmt = select(CmsPage).where(
        CmsPage.tenant_id == tenant_id,
        CmsPage.slug == slug,
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def list_for_tenant(
    session: AsyncSession, *, tenant_id: UUID, limit: int, offset: int
) -> list[CmsPage]:
    """Return one page of rows for a tenant, newest first."""
    stmt = (
        select(CmsPage)
        .where(CmsPage.tenant_id == tenant_id)
        .order_by(CmsPage.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    return list((await session.execute(stmt)).scalars().all())


async def count_for_tenant(session: AsyncSession, *, tenant_id: UUID) -> int:
    """Return the total page count for a tenant."""
    stmt = select(func.count(CmsPage.id)).where(CmsPage.tenant_id == tenant_id)
    return int((await session.execute(stmt)).scalar_one())


async def list_published_for_tenant(
    session: AsyncSession, *, tenant_id: UUID
) -> list[CmsPage]:
    """Every published page for a tenant in creation order.

    Used by ``CmsPageService.reindex_all`` — kept distinct from
    ``list_published_pages`` (which is keyed off the same composite
    index but exposes a different ordering for other callers).
    """
    stmt = (
        select(CmsPage)
        .where(CmsPage.tenant_id == tenant_id)
        .where(CmsPage.status == CmsPageStatus.published)
        .order_by(CmsPage.created_at.asc())
    )
    return list((await session.execute(stmt)).scalars().all())


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


async def add(session: AsyncSession, page: CmsPage) -> None:
    """Stage one new ``CmsPage`` row and flush."""
    session.add(page)
    await session.flush()


async def flush_pending(session: AsyncSession) -> None:
    """Flush in-place mutations on a row previously fetched through the repo."""
    await session.flush()


async def remove(session: AsyncSession, page: CmsPage) -> None:
    """Delete one ``CmsPage`` row and flush."""
    await session.delete(page)
    await session.flush()
