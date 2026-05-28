"""EscalationService — flag a conversation for a human (Spec 012 FR-008…FR-012).

Two callers:

* The ``escalate`` agent tool — when the LLM picks it.
* ``HumanWorkflow`` — when the router classified the inbound turn as
  ``human``.

Idempotency (Spec 012 FR-012): a unique constraint on ``conversation_id``
means a second create attempt returns the existing row instead of
inserting a duplicate. After the row is durable, we flip the parent
``Conversation.status`` to ``escalated`` (FR-009) — that flip is
idempotent on its own.

Owner: Person B.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import structlog
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.conversation import ConversationStatus
from app.models.escalation import Escalation, EscalationStatus
from app.services.conversation_service import ConversationService
from app.services.tools.escalate import EscalateResult

logger = structlog.get_logger(__name__)


class EscalationService:
    def __init__(
        self,
        *,
        session: AsyncSession,
        conversation_service: ConversationService,
    ) -> None:
        self._session = session
        self._conversations = conversation_service

    async def create(
        self,
        *,
        tenant_id: UUID,
        conversation_id: UUID,
        reason: str,
        context: str | None,
    ) -> EscalateResult:
        """Insert (or fetch existing) escalation, then flip conversation status.

        Two-step idempotency: we first check for an existing row, and as a
        belt-and-braces guard we catch ``IntegrityError`` on the unique
        constraint in case two concurrent turns race past the lookup.
        """
        existing = await self._lookup(tenant_id, conversation_id)
        if existing is not None:
            await self._mark_conversation_escalated(tenant_id, conversation_id)
            logger.info(
                "escalation.idempotent_return",
                tenant_id=str(tenant_id),
                conversation_id=str(conversation_id),
                escalation_id=str(existing.id),
            )
            return EscalateResult(escalation_id=existing.id, status="created")

        escalation = Escalation(
            id=uuid4(),
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            reason=reason,
            context=context,
            status=EscalationStatus.open,
        )
        self._session.add(escalation)
        try:
            await self._session.flush()
        except IntegrityError as exc:
            # Race: another turn inserted between our lookup and flush. Roll
            # back to recover this session, then return the row that won.
            await self._session.rollback()
            logger.info(
                "escalation.race_recovered",
                tenant_id=str(tenant_id),
                conversation_id=str(conversation_id),
                error=str(exc),
            )
            winner = await self._lookup(tenant_id, conversation_id)
            if winner is None:
                # Shouldn't happen — unique violation without a row. Re-raise.
                raise
            await self._mark_conversation_escalated(tenant_id, conversation_id)
            return EscalateResult(escalation_id=winner.id, status="created")

        await self._mark_conversation_escalated(tenant_id, conversation_id)
        logger.info(
            "escalation.created",
            tenant_id=str(tenant_id),
            conversation_id=str(conversation_id),
            escalation_id=str(escalation.id),
        )
        return EscalateResult(escalation_id=escalation.id, status="created")

    # ── Admin surface (Spec 012 FR-010 / FR-011) ───────────────────────────
    async def list_escalations(
        self,
        *,
        tenant_id: UUID,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[Escalation], int]:
        """Return ``(items, total)`` for the caller's tenant, newest first.

        Default page size matches Spec 012 Assumptions (50). Both the SQL
        filter and the RLS policy scope rows to ``tenant_id``.
        """
        if limit < 1 or limit > 500:
            raise ValueError("list_escalations: limit must be in [1, 500]")
        if offset < 0:
            raise ValueError("list_escalations: offset must be >= 0")

        total_stmt = select(func.count(Escalation.id)).where(Escalation.tenant_id == tenant_id)
        total = (await self._session.execute(total_stmt)).scalar_one()

        items_stmt = (
            select(Escalation)
            .where(Escalation.tenant_id == tenant_id)
            .order_by(Escalation.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        items = (await self._session.execute(items_stmt)).scalars().all()
        return list(items), int(total)

    async def get_escalation(self, *, tenant_id: UUID, escalation_id: UUID) -> Escalation | None:
        """Return one escalation or ``None`` if absent / cross-tenant."""
        stmt = select(Escalation).where(
            Escalation.tenant_id == tenant_id,
            Escalation.id == escalation_id,
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def update_escalation(
        self,
        *,
        tenant_id: UUID,
        escalation_id: UUID,
        status: EscalationStatus,
    ) -> Escalation | None:
        """Update an escalation's status. Returns ``None`` if not found.

        Spec 012 FR-011: admin transitions the row through its lifecycle.
        ``reason`` / ``context`` were captured by the agent tool at create
        time and are intentionally immutable from the admin surface.

        Note: the parent ``Conversation.status`` is *not* flipped back to
        ``active`` when an escalation is resolved. Tenant ops typically
        keep escalated conversations in a separate review state until the
        full erasure flow lands; flipping conversation status as a
        side-effect of resolve would require business-decision design
        that is intentionally out of this PR's scope.
        """
        escalation = await self.get_escalation(tenant_id=tenant_id, escalation_id=escalation_id)
        if escalation is None:
            return None

        escalation.status = status
        await self._session.flush()
        logger.info(
            "escalation.patched",
            tenant_id=str(tenant_id),
            escalation_id=str(escalation.id),
            status=status.value,
        )
        return escalation

    async def _lookup(self, tenant_id: UUID, conversation_id: UUID) -> Escalation | None:
        stmt = select(Escalation).where(
            Escalation.tenant_id == tenant_id,
            Escalation.conversation_id == conversation_id,
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def _mark_conversation_escalated(self, tenant_id: UUID, conversation_id: UUID) -> None:
        await self._conversations.set_status(
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            status=ConversationStatus.escalated,
        )
