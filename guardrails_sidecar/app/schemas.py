# Contracts defined in docs/SPEC.md §5. Do not change without team consensus.
from uuid import UUID

from pydantic import BaseModel


class CheckInputRequest(BaseModel):
    message: str
    tenant_id: UUID
    conversation_id: UUID | None = None


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
