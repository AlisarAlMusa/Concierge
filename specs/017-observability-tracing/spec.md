# Feature Specification: Observability — OpenTelemetry Tracing with In-Process PII Redaction

> **Owner**: Person C — `feature/mahdi-tracing-redaction` branch

**Feature Branch**: `017-observability-tracing`

**Created**: 2026-05-27

**Status**: Draft

---

## User Scenarios & Testing *(mandatory)*

### User Story 1 — Operator Can Trace a Chat Request Across Services (Priority: P1)

An on-call engineer is investigating a slow `/public/chat` request. They open the local Arize Phoenix UI and find a single trace that spans the FastAPI handler, every outbound `httpx` call (model-server, guardrails sidecar, optional LLM), and the underlying span hierarchy showing where time was spent.

**Why this priority**: Without end-to-end tracing across the FastAPI app and its sidecars, every multi-service incident is debugged by tailing four log streams in parallel. A single trace per request is the prerequisite for any meaningful incident response and for tuning agent latency.

**Independent Test**: With the stack running locally, send one request to any API route. Open `http://localhost:6006` and confirm exactly one trace exists with at least two spans (the FastAPI server span plus one outbound HTTPX span).

**Acceptance Scenarios**:

1. **Given** the stack is running, **When** a request hits any FastAPI route, **Then** a trace appears in the Phoenix UI with the FastAPI server span as its root.
2. **Given** the same request makes an outbound HTTPX call, **When** the trace is opened, **Then** the HTTPX call appears as a child span under the FastAPI root.
3. **Given** the OpenAI Python SDK is used during a request, **When** the trace is opened, **Then** an LLM span appears with model and token-count attributes (best-effort; only when `openinference-instrumentation-openai` is installed).

---

### User Story 2 — PII and Secrets Are Redacted Before the Trace Leaves the Process (Priority: P1)

A visitor pastes an API key, email address, or phone number into the chat. The FastAPI handler sets those values onto span attributes (intentionally or via instrumentor auto-capture). Before the trace payload is handed to the OTLP exporter, every string attribute and event attribute is run through the in-process `PIIRedactor`, so the raw secret never reaches the Phoenix container, never reaches disk, and never reaches the network.

**Why this priority**: Visitors paste secrets into chat boxes constantly. Once a raw API key reaches an external collector, it is on disk somewhere outside the security boundary — a compliance failure independent of how careful the application code was. Redaction in the application process is the only design that survives mistakes elsewhere.

**Independent Test**: Initialise a mock OpenTelemetry tracer with the `RedactingSpanProcessor` wired to an `InMemorySpanExporter`. Start a span, set an attribute containing a fake API key, end the span, force-flush. Read the exported span back from the in-memory exporter and assert (a) it contains `[REDACTED_API_KEY]`, (b) the raw key string is absent.

**Acceptance Scenarios**:

1. **Given** a span attribute is set to a string containing `sk_live_…`, **When** the span ends, **Then** the exported span value contains `[REDACTED_API_KEY]` and does not contain the original secret.
2. **Given** a span attribute is set to a string containing an email address, **When** the span ends, **Then** the exported span value contains `[REDACTED_EMAIL]` and does not contain the original address.
3. **Given** a span attribute is set to a string containing a phone number, **When** the span ends, **Then** the exported span value contains `[REDACTED_PHONE]`.
4. **Given** a span attribute is set to a string containing `Bearer <token>`, **When** the span ends, **Then** the bearer value is replaced with `[REDACTED_API_KEY]`.
5. **Given** a span event carries a string attribute with any of the above PII, **When** the span ends, **Then** the event attribute is redacted in the same way.
6. **Given** the redactor raises unexpectedly on a malformed value, **When** the span ends, **Then** the original telemetry is exported as-is (no exception escapes the span processor) and a warning is logged.

---

### User Story 3 — Tracing Is Local-Only by Construction (Priority: P1)

All traces are exported to a Phoenix container running inside the local Compose stack on `phoenix:4317`. No tracing data is sent to LangSmith, DataDog, or any other cloud collector by default. If the Phoenix container is unavailable, the exporter retries silently in the background and the application continues serving requests.

**Why this priority**: A cloud collector is a second jurisdiction for the same data. The team's compliance posture only works if telemetry remains under the same security boundary as the rest of the stack. A misconfigured environment variable must not silently exfiltrate spans.

**Independent Test**: Stop the Phoenix container. Make a request to the API. Confirm the request completes successfully (no 500), and the exporter logs a connection-refused warning rather than blocking.

**Acceptance Scenarios**:

1. **Given** no `PHOENIX_COLLECTOR_ENDPOINT` env var is set, **When** the application starts, **Then** the exporter defaults to `http://phoenix:4317` (the Compose service).
2. **Given** the Phoenix container is down, **When** requests are served, **Then** application response latency is unaffected (the OTLP exporter is non-blocking) and no requests fail.
3. **Given** the `PHOENIX_COLLECTOR_ENDPOINT` is set to a non-loopback hosted URL, **When** the application starts in a non-local environment, **Then** a documented review gate prevents accidental cloud export. *(Out of scope for v1 — documented as a follow-up.)*

---

### Edge Cases

- **Span attribute is a list of strings**: each list element is redacted individually; non-string elements pass through untouched.
- **Span attribute is `None` or a non-string scalar** (int, bool, float): passes through unchanged.
- **Redactor pattern overlap** (e.g. an email containing what looks like a phone-number digit run): the redactor applies API-key/bearer patterns first, then email, then phone — so a redacted email is not re-matched as a phone number.
- **Phoenix container unavailable at boot**: tracing initialisation must succeed; the OTLP exporter retries in the background.
- **OpenAI instrumentor not installed**: tracing initialisation must succeed; the optional instrumentor wiring is wrapped in `try/except ImportError`.

---

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The backend MUST initialise OpenTelemetry tracing during FastAPI application startup via a single `setup_tracing(app)` call in `app/main.py`.
- **FR-002**: All tracing configuration MUST live in `backend/app/core/tracing.py` — `app/main.py` MUST NOT import any OpenTelemetry symbols directly.
- **FR-003**: The trace exporter endpoint MUST be read from the `PHOENIX_COLLECTOR_ENDPOINT` environment variable, defaulting to `http://phoenix:4317`.
- **FR-004**: A `RedactingSpanProcessor` (subclass of OpenTelemetry's `BatchSpanProcessor`) MUST redact every string-typed span attribute and event attribute in `on_end` **before** delegating to the parent processor for batching and export.
- **FR-005**: The redactor MUST detect and replace, at minimum: email addresses (`[REDACTED_EMAIL]`), phone numbers (`[REDACTED_PHONE]`), API-key-like strings matching `sk_live_…` and `sk_test_…` (`[REDACTED_API_KEY]`), and `Bearer …` tokens (`[REDACTED_API_KEY]`).
- **FR-006**: The redactor MUST be implemented as `PIIRedactor` in `backend/app/core/redaction.py` and MUST expose `redact_text(text: str) -> str` as its public method. The same class MUST be reusable by the guardrails sidecar (`POST /guardrails/redact`, see spec 010 FR-005) without modification.
- **FR-007**: An exception inside the redactor MUST NOT propagate out of the span processor — it MUST be logged and the original span exported unchanged (telemetry availability > redaction perfection).
- **FR-008**: The FastAPI app and the HTTPX async client MUST be auto-instrumented via `FastAPIInstrumentor.instrument_app` and `HTTPXClientInstrumentor().instrument()`.
- **FR-009**: A unit test using `InMemorySpanExporter` MUST prove end-to-end that a span attribute containing a fake API key is exported as `[REDACTED_API_KEY]` and never contains the raw key. This test MUST live in `backend/tests/test_redaction.py` and MUST run in the CI gate alongside the broader redaction CI test in spec 016.
- **FR-010**: The Compose stack MUST include a `phoenix` service exposing port `6006` (UI), `4317` (OTLP gRPC), and `4318` (OTLP HTTP). The `api` service MUST declare a non-blocking `depends_on` on `phoenix` (`condition: service_started`) so a failed Phoenix container does not cascade.
- **FR-011**: No tracing data MAY be sent to any external (cloud) collector by default configuration. The default endpoint MUST resolve only inside the Compose network.

### Key Entities

- **PIIRedactor**: Synchronous regex-based class with one public method, `redact_text(text: str) -> str`. Internally an ordered list of `(compiled_pattern, replacement)` tuples; API-key and bearer patterns run first so they are not re-matched by email/phone patterns afterwards. Stateless; safe to share across threads and span processors.
- **RedactingSpanProcessor**: Subclass of `BatchSpanProcessor`. Holds a `PIIRedactor` instance. Override of `on_end` iterates `span._attributes` and each event's attributes, scrubbing string values in place, then calls `super().on_end(span)`.
- **Phoenix Service**: Local-only OTLP collector (`arizeai/phoenix:latest`) listening on `4317` (gRPC), `4318` (HTTP), with a UI on `6006`. No persistent volume in v1.

---

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Redaction pass rate = 1.0 for the in-memory span test — a fake API key set as a span attribute never appears unredacted in the exported span. This is a CI gate.
- **SC-002**: Tracing startup adds < 100 ms to FastAPI boot in `local` mode (single OTLP exporter, no instrumentation discovery beyond FastAPI + HTTPX + optional OpenAI).
- **SC-003**: A `docker compose up` with Phoenix unhealthy or absent does not increase tail latency on `/health` by more than 5 ms (proving the exporter is non-blocking).
- **SC-004**: For every chat request, the corresponding trace in Phoenix shows the FastAPI server span as root and at least one HTTPX child span per outbound service call.
- **SC-005**: Inspection of any span in Phoenix produced from a chat that included PII shows redaction tokens (`[REDACTED_EMAIL]`, `[REDACTED_PHONE]`, `[REDACTED_API_KEY]`) and no raw values.

---

## Assumptions

- The Compose network is the security boundary. Phoenix runs as a sibling container and is not exposed beyond the local host; this is enforced operationally, not by code.
- Presidio is intentionally NOT used. The constitution forbids `torch`/`transformers` in production containers (Principle V, "Lean Containers"), and Presidio's `nlp_engine_provider` pulls a spaCy + transformers chain. Regex coverage of email / phone / API-key / bearer is sufficient for the categories listed in this spec; richer entity types (names, locations, credit cards) are out of scope here and may live in the guardrails sidecar (spec 010) if needed.
- The `request_id` / `trace_id` values bound by `RequestIDMiddleware` (spec 001, post-merge) are independent of OpenTelemetry's trace IDs. Future work MAY unify them by reading the active span ID into the log context; this spec does not require it.
- `WIDGET_TOKEN_SECRET` and `SERVICE_AUTH_SECRET` are out of scope here; secrets that arrive as span attributes via auto-instrumentation are caught by the bearer-token regex.
- The legacy `docs/SPEC_TRACING.md` (pre-spec-kit) is superseded by this file and is removed in the same commit.
