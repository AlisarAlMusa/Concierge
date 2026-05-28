"""Lead — durable record of a captured visitor (Spec 012).

Written by ``LeadService.capture`` when the ``capture_lead`` tool fires.
Per Spec 012 FR-002 ``tenant_id`` is **never** read from the request body —
it always comes from the verified widget token via ``ToolContext``.

``lead_score`` is nullable: the model_server endpoint that scores leads is
Owner C's surface and lands later. Until then the column accepts null so
agent traffic keeps flowing.

Owner: Person B.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    DateTime,
    Float,
    ForeignKey,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Lead(Base):
    __tablename__ = "leads"

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
    # Anonymous visitor identifier from the widget token. Indexed because the
    # per-session rate limit (Spec 012 FR-003) counts rows by this column.
    visitor_session_id: Mapped[UUID | None] = mapped_column(nullable=True, index=True)
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    intent: Mapped[str] = mapped_column(Text, nullable=False)
    context: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Spec 012 FR-004: populated by Owner C's /predict-lead-score endpoint.
    # Null on insert today; backfilled when that surface lands.
    lead_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    source: Mapped[str] = mapped_column(String(32), nullable=False, server_default="agent")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
