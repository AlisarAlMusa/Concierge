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

### User Story 4 — Held-Out "Golden Set" Is Derived From Test Data With Zero Training Leakage (Priority: P1)

The evaluation pipeline depends on a small, stable evaluation set committed to the repo. The set MUST be derived **only** from the held-out test artifact that the trained models never saw — never from train or val splits. The derivation is deterministic (fixed seed) and stratified so that every intent label is represented in proportion to its frequency in the test set.

**Why this priority**: CI gates that block merges on classifier regression are only meaningful if the eval set is itself trustworthy. A single row of train-set contamination invalidates every subsequent comparison. The "test-only, stratified, seeded" rule makes the data lineage auditable.

**Independent Test**: Run `python evals/prepare_golden_set.py`. Confirm: (a) the script reads `model_server/artifacts/data/X_test_emb.npy` and `y_test.npy` and no other file; (b) the output `evals/classifier/golden_set.json` has 50–100 rows; (c) the row indices are a strict subset of `range(len(y_test))`; (d) every label in the output also appears in `y_test`; (e) re-running the script with the same seed produces a byte-identical file.

**Acceptance Scenarios**:

1. **Given** the test-data artifact exists, **When** `prepare_golden_set.py` is run, **Then** an output file is written with 50–100 rows and a deterministic SHA-256 (committed alongside the file).
2. **Given** the script's source code, **When** reviewed, **Then** it imports only from `model_server/artifacts/data/*test*` paths — there is no path or filename containing `train` or `val`.
3. **Given** the produced golden set, **When** inspected, **Then** each row carries `{index, label, embedding}`. (`text` field is included when raw test text is available — see Assumptions for the current gap.)
4. **Given** stratified sampling, **When** the label distribution of the golden set is compared to the test set, **Then** the per-label proportions are within ±10% of the source distribution.

---

### User Story 5 — Three-Way Eval Harness Computes Macro-F1 and Gates CI on the Winner (Priority: P1)

A single script (`evals/classifier/run_3way_eval.py`) loads the golden set, runs all three approaches against it, computes macro-F1 for each, picks the winner, and compares the winner against `eval_thresholds.yaml`. Below threshold → exit 1, blocking the merge. Above threshold → exit 0, with a structured report committed to the run logs.

**Why this priority**: "Three numbers, one shipping decision" is the engineering grade. Manual comparisons rot; an automated harness with a hard threshold makes the decision auditable and prevents silent regression.

**Independent Test**: Run `python evals/classifier/run_3way_eval.py`. Confirm: (a) the script prints macro-F1 for the classical, ONNX, and LLM zero-shot models on the same golden set; (b) the winner is identified; (c) exit code is 0 when the winner ≥ threshold and 1 when below; (d) a JSON report is written to `evals/classifier/last_report.json` with per-model scores and the resolved threshold.

**Acceptance Scenarios**:

1. **Given** all three artifacts (joblib, onnx, LLM creds) are available, **When** the eval runs, **Then** three macro-F1 numbers are printed and a winner is named.
2. **Given** the winner's score ≥ `classifier.macro_f1_min`, **When** the script finishes, **Then** exit code is 0.
3. **Given** the winner's score < threshold, **When** the script finishes, **Then** exit code is 1 and the failing model + score are emitted to stderr in a CI-parseable format.
4. **Given** the LLM API is unreachable, **When** the script runs, **Then** the LLM baseline records `score=null` with a documented reason, the classical + ONNX scores are still computed, and the winner is selected among the available models. The exit code still reflects threshold compliance.
5. **Given** the golden set is missing, **When** the script runs, **Then** it exits 2 (configuration error) with a clear message telling the user to run `prepare_golden_set.py`.

---

### User Story 6 — Lead Score Endpoint Scores a Visitor Message (Priority: P2)

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
- **FR-013**: The model server MUST load the joblib classical artifact AND the ONNX DL artifact at startup (lifespan), keep both in memory, and serve `POST /predict-intent` from whichever is named the "deployed" model in `model_card.json`. Loading happens once; per-request inference MUST NOT touch disk.
- **FR-014**: `POST /predict-intent` MUST compute `confidence` as the maximum class probability. For the classical model, this is `predict_proba(x).max()`; for ONNX it is `softmax(logits).max()`. A raw decision-function score (no probability) MUST NOT be returned as `confidence`.
- **FR-015**: `POST /predict-intent` MUST resolve `label` from a committed integer-to-string mapping (`model_server/artifacts/label_map.json`). If the mapping is absent at startup, the model server MUST refuse to start.
- **FR-016**: `model_version` in the response MUST come from `model_card.json`. If the field is missing, the server fills `model_version` with the artifact's first 12 hex chars of SHA-256 — never `unknown`, never the empty string.
- **FR-017**: A `prepare_golden_set.py` script under `evals/` MUST stratify-sample 50–100 rows **exclusively** from the test-split artifacts (`X_test_emb.npy`, `y_test.npy`, and the test text file). It MUST use a fixed numpy seed (committed) so re-runs are byte-identical. It MUST refuse to read any file whose name contains `train` or `val`.
- **FR-018**: A `run_3way_eval.py` script under `evals/classifier/` MUST evaluate (a) the joblib classical model, (b) the ONNX DL model, (c) an LLM zero-shot baseline using the project's hosted LLM API, on the same `golden_set.json`. It MUST report macro-F1 per model, pick the highest scorer as the "winner", and compare the winner against `classifier.macro_f1_min` from `evals/eval_thresholds.yaml`. Exit 0 on pass, 1 on regression, 2 on configuration error.
- **FR-019**: The LLM zero-shot baseline MUST send the **raw text** of each golden-set row to the LLM with a prompt that lists the allowed labels and asks for one-shot classification. The response MUST be parsed strictly — unparseable replies count as misclassifications, not exceptions.
- **FR-020**: When the eval script runs, it MUST write `evals/classifier/last_report.json` containing: timestamp, golden-set SHA-256, per-model F1 (and per-class F1 if cheap), winner, threshold, pass/fail. This file is **not** committed (it is a CI artifact); the SHA-256 of the *golden set* IS committed alongside the golden set itself.

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
- **SC-007**: `prepare_golden_set.py` is byte-deterministic across runs on the same seed — diffing two outputs from independent clones returns no difference.
- **SC-008**: `run_3way_eval.py` exits with code 1 within 60 seconds when the deployed model regresses below threshold, in 100% of injected-regression test cases.
- **SC-009**: `prepare_golden_set.py` opens zero files whose name matches `*train*` or `*val*`. Verified by a grep-based static check in CI (no `train` or `val` literal in path constants).
- **SC-010**: The LLM zero-shot baseline submits at most one API call per golden-set row (no retries beyond a single 429 backoff). Cost is bounded by `len(golden_set) * 1 call`.

---

## Assumptions

- The training dataset is a small public text classification dataset (e.g., intent classification dataset from Kaggle or HuggingFace) — Person C picks it on Day 1 and commits the choice.
- Training happens in a notebook or Colab (offline, never in Docker). Only the exported artifact is committed.
- The DL model is a small architecture (e.g., a lightweight text classifier); no pretrained transformer fine-tuning.
- The LLM zero-shot baseline uses the same hosted LLM API as the main agent (no additional model cost).
- `tenant_id` is passed to the model server for future per-tenant model support, but in Week 8 a single shared model serves all tenants.
- The model server is called synchronously by the RouterService with an HTTP client that has timeout and retry configured.
- Intent classes are fixed at 5: `spam`, `faq_support`, `sales_contact`, `human_request`, `ambiguous`. Changes require a retraining cycle.
- The committed trained artifacts (`best_intent_classifier.joblib`, `intent_classifier_nn.onnx`) are over a richer label space (151 classes, sourced from a CLINC-style intent dataset — see `model_card_ml.json` / `model_card_nn.json`). The mapping from the raw 151-class output to the 5 routing intents lives in `model_server/artifacts/label_map.json` and is committed alongside the artifacts. Both layers (raw classifier label + routing intent) are returned in the model card; only the routing intent appears in `PredictResponse.label`.
- The `model_server/artifacts/data/` directory contains test-split embeddings (`X_test_emb.npy`, 1200×1024 float64) and integer labels (`y_test.npy`). The raw test text is committed as `model_server/artifacts/data/text_test.json` (or `.txt`), enabling the LLM zero-shot baseline. If the text file is missing, the LLM baseline records `score=null` (User Story 5, Acceptance Scenario 4) instead of failing the run.
- Each row in `golden_set.json` is JSON-serialisable: integer index, integer label, optional string text, and a list of 1024 floats for the embedding. The file size is bounded (≈ 100 rows × ~16 KB ≈ ~1.6 MB) — committing it is acceptable.
- The eval script uses the same hosted LLM API the agent uses for tool calls (env: `LLM_PROVIDER`, `LLM_MODEL`). API credentials are passed via env var at CI run time; they are NOT stored in the repo.
- The CI threshold (`classifier.macro_f1_min`) currently sits at the Day-1 placeholder of `0.50` and MUST be tightened to `0.75` (FR-010) once the eval harness is wired and the first real numbers are recorded. Tightening is a separate, conscious commit — not part of this feature's mechanical work.
