# Research: 016-Evals & CI — Person A Scope

## Decision 1: `uv` in GitHub Actions

**Decision**: Install `uv` via the official `astral-sh/setup-uv@v5` action, then run `uv sync --group dev` inside the `backend/` directory.

**Rationale**: The current `ci.yml` uses `pip install uv` + `uv pip install --system -e ".[dev]"`. This is the legacy pattern. The official `astral-sh/setup-uv` action caches the uv binary and the pip cache, cutting install time by ~50 %. `uv sync` respects the lock file (`uv.lock`) and the `[dependency-groups]` declared in `pyproject.toml`, which is what the project already uses for dependency management.

**Alternatives considered**: `pip install uv` (current, functional but slow and not cache-aware), `pipx install uv` (no caching benefit), `actions/setup-python` + pip (no uv benefit at all).

---

## Decision 2: Pytest must not use `|| true`

**Decision**: Remove `|| true` from the pytest step in ci.yml. Tests MUST be allowed to fail CI.

**Rationale**: FR-003 states "any step failure MUST block the merge". The current `|| true` masks test failures — a broken test suite produces a green CI badge. This is a Category-1 violation per the Constitution (Principle VI). There is no reason to gate-keep behind `|| true` once the test suite is real.

**Alternatives considered**: Keeping `|| true` with a `# TODO` comment — rejected because it directly contradicts FR-003 and produces silent regressions.

---

## Decision 3: eval-gates job wiring

**Decision**: Uncomment and wire the `eval-gates` CI job. Each gate calls `uv run python evals/<suite>/evaluate_<suite>.py` directly (or `bash scripts/run_evals.sh <suite>`). The job depends on `smoke-test` so the stack is already up.

**Rationale**: Person C's eval scripts need a landing zone in CI. Wiring the job structure with proper `needs:` ordering and per-gate steps means Person C only has to write the scripts — no CI edits needed. Each step is a separate named job step so failures are individually identifiable in the GitHub Actions UI.

**Alternatives considered**: One monolithic eval step calling `run_evals.sh all` — rejected because it hides which gate failed. Per-step granularity is required by FR-002 ("CI runs in order") and gives cleaner failure attribution.

---

## Decision 4: `scripts/run_evals.sh` structure

**Decision**: `run_evals.sh` dispatches to individual Python eval scripts. It reads `evals/eval_thresholds.yaml` and passes the threshold file path as an argument. It exits non-zero if any eval script exits non-zero.

**Rationale**: FR-009 mandates `eval_thresholds.yaml` as the single source of truth. FR-013 mandates a clear non-zero exit on missing golden sets or threshold files. Passing the threshold file path explicitly (rather than having each script discover it independently) ensures there is one canonical path: the shell script is the single place that knows the path.

**Alternatives considered**: Hardcoding thresholds inside eval scripts (violates FR-009), a Python runner (more complex, no benefit over a simple shell script given the scripts are Python anyway).

---

## Decision 5: smoke-test scope

**Decision**: The smoke test already tests `GET /health` and `GET /ready`. Add `GET http://localhost:8001/health` (model_server) and `GET http://localhost:8002/health` (guardrails_sidecar) to complete FR-010.

**Rationale**: FR-010 says "all health endpoints return 200". The current smoke-test only checks the main API. Model-server and guardrails-sidecar each expose `/health` and must be included for the gate to be meaningful.

**Alternatives considered**: Adding the extra checks to `scripts/smoke_test.sh` only — but the CI smoke-test step inline-checks these, so both places need updating for consistency.
