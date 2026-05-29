"""Tests for `guardrails_sidecar.app.actions::check_blocked_topics`."""

from __future__ import annotations


import pytest

from app.actions import _evaluate, check_blocked_topics


def test_empty_blocked_topics_passes() -> None:
    is_blocked, sim, topic = _evaluate("anything goes here", [])
    assert is_blocked is False
    assert sim == 0.0
    assert topic == ""


def test_paraphrase_match_blocks(rails_engine) -> None:
    """rails_engine fixture brings up the global TopicEmbedder."""
    is_blocked, sim, topic = _evaluate(
        "How does your service compare to competitors?", ["competitors"]
    )
    assert is_blocked is True
    assert topic == "competitors"
    assert sim >= 0.55  # default threshold


def test_unrelated_message_passes(rails_engine) -> None:
    is_blocked, sim, _ = _evaluate(
        "What are your business hours?", ["politics"]
    )
    assert is_blocked is False
    assert sim < 0.5


def test_threshold_env_override_tightens(rails_engine, monkeypatch) -> None:
    monkeypatch.setenv("GUARDRAILS_TOPIC_SIM_THRESHOLD", "0.99")
    # Even an exact match like 'competitors' embed vs 'competitors' embed = 1.0
    # would pass; but the paraphrase that matched at 0.64 with default threshold
    # must NOT block at threshold=0.99.
    is_blocked, _, _ = _evaluate(
        "How does your service compare to competitors?", ["competitors"]
    )
    assert is_blocked is False


def test_threshold_env_override_loosens(rails_engine, monkeypatch) -> None:
    monkeypatch.setenv("GUARDRAILS_TOPIC_SIM_THRESHOLD", "0.10")
    # Almost anything blocks at threshold=0.10.
    is_blocked, _, _ = _evaluate("What's the weather?", ["competitors"])
    assert is_blocked is True


def test_bad_threshold_env_falls_back_to_default(rails_engine, monkeypatch) -> None:
    monkeypatch.setenv("GUARDRAILS_TOPIC_SIM_THRESHOLD", "not-a-number")
    is_blocked, _, _ = _evaluate(
        "How does your service compare to competitors?", ["competitors"]
    )
    # default threshold 0.55 → still blocks the 0.64 paraphrase
    assert is_blocked is True


@pytest.mark.asyncio
async def test_async_wrapper_delegates(rails_engine) -> None:
    result = await check_blocked_topics(
        "How does your service compare to competitors?",
        ["competitors"],
    )
    assert result is True


@pytest.mark.asyncio
async def test_async_wrapper_empty_topics(rails_engine) -> None:
    assert (await check_blocked_topics("anything", [])) is False
    assert (await check_blocked_topics("anything", None)) is False


def test_non_string_topic_is_skipped(rails_engine) -> None:
    # Belt-and-suspenders: Pydantic validates types at the API layer, but the
    # action itself defends against garbage.
    is_blocked, _, _ = _evaluate(
        "How does your service compare to competitors?",
        [None, 42, "competitors"],  # type: ignore[list-item]
    )
    assert is_blocked is True
