"""Build the held-out evaluation "golden set" — spec 007 US4 / FR-017.

Stratified sample from the **test split only** (`X_test_emb.npy`,
`y_test.npy`, `text_test.json`). Refuses to touch any path containing the
substrings ``train`` or ``val`` — enforced at the source level so a CI grep
can verify (SC-009).

Output: `evals/classifier/golden_set.json` is JSON with sorted keys and a
fixed seed, so two clones of the repo produce byte-identical files. A
companion `golden_set.sha256` records the file's digest at commit time.

Run with `uv run --project evals python evals/prepare_golden_set.py`.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import sys
from pathlib import Path

import numpy as np
from sklearn.model_selection import train_test_split

logger = logging.getLogger(__name__)

# Repo-root-relative paths. NB: any constant referencing the source data MUST
# only point at the test split — see the source-level invariant tested in
# evals/tests/test_prepare_golden_set.py::test_no_train_or_val_references.
REPO_ROOT = Path(__file__).resolve().parents[1]
TEST_EMBEDDINGS = REPO_ROOT / "model_server" / "artifacts" / "data" / "embeddings" / "X_test_emb.npy"
TEST_LABELS = REPO_ROOT / "model_server" / "artifacts" / "data" / "raw" / "y_test.npy"
# test.csv carries both `text` and `intent` columns, positionally aligned
# with y_test.npy. The alignment is verified at load time — if any row's
# intent disagrees with y_test, the script aborts (data-split lineage
# regression).
TEST_CSV = REPO_ROOT / "model_server" / "artifacts" / "data" / "raw" / "test.csv"

OUTPUT_PATH = REPO_ROOT / "evals" / "classifier" / "golden_set.json"
OUTPUT_SHA_PATH = REPO_ROOT / "evals" / "classifier" / "golden_set.sha256"

SEED = 20260528
TARGET_SIZE = 80


def _read_test_csv(csv_path: Path) -> tuple[list[str], list[str]] | None:
    """Read `(text, intent)` columns from test.csv as positional lists.

    Returns None if the file is absent — `run_3way_eval.py`'s LLM lane will
    then score `null` (User Story 5 Acceptance Scenario 4).
    """
    if not csv_path.exists():
        return None
    import csv as _csv

    with csv_path.open(encoding="utf-8") as fh:
        reader = _csv.DictReader(fh)
        fields = reader.fieldnames or []
        if "text" not in fields or "intent" not in fields:
            raise RuntimeError(
                f"{csv_path.name} must have `text` and `intent` columns; got {fields}"
            )
        rows = list(reader)
    return [r["text"] for r in rows], [r["intent"] for r in rows]


def _stratified_indices(y: np.ndarray, target_size: int, seed: int) -> np.ndarray:
    """Return `target_size` indices into y, stratified by label."""
    if target_size >= len(y):
        return np.arange(len(y))
    # train_test_split is happy to split into 80-of-1200 stratified; the
    # `train_size` here is the *kept* small side. (sklearn renamed `test_size`
    # to control the second array.)
    _, keep_idx = train_test_split(
        np.arange(len(y)),
        train_size=len(y) - target_size,
        test_size=target_size,
        random_state=seed,
        stratify=y,
        shuffle=True,
    )
    return np.sort(keep_idx)


def _sha256_of_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--size",
        type=int,
        default=TARGET_SIZE,
        help=f"target row count (default {TARGET_SIZE}; spec asks 50–100)",
    )
    parser.add_argument("--seed", type=int, default=SEED, help=f"rng seed (default {SEED})")
    parser.add_argument("--out", type=Path, default=OUTPUT_PATH)
    args = parser.parse_args(argv)

    if not (50 <= args.size <= 100):
        print(f"--size must be in [50,100], got {args.size}", file=sys.stderr)
        return 2

    X = np.load(TEST_EMBEDDINGS)
    y = np.load(TEST_LABELS, allow_pickle=True)

    text: list[str] | None
    csv_payload = _read_test_csv(TEST_CSV)
    if csv_payload is None:
        text = None
        logger.warning(
            "%s not found — golden set will omit raw text; LLM lane "
            "in run_3way_eval.py will score=null.",
            TEST_CSV,
        )
    else:
        text, intents = csv_payload
        if len(text) != len(y):
            raise RuntimeError(
                f"{TEST_CSV.name} rows={len(text)} != y_test rows={len(y)} "
                "— split mismatch"
            )
        # Lineage gate: every row's intent column must match y_test.npy. Any
        # disagreement means test.csv and y_test.npy are from different splits
        # — a silent data-leakage hazard if we let it through.
        mismatches = [i for i, (a, b) in enumerate(zip(intents, y)) if a != str(b)]
        if mismatches:
            sample = mismatches[:3]
            raise RuntimeError(
                f"{TEST_CSV.name} intent column disagrees with y_test.npy at "
                f"{len(mismatches)} of {len(y)} rows (first: {sample}). The "
                f"text and labels come from different splits — refusing to "
                f"build a golden set that mixes them."
            )
        logger.info(
            "%s intent column 100%% aligned with y_test.npy", TEST_CSV.name
        )

    keep_idx = _stratified_indices(y, args.size, args.seed)

    rows = []
    for i in keep_idx:
        i = int(i)
        row: dict = {
            "index": i,
            "label": str(y[i]),
            "embedding": [float(x) for x in X[i].tolist()],
        }
        if text is not None:
            row["text"] = text[i]
        rows.append(row)

    payload = {
        "seed": args.seed,
        "size": len(rows),
        "source": "test-split-only",
        "rows": rows,
    }

    args.out.parent.mkdir(parents=True, exist_ok=True)
    # sort_keys=True is the byte-determinism trick. indent=2 keeps it diffable.
    args.out.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    sha = _sha256_of_file(args.out)
    # SHA companion lives next to the output (`golden_set.sha256` next to
    # `golden_set.json`). Passing --out to a tmp path also writes the SHA to
    # a tmp path so tests can't trample the committed one.
    sha_path = args.out.parent / f"{args.out.stem}.sha256"
    sha_path.write_text(sha + "\n")
    print(f"wrote {args.out} (size={len(rows)}, sha256={sha[:12]}…)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
