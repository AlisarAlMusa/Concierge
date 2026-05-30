"""Escalation repository — tenant-scoped data access for ``escalations``.

Every statement carries an explicit ``WHERE tenant_id = $1`` clause; RLS
on ``escalations`` is the second wall. The repository owns the raw INSERT
and lookup paths; idempotency / ``IntegrityError`` race recovery lives in
``EscalationService``.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.escalation import Escalation


async def get_for_tenant(
    session: AsyncSession, *, tenant_id: UUID, escalation_id: UUID
) -> Escalation | None:
    """Return one escalation or ``None`` if absent / cross-tenant."""
    stmt = select(Escalation).where(
        Escalation.tenant_id == tenant_id,
        Escalation.id == escalation_id,
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_by_conversation(
    session: AsyncSession, *, tenant_id: UUID, conversation_id: UUID
) -> Escalation | None:
    """Return the escalation for ``(tenant_id, conversation_id)`` or ``None``.

    Used by ``EscalationService.create`` for the FR-012 idempotency
    check and by the race-recovery path after an ``IntegrityError`` on
    the ``uq_escalations_conversation`` unique constraint.
    """
    stmt = select(Escalation).where(
        Escalation.tenant_id == tenant_id,
        Escalation.conversation_id == conversation_id,
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def list_for_tenant(
    session: AsyncSession, *, tenant_id: UUID, limit: int, offset: int
) -> list[Escalation]:
    """Return one page of escalations for a tenant, newest first."""
    stmt = (
        select(Escalation)
        .where(Escalation.tenant_id == tenant_id)
        .order_by(Escalation.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    return list((await session.execute(stmt)).scalars().all())


async def count_for_tenant(session: AsyncSession, *, tenant_id: UUID) -> int:
    """Return the total escalation count for a tenant."""
    stmt = select(func.count(Escalation.id)).where(Escalation.tenant_id == tenant_id)
    return int((await session.execute(stmt)).scalar_one())


async def add(session: AsyncSession, escalation: Escalation) -> None:
    """Stage a new ``Escalation`` row and flush.

    ``IntegrityError`` from the ``uq_escalations_conversation`` unique
    constraint is intentionally not caught here — the service layer owns
    the race-recovery decision (rollback + lookup of the winning row).
    """
    session.add(escalation)
    await session.flush()


async def flush_pending(session: AsyncSession) -> None:
    """Flush in-place mutations on a row previously fetched through the repo."""
    await session.flush()
