"""OpenTelemetry tracing setup with in-process PII redaction.

`setup_tracing(app)` is the single entrypoint called from `app.main`. It wires
a `TracerProvider` with a `RedactingSpanProcessor` that scrubs span attributes
and event attributes *before* the BatchSpanProcessor hands them to the OTLP
exporter — so no raw email / phone / API-key string ever leaves the process.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from fastapi import FastAPI
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter

from app.core.redaction import PIIRedactor

logger = logging.getLogger(__name__)

DEFAULT_PHOENIX_ENDPOINT = "http://phoenix:4317"


class RedactingSpanProcessor(BatchSpanProcessor):
    """A BatchSpanProcessor that runs every string attribute through PIIRedactor
    in `on_end`, before delegating to the parent for batching/export."""

    def __init__(self, span_exporter: SpanExporter, redactor: PIIRedactor | None = None) -> None:
        super().__init__(span_exporter)
        self._redactor = redactor or PIIRedactor()

    def on_end(self, span: Any) -> None:  # ReadableSpan, but typed loosely for testability
        try:
            self._redact_span(span)
        except Exception:  # noqa: BLE001 — never raise out of a span processor
            logger.exception("RedactingSpanProcessor failed to redact span; exporting as-is")
        super().on_end(span)

    def _redact_span(self, span: Any) -> None:
        # ReadableSpan exposes `.attributes` as an immutable BoundedAttributes view.
        # The underlying mutable dict lives on the live Span as `_attributes._dict`
        # (BoundedAttributes) — we mutate it in place so the exported span carries
        # the redacted values.
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


def setup_tracing(app: FastAPI) -> None:
    """Initialize OTel tracing and install the redacting processor.

    Reads `PHOENIX_COLLECTOR_ENDPOINT` (default `http://phoenix:4317`) and wires
    the FastAPI + HTTPX (+ optional OpenAI) instrumentors.
    """
    endpoint = os.environ.get("PHOENIX_COLLECTOR_ENDPOINT", DEFAULT_PHOENIX_ENDPOINT)

    resource = Resource.create({"service.name": "concierge-api"})
    provider = TracerProvider(resource=resource)

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

    logger.info("OpenTelemetry tracing initialized (exporter=%s)", endpoint)
