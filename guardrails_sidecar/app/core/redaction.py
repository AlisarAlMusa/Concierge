"""PII / secret redaction for the guardrails sidecar (output rails).

**Single-source contract**: the canonical patterns live in
`backend/app/core/redaction.py`. This sidecar copy is intentional — each
service ships its own image and we keep deps independent (same pattern as
`core/vault.py` and `core/security.py` from spec 018). A CI drift detector
runs both implementations over the same fixture set and fails on
disagreement (spec 010 task T031 / FR-011).

If you change a pattern here, change it there too. The drift test exists
to catch the day you forget.
"""

from __future__ import annotations

import re
from typing import Pattern

# Order matters: API-key / bearer patterns are applied first so the secret
# value is not later partially-matched by the looser email or phone regexes.
_PATTERNS: list[tuple[Pattern[str], str]] = [
    (re.compile(r"sk_(?:live|test)_[A-Za-z0-9]+"), "[REDACTED_API_KEY]"),
    (re.compile(r"\b[Bb]earer\s+[A-Za-z0-9._\-]+"), "[REDACTED_API_KEY]"),
    (re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}"), "[REDACTED_EMAIL]"),
    (re.compile(r"\+?\d[\d\s().\-]{7,}\d"), "[REDACTED_PHONE]"),
]


class PIIRedactor:
    """Regex-based redactor — must mirror backend/app/core/redaction.py."""

    def __init__(self) -> None:
        self._patterns = _PATTERNS

    def redact_text(self, text: str) -> str:
        if not isinstance(text, str) or not text:
            return text
        for pattern, replacement in self._patterns:
            text = pattern.sub(replacement, text)
        return text


_default_redactor = PIIRedactor()


def redact(text: str) -> str:
    return _default_redactor.redact_text(text)
