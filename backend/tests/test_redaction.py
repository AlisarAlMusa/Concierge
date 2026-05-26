"""Unit tests for PII redaction.

Covers two layers:
1. `PIIRedactor.redact_text` for direct string redaction.
2. `RedactingSpanProcessor` end-to-end — proves that span attributes are
   scrubbed *before* they reach the exporter, using an `InMemorySpanExporter`.
"""

from __future__ import annotations

from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from app.core.redaction import PIIRedactor
from app.core.tracing import RedactingSpanProcessor


def test_pii_redactor_replaces_known_patterns() -> None:
    redactor = PIIRedactor()

    assert "[REDACTED_API_KEY]" in redactor.redact_text("token=sk_live_abc123XYZ")
    assert "sk_live_abc123XYZ" not in redactor.redact_text("token=sk_live_abc123XYZ")

    assert "[REDACTED_API_KEY]" in redactor.redact_text("sk_test_deadbeef")
    assert "[REDACTED_API_KEY]" in redactor.redact_text("Authorization: Bearer abc.def-ghi")

    assert "[REDACTED_EMAIL]" in redactor.redact_text("ping me at alice@example.com")
    assert "alice@example.com" not in redactor.redact_text("ping me at alice@example.com")

    assert "[REDACTED_PHONE]" in redactor.redact_text("call +1 415-555-0199 today")


def test_pii_redactor_passthrough_for_non_strings() -> None:
    redactor = PIIRedactor()
    assert redactor.redact_text("") == ""
    # The signature is str-only at the contract level, but the guard should be
    # defensive — non-string inputs return unchanged rather than raising.
    assert redactor.redact_text(None) is None  # type: ignore[arg-type]
    assert redactor.redact_text(42) == 42  # type: ignore[arg-type]


def test_redacting_span_processor_scrubs_attributes_in_memory() -> None:
    exporter = InMemorySpanExporter()
    provider = TracerProvider(resource=Resource.create({"service.name": "test"}))
    provider.add_span_processor(RedactingSpanProcessor(exporter))
    tracer = provider.get_tracer(__name__)

    with tracer.start_as_current_span("llm.call") as span:
        span.set_attribute("llm.prompt", "Here is my key sk_live_123456789")
        span.set_attribute("user.email", "contact me at bob@example.com please")

    provider.force_flush()

    spans = exporter.get_finished_spans()
    assert len(spans) == 1
    attrs = spans[0].attributes
    assert attrs is not None

    prompt = attrs["llm.prompt"]
    assert "[REDACTED_API_KEY]" in prompt
    assert "sk_live_123456789" not in prompt

    email_attr = attrs["user.email"]
    assert "[REDACTED_EMAIL]" in email_attr
    assert "bob@example.com" not in email_attr
