"""Static and behavioural checks for `evals/prepare_golden_set.py`.

The leakage guard (SC-009) is the most important one — it prevents a future
edit from accidentally reading from `*_train*` or `*_val*` data.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PREP_SCRIPT = REPO_ROOT / "evals" / "prepare_golden_set.py"
GOLDEN = REPO_ROOT / "evals" / "classifier" / "golden_set.json"
SHA = REPO_ROOT / "evals" / "classifier" / "golden_set.sha256"


def test_no_train_or_val_path_literals_in_source() -> None:
    """SC-009: the script must never reference train/val data."""
    source = PREP_SCRIPT.read_text()
    # Strip comments first — `train_test_split` (a sklearn function name) is
    # allowed, and the docstring legitimately *names* the rule being enforced.
    code_only = re.sub(r"#.*", "", source)
    code_only = re.sub(r'""".*?"""', "", code_only, flags=re.DOTALL)
    code_only = re.sub(r"'''.*?'''", "", code_only, flags=re.DOTALL)
    # Now look only at string literals — Path("…/X_train…") would be a leak.
    string_literals = re.findall(r'"[^"]*"|\'[^\']*\'', code_only)
    leakage = [
        s
        for s in string_literals
        if re.search(r"(?:^|[^a-zA-Z_])(train|val)(?:[^a-zA-Z_]|$)", s)
        and "train_test_split" not in s
    ]
    assert not leakage, f"prepare_golden_set.py references train/val data: {leakage}"


def test_golden_set_committed_and_sha_matches() -> None:
    """The on-disk golden_set.json must match the committed sha (SC-007)."""
    import hashlib

    assert GOLDEN.exists(), f"{GOLDEN} not committed"
    assert SHA.exists(), f"{SHA} not committed"
    computed = hashlib.sha256(GOLDEN.read_bytes()).hexdigest()
    expected = SHA.read_text().strip()
    assert computed == expected, (
        f"golden_set.json sha {computed[:12]}… does not match "
        f"golden_set.sha256 {expected[:12]}…. Re-run prepare_golden_set.py."
    )


def test_golden_set_shape_and_lineage() -> None:
    payload = json.loads(GOLDEN.read_text())
    assert payload["source"] == "test-split-only"
    assert 50 <= payload["size"] <= 100
    assert payload["size"] == len(payload["rows"])
    for row in payload["rows"]:
        assert isinstance(row["index"], int)
        assert isinstance(row["label"], str)
        assert isinstance(row["embedding"], list)
        assert len(row["embedding"]) == 1024
        # `text` is optional — present only when the CSV is aligned.
        if "text" in row:
            assert isinstance(row["text"], str) and row["text"]


def test_script_is_deterministic(tmp_path: Path) -> None:
    """Running the script twice with the same seed produces a byte-identical
    file (SC-007)."""
    out_a = tmp_path / "a.json"
    out_b = tmp_path / "b.json"
    for out in (out_a, out_b):
        subprocess.run(
            [
                sys.executable,
                str(PREP_SCRIPT),
                "--size",
                "60",
                "--seed",
                "424242",
                "--out",
                str(out),
            ],
            check=True,
            capture_output=True,
        )
    assert out_a.read_bytes() == out_b.read_bytes()
