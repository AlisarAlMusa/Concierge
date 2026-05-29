# Implementation Plan: Observability Phase 2 — Baggage + Groq + Chat-Flow Spans

**Branch**: `feature/guardrails-sidecar` (or follow-up branch) | **Date**: 2026-05-29 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/017-observability-tracing/spec.md`

---

## Summary

Phase 1 (FR-001..011, shipped in week 8) initialised OpenTelemetry, wired the FastAPI + HTTPX auto-instrumentors, added the `RedactingSpanProcessor`, and put the Phoenix container in Compose. Phase 1 produces useful timing traces but **Phoenix today is blind to chat business semantics**:

- Every span lacks `tenant_id` / `conversation_id` — operators cannot filter Phoenix by tenant.
- The agent uses **Groq** (`from groq import AsyncGroq`); the Phase-1 instrumentor wires the **OpenAI** SDK which the project never calls. So LLM input / completion / token counts are not captured.
- Business operations (`ChatOrchestrator.handle_turn`, `RouterService.classify`, agent loop iterations, tool calls, guardrails check) emit **zero custom spans**. The trace tree shows HTTP timing but no answer to "why did the agent loop 3 times?" or "which tool was slow?".

Phase 2 closes all three gaps. The Protocol/Service-method surface of the chat flow is unchanged — instrumentation is additive.

---

## Technical Context

**Language/Version**: Python 3.11 (CI) / 3.12 (containers). Unchanged.

**Primary new deps**:

- `openinference-instrumentation-groq>=0.1` (Groq LLM-call observability).
- `opentelemetry-api>=1.27` `baggage` module — already pulled by Phase 1.

**No** new infra. No model. No artifact. No DB migration.

**Storage**: Spans flow through the existing OTLP exporter to Phoenix. No persistence change.

**Testing**: `pytest` with `InMemorySpanExporter` (already used by Phase 1's redaction test). Concurrent-tenant isolation test via `asyncio.gather`.

**Target Platform**: Linux containers (unchanged).

**Performance Goals**:

- `BaggageSpanProcessor.on_start` MUST add < 100 µs per span (it's a small dict copy).
- Lifespan startup cost MUST stay under the Phase-1 budget of < 100 ms.

**Constraints**:

- The `BaggageSpanProcessor` MUST run alongside the existing `RedactingSpanProcessor`; ordering rule: baggage processor runs **before** the redacting processor so baggage-derived attributes are subject to the same PII redaction.
- No span attribute MAY carry a raw secret; the existing redaction codepath covers this for free (FR-020).
- Custom spans MUST follow OpenInference semantic conventions where they apply (`llm.*`, `tool.*`); project-specific namespaces (`chat.*`, `router.*`, `agent.*`, `guardrails.*`) are documented in this file.

**Scale/Scope**: ~150 LoC across `core/tracing.py` (~80) + 4 service files (~15 LoC each). No new modules.

---

## Constitution Check

*Gate evaluated against [constitution.md](../../.specify/memory/constitution.md) v1.0.0*

| Principle | Status | Notes |
|---|---|---|
| I. Tenant Isolation | ✅ Pass | Baggage carries `tenant_id`; SC-007 asserts no cross-request leakage on the same worker. Concurrent test gate. |
| II. Clean Layered Architecture | ✅ Pass | All tracing helpers live in `core/tracing.py`; service code imports `tracer.start_as_current_span` only, never raw OTel internals. |
| III. Security by Default | ✅ Pass | Custom spans inherit the `RedactingSpanProcessor` codepath — no new PII surface (FR-020). Bearer / `sk_live_…` patterns continue to be scrubbed. |
| IV. Async All the Way Down | ✅ Pass | Span entry / exit is sync (microsecond cost); `span.end()` is synchronous; no `await` inside a span context manager would block. |
| V. Lean Containers — No Torch | ✅ Pass | The Groq instrumentor is a thin HTTP wrapper — no ML deps. |
| X. PII Redaction (logs / traces / Redis / evals) | ✅ Pass | FR-020 explicitly routes custom-span text through the existing processor. |

**Post-design re-check**: No new violations.

---

## Project Structure

### Documentation (this feature)

```text
specs/017-observability-tracing/
├── plan.md              # This file
├── spec.md              # Feature spec (Phase 1 + Phase 2)
├── tasks.md             # Granular task checklist
└── checklists/
    └── requirements.md
```

### Source Code (repository root)

```text
backend/
├── app/
│   ├── core/
│   │   └── tracing.py          # MODIFY: add BaggageSpanProcessor + set_request_baggage; wire GroqInstrumentor
│   ├── services/
│   │   ├── chat_orchestrator.py    # MODIFY: wrap handle_turn in a span; record guardrails outcomes
│   │   ├── router_service.py       # MODIFY: wrap classify in a span; record intent_label + confidence
│   │   ├── agent_service.py        # MODIFY: wrap each loop iteration; wrap each tool call
│   │   └── guardrail_service.py    # MODIFY: wrap check_input / check_output; record allowed + reason
│   └── api/routes/
│       └── public.py               # MODIFY: call set_request_baggage(tenant_id, conversation_id) right after auth resolution
└── tests/
    ├── test_tracing_baggage.py     # NEW: BaggageSpanProcessor unit + per-request isolation
    ├── test_tracing_chat_spans.py  # NEW: synthetic chat turn → assert span tree shape
    └── test_redaction.py           # KEEP: Phase 1 redaction test — confirm still green with new processor in chain
```

**Structure Decision**: Everything lives inside the existing `backend/` tree — no new package, no new module. The `BaggageSpanProcessor` is added to `core/tracing.py` alongside `RedactingSpanProcessor` because they share a lifecycle and a context. The chat-flow span additions are inline in the four service files because each span is just a `with tracer.start_as_current_span(...):` wrapping an existing method body.

---

## Phase 0: Research

| Decision | Choice | Why |
|---|---|---|
| Baggage propagation mechanism | OpenTelemetry baggage API + custom `BaggageSpanProcessor` | Standard pattern. Single-process scope. No third-party HTTP would inherit. |
| Where to set baggage | Explicit `set_request_baggage(...)` call inside the chat route handler (after auth + after orchestrator mints conversation_id) | Auth resolves AFTER any middleware would run, so a pure middleware can't see `tenant_id`. The route handler is the earliest point where both values are known. |
| Span on which side of redaction | Baggage processor runs BEFORE redacting processor | Baggage values may carry text that contains PII (extremely unlikely for tenant_id, but defensively safe). |
| LLM instrumentor | `openinference-instrumentation-groq` | The agent uses `groq.AsyncGroq` (`backend/app/services/llm_client.py`). The OpenAI instrumentor we wired in Phase 1 is a no-op for Groq calls. |
| OpenInference vs OTel semantic conventions | OpenInference for LLM/tool fields; project-specific for chat/router/agent/guardrails | Phoenix UI's LLM-specific views are built against OpenInference. |
| Custom span names | `chat.handle_turn`, `router.classify`, `agent.iteration`, `tool.<name>`, `guardrails.check_input`, `guardrails.check_output` | Domain-meaningful, namespaced, sortable in Phoenix tree views. |
| Attribute naming | snake_case, dot-namespaced — `router.intent_label`, `router.confidence`, `agent.iteration_index`, `tool.name`, `tool.success`, `guardrails.allowed`, `guardrails.reason`, `chat.tenant_id`, `chat.conversation_id`, `chat.visitor_message.length` | Matches OpenInference style. |
| Tool span granularity | One span per tool invocation (not per tool *class*) | Each invocation can have different latency / success / arguments. |
| Tool argument capture | Only `tool.name` and `tool.success` are mandatory; full arguments JSON is OPTIONAL | Argument dumps can be large; let the trace tree stay readable. |
| LLM message body capture | Delegated to `GroqInstrumentor` — we do not duplicate | Single source of truth. The redactor scrubs PII before export. |

---

## Phase 1: Design

### 1.1 Baggage processor — `core/tracing.py`

```python
from opentelemetry import baggage as _baggage, context as _otel_context
from opentelemetry.sdk.trace import Span, SpanProcessor

_BAGGAGE_PROPAGATED_KEYS = ("tenant_id", "conversation_id", "request_id")

class BaggageSpanProcessor(SpanProcessor):
    """Copies baggage items to span attributes on span start.

    Runs BEFORE RedactingSpanProcessor in the pipeline so baggage-derived
    attributes are subject to the same PII redaction as any other string
    attribute (FR-020).
    """
    def on_start(self, span: Span, parent_context=None):
        try:
            ctx = parent_context or _otel_context.get_current()
            for key in _BAGGAGE_PROPAGATED_KEYS:
                v = _baggage.get_baggage(key, ctx)
                if v is not None:
                    span.set_attribute(f"chat.{key}" if key in {"tenant_id", "conversation_id"} else key, str(v))
        except Exception:
            logger.exception("BaggageSpanProcessor.on_start failed; continuing")

    def on_end(self, span):           # no-op
        return
    def shutdown(self):                # no-op
        return
    def force_flush(self, timeout_millis=30000):
        return True
```

### 1.2 Helper — `core/tracing.py`

```python
def set_request_baggage(items: dict[str, str]) -> None:
    """Attach baggage to the current OTel context.

    Subsequent spans started under this context — including auto-instrumented
    HTTPX client spans for outbound sidecar calls — will inherit these as
    attributes via BaggageSpanProcessor.

    No-op (with a debug log) if called outside an active context.
    """
    ctx = _otel_context.get_current()
    for k, v in items.items():
        if v is None:
            continue
        ctx = _baggage.set_baggage(k, str(v), context=ctx)
    _otel_context.attach(ctx)
```

### 1.3 Groq instrumentor — `core/tracing.py::setup_tracing`

Add alongside the existing OpenAI block:

```python
try:
    from openinference.instrumentation.groq import GroqInstrumentor
    GroqInstrumentor().instrument()
except ImportError:
    logger.debug("openinference-instrumentation-groq not installed; skipping")
```

Lifespan stays under the 100 ms budget — the instrumentor is a single function call that patches the Groq client at import time.

### 1.4 Custom spans

#### `ChatOrchestrator.handle_turn`

```python
async def handle_turn(self, ...) -> ChatTurn:
    with tracer.start_as_current_span("chat.handle_turn") as span:
        span.set_attribute("chat.visitor_message.length", len(message))
        span.set_attribute("chat.tenant_id", str(tenant_id))
        span.set_attribute("chat.conversation_id", str(conversation_id))
        ...  # existing body
```

#### `RouterService.classify`

```python
async def classify(self, ...) -> RouteDecision:
    with tracer.start_as_current_span("router.classify") as span:
        decision = ...  # existing body
        span.set_attribute("router.intent_label", decision.intent.value)
        span.set_attribute("router.confidence", float(decision.confidence))
        return decision
```

#### `AgentService` loop

```python
for iteration_index, _ in enumerate(...):
    with tracer.start_as_current_span("agent.iteration") as span:
        span.set_attribute("agent.iteration_index", iteration_index)
        ...
        for tool_call in tool_calls:
            with tracer.start_as_current_span(f"tool.{tool_call.name}") as tool_span:
                tool_span.set_attribute("tool.name", tool_call.name)
                try:
                    result = await self._dispatch_tool(tool_call)
                    tool_span.set_attribute("tool.success", True)
                except Exception as exc:
                    tool_span.set_attribute("tool.success", False)
                    tool_span.record_exception(exc)
                    tool_span.set_status(StatusCode.ERROR, str(exc))
                    raise
```

#### `GuardrailService.check_input` / `check_output`

```python
async def check_input(self, ...) -> GuardrailDecision:
    with tracer.start_as_current_span("guardrails.check_input") as span:
        ...  # existing body
        span.set_attribute("guardrails.allowed", decision.allowed)
        if decision.reason:
            span.set_attribute("guardrails.reason", decision.reason)
        return decision
```

### 1.5 Route-handler baggage call — `api/routes/public.py`

Right after auth resolution AND right after `conversation_id` is known:

```python
from app.core.tracing import set_request_baggage
...
set_request_baggage({
    "tenant_id": str(claims.tenant_id),
    "conversation_id": str(conversation_id),
})
```

---

## Phase 2: Implementation Order

| # | Step | Output |
|---|---|---|
| 1 | Add `openinference-instrumentation-groq>=0.1` to `backend/pyproject.toml`. `uv lock`. | New dep resolved without torch. |
| 2 | Implement `BaggageSpanProcessor` + `set_request_baggage` in `core/tracing.py`. Register the processor in `setup_tracing` BEFORE the redacting processor. | Baggage propagation pipeline live. |
| 3 | Wire `GroqInstrumentor` in `setup_tracing` (try/except ImportError). | LLM spans appear in Phoenix. |
| 4 | Wrap `RouterService.classify` and `GuardrailService.check_input/check_output` in spans with semantic attributes. | Phase 2 surface stable in routing + guardrails. |
| 5 | Wrap `ChatOrchestrator.handle_turn` in a root span and call `set_request_baggage` from the chat route handler. | Per-tenant filtering works in Phoenix. |
| 6 | Add per-iteration and per-tool spans in `AgentService`. | Agent loop introspectable. |
| 7 | Write `tests/test_tracing_baggage.py`: baggage processor unit test + per-request isolation test (`asyncio.gather` two tenants). | SC-006 + SC-007 + SC-008 enforced. |
| 8 | Write `tests/test_tracing_chat_spans.py`: synthetic chat turn → assert span tree shape, `router.intent_label`, `router.confidence`, `guardrails.allowed`. | SC-010 enforced. |
| 9 | Run full backend suite. Confirm Phase 1 redaction test still green (no new bypass). | Regression check. |

---

## Complexity Tracking

No constitution violations. One minor design tension worth recording:

| Item | Why it's here | Mitigation |
|---|---|---|
| Two SpanProcessors in the pipeline | Phase 1's `RedactingSpanProcessor` extends `BatchSpanProcessor` (export pipeline); the new `BaggageSpanProcessor` is a plain `SpanProcessor` that only owns `on_start`. They serve different lifecycle hooks and do not conflict. | The processor ordering rule is documented in this file and enforced by registration order in `setup_tracing`. |
| Bilingual span naming (OpenInference + project-specific) | OpenInference only covers LLM/tool spans; chat/router/agent/guardrails have no OpenInference analogue | Documented in plan.md "Research" table and in the spec's Phase 2 assumptions. |

---

## Open Gaps (Phase 3+)

| Gap | Owner | Trigger |
|---|---|---|
| Auto-instrument cohere embeddings as semantic LLM-class spans (not just raw HTTPX) | Person C | When the embedding lane shows up as an opaque HTTPX hotspot in Phoenix. `openinference-instrumentation-cohere` exists. |
| Unify `trace_id` from `RequestIDMiddleware` with OTel's span trace_id | Person A | When log correlation in Phoenix becomes a meaningful debugging tool. |
| Lower-level repository spans (e.g. `repo.tenant.get_by_id`) | TBD | When DB latency outliers in Phoenix become unattributable. |
| `OTEL_PROPAGATORS=baggage,tracecontext` for cross-service baggage | TBD | When Phoenix needs `tenant_id` on spans emitted by the sidecars themselves. |
