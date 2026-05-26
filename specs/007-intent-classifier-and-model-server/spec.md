# Feature Specification: Intent Classifier & Model Server

> **Owner**: Person C — `feature/ml-guardrails-evals` branch

**Feature Branch**: `007-intent-classifier-and-model-server`

**Created**: 2026-05-27

**Status**: Draft

---

## User Scenarios & Testing *(mandatory)*

### User Story 1 — Router Gets an Intent Label for an Inbound Visitor Message (Priority: P1)

The RouterService sends a visitor message to the model server and receives an intent label and confidence score within a latency budget. The model server returns the result without requiring a torch or transformers runtime.

**Why this priority**: The classifier is the brain of the router. Every inbound message passes through it. If the model server is unavailable or too slow, the entire chat flow degrades.

**Independent Test**: Send `POST /predict-intent` to the model server with a sample message; confirm a response with `label`, `confidence`, and `model_version` is returned in under 200ms.

**Acceptance Scenarios**:

1. **Given** the model server is running, **When** `POST /predict-intent` is called with `{"tenant_id": "<uuid>", "message": "I want pricing"}`, **Then** the response includes `label` (e.g., `sales`), `confidence` (0.0–1.0), and `model_version`.
2. **Given** the model server container, **When** its image is inspected, **Then** it contains no `torch` or `transformers` packages.
3. **Given** an invalid request body, **When** `POST /predict-intent` is called, **Then** the response is HTTP 422 with validation detail.
4. **Given** the service credential is missing or wrong, **When** the endpoint is called, **Then** the response is HTTP 401/403.

---

### User Story 2 — Three Models Are Trained and Compared; One Is Shipped (Priority: P1)

Person C trains three approaches offline (classical ML, small DL → ONNX, LLM zero-shot) on a public labeled dataset, compares them on macro-F1, latency, and cost, and ships the best-justified choice. The comparison is committed alongside the model card.

**Why this priority**: "Three models, three numbers, one production choice" is the graded ML engineering decision. The winner is not always highest F1 — latency and cost matter for a SaaS router.

**Independent Test**: The eval script runs the held-out test set through the shipped model and reports macro-F1 ≥ 0.75. The model card documents all three comparison numbers.

**Acceptance Scenarios**:

1. **Given** the held-out test set, **When** the classifier eval script runs, **Then** macro-F1 ≥ 0.75 (threshold in `eval_thresholds.yaml`).
2. **Given** the model card, **When** it is read, **Then** it contains: task, dataset name + hash, classical ML F1, DL(ONNX) F1, LLM zero-shot F1, per-class F1 for each, latency and cost comparison, deployed model choice, and SHA-256 of the artifact.
3. **Given** the ML and DL comparison, **When** the choice is documented in `docs/DECISIONS.md`, **Then** the reasoning includes at least one non-F1 factor (latency or cost).

---

### User Story 3 — Model Server Refuses to Start If Artifact Hash Does Not Match (Priority: P1)

On startup, the model server reads the SHA-256 hash from the model card and verifies it against the deployed artifact file. If they do not match, the server refuses to start.

**Why this priority**: A tampered or accidentally replaced model artifact could change routing behaviour silently. The hash check makes model integrity observable and enforceable.

**Independent Test**: Deploy the model server with the correct artifact — it starts. Replace the artifact with a different file (wrong hash) — it fails to start with a clear error message.

**Acceptance Scenarios**:

1. **Given** the artifact's SHA-256 matches the model card, **When** the model server starts, **Then** it initialises and `GET /health` returns 200.
2. **Given** the artifact's SHA-256 does not match the model card, **When** the model server starts, **Then** it exits with an error and does not serve requests.
3. **Given** the model card is absent, **When** the model server starts, **Then** it exits with an error.

---

### User Story 4 — Lead Score Endpoint Scores a Visitor Message (Priority: P2)

The model server exposes `POST /predict-lead-score`, which accepts a visitor message and returns a numeric lead quality score (0.0–1.0). This gates the `capture_lead` write — low-scoring messages are not written.

**Why this priority**: Lead scoring prevents the `capture_lead` tool from being used as a spam cannon. An LLM-triggered write with no quality gate is a liability.

**Independent Test**: Send a message with clear buying intent; confirm a high score. Send a spam message; confirm a low score.

**Acceptance Scenarios**:

1. **Given** a message with strong purchase intent, **When** `POST /predict-lead-score` is called, **Then** the score is above 0.5.
2. **Given** a spam-like message, **When** `POST /predict-lead-score` is called, **Then** the score is below 0.3.
3. **Given** an invalid request, **When** the endpoint is called, **Then** HTTP 422 is returned.

---

### Edge Cases

- What happens when the model server receives a very long message (5000+ characters)? → The input is truncated or rejected with 422 before inference.
- What happens when the classifier returns a confidence below the routing threshold? → The RouterService treats this as "ambiguous" and escalates to the agent (see feature 008).
- What happens when inference throws an unexpected exception? → The model server returns HTTP 500 with a structured error; the router falls back to "ambiguous" intent.
- What happens when the ONNX runtime cannot load the artifact? → The server fails to start with a clear error logged.

---

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The model server MUST expose `GET /health`, `POST /predict-intent`, and `POST /predict-lead-score`.
- **FR-002**: `POST /predict-intent` MUST accept `{tenant_id, message}` and return `{label, confidence, model_version}`.
- **FR-003**: `POST /predict-lead-score` MUST accept `{tenant_id, message}` and return `{score, model_version}`.
- **FR-004**: The model server MUST authenticate requests using a service credential (shared secret from Vault). Unauthenticated requests MUST be rejected.
- **FR-005**: The model server container MUST NOT include `torch` or `transformers`. DL serving uses `onnxruntime`; classical serving uses `scikit-learn` + `joblib`.
- **FR-006**: On startup, the model server MUST verify the artifact SHA-256 against the model card; it MUST refuse to start if they do not match.
- **FR-007**: Three models MUST be trained offline and compared: classical (TF-IDF + Logistic Regression), DL (exported to ONNX), and LLM zero-shot baseline.
- **FR-008**: The comparison MUST include macro-F1, per-class F1, inference latency, and estimated cost per 1000 inferences.
- **FR-009**: The model card MUST be a JSON file at `model_server/artifacts/model_card.json` with: task, dataset, all three comparison results, deployed model choice, and artifact SHA-256.
- **FR-010**: The shipped classifier MUST achieve macro-F1 ≥ 0.75 on the held-out test set (gated in CI via `eval_thresholds.yaml`).
- **FR-011**: All inference requests MUST have a timeout (configurable, default 500ms); slow requests return HTTP 503.
- **FR-012**: The model server image MUST be under 500MB.

### Key Entities

- **Intent Label**: One of `spam`, `faq_support`, `sales_contact`, `human_request`, `ambiguous`. The RouterService uses this to determine the handling path.
- **Lead Score**: A float 0.0–1.0 representing estimated lead quality.
- **Model Card**: JSON document with task, dataset provenance (name + hash), three-model comparison table, deployed choice, artifact SHA-256.
- **Classifier Artifact**: Either `classifier.joblib` (classical) or `classifier.onnx` (DL), stored in `model_server/artifacts/`.

---

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: `POST /predict-intent` responds in under 200ms (p95) under normal single-request load.
- **SC-002**: Classifier macro-F1 ≥ 0.75 on the held-out test set (CI gate).
- **SC-003**: Model server container image is under 500MB.
- **SC-004**: Startup hash check catches a tampered artifact in 100% of test cases.
- **SC-005**: Model card documents all three comparison results; deployed choice is backed by at least two dimensions (e.g., F1 + latency).
- **SC-006**: 100% of unauthenticated requests to model server endpoints receive 401/403.

---

## Assumptions

- The training dataset is a small public text classification dataset (e.g., intent classification dataset from Kaggle or HuggingFace) — Person C picks it on Day 1 and commits the choice.
- Training happens in a notebook or Colab (offline, never in Docker). Only the exported artifact is committed.
- The DL model is a small architecture (e.g., a lightweight text classifier); no pretrained transformer fine-tuning.
- The LLM zero-shot baseline uses the same hosted LLM API as the main agent (no additional model cost).
- `tenant_id` is passed to the model server for future per-tenant model support, but in Week 8 a single shared model serves all tenants.
- The model server is called synchronously by the RouterService with an HTTP client that has timeout and retry configured.
- Intent classes are fixed at 5: `spam`, `faq_support`, `sales_contact`, `human_request`, `ambiguous`. Changes require a retraining cycle.
