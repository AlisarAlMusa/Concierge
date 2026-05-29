---
description: "Task list for Intent Classifier & Model Server + 3-way evaluation gate — Owner C"
---

# Tasks: Intent Classifier & Model Server

**Input**: Design documents from `specs/007-intent-classifier-and-model-server/`

**Owner**: Person C (`feature/model_server` branch)

**Tests**: Mandatory — FR-009/FR-010/FR-018 make the eval harness an acceptance gate.

---

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel with sibling tasks (different files, no dependencies)
- **[Story]**: Maps to user stories in [spec.md](./spec.md) (US1, US2, US3, US4, US5, US6)

---

## Phase 1: Setup — Dependencies & Data Artifacts

**Purpose**: Get the deps and the data committed. Nothing in Phase 1 changes runtime behaviour beyond what the existing image already does.

- [ ] **T001** [US1] Add `onnxruntime>=1.18` to `model_server/pyproject.toml` dependencies. (joblib + scikit-learn + numpy are already present from prior work.)
- [ ] **T002** [P] [US5] Create `evals/pyproject.toml` (or `evals/requirements.txt`) listing `numpy`, `scikit-learn`, `PyYAML`, and the LLM SDK matching `LLM_PROVIDER` (default `openai>=1.30`). Used by CI's `classifier-eval` job.
- [ ] **T003** [US4] Commit `model_server/artifacts/data/text_test.json` — a JSON list of 1200 strings, aligned positionally with `y_test.npy`. Pinned to the same source dataset that produced `X_test_emb.npy`. (Provided by the user — do not regenerate from train data.)
- [ ] **T004** [US1] Commit `model_server/artifacts/label_map.json` with shape:
      ```json
      {
        "raw":     {"0": "restaurant_reviews", "1": "...", ..., "150": "..."},
        "routing": {"restaurant_reviews": "faq_support", "translate": "faq_support", ...}
      }
      ```
      `raw` ids cover all 151 source-dataset classes. `routing` collapses raw names to one of the 5 platform intents (`spam`, `faq_support`, `sales_contact`, `human_request`, `ambiguous`). Any raw name NOT in `routing` falls through to `ambiguous` at load time and is logged once.
- [ ] **T005** [P] [US1] Add `ARTIFACTS_DIR` field to `model_server/app/core/config.py` (default `"/app/artifacts"` in the container, `"model_server/artifacts"` for tests).
- [ ] **T006** [P] [US3] Add `model_version` to `model_card_ml.json` and `model_card_nn.json` (a short string — date or git SHA of the training run). Optional per FR-016, but trivial to fix here.

**Checkpoint**: `uv lock` clean in both directories; all four data/label files committed and < ~5 MB combined.

---

## Phase 2: Foundational — Model Loader + Hash Verification

**Purpose**: Single trusted entry point that loads both artifacts and refuses to start on tampering.

**⚠️ Blocks Phase 3+**: User-story work cannot begin until artifacts load cleanly.

- [ ] **T007** [US1, US3] Implement `model_server/app/core/model_loader.py` per plan §1.1:
      - `verify_sha256(path: Path, expected: str) -> None` raises `IntegrityError` on mismatch.
      - `load_classical(path)` returns a `LoadedModel` whose `predict(emb)` returns `(label_id, confidence)`. Confidence path: prefer `predict_proba`; if absent, softmax over `decision_function` (LinearSVC fallback).
      - `load_onnx(path)` returns a `LoadedModel` whose `predict(emb)` calls `InferenceSession.run`, softmaxes logits, returns `(argmax, max_prob)`.
      - `load_label_map(path) -> LabelMap`. Refuses to start if missing (FR-015).
      - `load_all(artifacts_dir)` orchestrates the above; picks `deployed_name` from whichever model card has `"deployed": true` (default: the higher macro-F1 in the cards).
- [ ] **T008** [P] [US3] Add `model_server/tests/__init__.py` and `tests/test_model_loader.py` covering:
      - Load both artifacts via `load_all` → succeeds.
      - Tamper one artifact (write a single extra byte to a copy) → `IntegrityError`.
      - Missing `label_map.json` → `IntegrityError` or `FileNotFoundError`.
      - `predict(emb)` returns confidence ∈ [0, 1] and label ∈ keys of `label_map.raw`.
- [ ] **T009** [US1] Wire `model_loader.load_all(...)` into `model_server/app/main.py`'s `lifespan`:
      ```python
      app.state.loader = load_all(Path(get_settings().ARTIFACTS_DIR))
      ```
      Add a `Depends()` accessor `get_loader(request) -> ModelLoader` in `model_server/app/dependencies.py` (new file).

**Checkpoint**: `docker compose up model_server` boots cleanly; `docker compose logs model_server` shows both artifacts loaded and the deployed-model name. Tamper-test passes.

---

## Phase 3: User Story 1 — `/predict-intent` Returns A Label + Confidence (P1) 🎯 MVP for serving

**Goal**: A real prediction comes out of `POST /predict-intent`.

- [ ] **T010** [US1] Implement `model_server/app/services/predict_service.py`:
      ```python
      async def predict_intent(embedding: list[float] | np.ndarray, loader: ModelLoader) -> PredictResponse:
          label_id, confidence = await asyncio.to_thread(loader.deployed.predict, np.asarray(embedding))
          raw = loader.label_map.raw[str(label_id)]
          routing = loader.label_map.routing.get(raw, "ambiguous")
          return PredictResponse(label=routing, confidence=float(confidence),
                                 model_version=loader.deployed.model_version)
      ```
- [ ] **T011** [US1] Replace the stub body in `model_server/app/main.py::predict_intent` with a call to `predict_service.predict_intent`, using the `get_loader` dependency. **Keep** the existing `Depends(require_service_token)` (spec 018).
- [ ] **T012** [US1] Per plan §1.2 "Open Gap": the current `PredictRequest.message: str` cannot be satisfied without an embedding pipeline. Until the follow-up spec lands, the route accepts a temporary additional field `embedding: list[float] | None` and prefers it when present; if only `message` is supplied, the route returns HTTP 503 with `{"detail": "text embedding pipeline not yet wired — pass `embedding` directly or wait for spec X18"}`. Document this in a TODO comment referencing the open gap.
- [ ] **T013** [US1] Write `model_server/tests/test_predict_endpoint.py` against the in-process app:
      - Without `X-Service-Token` → 403 (regression check for spec 018).
      - With valid token + a synthetic 1024-dim embedding → 200 with `label` in the 5 routing intents and `0 ≤ confidence ≤ 1`.
      - Missing both `message` and `embedding` → 422 (Pydantic).
      - Only `message` supplied → 503 with documented `detail`.

**Checkpoint** (US1 Acceptance): `pytest model_server/tests/ -v` green. Manual `curl` returns a real prediction.

---

## Phase 4: User Story 4 — Golden Set Preparation Script (P1)

**Goal**: A committed, deterministic, leakage-free `golden_set.json`.

- [ ] **T014** [US4] Create `evals/__init__.py` and `evals/classifier/__init__.py`.
- [ ] **T015** [US4] Implement `evals/prepare_golden_set.py` per plan §1.4:
      - Hard-coded `SEED = 20260528`, `TARGET_SIZE = 80`.
      - Reads only `X_test_emb.npy`, `y_test.npy`, `text_test.json` — path constants MUST NOT include the substrings `train` or `val` anywhere (SC-009).
      - Stratified sample via `train_test_split(stratify=y, test_size=TARGET_SIZE, random_state=SEED)`; emit the small-side indices.
      - Output schema per row: `{index: int, label: int, embedding: list[float], text: str|null}`.
      - `json.dumps(..., sort_keys=True, indent=2)` for byte-determinism.
      - Compute SHA-256 of the output file and write it to `evals/classifier/golden_set.sha256`.
- [ ] **T016** [US4] Write `evals/tests/test_prepare_golden_set.py`:
      - `test_no_train_or_val_references_in_source` — read the script source, assert neither `train` nor `val` appears in any string literal.
      - `test_output_is_deterministic` — run the script twice (delete output between), assert SHA-256 matches.
      - `test_size_in_range` — assert `50 <= size <= 100`.
      - `test_indices_subset_of_test_set` — assert every `index` is in `range(len(y_test))`.
      - `test_label_distribution_within_10pct` — compare per-label proportion to source `y_test`.
- [ ] **T017** [US4] Run the script for real; commit `evals/classifier/golden_set.json` + `evals/classifier/golden_set.sha256`. (One-time deterministic generation; future re-runs validate, not regenerate, unless the source data changes.)

**Checkpoint** (US4 Acceptance): All five tests pass; `golden_set.json` and its SHA are committed.

---

## Phase 5: User Story 5 — 3-Way Eval Harness + CI Gate (P1)

**Goal**: One script that prints three numbers and exits 1 on regression.

- [ ] **T018** [US5] Implement `evals/classifier/run_3way_eval.py` per plan §1.5:
      - Load `golden_set.json` + verify its SHA against `golden_set.sha256`.
      - Run classical: `joblib.load(...).predict(X)`.
      - Run ONNX: `onnxruntime.InferenceSession(...).run(...)`; argmax over logits.
      - Run LLM zero-shot: one API call per row, temperature 0, retry once on 429, strict string parse. Misparse counts as incorrect. Missing `OPENAI_API_KEY` (or equivalent) → record `f1_llm = None` and continue.
      - Compute macro-F1 per model with `sklearn.metrics.f1_score(..., average="macro")`.
      - Pick winner among non-None scores.
      - Load `classifier.macro_f1_min` from `evals/eval_thresholds.yaml`.
      - Write `evals/classifier/last_report.json` (not committed; in `.gitignore`).
      - Print machine-parseable line: `RESULT classical=X.XXXX onnx=X.XXXX llm=X.XXXX|null winner=NAME threshold=X.XXXX passed=true|false`.
      - Exit 0 (pass), 1 (regression), 2 (config error — missing golden set, missing artifact, missing thresholds file).
- [ ] **T019** [US5] Add the 5-class label name resolution for the LLM prompt:
      - The LLM is asked to choose among the **5 routing intents**, not the 151 raw classes (asking an LLM to pick among 151 fine-grained intents is unfair and outside the project's contract).
      - The eval first collapses `y_true` to routing intents via `label_map.routing`; the LLM's predicted string is mapped back the same way. F1 is computed on the routing-intent space.
      - This is also what the in-cluster `/predict-intent` returns, so the three models are scored on the same target.
- [ ] **T020** [US5] Add `evals/classifier/last_report.json` to `.gitignore`. **Do not** gitignore `golden_set.json` or its SHA — those are committed.
- [ ] **T021** [US5] Append a `classifier-eval` job to `.github/workflows/ci.yml` per plan §1.6:
      - Runs after `lint-and-test`.
      - Grep gate first (SC-009): fail the job if `evals/prepare_golden_set.py` references `X_train` or `X_val`.
      - Then `python evals/classifier/run_3way_eval.py`. Exit 1 from the script fails the job.
      - Pass `OPENAI_API_KEY` from repo secrets; the script tolerates its absence.
- [ ] **T022** [US5] Add a regression smoke-test under `evals/tests/test_run_3way_eval.py`:
      - Mock the classical & ONNX predictions to be all-zeros (forcing low F1).
      - Run the script; assert exit code is 1.
      - Restore truthful predictions; assert exit code is 0.

**Checkpoint** (US5 Acceptance): Local run prints three numbers and exits 0. Injected regression exits 1. CI shows the job.

---

## Phase 6: Threshold Tightening + Polish

- [ ] **T023** [US5] After the first green CI run, raise `classifier.macro_f1_min` in `evals/eval_thresholds.yaml` from `0.50` to `0.75` (FR-010). **Separate commit** so the threshold change is auditable.
- [ ] **T024** [P] Update [docs/SPEC.md](../../docs/SPEC.md) §4 if any contract details changed (`label` values, response shape). Today's plan keeps the contract intact, so this should be a one-line note about the routing-intent label space.
- [ ] **T025** [P] Update [docs/RUNBOOK.md](../../docs/RUNBOOK.md) with: how to regenerate the golden set, how to run `run_3way_eval.py` locally, where the LLM API key is sourced from.
- [ ] **T026** [P] Bump `model_server` image and check size is < 500 MB (FR-012 / SC-003): `docker images concierge-model_server --format '{{.Size}}'`. Document the number in the commit message.

---

## Phase 7: Out-of-Scope (Track as Follow-ups)

These are explicitly NOT part of this spec's deliverable; record so they don't get silently dropped:

- [ ] Text-→-embedding pipeline for `/predict-intent` (option 3 from plan §1.2 — hosted-API embedder). Blocks the literal SPEC.md §4 contract.
- [ ] `POST /predict-lead-score` (User Story 6, P2).
- [ ] Per-tenant model serving.
- [ ] Replace the LLM-baseline strict-string parse with a JSON-mode response and a function-calling tool — would reduce LLM noise but adds provider-specific complexity.
- [ ] Add per-class F1 in `last_report.json` once the routing-label space is stable.

---

## Dependency Graph

```text
T001..T006   (Setup — parallel where [P])
     │
     ▼
T007  ───► T008  ───► T009            (Model loader, then test, then wire)
                          │
                          ▼
                     T010 ─► T011 ─► T012 ─► T013          (US1: /predict-intent)
                          │
                          │ (independent path)
                          ▼
                     T014 ─► T015 ─► T016 ─► T017          (US4: golden set)
                                                  │
                                                  ▼
                                             T018 ─► T019 ─► T020 ─► T021 ─► T022   (US5: 3-way + CI)
                                                                                │
                                                                                ▼
                                                                          T023..T026  (polish + tighten)
```

**MVP cut**: T001–T013 alone deliver User Story 1 (`/predict-intent` returns a real label). T014–T022 deliver the eval gate. Both halves are independently mergeable; landing only the eval gate without the endpoint is still useful (CI starts failing on regression even before the endpoint is live).
