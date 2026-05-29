---
description: "Task list for 016-Evals & CI — Person A scope only (CI pipeline owner)"
---

# Tasks: Evals & CI — Person A Scope

**Input**: Design documents from `specs/016-evals-and-ci/`

**Scope**: Person A (CI pipeline). Does NOT include Person C's eval script implementations.

**Files changed**: `.github/workflows/ci.yml`, `scripts/run_evals.sh`, `scripts/smoke_test.sh`

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (US1, US2, US5+)

---

## Phase 1: Setup — Audit Current State

**Purpose**: Understand exactly what is broken before touching files.

- [x] T001 Read .github/workflows/ci.yml and confirm the three known issues: (a) `|| true` on pytest, (b) eval-gates job commented out, (c) sidecar health checks absent
- [x] T002 [P] Read scripts/run_evals.sh and confirm it is a TODO stub with no dispatch logic
- [x] T003 [P] Read scripts/smoke_test.sh and confirm it only checks port 8000

---

## Phase 2: Foundational — Nothing blocking here

This feature is self-contained CI infrastructure. No blocking prerequisites beyond the audit above.

**Checkpoint**: Audit complete — all three files confirmed and ready to edit.

---

## Phase 3: User Story 1 — Lint, Format, and Unit Tests Gate (Priority: P1) 🎯 MVP

**Goal**: Every push runs ruff, black, and pytest. Any failure blocks merge. No `|| true` bypass.

**Independent Test**: Push a branch that has a deliberate `import os; x = 1` unused-import lint error. Confirm CI fails at the ruff step. Fix it; confirm CI passes through pytest.

### Implementation for User Story 1

- [x] T004 [US1] In .github/workflows/ci.yml: replace `pip install uv` step with `uses: astral-sh/setup-uv@v4` action and replace `uv pip install --system -e ".[dev]"` with `uv sync --group dev` (working-directory: backend)
- [x] T005 [US1] In .github/workflows/ci.yml: remove `|| true` from the pytest step so test failures actually fail CI; verify `continue-on-error` is not set (default false is fine — just remove the bypass)

**Checkpoint**: User Story 1 complete. A broken test must now fail the `lint-and-test` job.

---

## Phase 4: User Story 2 — Docker Build and Full Smoke Test (Priority: P1)

**Goal**: CI builds all service images and confirms all three health endpoints (api :8000, model_server :8001, guardrails_sidecar :8002) return 200. FR-010 is fully satisfied.

**Independent Test**: Trigger CI on a clean branch. Confirm all three `/health` curl calls return 200. Then temporarily remove the guardrails_sidecar service from docker-compose.yml (in a local test only) and confirm the smoke-test step fails.

### Implementation for User Story 2

- [x] T006 [US2] In .github/workflows/ci.yml (smoke-and-evals job): after "Verify /ready returns ready", add "Verify model_server /health returns ok" and "Verify guardrails_sidecar /health returns ok" steps each asserting `{"status":"ok"}`
- [x] T007 [P] [US2] scripts/smoke_test.sh already checks 8001 and 8002 — no change needed

**Checkpoint**: User Story 2 complete. Fresh docker compose up now validates all three services.

---

## Phase 5: User Story 5 — Eval Gates CI Job (Priority: P2, but Person A wires the job now)

**Goal**: Wire the eval-gates CI job with one named step per gate. The job depends on `smoke-test`. Person C's scripts drop in without further CI changes.

**Note**: This phase is Person A wiring the CI structure. The actual eval logic is Person C's. The scripts called here may not yet exist — they will exit 0 when not found (guarded by `run_evals.sh`) until Person C adds them.

**Independent Test**: Confirm the `eval-gates` job appears in the GitHub Actions workflow graph on the next push. Each gate step is named. With the current TODO `run_evals.sh` stub, the steps pass (exit 0). Once Person C lands scripts that fail, those steps will fail.

### Implementation for User Story 5

- [x] T008 [US5] Eval gate steps merged into smoke-and-evals job (same docker stack, one build). Five named steps: classifier, rag, agent, red-team, redaction — each calling `bash scripts/run_evals.sh <suite>`
- [x] T009 [US5] scripts/run_evals.sh rewritten: thresholds check, per-suite golden-set check (exit 1 if missing), graceful TODO skip if eval script absent, correct per-suite invocation (PYTHONPATH for rag, pytest for redaction)

**Checkpoint**: Eval-gates job is visible in CI and correctly guards on exit codes. Person C can now add eval scripts without touching ci.yml.

---

## Phase 6: Polish & Cross-Cutting

- [x] T010 [P] Verified ci.yml has no remaining `|| true` or `continue-on-error: true` bypasses
- [x] T011 [P] scripts/run_evals.sh is executable with `set -euo pipefail`
- [x] T012 `bash scripts/run_evals.sh classifier` exits 1 with "ERROR: golden set not found for 'classifier'" ✓

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1 (Audit)**: No dependencies — start immediately
- **Phase 2**: Nothing — no foundational blockers
- **Phase 3 (US1)**: Depends on Phase 1 audit
- **Phase 4 (US2)**: Depends on Phase 1 audit; can run in parallel with Phase 3
- **Phase 5 (US5)**: Depends on Phases 3 and 4 being stable (so eval-gates runs after a passing smoke test)
- **Phase 6 (Polish)**: Depends on Phases 3–5 complete

### Within Each Phase

- T004 before T005 (upgrade uv before removing `|| true` — both are in same job step block)
- T006 before T007 (add CI steps, then update the shell script to match)
- T008 before T009 (wire the CI job, then implement the shell dispatcher it calls)

### Parallel Opportunities

- T002 and T003 can run in parallel with T001 (different files)
- Phase 3 (T004–T005) and Phase 4 (T006–T007) can be worked in parallel (different CI job blocks)
- T010 and T011 polish tasks can run in parallel

---

## Parallel Example: Phase 3 + Phase 4

```bash
# Run in parallel once audit (Phase 1) is done:
# Thread A — Phase 3
T004: Upgrade uv setup in lint-and-test job
T005: Remove || true from pytest step

# Thread B — Phase 4
T006: Add sidecar health check steps to smoke-test CI job
T007: Add sidecar health checks to scripts/smoke_test.sh
```

---

## Implementation Strategy

### MVP First (User Stories 1 + 2 Only)

1. Complete Phase 1 (audit)
2. Complete Phase 3 (T004–T005) — pytest gate is now real
3. Complete Phase 4 (T006–T007) — smoke test covers all services
4. **STOP and VALIDATE**: Push to a branch and confirm lint-and-test + smoke-test both run correctly
5. Phase 5 + Phase 6 after Person C's scripts are in review

### Full Delivery

1. Phases 1–4 → functional CI backbone
2. Phase 5 (T008–T009) → eval-gates job wired
3. Phase 6 → polish and verify
4. Coordinate with Person C: once `evaluate_classifier.py` etc. land, eval-gates will actually gate

---

## Notes

- [P] tasks touch different files — safe to parallelise
- Do NOT implement any eval script logic (classifier, RAG, agent, red-team, redaction) — that is Person C's work
- `evals/eval_thresholds.yaml` structure is already correct — only tighten values when Person C provides real numbers
- After T005, there must be NO `|| true` anywhere in ci.yml that would hide a real test failure
- The `run_evals.sh` dispatcher (T009) should exit 0 gracefully (with a "TODO: eval script not yet implemented" message) when a script file does not exist yet — this avoids blocking CI before Person C lands scripts
