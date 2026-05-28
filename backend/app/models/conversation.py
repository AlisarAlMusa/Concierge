# TODO: Person B — implement Conversation and Message models
# Conversation fields: id, tenant_id, widget_id, visitor_session_id, status, created_at, updated_at
# Message fields: id, tenant_id, conversation_id, role (visitor/assistant/tool/system),
#                 content_redacted, metadata jsonb, created_at
# Then uncomment the import in backend/app/db/base.py

import enum
"""Conversation + Message — durable chat history (Spec 009 FR-009, Spec 012 FR-009).

One ``Conversation`` per visitor chat session; one ``Message`` per turn (visitor
and assistant interleaved). ``status`` is flipped to ``escalated`` by
``EscalationService.create`` (Spec 012 FR-009) and to ``closed`` by future admin
tooling.

All content is stored **redacted** — ``ChatOrchestrator._finalize`` runs the
guardrail redaction layer before insert, and ``MemoryService`` does the same
for Redis. This model only ever sees the post-redaction string.

Owner: Person B.
"""

from __future__ import annotations

import enum
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ConversationStatus(str, enum.Enum):
    active = "active"
    escalated = "escalated"
    closed = "closed"


class MessageRole(str, enum.Enum):
    visitor = "visitor"
    assistant = "assistant"
    tool = "tool"
    system = "system"


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    widget_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("widgets.id", ondelete="SET NULL"),
        nullable=True,
    )
    # Anonymous identifier minted by the widget loader and carried in the
    # session token. Used by ``LeadService`` for per-session rate limiting
    # (Spec 012 FR-003).
    visitor_session_id: Mapped[UUID | None] = mapped_column(nullable=True, index=True)
    status: Mapped[ConversationStatus] = mapped_column(
        Enum(ConversationStatus, name="conversation_status", native_enum=True),
        nullable=False,
        default=ConversationStatus.active,
        server_default=ConversationStatus.active.value,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    conversation_id: Mapped[UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    role: Mapped[MessageRole] = mapped_column(
        Enum(MessageRole, name="message_role", native_enum=True),
        nullable=False,
    )
    content_redacted: Mapped[str] = mapped_column(Text, nullable=False)
    # Opaque per-turn telemetry (route path, confidence, sources, agent
    # iterations, …). Read-only from the persistence service's point of view;
    # the orchestrator owns the shape.
    meta: Mapped[dict] = mapped_column("metadata", JSON, nullable=False, server_default="{}")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (Index("ix_messages_conversation_created", "conversation_id", "created_at"),)
