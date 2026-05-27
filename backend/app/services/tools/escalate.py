"""escalate tool — hand the conversation off to a human. SPEC §3.3."""

from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.services.tools.base import ToolContext, ToolHandler


class EscalateArgs(BaseModel):
    reason: str = Field(
        ...,
        description="Why a human is needed for this conversation.",
    )
    context: str | None = None


class EscalateResult(BaseModel):
    escalation_id: UUID
    status: Literal["created"]


_DESCRIPTION = (
    "Hand the conversation to a human when the request is out of scope for the business's "
    "content, the visitor explicitly asks to speak with a person, or you've already tried "
    "and failed to answer."
)


def build_handler(escalation_service: Any) -> ToolHandler:
    """Bind escalate to an EscalationService instance.

    escalation_service must expose:
        async create(
            *,
            tenant_id: UUID,
            conversation_id: UUID,
            reason: str,
            context: str | None,
        ) -> EscalateResult
    """

    async def invoke(args: EscalateArgs, ctx: ToolContext) -> EscalateResult:
        return await escalation_service.create(
            tenant_id=ctx.tenant_id,
            conversation_id=ctx.conversation_id,
            reason=args.reason,
            context=args.context,
        )

    return ToolHandler(
        name="escalate",
        description=_DESCRIPTION,
        args_schema=EscalateArgs,
        invoke_fn=invoke,
    )
