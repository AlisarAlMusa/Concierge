"""LeadService — durable lead capture with per-session rate limit (Spec 012).

``capture`` is invoked from the ``capture_lead`` tool. The tool registry
translates ``RateLimitError`` into a ``ToolError(code='rate_limited')``
that the agent can recover from (it will typically apologize and stop
asking for contact details), so we raise that exception when the visitor
has hit ``Settings.LEAD_CAPTURE_LIMIT_PER_SESSION`` within the last
``Settings.LEAD_CAPTURE_WINDOW_HOURS``.

``tenant_id`` always comes from ``ToolContext`` (which itself comes from
the verified widget token); we never accept it via tool arguments. Spec
012 FR-002.

Owner: Person B.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import RateLimitError
from app.models.lead import Lead, LeadStatus
from app.services.tools.capture_lead import CaptureLeadResult

logger = structlog.get_logger(__name__)


class LeadService:
    def __init__(self, *, session: AsyncSession, settings: Settings) -> None:
        self._session = session
        self._limit = settings.LEAD_CAPTURE_LIMIT_PER_SESSION
        self._window = timedelta(hours=settings.LEAD_CAPTURE_WINDOW_HOURS)

    async def capture(
        self,
        *,
        tenant_id: UUID,
        conversation_id: UUID,
        visitor_session_id: UUID | None,
        name: str | None,
        email: str | None,
        phone: str | None,
        intent: str,
        context: str | None,
    ) -> CaptureLeadResult:
        """Insert one ``Lead`` row, enforcing the per-session window.

        The rate limit is intentionally per ``visitor_session_id`` rather
        than per tenant or per conversation: the PDF threat model is the
        injection-driven spam cannon, which is bound to a single session
        in practice. A null ``visitor_session_id`` skips the limit so old
        clients that don't carry one still work.
        """
        if visitor_session_id is not None:
            await self._enforce_session_limit(tenant_id, visitor_session_id)

        lead = Lead(
            id=uuid4(),
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            visitor_session_id=visitor_session_id,
            name=name,
            email=email,
            phone=phone,
            intent=intent,
            context=context,
            lead_score=None,  # Owner C's /predict-lead-score lands later
            source="agent",
        )
        self._session.add(lead)
        await self._session.flush()
        logger.info(
            "lead.captured",
            tenant_id=str(tenant_id),
            conversation_id=str(conversation_id),
            visitor_session_id=str(visitor_session_id) if visitor_session_id else None,
            lead_id=str(lead.id),
        )
        return CaptureLeadResult(lead_id=lead.id, status="created")

    # ── Admin surface (Spec 012 FR-005 / FR-006 / FR-007) ──────────────────
    async def list_leads(
        self,
        *,
        tenant_id: UUID,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[Lead], int]:
        """Return ``(items, total)`` for the caller's tenant, newest first.

        Pagination defaults from Spec 012 Assumptions (page size 50). Both
        the SQL filter and the RLS policy scope rows to ``tenant_id`` —
        the two-wall pattern used everywhere else in the codebase.
        """
        if limit < 1 or limit > 500:
            raise ValueError("list_leads: limit must be in [1, 500]")
        if offset < 0:
            raise ValueError("list_leads: offset must be >= 0")

        total_stmt = select(func.count(Lead.id)).where(Lead.tenant_id == tenant_id)
        total = (await self._session.execute(total_stmt)).scalar_one()

        items_stmt = (
            select(Lead)
            .where(Lead.tenant_id == tenant_id)
            .order_by(Lead.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        items = (await self._session.execute(items_stmt)).scalars().all()
        return list(items), int(total)

    async def get_lead(self, *, tenant_id: UUID, lead_id: UUID) -> Lead | None:
        """Return one lead or ``None`` if absent / cross-tenant.

        Cross-tenant access surfaces as ``None`` here so the route can map
        it to a 404 — never a 403, which would leak existence.
        """
        stmt = select(Lead).where(Lead.tenant_id == tenant_id, Lead.id == lead_id)
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def update_lead(
        self,
        *,
        tenant_id: UUID,
        lead_id: UUID,
        status: LeadStatus | None = None,
        notes: str | None = None,
    ) -> Lead | None:
        """Partial update of one lead. Returns ``None`` if not found.

        Spec 012 FR-006: admin may update ``status`` and ``notes``. Other
        columns are visitor-provided or pipeline-owned and not mutable
        through this surface. Passing both fields as ``None`` is a no-op
        but still returns the row so the client gets a fresh view.
        """
        lead = await self.get_lead(tenant_id=tenant_id, lead_id=lead_id)
        if lead is None:
            return None

        if status is not None:
            lead.status = status
        if notes is not None:
            # Empty string is an explicit clear; ``None`` means "no change".
            lead.notes = notes or None

        await self._session.flush()
        logger.info(
            "lead.patched",
            tenant_id=str(tenant_id),
            lead_id=str(lead.id),
            status=lead.status.value,
            notes_changed=notes is not None,
        )
        return lead

    async def delete_lead(self, *, tenant_id: UUID, lead_id: UUID) -> bool:
        """Hard-delete one lead. Returns ``True`` if removed, ``False`` if absent.

        Spec 012 FR-007 + edge case: erasure flow (feature 015) is the
        long-term home for tenant-wide right-to-be-forgotten; this route
        is the per-row admin delete. Hard delete keeps the schema simple —
        no ``deleted`` enum variant to maintain on every read path.
        """
        lead = await self.get_lead(tenant_id=tenant_id, lead_id=lead_id)
        if lead is None:
            return False
        await self._session.delete(lead)
        await self._session.flush()
        logger.info(
            "lead.deleted",
            tenant_id=str(tenant_id),
            lead_id=str(lead_id),
        )
        return True

    async def _enforce_session_limit(self, tenant_id: UUID, visitor_session_id: UUID) -> None:
        since = datetime.now(timezone.utc) - self._window
        stmt = (
            select(func.count(Lead.id))
            .where(Lead.tenant_id == tenant_id)
            .where(Lead.visitor_session_id == visitor_session_id)
            .where(Lead.created_at >= since)
        )
        existing = (await self._session.execute(stmt)).scalar_one()
        if existing >= self._limit:
            logger.info(
                "lead.rate_limited",
                tenant_id=str(tenant_id),
                visitor_session_id=str(visitor_session_id),
                window_hours=self._window.total_seconds() / 3600,
                existing=existing,
                limit=self._limit,
            )
            raise RateLimitError(
                f"lead capture limit reached ({self._limit} per "
                f"{int(self._window.total_seconds() // 3600)}h)"
            )
