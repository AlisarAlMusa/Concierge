"""Sidecar request / response schemas.

Frozen contract noted in `docs/SPEC.md §5`. Spec 010 FR-018 / FR-019 / FR-021
extend `CheckInputRequest` to carry `tenant_config` and `conversation_history`
so the rails engine can run multi-tenant + multi-turn checks.
"""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


class HistoryEntry(BaseModel):
    role: Literal["visitor", "assistant"]
    content: str


class TenantConfig(BaseModel):
    """Subset of `tenants.guardrails_config` the sidecar consumes.

    Strict validation limits live at the main API's PATCH /config/guardrails
    route (spec 010 FR-023). The sidecar trusts what comes through but
    defensively skips non-string topics in the action.
    """

    persona: str | None = None
    refusal_tone: str | None = None
    blocked_topics: list[str] = Field(default_factory=list)


class CheckInputRequest(BaseModel):
    message: str
    tenant_id: UUID
    conversation_id: UUID | None = None
    tenant_config: TenantConfig = Field(default_factory=TenantConfig)
    conversation_history: list[HistoryEntry] = Field(default_factory=list)


class CheckInputResponse(BaseModel):
    allowed: bool
    reason: str | None = None
    safe_reply: str | None = None
    redacted_text: str


class CheckOutputRequest(BaseModel):
    message: str
    tenant_id: UUID


class CheckOutputResponse(BaseModel):
    allowed: bool
    reason: str | None = None
    redacted_text: str


class RedactRequest(BaseModel):
    text: str


class RedactResponse(BaseModel):
    redacted_text: str
