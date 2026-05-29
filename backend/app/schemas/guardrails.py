"""Tenant guardrails-config schemas (spec 010 FR-023 / US7).

Strict validation at the boundary keeps the semantic-router lane in the
sidecar safe — long paragraphs as `blocked_topics` entries dilute cosine
discrimination, large arrays bloat per-request compute, and non-string
entries crash the embedder.

PATCH semantics: missing field = "no change". `blocked_topics=[]` clears
the list; `blocked_topics=None` (omitted) leaves it alone.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator

MAX_PERSONA_CHARS = 500
MAX_TONE_CHARS = 100
MAX_TOPICS = 10
MAX_TOPIC_CHARS = 30


class GuardrailsConfigUpdate(BaseModel):
    """Partial update payload for `PATCH /config/guardrails`.

    All fields optional. Validation:
      - persona: <=500 chars
      - refusal_tone: <=100 chars
      - blocked_topics: <=10 entries, each 1..30 chars, case-insensitive dedupe
    """

    model_config = ConfigDict(extra="forbid")

    persona: Annotated[
        str | None,
        Field(default=None, max_length=MAX_PERSONA_CHARS),
    ] = None
    refusal_tone: Annotated[
        str | None,
        Field(default=None, max_length=MAX_TONE_CHARS),
    ] = None
    blocked_topics: Annotated[
        list[str] | None,
        Field(default=None, max_length=MAX_TOPICS),
    ] = None

    @field_validator("blocked_topics")
    @classmethod
    def _validate_topics(cls, v: list[str] | None) -> list[str] | None:
        if v is None:
            return None
        cleaned: list[str] = []
        seen: set[str] = set()
        for raw in v:
            if not isinstance(raw, str):
                raise ValueError("each topic must be a string")
            s = raw.strip()
            if not (1 <= len(s) <= MAX_TOPIC_CHARS):
                raise ValueError(
                    f"each topic must be 1..{MAX_TOPIC_CHARS} characters"
                )
            key = s.lower()
            if key in seen:
                continue
            seen.add(key)
            cleaned.append(s)
        return cleaned


class GuardrailsConfigRead(BaseModel):
    """Shape of the JSONB column when read back."""

    persona: str | None = None
    refusal_tone: str | None = None
    blocked_topics: list[str] = Field(default_factory=list)
