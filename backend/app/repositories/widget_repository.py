"""Widget repository — tenant-scoped data access for ``widgets``.

The ``get_by_public_id`` lookup intentionally has no ``tenant_id``
predicate — it runs before the session has a tenant context (it's how a
host site bootstraps the token in the first place). RLS on ``widgets``
is relaxed to allow that pre-auth read; the lookup is constrained to a
non-secret public identifier and an ``enabled = TRUE`` clause so this is
safe. Every other lookup carries the explicit ``WHERE tenant_id = $1``
clause; RLS is the second wall.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.widget import Widget


async def get_by_public_id(
    session: AsyncSession, *, public_widget_id: str
) -> Widget | None:
    """Return the enabled widget for ``public_widget_id``, or ``None``.

    Disabled widgets are filtered server-side so callers cannot mint
    session tokens for them.
    """
    stmt = select(Widget).where(
        Widget.public_widget_id == public_widget_id,
        Widget.enabled.is_(True),
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_for_tenant(
    session: AsyncSession, *, tenant_id: UUID, widget_id: UUID
) -> Widget | None:
    """Return the enabled widget for ``(widget_id, tenant_id)`` or ``None``.

    Explicit ``WHERE tenant_id = $1`` in addition to RLS so cross-tenant
    lookup is impossible even if RLS were bypassed by a future test
    harness.
    """
    stmt = select(Widget).where(
        Widget.id == widget_id,
        Widget.tenant_id == tenant_id,
        Widget.enabled.is_(True),
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def list_for_tenant(session: AsyncSession, *, tenant_id: UUID) -> list[Widget]:
    """Return every widget for a tenant (enabled or not), ordered by name."""
    stmt = select(Widget).where(Widget.tenant_id == tenant_id).order_by(Widget.name)
    return list((await session.execute(stmt)).scalars().all())


async def get_widget_by_tenant(session: AsyncSession, tenant_id: UUID) -> Widget | None:
    """Return the first enabled widget for a tenant, or None if none configured."""
    result = await session.execute(
        select(Widget).where(Widget.tenant_id == tenant_id, Widget.enabled.is_(True)).limit(1)
    )
    return result.scalar_one_or_none()
