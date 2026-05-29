# Specification Quality Checklist: Observability — Tracing (Phase 1 + Phase 2)

**Purpose**: Validate specification completeness and quality before proceeding to implementation
**Created**: 2026-05-27 (Phase 1 ship)
**Updated**: 2026-05-29 — added Phase 2 (User Stories 4, 5, 6; FR-012 through FR-020; SC-006 through SC-010) covering baggage propagation, Groq LLM instrumentation, and custom chat-flow spans
**Feature**: [spec.md](../spec.md)

## Content Quality

- [x] No implementation details in the spec body (frameworks, lib names, exact SDK calls live in [plan.md](../plan.md))
- [x] Focused on user value and business needs (operator debugging, per-tenant filtering, LLM cost attribution)
- [x] Written for non-technical stakeholders
- [x] All mandatory sections completed (User Scenarios, Requirements, Success Criteria, Assumptions, Edge Cases)

## Requirement Completeness

- [x] No `[NEEDS CLARIFICATION]` markers remain
- [x] Requirements are testable and unambiguous (every FR maps to a pytest assertion or a Phoenix UI inspection)
- [x] Success criteria are measurable (percentages, latency budgets, span counts — SC-001 through SC-010)
- [x] Success criteria are technology-agnostic (no `OpenInference` / `Groq` / `OTel` strings inside SC text)
- [x] All acceptance scenarios are defined per user story (Given / When / Then)
- [x] Edge cases identified (instrumentor missing, baggage outside request, auth fails before baggage, attribute size limits)
- [x] Scope is clearly bounded — Phase 1 (FR-001..011) shipped, Phase 2 (FR-012..020) is this PR's deliverable
- [x] Dependencies and assumptions identified (Groq SDK is the LLM, OpenInference is Phoenix's preferred dialect, baggage is single-process)

## Feature Readiness

- [x] All functional requirements (FR-001 through FR-020) have clear acceptance criteria mapped to user stories
- [x] User scenarios cover the six independent journeys: end-to-end trace (US1), in-process redaction (US2), local-only collector (US3), per-tenant filter (US4), LLM observability (US5), chat-flow structure (US6)
- [x] Feature meets measurable outcomes defined in Success Criteria
- [x] No implementation details leak into specification

## Constitutional Alignment (re-checked 2026-05-29)

- [x] **Principle I — Tenant Isolation**: SC-007 mandates concurrent-request isolation; baggage propagation does NOT leak across request scopes on the same worker
- [x] **Principle III — Security by Default**: FR-020 keeps all custom-span string attributes inside the existing `RedactingSpanProcessor` codepath; no new PII surface
- [x] **Principle V — Lean Containers**: the Groq instrumentor is a thin HTTP wrapper, no torch/transformers transitive deps
- [x] **Principle X — PII Redaction**: explicitly extended to LLM input/output bodies via the same processor

## Cross-Reference Integrity

- [x] References to spec 010 (guardrails sidecar) — `GuardrailService.check_input` / `check_output` span coverage in FR-019 is consistent with the Protocol surface declared in `chat_orchestrator.py`
- [x] References to spec 008 (router) — `RouterService.classify` span in FR-017 wraps the existing service method, no contract change
- [x] References to spec 009 (agent) — `AgentService.tool_complete` per-iteration span in FR-018 wraps each loop iteration without changing the loop's external behaviour
- [x] References to constitution principles (I, III, V, X) match `.specify/memory/constitution.md` v1.0.0
- [x] Reference to `RedactingSpanProcessor` from Phase 1 is correctly preserved — Phase 2 layers ON TOP, not replaces

## Honest Reality Check (2026-05-29)

- **Baggage is single-process.** We do NOT propagate baggage over outbound HTTP to the sidecars. If `tenant_id` needs to land on sidecar spans, that is `OTEL_PROPAGATORS=baggage,tracecontext` work documented as a Phase 3 follow-up in `plan.md`.
- **Two SpanProcessors** run in pipeline order — `BaggageSpanProcessor` (start-only) then `RedactingSpanProcessor` (end-only, BatchSpanProcessor subclass). Different hooks, no conflict; documented in plan.md "Complexity Tracking".
- **Bilingual span naming** — OpenInference for LLM/tool fields, project-specific (`chat.*`, `router.*`, `agent.*`, `guardrails.*`) where OpenInference has no analogue. Phoenix's LLM-specific views read OpenInference; other views read any namespace. Documented as a Phase-2 assumption.
- **Tool argument capture is optional, not mandatory** — `tool.name` and `tool.success` are required; full argument JSON is left out by default to keep traces readable. A future spec may add an opt-in env knob to capture arguments.

## Known Out-of-Scope (Documented, Not Hidden)

The following are explicitly deferred to follow-up work and are listed in `plan.md` "Open Gaps":

- Cohere embeddings as semantic spans (currently raw HTTPX)
- Cross-service baggage propagation via `OTEL_PROPAGATORS=baggage,tracecontext`
- Repository-level spans (`repo.*.get_by_id` etc.)
- Unifying structlog's `trace_id` with OTel's span trace_id

## Notes

- The Phase 1 surface (FR-001..011) shipped in week 8 with the `RedactingSpanProcessor` + `phoenix` service. Phase 2 is **additive** — no existing behaviour changes, no existing test fails. Verified by the regression checkpoint in `tasks.md` after T005.
- Ready for `/speckit-implement` once T001 (deps) is landed; T002..T011 are mechanical and gated by T012..T013.
