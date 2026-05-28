"""Escalation — flag a conversation for a human (Spec 012 FR-008…FR-012).

Created by ``EscalationService.create`` when either the agent calls the
``escalate`` tool or the router classifies the turn as ``human``. The same
service flips the parent ``Conversation.status`` to ``escalated``.

Idempotency invariant (Spec 012 FR-012): a unique constraint on
``conversation_id`` means a second escalation attempt returns the existing
row instead of inserting a duplicate. ``status`` is updatable by tenant
admins via Owner A's CRUD route.

Owner: Person B.
"""

from __future__ import annotations

import enum
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    DateTime,
    Enum,
    ForeignKey,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class EscalationStatus(str, enum.Enum):
    open = "open"
    in_progress = "in_progress"
    resolved = "resolved"
    dismissed = "dismissed"


class Escalation(Base):
    __tablename__ = "escalations"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    conversation_id: Mapped[UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
    )
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    context: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[EscalationStatus] = mapped_column(
        Enum(EscalationStatus, name="escalation_status", native_enum=True),
        nullable=False,
        default=EscalationStatus.open,
        server_default=EscalationStatus.open.value,
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

    __table_args__ = (
        # Spec 012 FR-012: at most one escalation per conversation.
        UniqueConstraint("conversation_id", name="uq_escalations_conversation"),
    )
