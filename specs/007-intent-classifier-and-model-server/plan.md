# Implementation Plan: Intent Classifier & Model Server

**Branch**: `feature/model_server` | **Date**: 2026-05-28 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/007-intent-classifier-and-model-server/spec.md`

---

## Summary

Two halves to land here:

1. **Model server `/predict-intent` endpoint** — load the two trained artifacts (`best_intent_classifier.joblib`, `intent_classifier_nn.onnx`) at FastAPI lifespan, hash-verify each against its model card, then serve predictions. The endpoint picks one "deployed" model per the card; the other is kept loaded so the 3-way eval and future A/B work can call it without redeploying. `confidence` is the max softmax probability; `label` is resolved through a committed integer→string mapping, then collapsed to one of the 5 routing intents.
2. **Golden-Set Evaluation Gate** — a deterministic, test-only sampler (`evals/prepare_golden_set.py`) produces `golden_set.json` from the existing `X_test_emb.npy` + `y_test.npy` + raw test text. A 3-way comparison script (`evals/classifier/run_3way_eval.py`) runs the classical, ONNX, and LLM zero-shot models against the same golden set, computes macro-F1, picks a winner, and exits 1 if the winner falls below `classifier.macro_f1_min` in `eval_thresholds.yaml`. This becomes a CI step that blocks merges on regression.

The Vault-backed service-auth from spec 018 is now in place across the three services, so `/predict-intent` is already 403-gated. This plan inherits that guarantee; nothing in the eval path touches the service-auth surface.

---

## Technical Context

**Language/Version**: Python 3.11 (CI) / 3.12 (container).

**Primary Dependencies**:
- `model_server/`: `scikit-learn`, `joblib`, `numpy`, `onnxruntime` (new). No torch, no transformers (constitution Principle V).
- `evals/`: `numpy`, `scikit-learn` (for `f1_score`), `PyYAML` (read thresholds), the project's LLM SDK (`openai>=1.x` or `anthropic` depending on `LLM_PROVIDER`).

**Storage**:
- Trained artifacts: `model_server/artifacts/best_intent_classifier.joblib`, `intent_classifier_nn.onnx`. SHA-256 in `model_card_*.json`.
- Test data: `model_server/artifacts/data/X_test_emb.npy` (1200×1024 float64), `y_test.npy` (1200 int64). Raw text test split: `model_server/artifacts/data/text_test.json` (loaded once the user commits it).
- Label mapping: `model_server/artifacts/label_map.json` (int → raw intent name → routing intent), committed once.
- Golden set: `evals/classifier/golden_set.json` + companion `golden_set.sha256`. Committed.
- Eval report: `evals/classifier/last_report.json`. CI artifact, **not** committed.

**Testing**: `pytest` + `pytest-asyncio` for the endpoint; a small `tests/test_prepare_golden_set.py` for determinism / no-leakage; the 3-way script is itself a CI-runnable test.

**Target Platform**: Linux container, same Compose stack as the rest of the SaaS.

**Project Type**: Multi-service web app (FastAPI sidecar + evaluation harness).

**Performance Goals**: `/predict-intent` p95 < 200 ms (SC-001); 3-way eval finishes in < 60 s on a 100-row golden set (SC-008).

**Constraints**:
- No torch/transformers in `model_server` image (constitution V).
- Image < 500 MB (FR-012).
- `prepare_golden_set.py` reads only test-split files; CI grep enforces (SC-009).
- LLM zero-shot baseline issues ≤ 1 API call per row (SC-010); failures degrade gracefully (no exception escapes the eval).

**Scale/Scope**: 1200-row test split → 50–100-row golden set. Three model approaches, one shared LLM API. ~250 LoC new code total.

---

## Constitution Check

*Gate evaluated against [constitution.md](../../.specify/memory/constitution.md) v1.0.0*

| Principle | Status | Notes |
|-----------|--------|-------|
| I. Tenant Isolation | ✅ N/A | The model is shared across tenants; `tenant_id` is logged for cost attribution only (SPEC.md §4). |
| II. Clean Layered Architecture | ✅ Pass | Routes delegate to a `predict_service` module; model loading lives in `core/model_loader.py`. No SQL or HTTP client construction inside routes. |
| III. Security by Default | ✅ Pass | `/predict-intent` carries `Depends(require_service_token)` from spec 018. Health stays open. No raw secrets in logs. |
| IV. Async All the Way Down | ✅ Pass | Inference is CPU work; wrapped in `asyncio.to_thread` so it doesn't block the loop. The eval script is sync (offline). |
| V. Lean Containers — No Torch | ✅ Pass | `model_server/pyproject.toml` gains `onnxruntime` only; `torch`/`transformers` remain absent. CI verifies via `! grep -E '(torch|transformers)' pyproject.toml`. |
| VI. Evals Are the Grade | ✅ Pass | This feature **is** the implementation of Principle VI for the classifier. The 3-way harness + CI gate is the artifact the rule demands. |

**Post-design re-check**: No new violations.

---

## Project Structure

### Documentation (this feature)

```text
specs/007-intent-classifier-and-model-server/
├── plan.md              # This file
├── spec.md              # Feature spec (updated 2026-05-28)
├── tasks.md             # Granular task checklist
└── checklists/
    └── requirements.md  # Spec quality checklist
```

### Source Code (repository root)

```text
model_server/
├── app/
│   ├── main.py                         # MODIFY: lifespan loads both artifacts + label_map
│   ├── schemas.py                      # No change (PredictRequest / PredictResponse already correct per SPEC.md §4)
│   ├── core/
│   │   ├── config.py                   # MODIFY: add ARTIFACTS_DIR field
│   │   ├── security.py                 # No change (spec 018)
│   │   └── model_loader.py             # NEW: load joblib + onnx, verify SHA, parse model card
│   └── services/
│       ├── __init__.py                 # NEW
│       └── predict_service.py          # NEW: predict_intent(text) routes to deployed model
├── artifacts/
│   ├── best_intent_classifier.joblib   # Already committed
│   ├── intent_classifier_nn.onnx       # Already committed
│   ├── model_card_ml.json              # Already committed (MODIFY: add `deployed: true|false`, `model_version`)
│   ├── model_card_nn.json              # Same
│   ├── label_map.json                  # NEW: { "raw": {"0": "restaurant_reviews", ...}, "routing": {"restaurant_reviews": "faq_support", ...} }
│   └── data/
│       ├── X_test_emb.npy              # Committed
│       ├── y_test.npy                  # Committed
│       └── text_test.json              # NEW (provided by user): list[str] of length 1200, aligned with y_test
└── tests/
    ├── __init__.py
    ├── test_predict_endpoint.py        # NEW: end-to-end via ASGITransport
    └── test_model_loader.py            # NEW: hash mismatch refuses startup

evals/
├── eval_thresholds.yaml                # Already committed (MODIFY at hand-off: 0.50 → 0.75 for classifier.macro_f1_min)
├── prepare_golden_set.py               # NEW: stratified-sample test split → golden_set.json
├── classifier/
│   ├── golden_set.json                 # NEW (generated; committed for reproducibility)
│   ├── golden_set.sha256               # NEW (committed)
│   ├── run_3way_eval.py                # NEW: classical + onnx + LLM zero-shot → macro-F1 → CI gate
│   └── __init__.py
└── tests/
    └── test_prepare_golden_set.py      # NEW: leakage + determinism

.github/
└── workflows/
    └── ci.yml                          # MODIFY: new "eval gate" job runs run_3way_eval.py with LLM creds from secrets
```

**Structure Decision**: The eval harness is a sibling of `model_server/`, not nested under it. This keeps the offline / online split visible: anything under `model_server/app/` ships in the production image; anything under `evals/` does not. The CI job that runs `run_3way_eval.py` uses a stand-alone Python 3.11 environment, not the model_server container.

---

## Phase 0: Research

| Decision | Choice | Why |
|---|---|---|
| Confidence source for ONNX | `softmax(logits).max()` computed in Python after inference | `onnxruntime` returns raw logits; we cannot rely on the exported graph to include softmax. Doing it in Python is one numpy line and keeps the artifact unchanged. |
| Confidence source for classical | `model.predict_proba(x).max()` | `LinearSVC` (per `model_card_ml.json`) does NOT expose `predict_proba`. The trained pipeline must include `CalibratedClassifierCV` wrapping, or the loader must call `decision_function` and pass it through softmax. **Open question — see "Open Gaps" below**. |
| Inference threading | `asyncio.to_thread(...)` per request | CPU-bound; keeps the event loop responsive under concurrent load. `onnxruntime` already releases the GIL in C++. |
| Hash verification | Read SHA-256 from each model card, compare to `hashlib.sha256(open(...).read()).hexdigest()` | Matches existing artifact metadata. FR-006 / US3. |
| Stratified sampling | `sklearn.model_selection.train_test_split(stratify=y_test)` with fixed `random_state` | Stratifies for free; deterministic on the same seed. |
| LLM zero-shot prompt format | "Reply with one label from the following list: {labels_csv}. Message: {text}. Reply with just the label, no other text." Temperature 0. | Lowest-variance prompt; deterministic-enough for a baseline. Parsing is `response.strip().lower()` matched against label set. |
| LLM SDK | Direct use of the provider SDK (`openai` if `LLM_PROVIDER=openai`); eval script does NOT go through the backend's stubbed `llm_client.py` | Eval is offline; bypassing the backend stub avoids coupling the CI gate to ongoing backend refactors. |
| Threshold source | `evals/eval_thresholds.yaml` (already committed) | Single source of truth across the codebase. Tightening to 0.75 is a separate decision-commit per the spec's Assumptions section. |

---

## Phase 1: Design

### 1.1 `model_server/app/core/model_loader.py`

Public surface:

```python
@dataclass
class LoadedModel:
    name: Literal["classical", "onnx"]
    predict: Callable[[np.ndarray], tuple[int, float]]   # returns (label_id, confidence in [0,1])
    model_version: str

class ModelLoader:
    classical: LoadedModel
    onnx: LoadedModel
    label_map: LabelMap          # raw_id → raw_name; raw_name → routing_intent
    deployed_name: Literal["classical", "onnx"]   # from model_card

def load_all(artifacts_dir: Path) -> ModelLoader: ...
```

Responsibilities:
- Read both model cards. Verify each artifact's SHA-256 against its card. Refuse to start on mismatch (FR-006).
- Load `best_intent_classifier.joblib` (scikit-learn pipeline) and `intent_classifier_nn.onnx` (`onnxruntime.InferenceSession`).
- Read `label_map.json`; refuse to start if absent (FR-015).
- For each loaded model, compose a `predict(emb)` callable that returns `(label_id, confidence)` where `confidence ∈ [0, 1]`. For ONNX: run the session, softmax the logits, argmax + max. For classical: prefer `predict_proba` if the pipeline exposes it; otherwise softmax of `decision_function` (Open Gap below).
- Default `model_version` to the model card field if present; else first 12 hex chars of artifact SHA (FR-016).

### 1.2 `model_server/app/services/predict_service.py`

```python
async def predict_intent(text: str, loader: ModelLoader) -> PredictResponse:
    embedding = await _embed(text)                      # uses backend's embedding pipeline OR offline embedder
    label_id, confidence = await asyncio.to_thread(loader.deployed.predict, embedding)
    raw_name = loader.label_map.raw[str(label_id)]
    routing_intent = loader.label_map.routing[raw_name]
    return PredictResponse(label=routing_intent, confidence=confidence, model_version=loader.deployed.model_version)
```

**Open Gap**: Embedding the inbound text to a 1024-dim vector is required because both shipped artifacts consume embeddings, not raw text. This sidecar currently has no embedding pipeline. Options:
1. Embed inside `predict_service` using the same model the training pipeline used (e.g., `sentence-transformers` — **violates** Principle V if the model ships in the container).
2. Have the **caller** (RouterService in `backend/`) embed first and POST the embedding instead of text. This breaks the contract in SPEC.md §4 (`message: str`).
3. Embed via the project's hosted-API embedder (env `EMBEDDING_MODEL=text-embedding-3-small`) — adds latency and cost per request.
**Tracked as a Phase-2 follow-up below.** For this spec's deliverable, the `/predict-intent` endpoint loads and serves on the existing embedding-input contract; the wiring of text → embedding is documented as an explicit gap and the endpoint accepts raw embeddings for the eval harness's sake. The agreed direction with the user is option (3) — hosted embedder — but the wiring lands in a follow-up spec.

### 1.3 `model_server/app/main.py` lifespan

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    app.state.loader = load_all(Path(settings.ARTIFACTS_DIR))
    yield
```

`POST /predict-intent` reads `app.state.loader` via a `Depends()` accessor. `Depends(require_service_token)` already enforces auth (spec 018).

### 1.4 `evals/prepare_golden_set.py`

```python
SEED = 20260528
TARGET_SIZE = 80   # spec asks 50–100; 80 is the mid-range default

def main() -> None:
    X = np.load(TEST_DIR / "X_test_emb.npy")
    y = np.load(TEST_DIR / "y_test.npy")
    text = json.loads((TEST_DIR / "text_test.json").read_text()) if (TEST_DIR / "text_test.json").exists() else None
    # NB: NEVER read any *train* or *val* path. CI grep enforces.

    rng = np.random.default_rng(SEED)
    keep_idx = _stratified_indices(y, TARGET_SIZE, rng)

    rows = []
    for i in keep_idx:
        row = {"index": int(i), "label": int(y[i]), "embedding": X[i].astype(float).tolist()}
        if text is not None:
            row["text"] = text[i]
        rows.append(row)

    out = {"seed": SEED, "size": len(rows), "rows": rows}
    Path("evals/classifier/golden_set.json").write_text(json.dumps(out, indent=2, sort_keys=True))
    Path("evals/classifier/golden_set.sha256").write_text(_sha256_of_file("evals/classifier/golden_set.json"))
```

- `_stratified_indices` uses `sklearn.model_selection.train_test_split(stratify=y, test_size=TARGET_SIZE, random_state=SEED)` and returns the small-side indices.
- `json.dumps(..., sort_keys=True)` is the byte-determinism trick (SC-007).
- The script imports `from pathlib import Path` and refers to paths only via `TEST_DIR / "..._test_..."` — no `train`/`val` literals anywhere (SC-009).

### 1.5 `evals/classifier/run_3way_eval.py`

```python
def main() -> int:
    golden = load_golden_set()                                       # returns rows + sha
    y_true = np.array([r["label"] for r in golden.rows])

    # 1. Classical
    classical = joblib.load(ART / "best_intent_classifier.joblib")
    X = np.stack([r["embedding"] for r in golden.rows])
    y_pred_ml = classical.predict(X)

    # 2. ONNX
    sess = onnxruntime.InferenceSession(str(ART / "intent_classifier_nn.onnx"))
    logits = sess.run(None, {sess.get_inputs()[0].name: X.astype(np.float32)})[0]
    y_pred_nn = logits.argmax(axis=1)

    # 3. LLM zero-shot — needs raw text
    if all("text" in r for r in golden.rows):
        y_pred_llm = run_llm_zero_shot(golden.rows, label_names)
        f1_llm: float | None = f1_score(y_true, y_pred_llm, average="macro")
    else:
        f1_llm = None
        log.warning("No raw text in golden set; LLM baseline skipped.")

    scores = {"classical": f1_score(y_true, y_pred_ml, average="macro"),
              "onnx":      f1_score(y_true, y_pred_nn, average="macro"),
              "llm":       f1_llm}
    winner = max((k, v) for k, v in scores.items() if v is not None, key=lambda kv: kv[1])
    threshold = load_threshold("classifier.macro_f1_min")

    report = {"timestamp": datetime.utcnow().isoformat(),
              "golden_set_sha256": golden.sha,
              "scores": scores,
              "winner": winner[0],
              "winner_score": winner[1],
              "threshold": threshold,
              "passed": winner[1] >= threshold}
    Path("evals/classifier/last_report.json").write_text(json.dumps(report, indent=2))

    if not report["passed"]:
        print(f"::error::classifier macro-F1 {winner[1]:.4f} below threshold {threshold:.4f} "
              f"(winner: {winner[0]})", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except _ConfigError as exc:
        print(f"::error::{exc}", file=sys.stderr)
        sys.exit(2)
```

`run_llm_zero_shot` issues one API call per row, temperature 0, retries once on `429` only. Parsing is strict string-match against the label set; misparses count as incorrect (FR-019). Any uncaught LLM exception is logged and the row is recorded as misclassified — never crashes the script.

### 1.6 CI wiring

`.github/workflows/ci.yml` gains a job after `lint-and-test`:

```yaml
classifier-eval:
  runs-on: ubuntu-latest
  needs: lint-and-test
  env:
    LLM_PROVIDER: openai
    LLM_MODEL: gpt-4o-mini
    OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}
  steps:
    - uses: actions/checkout@v4
    - uses: actions/setup-python@v5
      with: { python-version: "3.11" }
    - run: pip install uv && cd evals && uv pip install --system -r requirements.txt
    - name: No train/val leakage in prep script (spec 007 SC-009)
      run: |
        if grep -E "\"X_(train|val)" evals/prepare_golden_set.py; then
          echo "Leakage: prepare_golden_set.py references train/val data" >&2; exit 1
        fi
    - name: Three-way classifier eval
      run: python evals/classifier/run_3way_eval.py
```

A missing `OPENAI_API_KEY` is non-fatal — the script logs `score=null` for the LLM lane and proceeds (User Story 5 Acceptance Scenario 4). Forks without the secret can still merge.

---

## Phase 2: Implementation Order

| # | Step | Output |
|---|---|---|
| 1 | Add `onnxruntime`, `PyYAML`, `openai` (or `anthropic`) to relevant pyproject files. | `uv lock` clean. |
| 2 | Commit `model_server/artifacts/label_map.json` (151-raw → 5-routing). User-supplied; spec 007 won't ship without it. | `label_map.json` validated by a startup load test. |
| 3 | Commit `model_server/artifacts/data/text_test.json`. | LLM baseline has raw text. |
| 4 | Implement `model_server/app/core/model_loader.py` + a single import-time hash test. | Both artifacts load; mismatch refuses startup. |
| 5 | Implement `model_server/app/services/predict_service.py` and wire `POST /predict-intent`. | Endpoint returns valid `PredictResponse` for a synthetic 1024-dim embedding. |
| 6 | Write `evals/prepare_golden_set.py` + leakage / determinism test. | `golden_set.json` + `golden_set.sha256` committed. |
| 7 | Write `evals/classifier/run_3way_eval.py`. | Local run prints three F1 numbers, exits 0. |
| 8 | Add the CI job. | CI gates merges on regression. |
| 9 | Tighten `evals/eval_thresholds.yaml` `classifier.macro_f1_min: 0.50 → 0.75`. **Separate commit.** | Live CI gate. |
| 10 | (Follow-up spec) Decide text-→-embedding strategy and wire it into `predict_service`. | `/predict-intent` accepts raw text per SPEC.md §4. |

---

## Complexity Tracking

No constitution violations. The one design tension worth recording: the text-→-embedding wiring is deliberately deferred (see §1.2 "Open Gap"). The endpoint cannot fulfill SPEC.md §4 literally until that lands. The eval gate and the model-loader work do not depend on it, which is why they are landed first.

| Deferred concern | Why deferred | Trigger |
|---|---|---|
| Text-→-embedding inside `/predict-intent` | Three viable options, none cheap; needs explicit user decision (in-container ML vs. hosted-API embedder vs. caller-side embedding) | Once RouterService is ready to call this endpoint with real visitor messages. |
| Tighten `classifier.macro_f1_min` to 0.75 | Day-1 placeholder of 0.50 is in place; tightening should follow the first green eval run | First passing run of `run_3way_eval.py`. |

---

## Open Gaps (Phase 2+)

| Gap | Owner | Trigger |
|---|---|---|
| Text-→-embedding pipeline (option 3 above) | Person C | RouterService integration. |
| `model_version` field absent from current model cards | Person C | Caught at startup; for now fills with truncated SHA per FR-016. Author should add an explicit `model_version` to each card on the next training pass. |
| Lead-score endpoint (`POST /predict-lead-score`) | Person C | User Story 6 (priority P2). Not required by this spec's MVP cut. |
| `classifier.macro_f1_min` tightening to 0.75 | Person C | First passing green run. |
| Per-tenant model serving | Person A + Person C | Beyond Week 8 scope. |
