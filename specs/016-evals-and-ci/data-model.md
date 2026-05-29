# Data Model: 016-Evals & CI — Person A Scope

This feature is CI/CD infrastructure — there are no new database tables or ORM models.

## Configuration Entity: eval_thresholds.yaml

**File**: `evals/eval_thresholds.yaml`
**Purpose**: Single source of truth for all eval pass/fail thresholds. Read by every eval script. Committed in source control. Never overridden at runtime.

```yaml
classifier:
  macro_f1_min: float   # minimum acceptable macro-F1 (0–1)

rag:
  hit_at_5_min: float   # minimum hit@5 recall (0–1)
  faithfulness_min: float  # minimum LLM faithfulness score (0–1)

agent:
  tool_selection_accuracy_min: float  # minimum tool-selection accuracy (0–1)

security:
  red_team_pass_rate: float   # must be 1.0, never lower
  redaction_pass_rate: float  # must be 1.0, never lower
```

**Invariants**:
- `security.red_team_pass_rate` MUST equal 1.0 — it is never lowered.
- `security.redaction_pass_rate` MUST equal 1.0 — it is never lowered.
- All other values start as lower placeholders and are tightened as real numbers land from Person C.

## File Artifacts

| Artifact | Owner | Status |
|---|---|---|
| `.github/workflows/ci.yml` | Person A | Exists — needs `|| true` fix, eval-gates wiring, sidecar health checks |
| `evals/eval_thresholds.yaml` | Person A | Exists — structure correct, placeholder values |
| `scripts/run_evals.sh` | Person A (scaffold), Person C (logic) | Exists as stub — needs proper dispatch + exit-code handling |
| `scripts/smoke_test.sh` | Person A | Exists — needs sidecar health checks added |
| `evals/classifier/test_set.csv` | Person C | Not yet created |
| `evals/rag/golden_set.yaml` | Person C | Exists |
| `evals/agent/tool_selection_golden.yaml` | Person C | Exists |
| `evals/security/red_team_prompts.yaml` | Person C | Not yet created |

## CI Job Dependency Graph

```
push / PR
  │
  ▼
lint-and-test
  │  ├── ruff check
  │  ├── black --check
  │  └── pytest (must fail on error — no || true)
  │
  ▼
smoke-test
  │  ├── docker compose up -d --build
  │  ├── GET /health (api)        → 200
  │  ├── GET /ready (api)         → 200
  │  ├── GET /health (model_server)      → 200
  │  └── GET /health (guardrails_sidecar) → 200
  │
  ▼
eval-gates
  │  ├── classifier eval  → macro_f1 ≥ threshold
  │  ├── RAG eval         → hit@5, faithfulness ≥ thresholds
  │  ├── agent eval       → tool_selection_accuracy ≥ threshold
  │  ├── red-team eval    → pass_rate = 1.0
  │  └── redaction eval   → pass_rate = 1.0
  │
  ▼
merge allowed
```
