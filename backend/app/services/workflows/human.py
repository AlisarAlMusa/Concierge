"""HumanWorkflow — escalation finalization.

Selected by ``ChatOrchestrator`` when ``RouterService`` returns ``path="human"``.
Wraps ``EscalationService.create`` and returns the canned acknowledgement.
On any failure (DB outage, etc.) returns the canned-failure reply with
``used_refusal_fallback=True`` so the orchestrator can attribute the
fallback in telemetry. Never crashes a turn.

See ``specs/workflow-services/spec.md §5``.
"""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

import structlog

from app.services.router_service import RouteDecision
from app.services.tools.escalate import EscalateResult
from app.services.workflows.base import WorkflowTurnResult

logger = structlog.get_logger(__name__)

HUMAN_REPLY = (
    "I've flagged this conversation for a human teammate — someone will follow up with you shortly."
)
ESCALATION_FAILURE_REPLY = (
    "I wasn't able to flag this for a human just now. Please try again in a moment "
    "or use the business's contact form."
)


class _EscalationService(Protocol):
    async def create(
        self,
        *,
        tenant_id: UUID,
        conversation_id: UUID,
        reason: str,
        context: str | None,
    ) -> EscalateResult: ...


class HumanWorkflow:
    def __init__(self, *, escalation_service: _EscalationService) -> None:
        self._escalation = escalation_service

    async def run(
        self,
        *,
        tenant_id: UUID,
        conversation_id: UUID,
        visitor_session_id: UUID | None,  # noqa: ARG002 — accepted for shape parity
        user_message: str,
        tenant_persona: str | None,  # noqa: ARG002 — accepted for shape parity
        route_decision: RouteDecision,  # noqa: ARG002 — accepted for shape parity
    ) -> WorkflowTurnResult:
        try:
            await self._escalation.create(
                tenant_id=tenant_id,
                conversation_id=conversation_id,
                reason="router classified intent as human",
                context=user_message,
            )
        except Exception as exc:  # noqa: BLE001 — orchestrator must never crash a turn
            logger.warning(
                "human_workflow.escalation_failed",
                tenant_id=str(tenant_id),
                conversation_id=str(conversation_id),
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return WorkflowTurnResult(
                reply=ESCALATION_FAILURE_REPLY,
                sources=[],
                used_refusal_fallback=True,
            )
        return WorkflowTurnResult(reply=HUMAN_REPLY, sources=[], used_refusal_fallback=False)
