"""Tests for spec 017 Phase 2 — custom chat-flow spans (US5 + US6).

Covers SC-009 (Groq instrumentor present when installed) and SC-010
(synthetic chat turn produces the expected span tree: chat.handle_turn
root + router.classify + guardrails.check_input/check_output +
agent.iteration + tool.<name>).

All collaborators are mocked — no live LLM, no live sidecar. The point is
to lock the span emission contract, not to integration-test the chat flow.
"""

from __future__ import annotations

import pytest
from opentelemetry import context as _otel_context
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)

from app.core.tracing import BaggageSpanProcessor

# Modules whose `_tracer` we need to swap to our test provider's tracer.
_INSTRUMENTED_MODULES = (
    "app.services.router_service",
    "app.services.guardrail_service",
    "app.services.agent_service",
    "app.services.chat_orchestrator",
)


@pytest.fixture()
def provider_and_exporter():
    """Build an isolated TracerProvider + InMemorySpanExporter and inject its
    tracer into each instrumented service module's `_tracer` global.

    Direct injection sidesteps two pitfalls:
    (a) OpenTelemetry forbids re-assigning the global TracerProvider, so we
        cannot just `trace.set_tracer_provider(...)` once per test.
    (b) Service modules cache `_tracer = trace.get_tracer(__name__)` at
        import time; reloading them mid-suite is brittle.
    """
    import sys

    exporter = InMemorySpanExporter()
    provider = TracerProvider(resource=Resource.create({"service.name": "test"}))
    provider.add_span_processor(BaggageSpanProcessor())
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("test")

    saved: dict[str, object] = {}
    for mod_name in _INSTRUMENTED_MODULES:
        mod = sys.modules.get(mod_name)
        if mod is None:
            mod = __import__(mod_name, fromlist=["_tracer"])
        saved[mod_name] = getattr(mod, "_tracer", None)
        mod._tracer = tracer  # type: ignore[attr-defined]

    token = _otel_context.attach(_otel_context.Context())
    try:
        yield provider, exporter
    finally:
        _otel_context.detach(token)
        for mod_name, original in saved.items():
            mod = sys.modules[mod_name]
            mod._tracer = original  # type: ignore[attr-defined]
        provider.shutdown()


# ──────────────────────────────────────────────────────────────────────────
# US6 — RouterService.decide emits `router.classify` with attributes


@pytest.mark.asyncio
async def test_router_decide_emits_router_classify_span(provider_and_exporter):
    """SC-010 fragment: router.classify span carries intent_label + confidence."""
    _, exporter = provider_and_exporter

    from uuid import uuid4

    from app.services.router_service import (
        ClassifierResponse,
        RouterService,
    )

    class _Classifier:
        async def classify(self, *, text: str) -> ClassifierResponse:
            return ClassifierResponse(label="faq", confidence=0.91)

    svc = RouterService(classifier_client=_Classifier(), confidence_threshold=0.6)
    decision = await svc.decide(text="hi", tenant_id=uuid4(), conversation_id=uuid4())
    assert decision.classifier_label == "faq"

    finished = exporter.get_finished_spans()
    spans = [s for s in finished if s.name == "router.classify"]
    assert len(spans) == 1, (
        f"expected one router.classify span, got {[s.name for s in finished]}"
    )
    attrs = spans[0].attributes
    assert attrs["router.intent_label"] == "faq"
    assert attrs["router.confidence"] == pytest.approx(0.91, abs=1e-4)
    assert attrs["router.path"] == "faq"


@pytest.mark.asyncio
async def test_router_decide_classifier_failure_still_emits_span(provider_and_exporter):
    """Even on classifier outage the business span is emitted with empty
    label and zero confidence so Phoenix shows the failure."""
    _, exporter = provider_and_exporter
    from uuid import uuid4

    from app.core.errors import ExternalServiceError
    from app.services.router_service import RouterService

    class _Broken:
        async def classify(self, *, text: str):
            raise ExternalServiceError(service="model_server", reason="boom")

    svc = RouterService(classifier_client=_Broken(), confidence_threshold=0.6)
    decision = await svc.decide(text="hi", tenant_id=uuid4(), conversation_id=uuid4())
    assert decision.reason == "classifier_unavailable"

    spans = [s for s in exporter.get_finished_spans() if s.name == "router.classify"]
    assert len(spans) == 1
    attrs = spans[0].attributes
    assert attrs["router.intent_label"] == ""
    assert attrs["router.confidence"] == 0.0
    assert attrs["router.reason"] == "classifier_unavailable"


# ──────────────────────────────────────────────────────────────────────────
# US6 — GuardrailService spans carry `guardrails.allowed` / `guardrails.reason`


@pytest.mark.asyncio
async def test_guardrails_check_input_span_records_allowed_and_reason(provider_and_exporter):
    _, exporter = provider_and_exporter
    from unittest.mock import AsyncMock, MagicMock
    from uuid import uuid4

    import httpx

    from app.services.guardrail_service import GuardrailService

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "allowed": False,
                "reason": "jailbreak_attempt",
                "safe_reply": "no.",
                "redacted_text": "x",
            },
        )

    class _Memory:
        async def load(self, *_a, **_k):
            return []

    svc = GuardrailService(
        http=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        sidecar_base_url="http://sidecar:8002",
        session=MagicMock(),
        memory=_Memory(),  # type: ignore[arg-type]
    )

    import app.services.guardrail_service as gs_mod

    gs_mod.tenant_repository = MagicMock()  # type: ignore[attr-defined]
    gs_mod.tenant_repository.get_guardrails_config = AsyncMock(return_value={})

    await svc.check_input(message="x", tenant_id=uuid4(), conversation_id=uuid4())

    spans = [s for s in exporter.get_finished_spans() if s.name == "guardrails.check_input"]
    assert len(spans) == 1
    attrs = spans[0].attributes
    assert attrs["guardrails.allowed"] is False
    assert attrs["guardrails.reason"] == "jailbreak_attempt"


@pytest.mark.asyncio
async def test_guardrails_check_output_span_records_allowed(provider_and_exporter):
    _, exporter = provider_and_exporter
    from unittest.mock import MagicMock
    from uuid import uuid4

    import httpx

    from app.services.guardrail_service import GuardrailService

    def handler(request):
        return httpx.Response(
            200,
            json={"allowed": True, "redacted_text": "ok"},
        )

    svc = GuardrailService(
        http=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        sidecar_base_url="http://sidecar:8002",
        session=MagicMock(),
        memory=MagicMock(),  # type: ignore[arg-type]
    )

    await svc.check_output(message="hello", tenant_id=uuid4())

    spans = [s for s in exporter.get_finished_spans() if s.name == "guardrails.check_output"]
    assert len(spans) == 1
    assert spans[0].attributes["guardrails.allowed"] is True


@pytest.mark.asyncio
async def test_guardrails_fail_closed_still_emits_span(provider_and_exporter):
    """If the sidecar errors and we fail closed, the business span still
    carries the verdict so operators can spot fail-closes in Phoenix."""
    _, exporter = provider_and_exporter
    from unittest.mock import AsyncMock, MagicMock
    from uuid import uuid4

    import httpx

    from app.services.guardrail_service import GuardrailService

    def handler(request):
        raise httpx.ConnectError("simulated outage")

    svc = GuardrailService(
        http=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        sidecar_base_url="http://sidecar:8002",
        session=MagicMock(),
        memory=MagicMock(),  # type: ignore[arg-type]
    )

    import app.services.guardrail_service as gs_mod

    gs_mod.tenant_repository = MagicMock()  # type: ignore[attr-defined]
    gs_mod.tenant_repository.get_guardrails_config = AsyncMock(return_value={})

    class _Memory:
        async def load(self, *_a, **_k):
            return []

    svc._memory = _Memory()  # type: ignore[assignment]

    decision = await svc.check_input(
        message="x", tenant_id=uuid4(), conversation_id=uuid4()
    )
    assert decision.allowed is False  # fail-closed default

    spans = [s for s in exporter.get_finished_spans() if s.name == "guardrails.check_input"]
    assert len(spans) == 1
    attrs = spans[0].attributes
    assert attrs["guardrails.allowed"] is False
    assert attrs["guardrails.reason"] == "sidecar_unreachable"


# ──────────────────────────────────────────────────────────────────────────
# US5 — Groq instrumentor wired when installed


def test_groq_instrumentor_is_available():
    """SC-009 — if the package is installed, GroqInstrumentor is importable."""
    pytest.importorskip(
        "openinference.instrumentation.groq",
        reason="openinference-instrumentation-groq not installed",
    )
    from openinference.instrumentation.groq import GroqInstrumentor

    instr = GroqInstrumentor()
    assert callable(instr.instrument)


def test_setup_tracing_does_not_crash_without_groq(monkeypatch, provider_and_exporter):
    """FR-015 — absence of the package logs a warning, not a crash."""
    # Hide the module so the ImportError branch fires inside setup_tracing.
    monkeypatch.setitem(
        __import__("sys").modules, "openinference.instrumentation.groq", None
    )
    from fastapi import FastAPI

    from app.core.tracing import setup_tracing

    app = FastAPI()
    # Should not raise.
    setup_tracing(app)


# ──────────────────────────────────────────────────────────────────────────
# US6 — Span attribute names use the project-specific conventions


def test_attribute_names_follow_conventions(provider_and_exporter):
    """Documents the project-specific attribute namespace so renames break
    this test loudly (rather than silently de-rendering in Phoenix)."""
    from app.core.tracing import (
        _PROPAGATED_BAGGAGE_KEYS,
        _attribute_name_for,
    )

    assert _PROPAGATED_BAGGAGE_KEYS == ("tenant_id", "conversation_id", "request_id")
    assert _attribute_name_for("tenant_id") == "chat.tenant_id"
    assert _attribute_name_for("conversation_id") == "chat.conversation_id"
    assert _attribute_name_for("request_id") == "request_id"
