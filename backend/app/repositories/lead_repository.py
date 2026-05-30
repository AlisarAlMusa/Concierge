"""Lead repository — tenant-scoped data access for ``leads``.

Every statement carries an explicit ``WHERE tenant_id = $1`` clause; RLS
on ``leads`` is the second wall. Writes flush once so the caller's
transaction boundary stays predictable.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.lead import Lead


async def get_for_tenant(
    session: AsyncSession, *, tenant_id: UUID, lead_id: UUID
) -> Lead | None:
    """Return one lead or ``None`` if absent / cross-tenant."""
    stmt = select(Lead).where(Lead.tenant_id == tenant_id, Lead.id == lead_id)
    return (await session.execute(stmt)).scalar_one_or_none()


async def list_for_tenant(
    session: AsyncSession, *, tenant_id: UUID, limit: int, offset: int
) -> list[Lead]:
    """Return one page of leads for a tenant, newest first."""
    stmt = (
        select(Lead)
        .where(Lead.tenant_id == tenant_id)
        .order_by(Lead.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    return list((await session.execute(stmt)).scalars().all())


async def count_for_tenant(session: AsyncSession, *, tenant_id: UUID) -> int:
    """Return the total lead count for a tenant."""
    stmt = select(func.count(Lead.id)).where(Lead.tenant_id == tenant_id)
    return int((await session.execute(stmt)).scalar_one())


async def count_recent_for_session(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    visitor_session_id: UUID,
    since: datetime,
) -> int:
    """Count leads for ``(tenant_id, visitor_session_id)`` since ``since``.

    Used by ``LeadService._enforce_session_limit`` to apply the per-session
    rate limit (Spec 012 FR-003).
    """
    stmt = (
        select(func.count(Lead.id))
        .where(Lead.tenant_id == tenant_id)
        .where(Lead.visitor_session_id == visitor_session_id)
        .where(Lead.created_at >= since)
    )
    return int((await session.execute(stmt)).scalar_one())


async def add(session: AsyncSession, lead: Lead) -> None:
    """Stage a new ``Lead`` row and flush."""
    session.add(lead)
    await session.flush()


async def flush_pending(session: AsyncSession) -> None:
    """Flush in-place mutations on a row previously fetched through the repo."""
    await session.flush()


async def remove(session: AsyncSession, lead: Lead) -> None:
    """Delete one ``Lead`` row and flush."""
    await session.delete(lead)
    await session.flush()
