"""Behavioural checks for `evals/classifier/run_3way_eval.py`.

Real LLM calls are NOT made here — the Groq client is monkeypatched. The
golden set is pinned to the committed one so the classical/ONNX lanes run
against the same data CI does.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable
from unittest.mock import MagicMock, patch

import pytest

from classifier import run_3way_eval as runner

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def _isolate_cache_and_report(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Redirect cache + report to a temp dir so tests don't trample real artifacts."""
    cache_dir = tmp_path / ".cache"
    monkeypatch.setattr(runner, "CACHE_DIR", cache_dir)
    monkeypatch.setattr(runner, "CACHE_PATH", cache_dir / "llm_zero_shot.json")
    monkeypatch.setattr(runner, "REPORT_PATH", tmp_path / "last_report.json")


def _mock_groq_client(replies: Iterable[str]) -> MagicMock:
    """Build a MagicMock that imitates `groq.Groq` returning canned replies."""
    iter_replies = iter(replies)
    client = MagicMock()

    def _create(**_kwargs):
        completion = MagicMock()
        completion.choices = [MagicMock(message=MagicMock(content=next(iter_replies)))]
        return completion

    client.chat.completions.create = _create
    return client


def test_full_three_way_run_passes_when_classical_wins(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Classical KNN beats threshold → exit 0."""
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    with patch(
        "classifier.run_3way_eval.predict_llm_zero_shot",
        return_value=(["faq_support"] * 80, 0, 80),
    ):
        rc = runner.main([])
    assert rc == 0


def test_exit_2_when_thresholds_file_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """A missing thresholds file is a configuration error (exit 2)."""
    monkeypatch.setattr(
        runner, "THRESHOLDS_PATH", REPO_ROOT / "evals" / "nonexistent.yaml"
    )
    rc = runner.main(["--no-llm"])
    assert rc == 2


def test_exit_1_when_winner_below_threshold(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force a high threshold to trigger a regression — exit 1."""
    fake = MagicMock()
    fake.read_text.return_value = "classifier:\n  macro_f1_min: 0.999\n"

    real_load = runner.load_classifier_threshold

    def _fake_load() -> float:
        return 0.999

    monkeypatch.setattr(runner, "load_classifier_threshold", _fake_load)
    rc = runner.main(["--no-llm"])
    assert rc == 1
    monkeypatch.setattr(runner, "load_classifier_threshold", real_load)


def test_cache_hit_skips_api_call(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A pre-warmed cache means predict_llm_zero_shot makes zero API calls."""
    # Seed the cache directly.
    lm = runner.LabelMap(
        routing={"spam": "spam", "faq": "faq_support"},
        routing_intents=["spam", "faq_support", "sales_contact", "human_request", "ambiguous"],
    )
    rows = [{"index": 0, "label": "spam", "text": "hi there"}]
    key = runner._cache_key(rows[0]["text"], "llama-3.1-8b-instant")
    runner.CACHE_DIR.mkdir(parents=True, exist_ok=True)
    runner.CACHE_PATH.write_text(
        json.dumps(
            {
                "cache_version": "1",
                "entries": {
                    key: {
                        "raw_response": "spam",
                        "parsed_label": "spam",
                        "model": "llama-3.1-8b-instant",
                        "prompt_version": runner.PROMPT_VERSION,
                        "ts": "test",
                    }
                },
            }
        )
    )

    # The Groq client constructor is fine; what matters is that no chat
    # completion request is ever issued (cache hit short-circuits the call).
    mock_client = MagicMock()
    mock_client.chat.completions.create.side_effect = AssertionError(
        "chat.completions.create must not be called on a cache hit"
    )
    with patch("groq.Groq", return_value=mock_client):
        preds, hits, calls = runner.predict_llm_zero_shot(
            rows, lm, "llama-3.1-8b-instant", api_key="ignored"
        )
    assert preds == ["spam"]
    assert hits == 1
    assert calls == 0
    mock_client.chat.completions.create.assert_not_called()


def test_cache_key_invalidates_on_prompt_version_bump(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bumping PROMPT_VERSION must change the cache key, forcing a re-score."""
    key_a = runner._cache_key("hello", "llama-3.1-8b-instant")
    monkeypatch.setattr(runner, "PROMPT_VERSION", "v2")
    key_b = runner._cache_key("hello", "llama-3.1-8b-instant")
    assert key_a != key_b


def test_strict_llm_reply_parse_rejects_explanation() -> None:
    """An LLM that adds explanation is counted as misclassified, never crashes."""
    lm = runner.LabelMap(
        routing={"spam": "spam"},
        routing_intents=["spam", "faq_support", "sales_contact", "human_request", "ambiguous"],
    )
    # Valid one-word reply
    assert runner._parse_llm_reply("faq_support", lm) == "faq_support"
    assert runner._parse_llm_reply("  FAQ_SUPPORT  ", lm) == "faq_support"
    assert runner._parse_llm_reply("faq_support.", lm) == "faq_support"
    # Multiline reply — first line only, second line ignored
    assert runner._parse_llm_reply("faq_support\n(because…)", lm) == "faq_support"
    # Garbage → ambiguous (never raises)
    assert runner._parse_llm_reply("I think it might be a question?", lm) == "ambiguous"
    assert runner._parse_llm_reply("", lm) == "ambiguous"
