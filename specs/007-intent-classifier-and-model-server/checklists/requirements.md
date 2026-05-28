# Specification Quality Checklist: Intent Classifier & Model Server

**Purpose**: Validate specification completeness and quality before proceeding to planning
**Created**: 2026-05-27
**Updated**: 2026-05-28 — added User Stories 4 + 5 (golden-set prep, 3-way eval gate); added FR-013…020 and SC-007…010
**Feature**: [spec.md](../spec.md)

## Content Quality

- [x] No implementation details (languages, frameworks, APIs) — confined to `plan.md`
- [x] Focused on user value and business needs (RouterService correctness; auditable model decisions; CI-enforced quality gate)
- [x] Written for non-technical stakeholders
- [x] All mandatory sections completed (User Scenarios, Requirements, Success Criteria, Assumptions, Edge Cases)

## Requirement Completeness

- [x] No `[NEEDS CLARIFICATION]` markers remain in the spec body
- [x] Requirements are testable and unambiguous (each FR maps to a measurable check)
- [x] Success criteria are measurable (latencies, percentages, exit codes — see SC-001…010)
- [x] Success criteria are technology-agnostic (no FastAPI / sklearn / onnxruntime references in SC)
- [x] All acceptance scenarios are defined per user story (Given / When / Then)
- [x] Edge cases identified (very long messages, low confidence, ONNX load failure, LLM API down, missing golden set, missing raw text)
- [x] Scope is clearly bounded (MVP vs. follow-ups split in [plan.md](../plan.md))
- [x] Dependencies and assumptions identified (151-class label space, embeddings-only test data, hosted LLM API, threshold tightening cadence)

## Feature Readiness

- [x] All functional requirements (FR-001…020) have clear acceptance criteria mapped to user stories
- [x] User scenarios cover the three independent journeys: serving (US1), training-comparison (US2), integrity (US3), golden-set lineage (US4), CI gate (US5), and the optional lead score (US6, P2)
- [x] Feature meets measurable outcomes defined in Success Criteria
- [x] No implementation details leak into specification
- [x] Constitution alignment confirmed (Principle V "no torch in prod" and Principle VI "evals are the grade" are this feature's reasons to exist)

## Cross-Reference Integrity

- [x] References to `docs/SPEC.md §4` (model server contract) are accurate as of 2026-05-28
- [x] References to spec 016 (evals-and-ci) correctly cite the CI gate relationship
- [x] References to spec 018 (service-to-service auth) correctly identify the 403 layer that already protects `/predict-intent`
- [x] References to constitution principles (V, VI) match `.specify/memory/constitution.md` v1.0.0

## Honest Reality Check (2026-05-28)

These are deliberate gaps in the implementation plan rather than the spec — flagged so future readers see them.

- The trained artifacts are over a **151-class label space** (CLINC-style intent dataset, evidenced by `model_card_nn.json`'s `num_classes: 151`). The platform's routing layer expects **5 intents**. The 151→5 collapse lives in `model_server/artifacts/label_map.json` (committed by Person C as part of T004).
- The committed test data is **embeddings only** (1024-dim float vectors). The LLM zero-shot baseline requires raw text; the user has agreed to commit `model_server/artifacts/data/text_test.json` as part of T003.
- The `/predict-intent` contract in `docs/SPEC.md §4` takes raw text but no embedding pipeline ships in the model_server image (constitution V forbids torch/transformers locally). Plan §1.2 documents the three options and defers the resolution to a follow-up spec. Until then, the route accepts an explicit `embedding` field as a transitional measure (T012).
- The `classifier.macro_f1_min` threshold in `evals/eval_thresholds.yaml` is the Day-1 placeholder `0.50`. The spec mandates `0.75` (FR-010). Tightening is a deliberate separate commit (T023).

## Known Out-of-Scope (Documented, Not Hidden)

The following are explicitly deferred to follow-up specs and are listed in `plan.md` "Open Gaps":

- Text-→-embedding pipeline inside `/predict-intent` (hosted-API embedder is the agreed direction)
- `POST /predict-lead-score` endpoint (User Story 6, P2)
- Per-tenant model serving
- LLM-baseline JSON-mode / function-calling response (would reduce parse noise)
- Per-class F1 in `last_report.json` (cheap; add once the routing-label space is stable)

## Notes

- CI eval gate wired in this spec's tasks T021–T022; spec 016 (evals-and-ci) is the umbrella for all eval gates and references this spec for the classifier rail.
- Ready for `/speckit-implement` once T001–T006 (data + label_map commits) are in place; Phases 2–5 are mechanical from there.
