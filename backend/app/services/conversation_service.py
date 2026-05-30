"""ConversationService — durable chat history (Spec 009 FR-009 / Spec 012).

Two responsibilities for the orchestrator:

1. ``get_or_create`` — resolve the conversation a turn belongs to. The
   first turn of a chat has no incoming ``conversation_id``; the
   orchestrator mints one client-side and we materialize the row on the
   way through here.

2. ``append_message`` — one redacted row per turn. ``metadata`` carries the
   per-turn telemetry (route path + confidence + agent iterations + sources)
   so the admin UI can replay a conversation later.

Everything is tenant-scoped at the SQL layer; the RLS policy on both
tables is the second wall. All ``session.execute`` / ``session.add`` /
``session.flush`` calls live in
``app.repositories.conversation_repository`` — this service contains only
business logic (DTO/ORM construction and logging).

Owner: Person B.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.conversation import (
    Conversation,
    ConversationStatus,
    Message,
    MessageRole,
)
from app.repositories import conversation_repository

logger = structlog.get_logger(__name__)


class ConversationService:
    def __init__(self, *, session: AsyncSession) -> None:
        self._session = session

    async def get_or_create(
        self,
        *,
        tenant_id: UUID,
        conversation_id: UUID,
        widget_id: UUID | None = None,
        visitor_session_id: UUID | None = None,
    ) -> Conversation:
        """Return the existing row or insert a fresh ``active`` one.

        ``conversation_id`` is always supplied by the caller — the orchestrator
        either propagates the client-supplied id or mints one client-side
        before calling this. We use that id verbatim (no auto-generation) so
        Redis memory keys and SQL rows stay aligned.
        """
        existing = await conversation_repository.get_for_tenant(
            self._session, tenant_id=tenant_id, conversation_id=conversation_id
        )
        if existing is not None:
            return existing

        conversation = Conversation(
            id=conversation_id,
            tenant_id=tenant_id,
            widget_id=widget_id,
            visitor_session_id=visitor_session_id,
            status=ConversationStatus.active,
        )
        await conversation_repository.add(self._session, conversation)
        return conversation

    async def append_message(
        self,
        *,
        tenant_id: UUID,
        conversation_id: UUID,
        role: MessageRole,
        content_redacted: str,
        metadata: dict[str, Any] | None = None,
    ) -> Message:
        """Insert one ``Message`` row. ``content_redacted`` is the post-guardrail string."""
        message = Message(
            id=uuid4(),
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            role=role,
            content_redacted=content_redacted,
            meta=dict(metadata or {}),
        )
        await conversation_repository.add_message(self._session, message)
        return message

    async def set_status(
        self,
        *,
        tenant_id: UUID,
        conversation_id: UUID,
        status: ConversationStatus,
    ) -> None:
        """Flip ``conversation.status`` — used by ``EscalationService.create`` (Spec 012 FR-009)."""
        conversation = await conversation_repository.get_for_tenant(
            self._session, tenant_id=tenant_id, conversation_id=conversation_id
        )
        if conversation is None:
            logger.warning(
                "conversation.set_status_missing",
                tenant_id=str(tenant_id),
                conversation_id=str(conversation_id),
                target_status=status.value,
            )
            return
        conversation.status = status
        await conversation_repository.flush_pending(self._session)
