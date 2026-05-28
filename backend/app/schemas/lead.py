"""HTTP contracts for the admin leads surface (GET/PATCH/DELETE /leads).

Mirrors the style of ``app.schemas.cms`` — only the fields the admin
client actually sends/receives. ``tenant_id`` stays on the wire (read-only)
so the admin UI can verify it matches the operating tenant, but it is
never accepted on writes — the server reads it from the authenticated
header.

The ``capture_lead`` agent tool has its own argument model
(``app.services.tools.capture_lead.CaptureLeadArgs``); these schemas are
strictly for the admin REST surface.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.lead import LeadStatus


class LeadRead(BaseModel):
    """``GET /leads[/{id}]`` and ``PATCH /leads/{id}`` response shape."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    conversation_id: UUID
    visitor_session_id: UUID | None = None
    name: str | None = None
    email: str | None = None
    phone: str | None = None
    intent: str
    context: str | None = None
    lead_score: float | None = None
    source: str
    status: LeadStatus
    notes: str | None = None
    created_at: datetime
    updated_at: datetime


class LeadList(BaseModel):
    """``GET /leads`` response — flat list scoped to the caller's tenant."""

    items: list[LeadRead]
    total: int


class LeadUpdate(BaseModel):
    """``PATCH /leads/{lead_id}`` request body — every field optional.

    Spec 012 FR-006: admin may update ``status`` and ``notes``. Other
    columns are visitor-provided (name/email/phone/intent) or
    pipeline-owned (``lead_score``) and are intentionally not exposed for
    admin edit on this PR's scope.
    """

    status: LeadStatus | None = None
    # Notes default to ``None`` (no change). Pass an empty string to clear.
    notes: str | None = Field(default=None, max_length=10_000)
