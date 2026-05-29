# Feature Specification: Observability — OpenTelemetry Tracing with In-Process PII Redaction

> **Owner**: Person C — `feature/mahdi-tracing-redaction` branch

**Feature Branch**: `017-observability-tracing`

**Created**: 2026-05-27

**Updated**: 2026-05-29 — added Phase 2 (US4/US5/US6) covering baggage propagation, Groq LLM instrumentation, and custom chat-flow spans. The Phase 1 surface (US1/US2/US3, FR-001..011) shipped in week 8 and is unchanged.

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

### User Story 4 — On-Call Filters Traces by Tenant in Under Five Seconds (Priority: P1)

An on-call engineer is investigating "Tenant A says chat is slow." They open Phoenix, type `tenant_id = <A>` into the trace filter, and see only that tenant's recent traces — no Tenant B noise to wade through. Every span in every returned trace carries the same `tenant_id` and `conversation_id`, so the engineer can pivot to a single conversation without losing tenant scope.

**Why this priority**: A SaaS without per-tenant trace filtering is a chat history dump where every tenant's traces are mixed together. "Slow for tenant A" debugging becomes "scan thousands of unrelated traces until you find one that looks slow." Without baggage propagation, the FastAPI server span is the only place `tenant_id` could attach — every downstream client span (model_server, guardrails, Groq) is unfilterable.

**Independent Test**: Run two chat conversations from two different tenants. In Phoenix, filter by `tenant_id = <tenant A's uuid>`. Confirm: (a) only tenant A's traces appear, (b) every span inside each trace (FastAPI server, every HTTPX child, every custom chat-flow span) carries the same `tenant_id` and the same `conversation_id`.

**Acceptance Scenarios**:

1. **Given** a chat request authenticated via widget token, **When** any span is emitted during that request, **Then** the span carries `tenant_id` (string UUID) and `conversation_id` (string UUID) as attributes.
2. **Given** a span created inside `RagService.retrieve` (a leaf operation 3 levels deep in the call stack), **When** the span is exported to Phoenix, **Then** the same `tenant_id` and `conversation_id` attributes are present without any explicit attribute call inside `RagService`.
3. **Given** two concurrent chat requests from different tenants on the same uvicorn worker, **When** their spans are exported, **Then** no span from one request inherits the other request's `tenant_id` (no context leakage).
4. **Given** an outbound httpx call to the guardrails sidecar, **When** the auto-instrumented client span is exported, **Then** that span ALSO carries the calling tenant's `tenant_id` (so per-tenant latency breakdowns are possible).
5. **Given** a request that fails inside a handler before any business logic runs (e.g. 401 at auth), **When** Phoenix receives the server span, **Then** the span MAY have no `tenant_id` (auth never resolved) but MUST NOT carry a stale `tenant_id` from a previous request on the same worker.

---

### User Story 5 — Operator Sees the LLM Prompt, Completion, and Token Count Inside the Trace (Priority: P1)

An on-call engineer drills into a trace where the agent took 4 seconds. Inside that trace they see a span labelled `groq.chat.completions.create` showing the system prompt, the messages list, the assistant's reply, the model name, the input/output token counts, and any tool calls the LLM emitted. The PII redactor has already scrubbed the visible-text fields so no visitor secret leaks into Phoenix.

**Why this priority**: The agent's LLM call is the single biggest unknown in the chat critical path. Without the prompt + completion + token counts as span attributes, latency debugging stops at "the network call took 4s" — there is no way to know whether the LLM was given an oversized context, returned a long completion, or fan-outed into a tool call that triggered another LLM round. Token attribution also feeds spec 013's cost tracking — a separate-but-related concern.

**Independent Test**: Send one chat request that routes to the agent path. Open the trace in Phoenix. Confirm there is at least one span produced by the Groq instrumentor that has, at minimum: `llm.model_name`, `llm.input_messages`, `llm.output_messages`, `llm.token_count.prompt`, `llm.token_count.completion` (or the OpenInference-equivalent semantic conventions). Confirm a fake `sk_live_…` injected into the input message appears as `[REDACTED_API_KEY]` in the span attributes.

**Acceptance Scenarios**:

1. **Given** the agent calls `groq.AsyncGroq.chat.completions.create`, **When** the span is exported, **Then** it carries the OpenInference semantic-convention attributes for LLM operations (`llm.*` namespace).
2. **Given** the LLM call emits tool calls, **When** the span is exported, **Then** each tool call appears as a structured attribute (name + arguments JSON), not as a free-text dump.
3. **Given** a visitor message containing `sk_live_…`, **When** the LLM span is exported to Phoenix, **Then** the input-messages attribute contains `[REDACTED_API_KEY]` and not the raw key (the `RedactingSpanProcessor` from User Story 2 handles this for free).
4. **Given** the Groq instrumentor cannot be imported (package missing in some environments), **When** the application starts, **Then** lifespan continues normally and a single structured warning is logged — tracing-without-LLM-spans is acceptable degradation.

---

### User Story 6 — Trace Shows Which Tools the Agent Called and How Many Loop Iterations (Priority: P1)

An on-call engineer opens a slow trace and immediately sees: `ChatOrchestrator.handle_turn → RouterService.classify (intent: ambiguous, conf: 0.42) → AgentService loop iteration 1 → tool: rag_search (3 chunks, 180ms) → AgentService loop iteration 2 → tool: capture_lead → AgentService loop iteration 3 → final reply`. The trace tree shows business semantics, not just HTTP. Every span carries the iteration number (`agent.iteration: 2`), tool name (`tool.name: rag_search`), the routing decision (`router.intent_label: ambiguous`, `router.confidence: 0.42`), and the guardrail verdict (`guardrails.allowed: false`, `guardrails.reason: tenant_blocked_topic` when applicable).

**Why this priority**: The chat flow's three biggest sources of cost and latency are router decisions, agent loop iterations, and tool calls. Auto-instrumented HTTP spans don't expose any of these — they show "the agent made 3 outbound calls" without telling you whether they were 3 LLM round-trips, 3 tool calls, or 3 retries. Custom spans on these business operations are the difference between "Phoenix is initialised" and "Phoenix is useful."

**Independent Test**: Send a chat request that triggers the ambiguous agent path. Open the trace. Confirm: (a) a root span named `chat.handle_turn` wraps the whole turn; (b) one child span named `router.classify` with `router.intent_label` and `router.confidence` attributes; (c) one child span per agent loop iteration named `agent.iteration` with `agent.iteration_index`; (d) one child span per tool invocation named `tool.<tool_name>`; (e) two child spans for `guardrails.check_input` and `guardrails.check_output`, each with `guardrails.allowed` and `guardrails.reason`.

**Acceptance Scenarios**:

1. **Given** any chat turn, **When** `ChatOrchestrator.handle_turn` runs, **Then** exactly one root business span is emitted with `chat.handle_turn` name and the visitor message length as an attribute (NOT the message itself — the message is already in the FastAPI server span, redacted, and duplicating it bloats Phoenix).
2. **Given** `RouterService.classify`, **When** it returns a `RouteDecision`, **Then** a child span is exported carrying `router.intent_label` (string) and `router.confidence` (float).
3. **Given** the agent loop runs N iterations, **When** the trace is inspected, **Then** there are exactly N child spans of type `agent.iteration` with `agent.iteration_index` 0..N-1.
4. **Given** the agent invokes a tool, **When** the trace is inspected, **Then** there is a child span `tool.<name>` with `tool.name` and `tool.success` (bool) attributes.
5. **Given** the guardrails sidecar returns `allowed=false`, **When** the orchestrator's `guardrails.check_input` span is inspected, **Then** it carries `guardrails.allowed=false` and `guardrails.reason` matching the sidecar's response (e.g. `jailbreak_attempt`, `tenant_blocked_topic`).
6. **Given** a synchronous failure inside a tool, **When** the trace is inspected, **Then** the tool span has `status = ERROR` and a recorded exception event — Phoenix surfaces this as a red span.

---

### Edge Cases

- **Span attribute is a list of strings**: each list element is redacted individually; non-string elements pass through untouched.
- **Span attribute is `None` or a non-string scalar** (int, bool, float): passes through unchanged.
- **Redactor pattern overlap** (e.g. an email containing what looks like a phone-number digit run): the redactor applies API-key/bearer patterns first, then email, then phone — so a redacted email is not re-matched as a phone number.
- **Phoenix container unavailable at boot**: tracing initialisation must succeed; the OTLP exporter retries in the background.
- **OpenAI instrumentor not installed**: tracing initialisation must succeed; the optional instrumentor wiring is wrapped in `try/except ImportError`.
- **Groq instrumentor not installed**: same — startup continues, a single warning is logged, no LLM-specific spans are produced for Groq calls (the raw HTTPX span still appears).
- **Baggage helper called outside an active request context**: the helper logs a warning and is a no-op. Tests and offline scripts can call it safely.
- **Auth fails before baggage is set**: the FastAPI server span lands in Phoenix without `tenant_id` — acceptable, since there is no tenant context to attach. The span MUST NOT carry a stale `tenant_id` left over from the previous request on the same worker (SC-008 enforces this).
- **Span attribute size limits**: visitor messages and LLM prompts are clamped to OTel's default attribute size limits (≈ 32 KB per attribute). The `RedactingSpanProcessor` runs before truncation occurs, so PII redaction is not affected by clamp behaviour.
- **Phoenix UI compatibility**: the OpenInference semantic-convention attributes (`llm.*`, `tool.*`) are what Phoenix's UI is built to filter on; using OTel's generic `attributes={"..."}` instead of OpenInference conventions would render the data invisible to Phoenix's LLM-specific views.

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

#### Phase 2 — baggage, LLM observability, chat-flow spans (FR-012..020)

- **FR-012**: A custom `BaggageSpanProcessor` (subclass of OpenTelemetry's `SpanProcessor`) MUST be registered alongside the existing `RedactingSpanProcessor`. On every span start (`on_start`), it MUST read the current context's baggage and copy each baggage item to the span as an attribute. Baggage keys MUST be propagated unchanged.
- **FR-013**: A helper `set_request_baggage(items: dict[str, str]) -> None` MUST live in `backend/app/core/tracing.py` and set the supplied items on the current OpenTelemetry context's baggage. Calling it twice for the same key updates the value; clearing happens automatically when the request context unwinds.
- **FR-014**: The chat route handler (or a thin middleware after the auth dependency resolves) MUST call `set_request_baggage` with at minimum `tenant_id` (string UUID) immediately after authentication completes, and with `conversation_id` (string UUID) immediately after the orchestrator mints or resolves it. The handler MUST NOT set baggage with values it does not yet have — partial baggage is fine.
- **FR-015**: `setup_tracing` MUST attempt to import and instrument the Groq SDK via `openinference.instrumentation.groq.GroqInstrumentor`. The wiring MUST be wrapped in `try / except ImportError`; absence of the package MUST NOT crash startup. A single structured warning MUST be logged if the instrumentor is unavailable. The optional OpenAI instrumentor (already wired in Phase 1) MUST remain in place for any future OpenAI-SDK usage.
- **FR-016**: `ChatOrchestrator.handle_turn` MUST wrap its body in a span named `chat.handle_turn`. The span MUST carry attributes: `chat.visitor_message.length` (int — character count, NOT the message body), `chat.conversation_id` (string), `chat.tenant_id` (string).
- **FR-017**: `RouterService.classify` MUST wrap its body in a span named `router.classify`. The span MUST carry attributes: `router.intent_label` (string) and `router.confidence` (float) once the decision is computed. The original `RouteDecision` return value is unchanged.
- **FR-018**: `AgentService.tool_complete` (or its equivalent loop entry point) MUST emit one child span per loop iteration, named `agent.iteration`, carrying `agent.iteration_index` (int, zero-based). Each tool invocation inside an iteration MUST emit its own child span named `tool.<tool_name>` (lowercased), with `tool.name` and `tool.success` (bool) attributes; on failure the span MUST be marked `status = ERROR` and the exception MUST be recorded via `span.record_exception`.
- **FR-019**: The `GuardrailService.check_input` and `GuardrailService.check_output` methods MUST each wrap their work in a span (`guardrails.check_input` / `guardrails.check_output`) carrying `guardrails.allowed` (bool) and `guardrails.reason` (string or absent). The outbound HTTPX client span remains a child of these business spans.
- **FR-020**: All custom span attribute values that are user-visible text (visitor messages, LLM prompts, LLM completions, tool arguments) MUST be passed through the existing `RedactingSpanProcessor` codepath — no separate redaction surface. This is automatic via the existing processor.

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
- **SC-006**: For every chat request authenticated via widget token, **100% of spans** emitted during that request carry `tenant_id` AND `conversation_id` as attributes. Verified by an `InMemorySpanExporter` test that asserts the attribute presence over every span produced by a synthetic chat turn.
- **SC-007**: Per-tenant span isolation — across two concurrent chat requests from different tenants on the same uvicorn worker, no span from request A contains tenant B's `tenant_id`. Verified by an `asyncio.gather` test with two concurrent ASGI calls.
- **SC-008**: Per-request baggage cleanup — after a request completes, the OpenTelemetry context's baggage for that request MUST be empty when a fresh request begins on the same worker. Verified by a sentinel test that runs one tenant-A request then asserts the next request's baggage is empty.
- **SC-009**: Groq instrumentor presence — when `openinference-instrumentation-groq` is installed, a synthetic agent turn produces at least one span carrying `llm.model_name` matching the configured Groq model.
- **SC-010**: Custom chat-flow span coverage — a synthetic chat turn that routes to the agent path produces, at minimum: one `chat.handle_turn` root, one `router.classify` child with `intent_label` + `confidence` attributes, N `agent.iteration` children, M `tool.<name>` children (one per tool call), one `guardrails.check_input` child, one `guardrails.check_output` child. CI gate.

---

## Assumptions

- The Compose network is the security boundary. Phoenix runs as a sibling container and is not exposed beyond the local host; this is enforced operationally, not by code.
- Presidio is intentionally NOT used. The constitution forbids `torch`/`transformers` in production containers (Principle V, "Lean Containers"), and Presidio's `nlp_engine_provider` pulls a spaCy + transformers chain. Regex coverage of email / phone / API-key / bearer is sufficient for the categories listed in this spec; richer entity types (names, locations, credit cards) are out of scope here and may live in the guardrails sidecar (spec 010) if needed.
- The `request_id` / `trace_id` values bound by `RequestIDMiddleware` (spec 001, post-merge) are independent of OpenTelemetry's trace IDs. Future work MAY unify them by reading the active span ID into the log context; this spec does not require it.
- `WIDGET_TOKEN_SECRET` and `SERVICE_AUTH_SECRET` are out of scope here; secrets that arrive as span attributes via auto-instrumentation are caught by the bearer-token regex.
- The legacy `docs/SPEC_TRACING.md` (pre-spec-kit) is superseded by this file and is removed in the same commit.

### Phase 2 assumptions

- The project uses **Groq** as the LLM provider (`backend/app/services/llm_client.py` imports `from groq import AsyncGroq`). The `openinference-instrumentation-groq` package exists on PyPI and instruments the same OpenAI-compatible chat-completion path Groq exposes.
- OpenInference semantic conventions are the **shared dialect** the Phoenix UI is designed to read. Custom span attributes for chat business logic follow OpenInference conventions (`llm.*`, `tool.*`) where they apply, and project-specific conventions (`chat.*`, `router.*`, `agent.*`, `guardrails.*`) where OpenInference has no analogue. Mixing dialects is acceptable; documenting them is in `plan.md`.
- Baggage propagation is **explicitly enabled** by the helper call inside the chat route handler. We do NOT use `OTEL_PROPAGATORS=baggage` at the framework level because the only baggage we care about (tenant_id, conversation_id) is internal to one process — there is no third-party HTTP service we want to leak baggage to.
- The custom spans are added at the **service-method** boundary (ChatOrchestrator, RouterService, AgentService, GuardrailService). Span coverage of repositories and lower-level utilities is out of scope; if needed it lives in a follow-up.
- The `BaggageSpanProcessor` runs **before** the `RedactingSpanProcessor` in the export pipeline ordering — baggage-derived attributes are subject to the same PII redaction as any other string attribute.
- We accept some span volume increase. Each chat turn that previously produced ~5 spans (FastAPI root + 4 HTTPX clients) will now produce ~12–18 spans (root + router + 1–3 agent iterations + 1–3 tool spans + 2 guardrails spans + 4 HTTPX clients + 1 LLM span). Phoenix is sized for this at the local-dev scale.
