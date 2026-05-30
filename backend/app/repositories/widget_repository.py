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


# ──────────────────────────────────────────────────────────────────────────────
# Mutation helpers used by the tenant-admin management routes.
#
# All three carry the explicit ``tenant_id`` predicate as defense in depth
# on top of the RLS policy set by ``require_tenant_admin``.
# ──────────────────────────────────────────────────────────────────────────────


async def get_for_tenant_admin(
    session: AsyncSession, *, tenant_id: UUID, widget_id: UUID
) -> Widget | None:
    """Return ``(widget_id, tenant_id)`` for the admin surface.

    Unlike ``get_for_tenant`` (visitor-runtime), this lookup does NOT
    filter on ``enabled`` — admins must be able to inspect and edit a
    disabled widget to re-enable it.
    """
    stmt = select(Widget).where(
        Widget.id == widget_id,
        Widget.tenant_id == tenant_id,
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def public_id_exists(
    session: AsyncSession, *, public_widget_id: str
) -> bool:
    """``True`` iff a row with this ``public_widget_id`` already exists.

    Used by ``WidgetService.create_widget`` to retry on the (astronomically
    rare) generation collision before relying on the unique constraint to
    surface as an IntegrityError.
    """
    stmt = select(Widget.id).where(Widget.public_widget_id == public_widget_id)
    return (await session.execute(stmt)).first() is not None


async def add(session: AsyncSession, widget: Widget) -> None:
    """Stage a new ``Widget`` for INSERT in the caller's transaction."""
    session.add(widget)


async def remove(session: AsyncSession, widget: Widget) -> None:
    """Stage a DELETE for ``widget`` in the caller's transaction."""
    await session.delete(widget)


async def flush_pending(session: AsyncSession) -> None:
    """Flush any staged INSERT/UPDATE/DELETE so DB-side defaults are
    visible to subsequent reads within the same transaction.

    Mirrors ``cms_repository.flush_pending`` so the service layer never
    touches ``session.flush`` directly.
    """
    await session.flush()
