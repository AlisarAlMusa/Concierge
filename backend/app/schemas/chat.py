# Stub — Person B implements the chat service.
# These schemas define the API contract agreed in docs/SPEC.md.
# Do not change field names without updating SPEC.md and notifying the team.
from pydantic import BaseModel


class ChatRequest(BaseModel):
    message: str
    conversation_id: str | None = None  # None = start new conversation


class ChatResponse(BaseModel):
    message: str
    conversation_id: str
    intent_label: str | None = None
    sources: list[str] = []
