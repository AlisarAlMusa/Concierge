"""Redaction drift detector — spec 010 task T031 / FR-011.

The PIIRedactor regex patterns are intentionally duplicated between
`backend/app/core/redaction.py` and `guardrails_sidecar/app/core/redaction.py`
(matches the `core/vault.py` precedent from spec 018). This test loads BOTH
implementations and asserts they produce identical output on a fixture set
that covers every pattern in the canonical version.

If you change a pattern, change it in both places. This test exists to fail
the day you forget.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SIDECAR_REDACTION_PATH = (
    REPO_ROOT / "guardrails_sidecar" / "app" / "core" / "redaction.py"
)


def _load_sidecar_redactor():
    """Load the sidecar's redaction module under an isolated name.

    The sidecar's `app/` package collides with the backend's. We isolate the
    sidecar redaction module by loading it directly via importlib without
    inserting the sidecar root on sys.path.
    """
    if not SIDECAR_REDACTION_PATH.exists():
        pytest.skip(f"sidecar redaction module not found at {SIDECAR_REDACTION_PATH}")
    saved = sys.modules.copy()
    try:
        spec = importlib.util.spec_from_file_location(
            "sidecar_redaction_under_test", SIDECAR_REDACTION_PATH
        )
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        # Restore sys.modules so other tests are not contaminated.
        for k in list(sys.modules):
            if k not in saved:
                del sys.modules[k]


PROBE_SET: list[str] = [
    # API keys
    "Use this key: sk_live_ABC123XYZ456",
    "Try sk_test_QWERTY789 in dev",
    "Authorization: Bearer abc.def-ghi",
    "auth header is bearer my_short_token",
    # Email
    "Email me at bob@example.com please",
    "user.name+tag@sub.domain.io is the canonical form",
    # Phone
    "Call us at +1 415-555-0199",
    "phone: (415) 555-0199",
    "europe number 0033 1 23 45 67 89",
    # Mixed
    "key=sk_live_ABCDEFGHIJ contact a@b.com phone +1 415-555-0199",
    # No PII
    "What time do you open?",
    "",
    # Edge: bearer at end of line
    "X-Foo: Bearer abcDEF.qux-12345",
]


@pytest.mark.parametrize("text", PROBE_SET)
def test_backend_and_sidecar_redaction_agree(text: str) -> None:
    from app.core.redaction import redact as backend_redact

    sidecar = _load_sidecar_redactor()
    sidecar_redact = sidecar.redact
    assert backend_redact(text) == sidecar_redact(text), (
        f"DRIFT: backend and sidecar redaction disagree on: {text!r}"
    )


def test_both_redactors_are_idempotent() -> None:
    from app.core.redaction import redact as backend_redact

    sidecar = _load_sidecar_redactor()
    for text in PROBE_SET:
        assert backend_redact(backend_redact(text)) == backend_redact(text)
        assert sidecar.redact(sidecar.redact(text)) == sidecar.redact(text)
