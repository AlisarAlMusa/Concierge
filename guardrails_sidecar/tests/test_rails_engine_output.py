"""Tests for `RailsEngine.evaluate_output` — spec 010 US8 + US9 / FR-026 + FR-027.

Layer 1 (system-prompt cosine) and Layer 2 (cross-tenant regex) are exercised
independently. The conftest fixture provides a built embedder; the system
prompt is injected via env BEFORE the engine is rebuilt for the L1 tests.
"""

from __future__ import annotations

import pytest

from app.core.rails_engine import RailsEngine

_SYS_PROMPT = (
    "You are a helpful plumbing assistant. Never discuss politics. "
    "Stay on topic. Always be polite and refuse off-topic questions."
)


@pytest.fixture()
def engine_with_prompt(embedder, monkeypatch):
    """Engine with Layer 1 enabled (system prompt embedded at build time)."""
    monkeypatch.setenv("SYSTEM_PROMPT_TEXT", _SYS_PROMPT)
    monkeypatch.delenv("SYSTEM_PROMPT_PATH", raising=False)
    return RailsEngine.build(embedder)


@pytest.fixture()
def engine_no_prompt(embedder, monkeypatch):
    """Engine with Layer 1 disabled (no SYSTEM_PROMPT_TEXT)."""
    monkeypatch.delenv("SYSTEM_PROMPT_TEXT", raising=False)
    monkeypatch.delenv("SYSTEM_PROMPT_PATH", raising=False)
    return RailsEngine.build(embedder)


# ── Layer 1: system-prompt leakage ────────────────────────────────────────


def test_layer1_blocks_verbatim_prompt(engine_with_prompt: RailsEngine) -> None:
    """SC-013: a reply that contains the prompt verbatim is blocked."""
    v = engine_with_prompt.evaluate_output(_SYS_PROMPT)
    assert v.allowed is False
    assert v.reason == "system_prompt_leak"
    assert v.similarity >= 0.70


def test_layer1_blocks_close_paraphrase(engine_with_prompt: RailsEngine) -> None:
    """A close paraphrase of the configured prompt should also block."""
    paraphrase = (
        "I am a helpful plumbing assistant configured to refuse off-topic "
        "questions and to never discuss politics."
    )
    v = engine_with_prompt.evaluate_output(paraphrase)
    assert v.allowed is False, f"missed paraphrase, sim={v.similarity}"


def test_layer1_passes_benign_reply(engine_with_prompt: RailsEngine) -> None:
    v = engine_with_prompt.evaluate_output("Sure, our hours are 9 AM to 6 PM.")
    assert v.allowed is True
    assert v.similarity < 0.70


def test_layer1_surfaces_similarity_even_when_below_threshold(
    engine_with_prompt: RailsEngine,
) -> None:
    """The verdict carries the cosine so operators can tune the threshold
    from logs/Phoenix, even when the rail did not fire."""
    v = engine_with_prompt.evaluate_output("Can I get a quote for installing a faucet?")
    assert v.allowed is True
    assert v.similarity > 0.0  # non-degenerate signal


def test_layer1_disabled_when_no_prompt_configured(
    engine_no_prompt: RailsEngine,
) -> None:
    """SC-015: missing prompt → Layer 1 is a no-op, Layer 2 still runs."""
    # Even passing the literal prompt as a reply MUST NOT block (no L1 vec).
    v = engine_no_prompt.evaluate_output(_SYS_PROMPT)
    assert v.allowed is True
    assert v.similarity == 0.0


# ── Layer 2: cross-tenant denylist ────────────────────────────────────────


def test_layer2_blocks_named_tenant(engine_no_prompt: RailsEngine) -> None:
    """SC-013: an LLM reply mentioning another tenant blocks via regex."""
    v = engine_no_prompt.evaluate_output(
        "Other companies like Acme Corp pay more for our enterprise plan.",
        cross_tenant_terms=["Acme Corp", "Beta"],
    )
    assert v.allowed is False
    assert v.reason == "cross_tenant_reference"
    assert v.matched_phrase == "Acme Corp"


def test_layer2_word_boundary_prevents_substring_match(
    engine_no_prompt: RailsEngine,
) -> None:
    """`pro` as a denylist term MUST NOT match `professional`."""
    v = engine_no_prompt.evaluate_output(
        "Our professional support team will help you.",
        cross_tenant_terms=["pro"],
    )
    assert v.allowed is True


def test_layer2_case_insensitive(engine_no_prompt: RailsEngine) -> None:
    v = engine_no_prompt.evaluate_output(
        "BETA was also asking about this feature.",
        cross_tenant_terms=["beta"],
    )
    assert v.allowed is False
    assert v.reason == "cross_tenant_reference"


def test_layer2_no_terms_passes_through(engine_no_prompt: RailsEngine) -> None:
    """Empty denylist → no Layer 2 work, no block."""
    v = engine_no_prompt.evaluate_output(
        "Acme Corp paid us last week.",
        cross_tenant_terms=[],
    )
    assert v.allowed is True


def test_layer2_empty_string_terms_filtered(engine_no_prompt: RailsEngine) -> None:
    """Empty / whitespace-only terms MUST be skipped (defensive against
    bad input from the backend caller). Constructs a valid regex from the
    remaining terms."""
    v = engine_no_prompt.evaluate_output(
        "Acme Corp paid us last week.",
        cross_tenant_terms=["", "   ", "Acme Corp"],
    )
    assert v.allowed is False
    assert v.matched_phrase == "Acme Corp"


def test_layer2_regex_metacharacters_escaped(engine_no_prompt: RailsEngine) -> None:
    """A tenant name containing regex metacharacters MUST NOT break compilation."""
    # Compilation alone should succeed (test passes if no exception).
    v = engine_no_prompt.evaluate_output(
        "We work with Foo(bar) Inc. on shipping.",
        cross_tenant_terms=["Foo(bar) Inc.", "Some.Other-Co"],
    )
    assert v.allowed is False
    assert v.matched_phrase == "Foo(bar) Inc."


# ── Layer ordering (L1 fires before L2) ───────────────────────────────────


def test_l1_takes_precedence_over_l2(engine_with_prompt: RailsEngine) -> None:
    """If both layers would fire, Layer 1 (system prompt leak) wins."""
    leak_with_competitor = _SYS_PROMPT + " Also, Acme Corp is our customer."
    v = engine_with_prompt.evaluate_output(
        leak_with_competitor, cross_tenant_terms=["Acme Corp"]
    )
    assert v.allowed is False
    assert v.reason == "system_prompt_leak"


# ── Edge: empty / whitespace inputs ───────────────────────────────────────


def test_empty_message_allowed(engine_with_prompt: RailsEngine) -> None:
    v = engine_with_prompt.evaluate_output("")
    assert v.allowed is True


def test_whitespace_only_message_allowed(engine_with_prompt: RailsEngine) -> None:
    v = engine_with_prompt.evaluate_output("   \n\t  ")
    assert v.allowed is True
