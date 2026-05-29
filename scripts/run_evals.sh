#!/usr/bin/env bash
# run_evals.sh — dispatcher for CI eval gates.
#
# Usage: bash scripts/run_evals.sh [classifier|rag|agent|security|redaction|all]
#
# Exit codes:
#   0 — gate passed (or eval script not yet implemented — graceful TODO)
#   1 — gate failed OR required file (thresholds, golden set) is missing
#
# CI assumptions (smoke-and-evals job):
#   - docker compose stack is up (postgres, redis, api, model_server, guardrails_sidecar)
#   - DATABASE_URL points to localhost:5432 (port-mapped from postgres container)
#   - COHERE_API_KEY set as a GitHub Actions secret (for the RAG eval)
#
# Person A owns this dispatcher and exit-code semantics.
# Person C owns evaluate_classifier.py, evaluate_agent_tools.py, evaluate_red_team.py,
#   and evaluate_redaction.py. Person B owns evaluate_rag.py.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
THRESHOLDS="$REPO_ROOT/evals/eval_thresholds.yaml"
SUITE="${1:-all}"

# ── Validate eval_thresholds.yaml exists (FR-009, FR-013) ────────────────────
if [ ! -f "$THRESHOLDS" ]; then
  echo "ERROR: eval_thresholds.yaml not found at $THRESHOLDS" >&2
  exit 1
fi

# ── run_suite: check files, then dispatch to the right invocation ─────────────

run_suite() {
  local suite="$1"
  local golden=""
  local script=""

  case "$suite" in
    classifier)
      golden="$REPO_ROOT/evals/classifier/test_set.csv"
      script="$REPO_ROOT/evals/classifier/evaluate_classifier.py"
      ;;
    rag)
      golden="$REPO_ROOT/evals/rag/golden_set.yaml"
      # evaluate_rag.py imports app.* — needs PYTHONPATH=backend/.
      # DATABASE_URL and COHERE_API_KEY must be set in the environment.
      # Person B owns the threshold-comparison logic in this script.
      script="$REPO_ROOT/evals/rag/evaluate_rag.py"
      ;;
    agent)
      golden="$REPO_ROOT/evals/agent/tool_selection_golden.yaml"
      script="$REPO_ROOT/evals/agent/evaluate_agent_tools.py"
      ;;
    security)
      golden="$REPO_ROOT/evals/security/red_team_prompts.yaml"
      script="$REPO_ROOT/evals/security/evaluate_red_team.py"
      ;;
    redaction)
      # Redaction gate runs as pytest against backend/tests/test_redaction.py.
      # No separate golden set — the test file IS the gate.
      golden=""
      script="$REPO_ROOT/backend/tests/test_redaction.py"
      ;;
    *)
      echo "ERROR: Unknown suite '$suite'. Valid: classifier|rag|agent|security|redaction|all" >&2
      return 1
      ;;
  esac

  # ── Check golden set exists (exit 1 if missing — FR-013) ──────────────────
  if [ -n "$golden" ] && [ ! -f "$golden" ]; then
    echo "ERROR: golden set not found for '$suite': $golden" >&2
    return 1
  fi

  # ── Check eval script exists — graceful exit 0 if not yet implemented ─────
  if [ ! -f "$script" ]; then
    echo "INFO: eval script not yet implemented for '$suite' — skipping (exit 0)"
    echo "      expected: $script"
    return 0
  fi

  # ── Run the eval ───────────────────────────────────────────────────────────
  echo "==> Running $suite eval..."
  cd "$REPO_ROOT"

  case "$suite" in
    rag)
      # Must run with backend/ on PYTHONPATH so app.* imports resolve.
      PYTHONPATH="$REPO_ROOT/backend" uv run python "$script"
      ;;
    redaction)
      # Runs as pytest — the test itself checks the full redaction pipeline.
      uv run pytest "$script" -x --tb=short
      ;;
    *)
      uv run python "$script"
      ;;
  esac
}

# ── Dispatch ──────────────────────────────────────────────────────────────────

if [ "$SUITE" = "all" ]; then
  FAILED=0
  for s in classifier rag agent security redaction; do
    echo ""
    echo "── Suite: $s ──────────────────────────────────"
    run_suite "$s" || FAILED=1
  done
  echo ""
  if [ "$FAILED" -ne 0 ]; then
    echo "ERROR: one or more eval suites failed." >&2
    exit 1
  fi
  echo "All eval suites passed."
else
  run_suite "$SUITE"
fi
