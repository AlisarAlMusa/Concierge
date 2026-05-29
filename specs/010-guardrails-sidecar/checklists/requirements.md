# Specification Quality Checklist: Guardrails Sidecar — The Guardrails Engine

**Purpose**: Validate specification completeness and quality before proceeding to implementation
**Created**: 2026-05-27
**Updated**: 2026-05-29 — checklist refreshed for the NeMo + ONNX MiniLM + admin PATCH expansion (User Stories 5, 6, 7; FR-013 through FR-025; SC-006 through SC-012)
**Feature**: [spec.md](../spec.md)

## Content Quality

- [x] No implementation details (languages, frameworks, APIs) — implementation details live in [plan.md](../plan.md); the spec stays at the "what / why" level
- [x] Focused on user value and business needs (tenant safety floor + tenant-configurable business rails)
- [x] Written for non-technical stakeholders
- [x] All mandatory sections completed (User Scenarios, Requirements, Success Criteria, Assumptions, Edge Cases)

## Requirement Completeness

- [x] No `[NEEDS CLARIFICATION]` markers remain in the spec body
- [x] Requirements are testable and unambiguous — every FR maps to a concrete pytest or CI gate
- [x] Success criteria are measurable (percentages, exit codes, latency budgets, kilobyte ceilings — SC-001 through SC-012)
- [x] Success criteria are technology-agnostic (no NeMo / ONNX / Pydantic name-drops in SC)
- [x] All acceptance scenarios are defined per user story (Given / When / Then)
- [x] Edge cases identified (sidecar down, fail-closed default, empty topics, missing history, NaN embedding, deleted config)
- [x] Scope is clearly bounded (MVP cut + Phase-2 follow-ups split in [plan.md](../plan.md))
- [x] Dependencies and assumptions identified (NeMo, ONNX MiniLM, tokenizers, MemoryService, lifespan-shared httpx client from spec 018)

## Feature Readiness

- [x] All functional requirements (FR-001 through FR-025) have clear acceptance criteria mapped to user stories
- [x] User scenarios cover the seven independent journeys: platform-rail block (US1), tenant-immutable-floor (US2), PII redaction (US3), output-rail content checks (US4), dynamic tenant topics (US5), multi-turn context (US6), admin PATCH validation (US7)
- [x] Feature meets measurable outcomes defined in Success Criteria
- [x] No implementation details leak into specification

## Constitutional Alignment (re-checked 2026-05-29)

- [x] **Principle III — Security by Default**: this feature *is* the platform-rails implementation; FR-006 + FR-014 enforce immutability; FR-022/023 forces validation at the boundary
- [x] **Principle V — Lean Containers**: resolved by exporting MiniLM to ONNX offline. Sidecar image MUST NOT contain torch / transformers / sentence-transformers (SC-010 grep gate)
- [x] **Principle VI — Evals Are the Grade**: SC-001 / SC-002 / SC-006 / SC-008 / SC-009 / SC-010 are mechanical CI gates with thresholds in `evals/eval_thresholds.yaml::guardrails:`
- [x] **Principle X — PII Redaction**: FR-005 / FR-020 / FR-021 + the drift-detector test cover regression risk

## Cross-Reference Integrity

- [x] References to `docs/SPEC.md §5` (sidecar contract) noted in plan.md / tasks.md as needing update with the new payload fields
- [x] References to spec 018 (service-to-service auth) correctly identify the 403 layer that already protects every sidecar route
- [x] References to spec 007 (model_server) correctly cite the SHA-verify pattern reused for MiniLM ONNX
- [x] References to constitution principles (III, V, VI, X) match `.specify/memory/constitution.md` v1.0.0
- [x] Reference to `chat_orchestrator.py`'s `GuardrailClient` Protocol confirmed live — wiring change is a single-line DI swap, not a protocol redesign

## Honest Reality Check (2026-05-29)

These are deliberate trade-offs encoded in the spec/plan rather than hidden technical debt — flagged so future readers see them:

- **MiniLM ONNX bundled into the sidecar image (~22 MB)** is a Constitution V tension resolved by offline-export. The committed artifact pattern matches spec 007 model_server. Refusal-to-start on SHA mismatch (FR-017 reuse pattern) is the safety net.
- **Redaction regex is duplicated** between `backend/app/core/redaction.py` and `guardrails_sidecar/app/core/redaction.py` — matches the `core/vault.py` precedent from spec 018. The drift-detector test (T031 in tasks.md) is the safety net.
- **Tenant config stored on the `tenants` table as JSONB** (Option A), not as a separate `tenant_guardrail_configs` table. Promotion to a dedicated table is a Phase-2 candidate.
- **Output rails are regex-only in Phase 1.** FR-004's "system prompt content in replies" semantic check is documented as a Phase-2 follow-up in plan.md "Open Gaps".
- **Default fail policy is fail-closed.** Reverses the Week-8 placeholder default in the original spec assumptions. `GUARDRAILS_FAIL_OPEN=true` requires an explicit `docs/DECISIONS.md` entry to flip.

## Known Out-of-Scope (Documented, Not Hidden)

The following are explicitly deferred to follow-up specs and are listed in `plan.md` "Open Gaps":

- Semantic check for system-prompt-content-in-replies (FR-004 semantic lane)
- Streamlit admin UI for editing `guardrails_config` (spec 014 extension)
- Per-tenant LRU cache on topic embeddings
- Promotion of `guardrails_config` to a separate `tenant_guardrail_configs` table
- Schema-version field inside the JSONB

## Notes

- Red-team CI gate (SC-001) and redaction CI gate (SC-002) land via this spec's tasks T034 / T035; spec 016 (evals-and-ci) is the umbrella for all eval gates and references this spec for the guardrails rail.
- Ready for `/speckit-implement` once T001–T004 (deps + MiniLM artifact) land; Phases 2–9 are mechanical from there.
