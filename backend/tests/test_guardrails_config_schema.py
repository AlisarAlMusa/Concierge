"""Strict validation tests for `GuardrailsConfigUpdate` (spec 010 FR-023 / US7).

This file exercises the Pydantic schema directly so it does not require a
running Postgres. The full PATCH-route integration test (E2E with DB)
belongs to `tests/integration/test_admin_guardrails_config.py`, which is
out of this PR's scope — wiring requires the existing tenant / auth
fixtures from spec 002.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.schemas.guardrails import (
    MAX_PERSONA_CHARS,
    MAX_TONE_CHARS,
    MAX_TOPIC_CHARS,
    MAX_TOPICS,
    GuardrailsConfigUpdate,
)


def test_empty_patch_is_valid() -> None:
    """PATCH semantics: all fields optional."""
    payload = GuardrailsConfigUpdate()
    assert payload.model_dump(exclude_unset=True) == {}


def test_happy_path() -> None:
    payload = GuardrailsConfigUpdate(
        persona="A friendly plumbing assistant.",
        refusal_tone="Polite but firm.",
        blocked_topics=["competitors", "politics"],
    )
    assert payload.persona == "A friendly plumbing assistant."
    assert payload.blocked_topics == ["competitors", "politics"]


def test_too_many_topics_rejected() -> None:
    with pytest.raises(ValidationError) as exc:
        GuardrailsConfigUpdate(blocked_topics=[f"t{i}" for i in range(MAX_TOPICS + 1)])
    assert "blocked_topics" in str(exc.value)


def test_topic_too_long_rejected() -> None:
    with pytest.raises(ValidationError) as exc:
        GuardrailsConfigUpdate(blocked_topics=["x" * (MAX_TOPIC_CHARS + 1)])
    assert f"{MAX_TOPIC_CHARS}" in str(exc.value)


def test_empty_topic_rejected() -> None:
    with pytest.raises(ValidationError):
        GuardrailsConfigUpdate(blocked_topics=["  "])


def test_non_string_topic_rejected() -> None:
    with pytest.raises(ValidationError):
        GuardrailsConfigUpdate(blocked_topics=["ok", 42])  # type: ignore[list-item]


def test_duplicates_deduped_case_insensitive() -> None:
    payload = GuardrailsConfigUpdate(
        blocked_topics=["Politics", "politics", " POLITICS "]
    )
    assert payload.blocked_topics == ["Politics"]


def test_persona_max_chars() -> None:
    payload = GuardrailsConfigUpdate(persona="a" * MAX_PERSONA_CHARS)
    assert len(payload.persona) == MAX_PERSONA_CHARS
    with pytest.raises(ValidationError):
        GuardrailsConfigUpdate(persona="a" * (MAX_PERSONA_CHARS + 1))


def test_refusal_tone_max_chars() -> None:
    payload = GuardrailsConfigUpdate(refusal_tone="a" * MAX_TONE_CHARS)
    assert len(payload.refusal_tone) == MAX_TONE_CHARS
    with pytest.raises(ValidationError):
        GuardrailsConfigUpdate(refusal_tone="a" * (MAX_TONE_CHARS + 1))


def test_empty_blocked_topics_clears() -> None:
    """`blocked_topics=[]` is valid and means "clear the list"."""
    payload = GuardrailsConfigUpdate(blocked_topics=[])
    dumped = payload.model_dump(exclude_unset=True)
    assert dumped == {"blocked_topics": []}


def test_extra_fields_rejected() -> None:
    """`extra="forbid"` prevents callers from sneaking in unexpected keys."""
    with pytest.raises(ValidationError):
        GuardrailsConfigUpdate(persona="hi", unknown_field="x")  # type: ignore[call-arg]


def test_partial_dump_omits_unset() -> None:
    payload = GuardrailsConfigUpdate(persona="hello")
    dumped = payload.model_dump(exclude_unset=True)
    assert dumped == {"persona": "hello"}
    # `refusal_tone` and `blocked_topics` are NOT included — PATCH means
    # "no change to those fields".
