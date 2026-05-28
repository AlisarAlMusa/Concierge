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

import enum
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    DateTime,
    Enum,
    Float,
    ForeignKey,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class LeadStatus(str, enum.Enum):
    """Admin-side lifecycle states for a captured lead (Spec 012 §Key Entities).

    Distinct from ``source`` (which records who captured the lead — the
    router workflow vs. the agent tool). ``status`` is the manual triage
    state the tenant admin moves the lead through.
    """

    new = "new"
    contacted = "contacted"
    converted = "converted"
    rejected = "rejected"


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
    # Spec 012 FR-006 — admin-side triage state. Added by migration
    # 0005_leads_admin_fields. Defaults to ``new`` for both freshly captured
    # leads and the back-fill of pre-existing rows.
    status: Mapped[LeadStatus] = mapped_column(
        Enum(LeadStatus, name="lead_status", native_enum=True),
        nullable=False,
        default=LeadStatus.new,
        server_default=LeadStatus.new.value,
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
