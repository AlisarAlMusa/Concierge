"""Unit tests for RouterService — pure decision logic.

Mocked classifier responses only. No I/O. Validates the frozen routing policy
before the real model_server adapter is wired.
"""

from __future__ import annotations

import pytest

from app.core.errors import ExternalServiceError
from app.services.router_service import ClassifierResponse, RouterService
from tests.conftest import (
    CONVO_1,
    TENANT_A,
    FakeClassifierClient,
)


def _router(
    *,
    response: ClassifierResponse | None = None,
    exc: Exception | None = None,
    threshold: float = 0.6,
) -> tuple[RouterService, FakeClassifierClient]:
    """Build a RouterService wired with a FakeClassifierClient."""
    fake = FakeClassifierClient(response=response, exc=exc)
    router = RouterService(classifier_client=fake, confidence_threshold=threshold)
    return router, fake


# ----- Confident, known labels -----------------------------------------------
async def test_faq_high_confidence_routes_to_faq():
    router, fake = _router(response=ClassifierResponse(label="faq", confidence=0.9))

    decision = await router.decide(
        text="When are you open?",
        tenant_id=TENANT_A,
        conversation_id=CONVO_1,
    )

    assert decision.path == "faq"
    assert decision.reason == "faq"
    assert decision.confidence == 0.9
    assert decision.classifier_label == "faq"
    assert fake.calls == ["When are you open?"]


async def test_sales_high_confidence_routes_to_sales():
    router, _ = _router(response=ClassifierResponse(label="sales", confidence=0.85))

    decision = await router.decide(
        text="How much for the premium plan?",
        tenant_id=TENANT_A,
        conversation_id=CONVO_1,
    )

    assert decision.path == "sales"
    assert decision.reason == "sales"
    assert decision.classifier_label == "sales"


async def test_human_high_confidence_routes_to_human():
    router, _ = _router(response=ClassifierResponse(label="human", confidence=0.92))

    decision = await router.decide(
        text="Let me talk to a person.",
        tenant_id=TENANT_A,
        conversation_id=CONVO_1,
    )

    assert decision.path == "human"
    assert decision.reason == "human"
    assert decision.classifier_label == "human"


async def test_spam_high_confidence_routes_to_drop():
    router, _ = _router(response=ClassifierResponse(label="spam", confidence=0.97))

    decision = await router.decide(
        text="buy crypto now click here",
        tenant_id=TENANT_A,
        conversation_id=CONVO_1,
    )

    assert decision.path == "drop"
    assert decision.reason == "spam"
    assert decision.classifier_label == "spam"


async def test_ambiguous_high_confidence_routes_to_agent():
    router, _ = _router(response=ClassifierResponse(label="ambiguous", confidence=0.8))

    decision = await router.decide(
        text="hmm.",
        tenant_id=TENANT_A,
        conversation_id=CONVO_1,
    )

    assert decision.path == "agent"
    assert decision.reason == "ambiguous"
    assert decision.classifier_label == "ambiguous"


# ----- Guarded fallbacks to agent --------------------------------------------
async def test_low_confidence_routes_to_agent_even_for_spam():
    """A low-confidence 'spam' label must NOT silently drop user traffic."""
    router, _ = _router(
        response=ClassifierResponse(label="spam", confidence=0.4),
        threshold=0.6,
    )

    decision = await router.decide(
        text="...",
        tenant_id=TENANT_A,
        conversation_id=CONVO_1,
    )

    assert decision.path == "agent"
    assert decision.reason == "low_confidence"
    assert decision.confidence == 0.4
    assert decision.classifier_label == "spam"  # raw label preserved for observability


async def test_unknown_label_routes_to_agent():
    """Labels outside the closed set fall to the agent."""
    router, _ = _router(response=ClassifierResponse(label="weather", confidence=0.95))

    decision = await router.decide(
        text="what's the weather?",
        tenant_id=TENANT_A,
        conversation_id=CONVO_1,
    )

    assert decision.path == "agent"
    assert decision.reason == "unknown_label"
    assert decision.classifier_label == "weather"


async def test_classifier_unavailable_routes_to_agent():
    """ExternalServiceError from the classifier → fail open to agent."""
    router, fake = _router(exc=ExternalServiceError(service="model_server", reason="503"))

    decision = await router.decide(
        text="hi",
        tenant_id=TENANT_A,
        conversation_id=CONVO_1,
    )

    assert decision.path == "agent"
    assert decision.reason == "classifier_unavailable"
    assert decision.confidence is None
    assert decision.classifier_label is None
    assert fake.calls == ["hi"]  # classifier was attempted before the fallback


# ----- Threshold boundary ----------------------------------------------------
async def test_confidence_at_threshold_routes_normally():
    """Threshold is inclusive on the upper side: confidence == threshold passes."""
    router, _ = _router(
        response=ClassifierResponse(label="faq", confidence=0.6),
        threshold=0.6,
    )

    decision = await router.decide(
        text="x",
        tenant_id=TENANT_A,
        conversation_id=CONVO_1,
    )

    assert decision.path == "faq"
    assert decision.reason == "faq"


# ----- Constructor validation ------------------------------------------------
def test_invalid_threshold_raises():
    """Constructor rejects thresholds outside [0.0, 1.0]."""
    with pytest.raises(ValueError):
        RouterService(
            classifier_client=FakeClassifierClient(
                response=ClassifierResponse(label="faq", confidence=0.9)
            ),
            confidence_threshold=1.5,
        )
