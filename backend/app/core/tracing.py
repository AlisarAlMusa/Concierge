"""OpenTelemetry tracing setup — Phase 1 redaction + Phase 2 baggage / Groq /
custom chat-flow spans (spec 017).

`setup_tracing(app)` is the single entrypoint called from `app.main`. It wires:

* A `TracerProvider` with two processors registered in order:
  1. `BaggageSpanProcessor` (on_start) — copies whitelisted baggage items to
     span attributes so every child span inherits tenant_id / conversation_id.
  2. `RedactingSpanProcessor` (on_end, BatchSpanProcessor) — scrubs PII /
     secrets from every string attribute before the OTLP exporter sees them.

* Auto-instrumentors: FastAPI server spans, HTTPX client spans.
* Optional LLM instrumentors: OpenAI + Groq (wrapped in try/except ImportError
  so absence of the optional package is logged but does not crash startup).

The processor ordering matters: baggage runs BEFORE redaction so any
baggage-derived attribute that happens to contain a secret value is still
scrubbed before export (FR-020).

`set_request_baggage()` is the helper the chat route handler calls right
after auth resolves and conversation_id is known.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from fastapi import FastAPI
from opentelemetry import baggage as _baggage
from opentelemetry import context as _otel_context
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import Span, SpanProcessor, TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter

from app.core.redaction import PIIRedactor

logger = logging.getLogger(__name__)

DEFAULT_PHOENIX_ENDPOINT = "http://phoenix:4317"

# Baggage keys we PROPAGATE to span attributes. Kept narrow on purpose —
# baggage is single-process; this is not a place to dump arbitrary state.
# Spec 017 FR-012.
_PROPAGATED_BAGGAGE_KEYS: tuple[str, ...] = (
    "tenant_id",
    "conversation_id",
    "request_id",
)


def _attribute_name_for(baggage_key: str) -> str:
    """Map a baggage key to its span-attribute name.

    Chat-scoped keys land in the `chat.*` namespace so they sort under the
    other chat-flow attributes in Phoenix's tree view; `request_id` keeps its
    own top-level name to match the existing structlog convention.
    """
    if baggage_key in {"tenant_id", "conversation_id"}:
        return f"chat.{baggage_key}"
    return baggage_key


# ── Baggage processor (spec 017 FR-012) ─────────────────────────────────────


class BaggageSpanProcessor(SpanProcessor):
    """Copies whitelisted baggage items to span attributes at span start.

    Lifecycle hook: `on_start` only. Runs BEFORE `RedactingSpanProcessor`
    (which is registered second) in the processor pipeline so any
    baggage-derived attribute carrying a secret is still scrubbed before
    export.

    All exceptions inside `on_start` are logged and swallowed — telemetry
    availability > attribute completeness.
    """

    def on_start(self, span: Span, parent_context: _otel_context.Context | None = None) -> None:
        try:
            ctx = parent_context or _otel_context.get_current()
            for key in _PROPAGATED_BAGGAGE_KEYS:
                value = _baggage.get_baggage(key, ctx)
                if value is None:
                    continue
                span.set_attribute(_attribute_name_for(key), str(value))
        except Exception:  # noqa: BLE001 — never raise out of a span processor
            logger.exception("BaggageSpanProcessor.on_start failed; continuing")

    def on_end(self, span: Any) -> None:
        return

    def shutdown(self) -> None:
        return

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return True


# ── Helper for chat route handler (spec 017 FR-013) ─────────────────────────


def set_request_baggage(items: dict[str, str | None]) -> None:
    """Attach `items` to the current OpenTelemetry context's baggage.

    Subsequent spans started under this context — including auto-instrumented
    HTTPX client spans for outbound sidecar calls — will inherit these items
    as attributes via `BaggageSpanProcessor`.

    `None`-valued items are skipped (partial baggage is fine — auth may not
    have resolved yet).

    Calling this outside an active context is a no-op: `_otel_context.attach`
    establishes a fresh context in that case. We do not raise — tests and
    offline scripts can call this safely.
    """
    ctx = _otel_context.get_current()
    for key, value in items.items():
        if value is None or value == "":
            continue
        ctx = _baggage.set_baggage(key, str(value), context=ctx)
    _otel_context.attach(ctx)


# ── Redacting processor (Phase 1 — unchanged) ───────────────────────────────


class RedactingSpanProcessor(BatchSpanProcessor):
    """A BatchSpanProcessor that runs every string attribute through PIIRedactor
    in `on_end`, before delegating to the parent for batching/export."""

    def __init__(self, span_exporter: SpanExporter, redactor: PIIRedactor | None = None) -> None:
        super().__init__(span_exporter)
        self._redactor = redactor or PIIRedactor()

    def on_end(self, span: Any) -> None:
        try:
            self._redact_span(span)
        except Exception:  # noqa: BLE001 — never raise out of a span processor
            logger.exception("RedactingSpanProcessor failed to redact span; exporting as-is")
        super().on_end(span)

    def _redact_span(self, span: Any) -> None:
        attrs = getattr(span, "_attributes", None)
        if attrs is not None:
            inner = getattr(attrs, "_dict", attrs)
            for key, value in list(inner.items()):
                inner[key] = self._scrub(value)

        events = getattr(span, "_events", None)
        if events:
            for event in events:
                ev_attrs = getattr(event, "attributes", None)
                if ev_attrs is None:
                    continue
                ev_inner = getattr(ev_attrs, "_dict", ev_attrs)
                for key, value in list(ev_inner.items()):
                    ev_inner[key] = self._scrub(value)

    def _scrub(self, value: Any) -> Any:
        if isinstance(value, str):
            return self._redactor.redact_text(value)
        if isinstance(value, (list, tuple)):
            scrubbed = [self._scrub(v) for v in value]
            return type(value)(scrubbed) if isinstance(value, tuple) else scrubbed
        return value


# ── Setup ───────────────────────────────────────────────────────────────────


def setup_tracing(app: FastAPI) -> None:
    """Initialize OTel tracing and install processors / instrumentors.

    Reads `PHOENIX_COLLECTOR_ENDPOINT` (default `http://phoenix:4317`).
    Processor ordering: BaggageSpanProcessor first (on_start), then
    RedactingSpanProcessor (on_end + export). LLM instrumentors are
    optional — absence of the package logs a warning, never crashes.
    """
    endpoint = os.environ.get("PHOENIX_COLLECTOR_ENDPOINT", DEFAULT_PHOENIX_ENDPOINT)

    resource = Resource.create({"service.name": "concierge-api"})
    provider = TracerProvider(resource=resource)

    # Order matters (spec 017 FR-012 / FR-020): baggage attaches attributes
    # on span start; redaction scrubs them on span end before export.
    provider.add_span_processor(BaggageSpanProcessor())
    exporter = OTLPSpanExporter(endpoint=endpoint, insecure=True)
    provider.add_span_processor(RedactingSpanProcessor(exporter))

    trace.set_tracer_provider(provider)

    FastAPIInstrumentor.instrument_app(app)
    HTTPXClientInstrumentor().instrument()

    try:
        from openinference.instrumentation.openai import OpenAIInstrumentor

        OpenAIInstrumentor().instrument()
    except ImportError:
        logger.debug("openinference-instrumentation-openai not installed; skipping")

    # Spec 017 FR-015: the project uses Groq, not OpenAI. Use `warning` (not
    # `debug`) so operators notice if LLM observability is degraded.
    try:
        from openinference.instrumentation.groq import GroqInstrumentor

        GroqInstrumentor().instrument()
    except ImportError:
        logger.warning(
            "openinference-instrumentation-groq not installed — Groq LLM calls "
            "will appear in Phoenix as raw HTTPX spans only"
        )

    logger.info("OpenTelemetry tracing initialized (exporter=%s)", endpoint)
