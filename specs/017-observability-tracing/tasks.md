---
description: "Task list for Observability Phase 2 — baggage, Groq instrumentor, chat-flow spans"
---

# Tasks: Observability Phase 2

**Input**: Design documents from `specs/017-observability-tracing/`

**Owner**: Person C (current branch `feature/guardrails-sidecar` or follow-up)

**Tests**: Mandatory — FR-019, SC-006, SC-007, SC-008, SC-009, SC-010 are acceptance gates.

---

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel with sibling tasks (different files, no dependencies)
- **[Story]**: Maps to user stories in [spec.md](./spec.md) (US4 = baggage, US5 = Groq LLM observability, US6 = chat-flow spans)

---

## Phase 1: Setup — Dependencies

- [ ] **T001** [US5] Add `openinference-instrumentation-groq>=0.1` to `backend/pyproject.toml` dependencies. Run `uv lock` and `uv sync`. Verify no torch / transformers pulled.

---

## Phase 2: Baggage Propagation (US4)

**⚠️ Blocks Phase 3+**: Custom spans depend on the baggage processor being registered first so they inherit `tenant_id` / `conversation_id` automatically.

- [ ] **T002** [US4] Implement `BaggageSpanProcessor` in `backend/app/core/tracing.py` per plan §1.1. Subclass `opentelemetry.sdk.trace.SpanProcessor`; override `on_start` to read baggage from the supplied parent_context (or current context if None) and copy whitelisted keys (`tenant_id`, `conversation_id`, `request_id`) to span attributes. All exceptions inside `on_start` MUST be logged and swallowed (telemetry availability > attribute completeness). Implement `on_end`, `shutdown`, `force_flush` as no-ops.
- [ ] **T003** [US4] Implement `set_request_baggage(items: dict[str, str]) -> None` in `core/tracing.py` per plan §1.2. Iterates items, calls `opentelemetry.baggage.set_baggage` to build a new context, then `opentelemetry.context.attach` to make it the current context. Skip items whose value is falsy.
- [ ] **T004** [US4] In `setup_tracing`, register the `BaggageSpanProcessor` BEFORE the existing `RedactingSpanProcessor`. Document the ordering as a comment referencing FR-020.
- [ ] **T005** [P] [US4] Add `from app.core.tracing import set_request_baggage` and a call `set_request_baggage({"tenant_id": str(claims.tenant_id), "conversation_id": str(conversation_id)})` to `backend/app/api/routes/public.py` at the chat handler — immediately after `claims` is resolved AND immediately after the orchestrator returns / mints `conversation_id`.

**Checkpoint**: Run `pytest tests/test_redaction.py` — the Phase 1 redaction test must still pass with both processors registered. No regression.

---

## Phase 3: Groq LLM Instrumentor (US5)

- [ ] **T006** [US5] In `setup_tracing`, add a `try / except ImportError` block per plan §1.3 that imports `GroqInstrumentor` and calls `.instrument()`. The try block MUST be the only place this import lives — service code does NOT import OTel. The except branch MUST log a single structured warning (NOT debug — operators want to know if observability is degraded). Place AFTER the existing OpenAI try-block.

---

## Phase 4: Custom Chat-Flow Spans (US6)

### 4a — Router + Guardrails (smallest blast radius)

- [ ] **T007** [P] [US6] In `backend/app/services/router_service.py::classify`, add `tracer = trace.get_tracer(__name__)` at module top and wrap the method body in `with tracer.start_as_current_span("router.classify") as span:`. Set `router.intent_label` (string from `decision.intent.value` or equivalent) and `router.confidence` (float) once the decision is computed.
- [ ] **T008** [P] [US6] In `backend/app/services/guardrail_service.py::check_input`, wrap the body in `with tracer.start_as_current_span("guardrails.check_input") as span:`. Set `guardrails.allowed` (bool) and `guardrails.reason` (string, only when present) on the way out.
- [ ] **T009** [P] [US6] Same as T008 for `guardrail_service.py::check_output`, span name `guardrails.check_output`.

### 4b — Orchestrator root span

- [ ] **T010** [US6] In `backend/app/services/chat_orchestrator.py::handle_turn`, wrap the body in `with tracer.start_as_current_span("chat.handle_turn") as span:`. Set `chat.visitor_message.length` (int, character count — NOT the message body), `chat.tenant_id` (string), `chat.conversation_id` (string).

### 4c — Agent loop + per-tool spans

- [ ] **T011** [US6] In `backend/app/services/agent_service.py`, wrap each loop iteration in `with tracer.start_as_current_span("agent.iteration") as span:` and set `agent.iteration_index` (zero-based int). For each tool invocation inside the iteration, wrap in `with tracer.start_as_current_span(f"tool.{tool_call.name.lower()}") as tool_span:` and set `tool.name`. On successful return set `tool.success=True`. In an exception handler set `tool.success=False`, call `tool_span.record_exception(exc)` and `tool_span.set_status(StatusCode.ERROR, str(exc))` BEFORE re-raising.

---

## Phase 5: Tests + Documentation

- [ ] **T012** [P] [US4] Create `backend/tests/test_tracing_baggage.py`:
      - `test_baggage_attribute_propagates_to_span`: set baggage `tenant_id=A`, start a span, force flush, assert `chat.tenant_id == "A"` in the exported attributes.
      - `test_baggage_does_not_leak_across_requests`: simulate two sequential request scopes; assert request 2's spans do not carry request 1's `tenant_id` (SC-008).
      - `test_concurrent_requests_isolated`: `asyncio.gather` two coroutines that each set a different `tenant_id` and produce a span; assert each span carries its own tenant (SC-007).
      - `test_baggage_processor_runs_before_redaction`: set baggage value containing `sk_live_…`, assert the exported attribute is `[REDACTED_API_KEY]`.
- [ ] **T013** [P] [US5, US6] Create `backend/tests/test_tracing_chat_spans.py`:
      - Synthetic chat turn through the orchestrator with mocked deps. Use `InMemorySpanExporter` (same pattern as the redaction test). Assert: one root span named `chat.handle_turn`, one child `router.classify` carrying `router.intent_label` + `router.confidence`, at least one `guardrails.check_input` child with `guardrails.allowed`, and (if the agent path is exercised) at least one `agent.iteration` child + one `tool.*` child.
      - `test_groq_instrumentor_wired_when_installed`: skip if `openinference.instrumentation.groq` is unavailable; otherwise assert calling `GroqInstrumentor().is_instrumented_by_opentelemetry` returns True after `setup_tracing` ran.
- [ ] **T014** [P] Update [docs/RUNBOOK.md](../../docs/RUNBOOK.md) with: how to filter Phoenix by `chat.tenant_id`, the list of project-specific span names emitted by the chat flow, and the `GUARDRAILS_TOPIC_SIM_THRESHOLD`-style env knobs that affect span values.

---

## Phase 6: Out-of-Scope (Track as Follow-ups)

- [ ] Cohere embeddings as semantic spans via `openinference-instrumentation-cohere` (currently appears as raw HTTPX in Phoenix).
- [ ] `OTEL_PROPAGATORS=baggage,tracecontext` so sidecar spans inherit caller's `tenant_id` (cross-service baggage).
- [ ] Repository-level spans (e.g. `repo.tenant.get_by_id`) — only if DB hotspots become unattributable.
- [ ] Unify the structlog `trace_id` from `RequestIDMiddleware` with OTel's span trace_id.

---

## Dependency Graph

```text
T001        (deps)
  │
  ▼
T002 ──► T003 ──► T004 ──► T005             (baggage pipeline + route call)
                          │
                          ▼
                     T006                    (Groq instrumentor)
                          │
                          ▼
                T007, T008, T009 [P]         (router + guardrails spans)
                          │
                          ▼
                     T010                    (orchestrator root)
                          │
                          ▼
                     T011                    (agent loop + tool spans)
                          │
                          ▼
                T012, T013 [P]               (tests)
                          │
                          ▼
                     T014                    (RUNBOOK)
```

**MVP cut**: T001..T006 alone give US4 (per-tenant filtering) + US5 (Groq prompts/completions). T007..T011 deliver US6 (chat-flow structure). T012..T013 lock the behaviour in CI.
