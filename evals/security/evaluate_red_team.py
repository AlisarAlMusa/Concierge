"""Red-team eval gate for the guardrails sidecar's input rail.

Loads `red_team_prompts.yaml`, runs each probe through the sidecar's
`RailsEngine.evaluate_input` IN-PROCESS (no network, no live sidecar),
computes recall + false-positive rate, writes a structured report, and
exits 1 if either gate fails.

Why in-process: the YAML probes test the **rail's logic** (corpus + threshold
+ embedder), not the HTTP transport. Transport correctness is covered by
`guardrails_sidecar/tests/test_routes.py` (spec 010). Decoupling them lets
CI run this gate without booting the sidecar container.

Why not exit-1 on recall < 1.0: no semantic guardrail is perfect. The
honest mature framing is "publish the measured recall + FPR, gate on
threshold values that humans would actually agree to ship at." Spec 016
threshold values live in `evals/eval_thresholds.yaml::security:`.

Spec 010 + spec 016. Companion to:
- evals/classifier/run_3way_eval.py  (classifier CI gate)
- evals/security/red_team_prompts.yaml  (this script's input)
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import yaml

# Make the sidecar importable WITHOUT installing it as a package.
# Same pattern as backend/tests/integration/conftest.py — the sidecar lives
# at the repo root with its own `app/` package; we put it on sys.path so
# `from app.X import ...` resolves to the sidecar's app/.
REPO_ROOT = Path(__file__).resolve().parents[2]
SIDECAR_ROOT = REPO_ROOT / "guardrails_sidecar"
sys.path.insert(0, str(SIDECAR_ROOT))

PROBES_PATH = Path(__file__).parent / "red_team_prompts.yaml"
REPORT_PATH = Path(__file__).parent / "last_security_report.json"
THRESHOLDS_PATH = REPO_ROOT / "evals" / "eval_thresholds.yaml"

logger = logging.getLogger(__name__)


class _ConfigError(RuntimeError):
    """Missing file, malformed YAML, etc. — exit code 2."""


def _load_probes(path: Path) -> list[dict]:
    if not path.exists():
        raise _ConfigError(f"probe file not found: {path}")
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or "probes" not in raw:
        raise _ConfigError(f"{path.name} must have a top-level `probes:` list")
    probes = raw["probes"]
    for i, p in enumerate(probes):
        if not isinstance(p, dict):
            raise _ConfigError(f"{path.name} probe[{i}] is not a mapping")
        for k in ("text", "expect", "category"):
            if k not in p:
                raise _ConfigError(f"{path.name} probe[{i}] missing {k!r}")
        if p["expect"] not in {"block", "allow"}:
            raise _ConfigError(
                f"{path.name} probe[{i}].expect must be 'block' or 'allow'"
            )
    return probes


def _load_thresholds(path: Path) -> tuple[float, float]:
    if not path.exists():
        raise _ConfigError(f"thresholds file not found: {path}")
    cfg = yaml.safe_load(path.read_text(encoding="utf-8"))
    try:
        sec = cfg["security"]
        recall_min = float(sec["red_team_recall_min"])
        fpr_max = float(sec["red_team_fpr_max"])
    except (KeyError, TypeError) as exc:
        raise _ConfigError(
            "eval_thresholds.yaml must contain "
            "security.red_team_recall_min and security.red_team_fpr_max"
        ) from exc
    return recall_min, fpr_max


def _build_engine():
    """Construct the same RailsEngine the sidecar boots with.

    Imported here (inside the function) so the sys.path manipulation above
    is already in effect.
    """
    from app.actions import set_embedder
    from app.core.rails_engine import RailsEngine
    from app.core.topic_similarity import build_embedder

    embedder = build_embedder(SIDECAR_ROOT / "models")
    set_embedder(embedder)
    return RailsEngine.build(embedder)


def _evaluate(engine, probes: list[dict]) -> dict:
    """Run every probe and return a structured result block.

    No tenant blocked-topics — we are testing the PLATFORM rail only.
    Tenant-topic blocking is covered by the sidecar's own unit tests.
    """
    confusion = {"tp": 0, "fp": 0, "tn": 0, "fn": 0}
    per_cat: dict[str, dict[str, int]] = defaultdict(
        lambda: {"expected_block": 0, "expected_allow": 0, "correct": 0}
    )
    per_probe: list[dict] = []
    latencies_ms: list[float] = []

    for probe in probes:
        text = probe["text"]
        expected = probe["expect"]
        category = probe["category"]

        t0 = time.perf_counter()
        verdict = engine.evaluate_input(message=text, blocked_topics=[])
        latency = (time.perf_counter() - t0) * 1000.0
        latencies_ms.append(latency)

        actual = "block" if not verdict.allowed else "allow"
        correct = actual == expected

        if expected == "block":
            per_cat[category]["expected_block"] += 1
            if correct:
                confusion["tp"] += 1
                per_cat[category]["correct"] += 1
            else:
                confusion["fn"] += 1
        else:
            per_cat[category]["expected_allow"] += 1
            if correct:
                confusion["tn"] += 1
                per_cat[category]["correct"] += 1
            else:
                confusion["fp"] += 1

        per_probe.append(
            {
                "text": text,
                "category": category,
                "expected": expected,
                "actual": actual,
                "similarity": round(verdict.similarity, 4),
                "reason": verdict.reason,
                "correct": correct,
                "latency_ms": round(latency, 3),
            }
        )

    expected_blocks = confusion["tp"] + confusion["fn"]
    expected_allows = confusion["tn"] + confusion["fp"]
    recall = confusion["tp"] / expected_blocks if expected_blocks else 0.0
    fpr = confusion["fp"] / expected_allows if expected_allows else 0.0

    per_cat_summary = {}
    for cat, counts in per_cat.items():
        total = counts["expected_block"] + counts["expected_allow"]
        per_cat_summary[cat] = {
            **counts,
            "accuracy": round(counts["correct"] / total, 4) if total else 0.0,
        }

    latencies_ms.sort()
    p50 = latencies_ms[len(latencies_ms) // 2] if latencies_ms else 0.0
    p95 = latencies_ms[int(len(latencies_ms) * 0.95)] if latencies_ms else 0.0

    return {
        "confusion": confusion,
        "recall": round(recall, 4),
        "false_positive_rate": round(fpr, 4),
        "total_probes": len(probes),
        "per_category": per_cat_summary,
        "latency_ms": {"p50": round(p50, 3), "p95": round(p95, 3)},
        "per_probe": per_probe,
    }


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--probes",
        type=Path,
        default=PROBES_PATH,
        help=f"probe YAML (default {PROBES_PATH})",
    )
    parser.add_argument(
        "--no-gate",
        action="store_true",
        help="report numbers but exit 0 regardless (for local exploration)",
    )
    args = parser.parse_args(argv)

    try:
        probes = _load_probes(args.probes)
        recall_min, fpr_max = _load_thresholds(THRESHOLDS_PATH)
        engine = _build_engine()
    except _ConfigError as exc:
        print(f"::error::{exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001
        print(f"::error::failed to build rails engine: {exc}", file=sys.stderr)
        return 2

    result = _evaluate(engine, probes)
    passed_recall = result["recall"] >= recall_min
    passed_fpr = result["false_positive_rate"] <= fpr_max
    passed = passed_recall and passed_fpr

    report = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "thresholds": {
            "red_team_recall_min": recall_min,
            "red_team_fpr_max": fpr_max,
        },
        "result": result,
        "passed_recall": passed_recall,
        "passed_fpr": passed_fpr,
        "passed": passed,
    }
    REPORT_PATH.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")

    print(
        f"RESULT recall={result['recall']:.4f} "
        f"(>={recall_min:.4f} {'OK' if passed_recall else 'FAIL'}) "
        f"fpr={result['false_positive_rate']:.4f} "
        f"(<={fpr_max:.4f} {'OK' if passed_fpr else 'FAIL'}) "
        f"latency_p50={result['latency_ms']['p50']:.1f}ms "
        f"latency_p95={result['latency_ms']['p95']:.1f}ms "
        f"passed={passed}"
    )
    print(f"report: {REPORT_PATH}")

    if not passed:
        if not passed_recall:
            print(
                f"::error::red-team recall {result['recall']:.4f} below "
                f"threshold {recall_min:.4f} — guardrail catches too few attacks",
                file=sys.stderr,
            )
        if not passed_fpr:
            print(
                f"::error::red-team FPR {result['false_positive_rate']:.4f} above "
                f"threshold {fpr_max:.4f} — guardrail blocks too many benign messages",
                file=sys.stderr,
            )
        return 0 if args.no_gate else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
