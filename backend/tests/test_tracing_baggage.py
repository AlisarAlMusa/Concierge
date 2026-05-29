"""Tests for spec 017 Phase 2 — baggage propagation (US4).

Covers SC-006 (every span carries tenant_id + conversation_id), SC-007
(no leakage across concurrent requests on the same worker), SC-008
(baggage clears between requests), and FR-020 (baggage-derived attribute
values are still passed through the RedactingSpanProcessor).
"""

from __future__ import annotations

import asyncio

import pytest
from opentelemetry import context as _otel_context
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    SimpleSpanProcessor,
)
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)

from app.core.tracing import (
    BaggageSpanProcessor,
    set_request_baggage,
)


@pytest.fixture()
def exporter_and_tracer():
    """Build an isolated TracerProvider with both processors registered in
    the same order as production (`BaggageSpanProcessor` first, then a
    redaction-aware exporter).

    A fresh empty OTel context is attached for the test and detached after
    so baggage from one test does not leak into the next (the context is a
    process-wide contextvar by default).
    """
    exporter = InMemorySpanExporter()
    provider = TracerProvider(resource=Resource.create({"service.name": "test"}))
    provider.add_span_processor(BaggageSpanProcessor())
    # Use SimpleSpanProcessor for redaction to keep the test deterministic
    # (Batch processor flushes async).
    provider.add_span_processor(_RedactingSimpleProcessor(exporter))
    tracer = provider.get_tracer(__name__)
    token = _otel_context.attach(_otel_context.Context())
    try:
        yield exporter, tracer
    finally:
        _otel_context.detach(token)
        provider.shutdown()


class _RedactingSimpleProcessor(SimpleSpanProcessor):
    """SimpleSpanProcessor that wraps the same redactor as the production
    BatchSpanProcessor — gives us the same on_end behaviour without async
    flushing."""

    def __init__(self, span_exporter) -> None:
        super().__init__(span_exporter)
        from app.core.redaction import PIIRedactor

        self._redactor = PIIRedactor()

    def on_end(self, span):
        try:
            attrs = getattr(span, "_attributes", None)
            if attrs is not None:
                inner = getattr(attrs, "_dict", attrs)
                for key, value in list(inner.items()):
                    if isinstance(value, str):
                        inner[key] = self._redactor.redact_text(value)
        except Exception:  # noqa: BLE001
            pass
        super().on_end(span)


# ── SC-006: baggage propagates as attributes ──────────────────────────────


def test_baggage_attribute_propagates_to_span(exporter_and_tracer):
    exporter, tracer = exporter_and_tracer
    set_request_baggage({"tenant_id": "tenant-a", "conversation_id": "conv-1"})
    with tracer.start_as_current_span("test"):
        pass
    spans = exporter.get_finished_spans()
    assert len(spans) == 1
    attrs = spans[0].attributes
    assert attrs is not None
    assert attrs["chat.tenant_id"] == "tenant-a"
    assert attrs["chat.conversation_id"] == "conv-1"


def test_baggage_propagates_to_nested_child_spans(exporter_and_tracer):
    """SC-006 — even spans 3 levels deep carry the baggage."""
    exporter, tracer = exporter_and_tracer
    set_request_baggage({"tenant_id": "tenant-a"})
    with tracer.start_as_current_span("root"):
        with tracer.start_as_current_span("child"):
            with tracer.start_as_current_span("grandchild"):
                pass
    for span in exporter.get_finished_spans():
        assert span.attributes["chat.tenant_id"] == "tenant-a", (
            f"missing tenant_id on span {span.name}"
        )


def test_request_id_baggage_keeps_its_key(exporter_and_tracer):
    """`request_id` is the only propagated key that does NOT get the
    `chat.` prefix — keeps existing log-correlation conventions."""
    exporter, tracer = exporter_and_tracer
    set_request_baggage({"request_id": "req-abc"})
    with tracer.start_as_current_span("test"):
        pass
    spans = exporter.get_finished_spans()
    assert spans[0].attributes["request_id"] == "req-abc"


# ── SC-007: per-request isolation under concurrency ─────────────────────


def _run_in_clean_context(coro):
    """Run `coro` in a fresh OTel context (mimics per-request scoping)."""
    return _otel_context.attach(_otel_context.Context())


@pytest.mark.asyncio
async def test_concurrent_requests_isolated(exporter_and_tracer):
    """SC-007 — two concurrent coroutines on the same worker do NOT see
    each other's baggage."""
    exporter, tracer = exporter_and_tracer

    async def request_for_tenant(tenant: str) -> None:
        # Each request gets its own context — this is what
        # asyncio.gather's task-scoped contextvars do automatically.
        token = _otel_context.attach(_otel_context.Context())
        try:
            set_request_baggage({"tenant_id": tenant})
            await asyncio.sleep(0.01)  # interleave the coroutines
            with tracer.start_as_current_span(f"work.{tenant}"):
                pass
        finally:
            _otel_context.detach(token)

    await asyncio.gather(
        request_for_tenant("tenant-A"),
        request_for_tenant("tenant-B"),
    )
    spans = {s.name: s for s in exporter.get_finished_spans()}
    assert spans["work.tenant-A"].attributes["chat.tenant_id"] == "tenant-A"
    assert spans["work.tenant-B"].attributes["chat.tenant_id"] == "tenant-B"


# ── SC-008: baggage clears between sequential requests ─────────────────


def test_sequential_request_does_not_inherit_previous_tenant(exporter_and_tracer):
    """SC-008 — the second request starts with a clean OTel context."""
    exporter, tracer = exporter_and_tracer

    # Request 1.
    token_a = _otel_context.attach(_otel_context.Context())
    try:
        set_request_baggage({"tenant_id": "tenant-A"})
        with tracer.start_as_current_span("req1"):
            pass
    finally:
        _otel_context.detach(token_a)

    # Request 2 — attaches a fresh empty context; no baggage carried over.
    token_b = _otel_context.attach(_otel_context.Context())
    try:
        with tracer.start_as_current_span("req2"):
            pass
    finally:
        _otel_context.detach(token_b)

    spans = {s.name: s for s in exporter.get_finished_spans()}
    assert spans["req1"].attributes["chat.tenant_id"] == "tenant-A"
    assert "chat.tenant_id" not in spans["req2"].attributes


# ── FR-020: baggage values still pass through redaction ────────────────


def test_baggage_value_with_secret_is_redacted(exporter_and_tracer):
    """A bizarre tenant_id containing `sk_live_…` must be scrubbed by the
    RedactingSpanProcessor that runs after the baggage processor on export."""
    exporter, tracer = exporter_and_tracer
    set_request_baggage({"tenant_id": "leaked-sk_live_VERYSECRETKEY12345"})
    with tracer.start_as_current_span("test"):
        pass
    spans = exporter.get_finished_spans()
    attrs = spans[0].attributes
    assert "[REDACTED_API_KEY]" in attrs["chat.tenant_id"]
    assert "sk_live_VERYSECRETKEY12345" not in attrs["chat.tenant_id"]


# ── Edge: empty / None values are skipped ──────────────────────────────


def test_empty_baggage_values_are_skipped(exporter_and_tracer):
    exporter, tracer = exporter_and_tracer
    set_request_baggage({"tenant_id": "", "conversation_id": None})  # type: ignore[dict-item]
    with tracer.start_as_current_span("test"):
        pass
    attrs = exporter.get_finished_spans()[0].attributes
    assert "chat.tenant_id" not in attrs
    assert "chat.conversation_id" not in attrs


def test_baggage_outside_request_context_is_a_no_op(exporter_and_tracer):
    """Calling set_request_baggage from a fresh context attaches it to a
    new context — does NOT raise."""
    exporter, tracer = exporter_and_tracer
    # Should not raise.
    set_request_baggage({"tenant_id": "fresh"})
    with tracer.start_as_current_span("test"):
        pass
    # And the attribute IS set, because the helper attached a new context.
    attrs = exporter.get_finished_spans()[0].attributes
    assert attrs["chat.tenant_id"] == "fresh"
