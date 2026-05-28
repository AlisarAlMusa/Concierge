"""HTTP contracts for the admin escalations surface (GET/PATCH /escalations).

Mirrors the style of ``app.schemas.cms`` and ``app.schemas.lead``. The
``escalate`` agent tool has its own argument model
(``app.services.tools.escalate.EscalateArgs``); these schemas are strictly
for the admin REST surface.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.models.escalation import EscalationStatus


class EscalationRead(BaseModel):
    """``GET /escalations[/{id}]`` and ``PATCH /escalations/{id}`` response shape."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    conversation_id: UUID
    reason: str
    context: str | None = None
    status: EscalationStatus
    created_at: datetime
    updated_at: datetime


class EscalationList(BaseModel):
    """``GET /escalations`` response — flat list scoped to the caller's tenant."""

    items: list[EscalationRead]
    total: int


class EscalationUpdate(BaseModel):
    """``PATCH /escalations/{escalation_id}`` request body.

    Spec 012 FR-011: admin may transition the escalation through its
    lifecycle (``open`` → ``in_progress`` → ``resolved`` / ``dismissed``).
    Reason/context are immutable from the admin surface — they were
    written by the agent tool and act as evidence.
    """

    status: EscalationStatus
