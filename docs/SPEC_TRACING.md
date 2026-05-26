# SPEC_TRACING.md — Tracing & PII Redaction (Owner C)

This file defines the tracing and redaction contract for the Concierge backend.
All persons must follow these exactly. Do not change without team consensus and a PR
that updates this file.

---

## 1. Context & Objectives

- As part of the Concierge AI SaaS, we must implement observability (tracing)
  across our FastAPI microservices to monitor network hops and LLM agent execution.
- We must satisfy a strict security requirement: PII and secrets (like API keys)
  must be redacted **before** the trace payload leaves the service.
- We use OpenTelemetry (OTel) for instrumentation and Arize Phoenix (local) as
  the observability backend.

---

## 2. Required Dependencies

Add the following to the backend's dependency manager (e.g. `uv add ...`):

```toml
opentelemetry-api
opentelemetry-sdk
opentelemetry-instrumentation-fastapi
opentelemetry-instrumentation-httpx
opentelemetry-exporter-otlp
openinference-instrumentation-openai   # if using OpenAI API
# PII detection — choose ONE:
presidio-analyzer + presidio-anonymizer
# OR
# robust regex utilities for PII detection
```

---

## 3. Implementation Steps

### 3.1 Redaction Engine (`backend/app/core/redaction.py`)

- Implement a `PIIRedactor` class.
- It must expose `redact_text(text: str) -> str`.
- It must detect and redact:
  - **Emails** → `[REDACTED_EMAIL]`
  - **Phone numbers** → `[REDACTED_PHONE]`
  - **API keys** — specifically `sk_live_...`, `sk_test_...`, and `Bearer ...`
    → `[REDACTED_API_KEY]`

### 3.2 Custom Span Processor (`backend/app/core/tracing.py`)

- Create a `RedactingSpanProcessor` that inherits from OpenTelemetry's
  `BatchSpanProcessor`.
- Override the `on_end` (or `export`) method.
- Before the span is exported, iterate through the span's attributes and events.
  If any attribute is a string, pass it through `PIIRedactor.redact_text()`.
- Create a `setup_tracing(app: FastAPI)` function that:
  1. Sets up the `TracerProvider`.
  2. Adds the `RedactingSpanProcessor` with an OTLP exporter pointing to
     `http://phoenix:4317` (gRPC) or `4318` (HTTP).
  3. Calls `FastAPIInstrumentor.instrument_app(app)`.
  4. Calls `HTTPXClientInstrumentor().instrument()`.

### 3.3 Wire to FastAPI (`backend/app/main.py`)

Import `setup_tracing` and execute it during FastAPI application startup.
The Phoenix endpoint must be read from `PHOENIX_COLLECTOR_ENDPOINT`,
defaulting to `http://phoenix:4317`.

### 3.4 Docker Compose Infrastructure (`docker-compose.yml`)

Add the Arize Phoenix service to the stack so the exporter has a destination:

```yaml
phoenix:
  image: arizeai/phoenix:latest
  ports:
    - "6006:6006"   # UI
    - "4317:4317"   # OTLP gRPC receiver
    - "4318:4318"   # OTLP HTTP receiver
```

### 3.5 CI/CD Proof (`backend/tests/test_redaction.py`)

Write a Pytest unit test that:

1. Initializes a mock OpenTelemetry tracer with the `RedactingSpanProcessor`
   (using an `InMemorySpanExporter`).
2. Starts a span and adds an attribute:
   `span.set_attribute("llm.prompt", "Here is my key sk_live_123456789")`.
3. Ends the span.
4. Asserts that the exported span in the `InMemorySpanExporter` contains
   `[REDACTED_API_KEY]` and does **not** contain the raw string `sk_live_123456789`.

---

## 4. Constraint Checklist

- No tracing data is sent to cloud services (LangSmith, DataDog).
  All data goes to the local Phoenix container.
- The redaction happens in memory inside the FastAPI process before network
  transmission.
- `app/main.py` is kept clean — tracing setup is abstracted to `core/tracing.py`.
