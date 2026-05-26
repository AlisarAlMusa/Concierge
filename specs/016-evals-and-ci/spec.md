# Feature Specification: Evals & CI/CD

> **Owner**: Person A (CI pipeline — `feature/platform-tenancy`) + Person C (eval gates — `feature/ml-guardrails-evals`)

**Feature Branch**: `016-evals-and-ci`

**Created**: 2026-05-27

**Status**: Draft

---

## User Scenarios & Testing *(mandatory)*

### User Story 1 — Every Push Runs Lint, Format, and Unit Tests (Priority: P1)

When any team member pushes to the repo, GitHub Actions automatically runs ruff lint, black format check, and pytest unit tests. A failure on any step blocks the merge.

**Why this priority**: Code quality gates are the table stakes of a professional repo. They catch regressions before they reach main.

**Independent Test**: Push a commit with a lint error. Confirm the CI job fails and the PR is blocked. Fix the error; confirm CI passes.

**Acceptance Scenarios**:

1. **Given** a push with a ruff lint violation, **When** CI runs, **Then** the lint step fails and the merge is blocked.
2. **Given** a push with unformatted code, **When** CI runs, **Then** the black check step fails.
3. **Given** a push with all clean code, **When** CI runs, **Then** lint and format checks pass and pytest runs.

---

### User Story 2 — Docker Images Build and Smoke Test Passes (Priority: P1)

CI builds all service Docker images and runs a `docker compose up` smoke test from a fresh state. All services reach healthy status. The health endpoints respond with 200. The test confirms the stack works from a clean clone.

**Why this priority**: "Works on my laptop" is not a CI gate. The smoke test is the proof that a fresh clone works, which is the submission requirement.

**Independent Test**: Trigger CI on a clean branch. Confirm all images build. Confirm `docker compose up` brings all services to healthy. Confirm `GET /health` returns 200.

**Acceptance Scenarios**:

1. **Given** a push, **When** CI runs, **Then** all Docker images (api, model_server, guardrails_sidecar, admin_app, worker) build successfully.
2. **Given** the images are built, **When** `docker compose up` runs in CI, **Then** all services reach healthy/running status.
3. **Given** the stack is up, **When** `GET /health` and `GET /ready` are called, **Then** both return 200.

---

### User Story 3 — Classifier Eval Gate Blocks a Regressed Model (Priority: P1)

The classifier eval script runs on every push, evaluates the shipped model on the held-out test set, and compares macro-F1 against the threshold in `eval_thresholds.yaml`. A model that falls below the threshold blocks the merge.

**Why this priority**: The classifier can silently get worse between Monday and Friday if there's no gate. The CI gate ensures the deployed model always meets the minimum quality bar.

**Independent Test**: Temporarily lower the threshold below the current model's F1. Confirm CI passes. Raise it above the model's F1. Confirm CI blocks. Restore the correct threshold.

**Acceptance Scenarios**:

1. **Given** the shipped classifier, **When** the eval script runs, **Then** macro-F1 is computed and compared to `eval_thresholds.yaml:classifier.macro_f1_min`.
2. **Given** macro-F1 falls below the threshold, **When** CI runs, **Then** the eval step fails and the merge is blocked.
3. **Given** macro-F1 meets or exceeds the threshold, **When** CI runs, **Then** the eval step passes.

---

### User Story 4 — Red-Team Gate Blocks Any Injection That Bypasses Guardrails (Priority: P1)

The red-team eval script runs all injection and cross-tenant probes from `evals/security/red_team_prompts.yaml` through the guardrails sidecar. Every probe must return `allowed: false`. A single passing probe blocks the merge.

**Why this priority**: The injection test passing today is not enough — a future refactor can silently reopen the hole. The CI gate ensures every merge re-proves the defence.

**Independent Test**: Add a known injection probe to the test set. Run the eval. Confirm it fails (probe was blocked). Remove a detection pattern from the guardrail. Confirm CI blocks on the next push.

**Acceptance Scenarios**:

1. **Given** the red-team test set, **When** the eval runs, **Then** every probe returns `allowed: false`.
2. **Given** a guardrail regression (a probe now returns `allowed: true`), **When** CI runs, **Then** the eval step fails and the merge is blocked.
3. **Given** all probes are blocked, **When** CI runs, **Then** `security.red_team_pass_rate = 1.0` is confirmed.

---

### User Story 5 — RAG and Agent Tool-Selection Evals Gate Quality (Priority: P2)

The RAG eval script runs 15 question/chunk/answer triples through the retrieval pipeline. The agent tool-selection eval runs 15 message/expected-tool examples. Both compare against thresholds in `eval_thresholds.yaml`. Regressions block merges.

**Why this priority**: RAG and agent quality can degrade silently with prompt changes or config changes. The golden sets make regressions visible.

**Independent Test**: Lower the RAG hit@5 threshold below the current score. Confirm CI passes. Raise it above. Confirm CI blocks.

**Acceptance Scenarios**:

1. **Given** the RAG golden set, **When** the eval runs, **Then** hit@5 ≥ `eval_thresholds.yaml:rag.hit_at_5_min` and faithfulness ≥ `rag.faithfulness_min`.
2. **Given** the agent tool-selection golden set, **When** the eval runs, **Then** tool selection accuracy ≥ `eval_thresholds.yaml:agent.tool_selection_accuracy_min`.
3. **Given** a regression in either metric, **When** CI runs, **Then** the merge is blocked.

---

### User Story 6 — Redaction CI Gate Proves No PII Leaks (Priority: P1)

The redaction test passes a fake API key, email, and phone number through the full chat flow. It checks logs, Redis, and the messages table to confirm none appear unredacted. This is a CI gate.

**Why this priority**: The redaction test proves the security property — not just that the redaction function works in isolation, but that PII cannot leak through any path in the system.

**Independent Test**: Run the redaction test. Confirm the fake API key is not present in any log line, Redis value, or message row after the chat request completes.

**Acceptance Scenarios**:

1. **Given** a chat message containing a fake API key (`sk-test-FAKE123`), **When** the full chat flow runs, **Then** the key does not appear in any log, Redis entry, or DB row.
2. **Given** the redaction test passes, **When** CI runs, **Then** `security.redaction_pass_rate = 1.0` is confirmed.
3. **Given** the redaction function is broken or bypassed, **When** CI runs, **Then** the redaction test fails and the merge is blocked.

---

### Edge Cases

- What happens when the eval golden sets are not committed? → The eval scripts fail with a clear "golden set not found" error rather than silently passing.
- What happens when the Docker build fails for one service? → The smoke test is skipped for that service; CI fails with a clear build error.
- What happens when external API credentials (LLM, embedding) are not available in CI? → Evals that require live API calls use pre-recorded fixtures or mocked responses; the CI environment docs specify which evals require real credentials.
- What happens when `eval_thresholds.yaml` thresholds are removed? → The eval scripts fail with a "threshold not found" error rather than defaulting to 0.

---

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: GitHub Actions workflow MUST run on every push to `main` and every pull request.
- **FR-002**: CI MUST run in order: lint (ruff) → format check (black) → unit tests (pytest) → image builds → smoke test → eval gates.
- **FR-003**: Any step failure MUST block the merge (no bypass via `--no-verify` or skipping steps).
- **FR-004**: The classifier eval gate MUST compare macro-F1 against `eval_thresholds.yaml:classifier.macro_f1_min`.
- **FR-005**: The RAG eval gate MUST compare hit@5 and faithfulness against their thresholds.
- **FR-006**: The agent tool-selection eval gate MUST compare accuracy against its threshold.
- **FR-007**: The red-team eval gate MUST verify every probe returns `allowed: false` (pass rate = 1.0).
- **FR-008**: The redaction test MUST verify the fake API key does not appear unredacted in any output (pass rate = 1.0).
- **FR-009**: `eval_thresholds.yaml` MUST be the single source of truth for all eval thresholds — hardcoded thresholds in scripts are not permitted.
- **FR-010**: The smoke test MUST bring up the full `docker compose` stack and confirm all health endpoints return 200.
- **FR-011**: CI MUST use `uv` for Python dependency installation (not pip).
- **FR-012**: The workflow MUST be defined in `.github/workflows/ci.yml`; no additional CI configuration files.
- **FR-013**: Eval scripts MUST fail with a clear error (non-zero exit) if golden sets or threshold files are missing.

### Key Entities

- **eval_thresholds.yaml**: Committed file defining minimum pass thresholds for all four eval gates. Format:
  ```yaml
  classifier:
    macro_f1_min: 0.75
  rag:
    hit_at_5_min: 0.70
    faithfulness_min: 0.80
  agent:
    tool_selection_accuracy_min: 0.80
  security:
    red_team_pass_rate: 1.0
    redaction_pass_rate: 1.0
  ```
- **Golden Sets**: `evals/rag/golden_set.yaml` (15 triples), `evals/agent/tool_selection_golden.yaml` (15 examples), `evals/security/red_team_prompts.yaml` (10+ probes), `evals/classifier/test_set.csv`.
- **CI Workflow**: `.github/workflows/ci.yml` — single workflow file, linear step order.

---

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: CI completes in under 15 minutes for a typical push (builds + all eval gates).
- **SC-002**: 100% of eval regressions (any metric dropping below threshold) block the merge — zero silent regressions.
- **SC-003**: The smoke test confirms a fresh `docker compose up` works on a clean clone in 100% of CI runs.
- **SC-004**: `eval_thresholds.yaml` thresholds are never bypassed — all eval scripts read from the file, not from hardcoded values.
- **SC-005**: A removed guardrail pattern causes the red-team gate to fail on the next CI run — the gate is proven effective.

---

## Assumptions

- CI runners have Docker and docker compose available (standard GitHub Actions ubuntu-latest).
- LLM and embedding API credentials are stored as GitHub Actions secrets and injected as environment variables for evals that require live calls.
- Evals that require the full stack (RAG, red-team) use the `docker compose` smoke test environment already running from the preceding CI step.
- The classifier eval runs offline against the committed test set and artifact — no live API calls required.
- RAGAS or a frozen LLM judge is used for RAG faithfulness scoring; the choice is Person C's to make and document.
- The `eval_thresholds.yaml` placeholder values (e.g., `macro_f1_min: 0.75`) are set on Day 1 and tightened as real numbers land during the week.
