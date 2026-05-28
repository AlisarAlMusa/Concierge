"""capture_lead tool — save visitor contact + intent. SPEC §3.2.

Per SPEC: schema-validated; rate-limited per visitor_session by LeadService;
writes only to the token's tenant_id (carried in ToolContext, NEVER in args).
"""

from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.services.tools.base import ToolContext, ToolHandler


class CaptureLeadArgs(BaseModel):
    name: str | None = None
    email: str | None = None
    phone: str | None = None
    intent: str = Field(
        ...,
        description="Short description of what the visitor wants.",
    )
    context: str | None = None


class CaptureLeadResult(BaseModel):
    lead_id: UUID
    status: Literal["created"]


_DESCRIPTION = (
    "Save a visitor's contact details and intent for sales follow-up. Use only when the "
    "visitor has clearly expressed interest in being contacted AND provided at least one "
    "of: name, email, or phone."
)


def build_handler(lead_service: Any) -> ToolHandler:
    """Bind capture_lead to a LeadService instance.

    lead_service must expose:
        async capture(
            *,
            tenant_id: UUID,
            conversation_id: UUID,
            visitor_session_id: UUID | None,
            name: str | None, email: str | None, phone: str | None,
            intent: str, context: str | None,
        ) -> CaptureLeadResult
    """

    async def invoke(args: CaptureLeadArgs, ctx: ToolContext) -> CaptureLeadResult:
        return await lead_service.capture(
            tenant_id=ctx.tenant_id,
            conversation_id=ctx.conversation_id,
            visitor_session_id=ctx.visitor_session_id,
            name=args.name,
            email=args.email,
            phone=args.phone,
            intent=args.intent,
            context=args.context,
        )

    return ToolHandler(
        name="capture_lead",
        description=_DESCRIPTION,
        args_schema=CaptureLeadArgs,
        invoke_fn=invoke,
    )
