"""Chat route — one HTTP turn through ``ChatOrchestrator``.

Authentication: ``Authorization: Bearer <widget session token>``.
``tenant_id``, ``widget_id``, and ``visitor_session_id`` are all sourced
from the verified JWT — never from the request body. See
``specs/widget-auth/spec.md``.

Schema: ``app.schemas.chat`` is the public API contract; do not change those
field names without updating ``docs/SPEC.md``.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from app.dependencies import (
    get_chat_orchestrator,
    get_tenant_id,
    get_visitor_session_id,
    get_widget_id,
)
from app.schemas.chat import ChatRequest, ChatResponse
from app.services.chat_orchestrator import ChatOrchestrator

router = APIRouter(tags=["chat"])


@router.post("", response_model=ChatResponse, status_code=200)
async def post_chat(
    request: ChatRequest,
    tenant_id: UUID = Depends(get_tenant_id),
    widget_id: UUID = Depends(get_widget_id),
    visitor_session_id: UUID = Depends(get_visitor_session_id),
    orchestrator: ChatOrchestrator = Depends(get_chat_orchestrator),
) -> ChatResponse:
    """Run one chat turn end to end.

    Errors:

    * 400 — empty message or malformed ``conversation_id``.
    * 401 — missing/invalid/expired widget session token.
    * 503 — upstream provider failure (LLM, embedding) surfaces via the
      global ``ExternalServiceError`` handler.
    """
    if not request.message or not request.message.strip():
        raise HTTPException(status_code=400, detail="message must be non-empty")

    incoming_conv_id: UUID | None = None
    if request.conversation_id:
        try:
            incoming_conv_id = UUID(request.conversation_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="conversation_id must be a UUID") from exc

    turn = await orchestrator.handle_turn(
        tenant_id=tenant_id,
        user_message=request.message,
        conversation_id=incoming_conv_id,
        visitor_session_id=visitor_session_id,
        widget_id=widget_id,
    )

    return ChatResponse(
        message=turn.reply,
        conversation_id=str(turn.conversation_id),
        intent_label=turn.route.classifier_label,
        sources=[str(s) for s in turn.sources],
    )
