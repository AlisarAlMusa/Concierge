"""Three-way classifier evaluation gate (spec 007 US5).

Loads the golden set built by `evals/prepare_golden_set.py`, runs three
approaches over it, computes macro-F1 in the routing-intent space, picks a
winner, and exits 1 if the winner falls below `classifier.macro_f1_min` in
`evals/eval_thresholds.yaml`. The three lanes:

  1. Classical KNN — `model_server/artifacts/ml/best_intent_classifier.joblib`
  2. ONNX FFN     — `model_server/artifacts/nn/intent_classifier_nn.onnx`
  3. LLM zero-shot — Groq `llama-3.1-8b-instant` (or whatever `LLM_MODEL_EVAL`
     points at), one prompt per row, deterministic temperature 0, with a
     disk-backed response cache keyed by SHA-256(text + model + prompt_version).

Free-tier mitigations baked in:
  • B — `llama-3.1-8b-instant` defaults give ~10× the throughput / token budget
    of the production `llama-3.1-70b-versatile`. Zero-shot intent classification
    on short messages does not need 70b reasoning.
  • A — On-disk JSON cache at `evals/classifier/.cache/llm_zero_shot.json`.
    Once a (text, model, prompt_version) triple is scored, re-runs cost zero
    API calls until any of those change. Cache invalidates automatically when
    the prompt template version is bumped.

If the LLM key is unset or the lane raises in a way that empties the cache,
the script logs `score=null` and proceeds — exit code is still driven by the
winner among the available lanes (User Story 5 Acceptance Scenario 4).

Exit codes:
  0 — pass (winner ≥ threshold)
  1 — regression (winner < threshold)
  2 — configuration error (missing artifacts, missing thresholds, etc.)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import sys
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import joblib
import numpy as np
import onnxruntime
import yaml
from dotenv import load_dotenv
from sklearn.metrics import f1_score

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]
ART_DIR = REPO_ROOT / "model_server" / "artifacts"
EVAL_DIR = REPO_ROOT / "evals" / "classifier"
GOLDEN_PATH = EVAL_DIR / "golden_set.json"
GOLDEN_SHA_PATH = EVAL_DIR / "golden_set.sha256"
THRESHOLDS_PATH = REPO_ROOT / "evals" / "eval_thresholds.yaml"
REPORT_PATH = EVAL_DIR / "last_report.json"
LABEL_MAP_PATH = ART_DIR / "label_map.json"

CACHE_DIR = EVAL_DIR / ".cache"
CACHE_PATH = CACHE_DIR / "llm_zero_shot.json"

PROMPT_VERSION = "v1"
DEFAULT_LLM_MODEL = "llama-3.1-8b-instant"


class _ConfigError(RuntimeError):
    """Raised when the eval cannot run because of missing inputs / artifacts."""


# ── golden-set loading ────────────────────────────────────────────────────────


@dataclass
class GoldenSet:
    rows: list[dict]
    sha: str
    has_text: bool


def _sha256_of_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def load_golden_set() -> GoldenSet:
    if not GOLDEN_PATH.exists():
        raise _ConfigError(
            f"{GOLDEN_PATH} not found. Run `python evals/prepare_golden_set.py`."
        )
    payload = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
    rows = payload["rows"]
    actual_sha = _sha256_of_file(GOLDEN_PATH)
    if GOLDEN_SHA_PATH.exists():
        expected_sha = GOLDEN_SHA_PATH.read_text().strip()
        if expected_sha != actual_sha:
            raise _ConfigError(
                f"golden_set.json sha256 {actual_sha[:12]}… does not match "
                f"golden_set.sha256 {expected_sha[:12]}…"
            )
    has_text = all("text" in r for r in rows)
    return GoldenSet(rows=rows, sha=actual_sha, has_text=has_text)


# ── label map / routing collapse ─────────────────────────────────────────────


@dataclass(frozen=True)
class LabelMap:
    routing: dict[str, str]
    routing_intents: list[str]


def load_label_map() -> LabelMap:
    if not LABEL_MAP_PATH.exists():
        raise _ConfigError(f"{LABEL_MAP_PATH} not found")
    raw = json.loads(LABEL_MAP_PATH.read_text())
    return LabelMap(
        routing=dict(raw["routing"]),
        routing_intents=list(raw["routing_intents"]),
    )


def to_routing(data_label: str, lm: LabelMap) -> str:
    return lm.routing.get(data_label, "ambiguous")


# ── classical KNN lane ───────────────────────────────────────────────────────


def predict_classical(X: np.ndarray) -> np.ndarray:
    clf = joblib.load(ART_DIR / "ml" / "best_intent_classifier.joblib")
    return clf.predict(X)  # array of strings (data-space)


# ── ONNX FFN lane ────────────────────────────────────────────────────────────


def predict_onnx(X: np.ndarray, data_classes: list[str]) -> np.ndarray:
    sess = onnxruntime.InferenceSession(
        str(ART_DIR / "nn" / "intent_classifier_nn.onnx"),
        providers=["CPUExecutionProvider"],
    )
    input_name = sess.get_inputs()[0].name
    logits = sess.run(None, {input_name: X.astype(np.float32)})[0]
    idx = logits.argmax(axis=1)
    return np.array([data_classes[i] for i in idx])


# ── LLM zero-shot lane (Groq llama-3.1-8b-instant by default) ────────────────


def _build_prompt(text: str, lm: LabelMap) -> tuple[str, str]:
    """Returns `(system, user)` messages for the chat completion.

    `prompt_version` is incorporated into the cache key — bumping it
    invalidates every cached row on the next run.
    """
    intents_csv = ", ".join(lm.routing_intents)
    system = (
        "You are a strict intent classifier for a customer-service chatbot. "
        f"Reply with EXACTLY ONE label from this list and nothing else: {intents_csv}. "
        "Do not explain. Do not add punctuation. Just the label."
    )
    user = f"Message: {text}\nLabel:"
    return system, user


def _cache_key(text: str, model: str) -> str:
    digest = hashlib.sha256()
    digest.update(text.encode("utf-8"))
    digest.update(b"::")
    digest.update(model.encode("utf-8"))
    digest.update(b"::")
    digest.update(PROMPT_VERSION.encode("utf-8"))
    return digest.hexdigest()


def _load_cache() -> dict:
    if not CACHE_PATH.exists():
        return {"cache_version": "1", "entries": {}}
    return json.loads(CACHE_PATH.read_text(encoding="utf-8"))


def _save_cache(cache: dict) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps(cache, indent=2, sort_keys=True) + "\n")


def _parse_llm_reply(raw: str, lm: LabelMap) -> str:
    """Strict parse: accept only an exact match in the routing-intent set,
    case-insensitive, trimmed. Anything else → 'ambiguous' (counts as a
    misclassification unless the true label happens to be ambiguous).
    """
    candidate = raw.strip().split("\n", 1)[0].strip().lower().rstrip(".")
    for intent in lm.routing_intents:
        if intent.lower() == candidate:
            return intent
    return "ambiguous"


def predict_llm_zero_shot(
    rows: list[dict],
    lm: LabelMap,
    model: str,
    api_key: str,
    *,
    max_rps: float = 25.0,
) -> tuple[list[str], int, int]:
    """Returns `(predictions, cache_hits, api_calls)`.

    One call per row, temperature 0, one retry on 429. Cache hits are free.
    Failures count as 'ambiguous' (strict-parse contract).
    """
    from groq import APIError, APIStatusError, Groq  # local import — only when lane runs

    client = Groq(api_key=api_key)
    cache = _load_cache()
    entries = cache.setdefault("entries", {})
    cache["model_default"] = model
    cache["prompt_version"] = PROMPT_VERSION

    preds: list[str] = []
    cache_hits = 0
    api_calls = 0
    sleep_per_call = 1.0 / max_rps

    for row in rows:
        text = row["text"]
        key = _cache_key(text, model)
        entry = entries.get(key)
        if entry is not None:
            preds.append(entry["parsed_label"])
            cache_hits += 1
            continue

        system, user = _build_prompt(text, lm)
        attempt = 0
        raw_response: str = ""
        while attempt < 2:
            try:
                completion = client.chat.completions.create(
                    model=model,
                    temperature=0.0,
                    max_tokens=8,
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                )
                raw_response = completion.choices[0].message.content or ""
                break
            except APIStatusError as exc:
                if exc.status_code == 429 and attempt == 0:
                    logger.warning("Groq 429 — backing off 2s and retrying once")
                    time.sleep(2.0)
                    attempt += 1
                    continue
                raw_response = ""  # strict: count as misclassification
                break
            except APIError as exc:
                logger.warning("Groq API error on row %s: %s", row["index"], exc)
                raw_response = ""
                break

        api_calls += 1
        parsed = _parse_llm_reply(raw_response, lm)
        entries[key] = {
            "raw_response": raw_response,
            "parsed_label": parsed,
            "model": model,
            "prompt_version": PROMPT_VERSION,
            "ts": datetime.now(timezone.utc).isoformat(),
        }
        preds.append(parsed)
        time.sleep(sleep_per_call)  # throttle to keep under free-tier RPM

    _save_cache(cache)
    return preds, cache_hits, api_calls


# ── threshold + report ───────────────────────────────────────────────────────


def load_classifier_threshold() -> float:
    if not THRESHOLDS_PATH.exists():
        raise _ConfigError(f"{THRESHOLDS_PATH} not found")
    cfg = yaml.safe_load(THRESHOLDS_PATH.read_text())
    try:
        return float(cfg["classifier"]["macro_f1_min"])
    except (KeyError, TypeError) as exc:
        raise _ConfigError(
            "eval_thresholds.yaml must contain `classifier.macro_f1_min`"
        ) from exc


def _macro_f1(y_true: list[str], y_pred: list[str], labels: Iterable[str]) -> float:
    return float(
        f1_score(
            y_true,
            y_pred,
            labels=list(labels),
            average="macro",
            zero_division=0,
        )
    )


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    load_dotenv(REPO_ROOT / ".env")  # so local runs pick up GROQ_API_KEY

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--llm-model",
        default=os.environ.get("LLM_MODEL_EVAL", DEFAULT_LLM_MODEL),
        help="Groq model id for the zero-shot lane (default %(default)s)",
    )
    parser.add_argument(
        "--no-llm",
        action="store_true",
        help="Skip the LLM zero-shot lane regardless of key availability",
    )
    args = parser.parse_args(argv)

    try:
        golden = load_golden_set()
        lm = load_label_map()
        threshold = load_classifier_threshold()
    except _ConfigError as exc:
        print(f"::error::{exc}", file=sys.stderr)
        return 2

    X = np.array([r["embedding"] for r in golden.rows], dtype=np.float64)
    y_true_data = [str(r["label"]) for r in golden.rows]
    y_true = [to_routing(lbl, lm) for lbl in y_true_data]

    # Macro-F1 is averaged over the routing intents that actually appear in
    # y_true. Including `human_request` / `ambiguous` (which the training data
    # has no examples of) would let those zero-support classes drag the
    # average down and gate against a metric the classifier was never trained
    # for.
    scoring_labels = sorted(set(y_true))

    # ── classical
    classical_data = predict_classical(X)
    classical_routing = [to_routing(p, lm) for p in classical_data]
    f1_classical = _macro_f1(y_true, classical_routing, scoring_labels)

    # ── onnx
    data_classes = sorted(set(y_true_data))  # alphabetical, matches training
    onnx_data = predict_onnx(X, data_classes)
    onnx_routing = [to_routing(p, lm) for p in onnx_data]
    f1_onnx = _macro_f1(y_true, onnx_routing, scoring_labels)

    # ── llm
    f1_llm: float | None = None
    llm_meta: dict = {}
    skip_reason: str | None = None
    if args.no_llm:
        skip_reason = "--no-llm flag"
    elif not golden.has_text:
        skip_reason = "golden_set.json has no `text` field; commit aligned text"
    elif not os.environ.get("GROQ_API_KEY"):
        skip_reason = "GROQ_API_KEY not set"

    if skip_reason is None:
        try:
            llm_preds, hits, calls = predict_llm_zero_shot(
                golden.rows,
                lm,
                args.llm_model,
                os.environ["GROQ_API_KEY"],
            )
            f1_llm = _macro_f1(y_true, llm_preds, scoring_labels)
            llm_meta = {
                "model": args.llm_model,
                "prompt_version": PROMPT_VERSION,
                "cache_hits": hits,
                "api_calls": calls,
                "pred_distribution": dict(Counter(llm_preds)),
            }
        except Exception as exc:  # noqa: BLE001
            logger.exception("LLM lane crashed; recording score=null")
            llm_meta = {"error": str(exc)}
    else:
        llm_meta = {"skipped": skip_reason}
        logger.warning("LLM lane skipped — %s", skip_reason)

    scores: dict[str, float | None] = {
        "classical": f1_classical,
        "onnx": f1_onnx,
        "llm": f1_llm,
    }

    available = {k: v for k, v in scores.items() if v is not None}
    if not available:
        print("::error::all lanes failed; cannot pick a winner", file=sys.stderr)
        return 2

    winner_name, winner_score = max(available.items(), key=lambda kv: kv[1])
    passed = winner_score >= threshold

    report = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "golden_set": {
            "sha256": golden.sha,
            "size": len(golden.rows),
            "label_distribution": dict(Counter(y_true_data)),
        },
        "scores": scores,
        "winner": winner_name,
        "winner_score": round(winner_score, 6),
        "threshold": threshold,
        "passed": passed,
        "llm_meta": llm_meta,
    }
    REPORT_PATH.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")

    print(
        f"RESULT classical={f1_classical:.4f} "
        f"onnx={f1_onnx:.4f} "
        f"llm={'null' if f1_llm is None else f'{f1_llm:.4f}'} "
        f"winner={winner_name} threshold={threshold:.4f} passed={passed}"
    )
    print(f"report: {REPORT_PATH}")

    if not passed:
        print(
            f"::error::classifier macro-F1 {winner_score:.4f} below threshold "
            f"{threshold:.4f} (winner: {winner_name})",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
