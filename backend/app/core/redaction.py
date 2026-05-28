"""PII / secret redaction for logs, span attributes, and pre-DB writes.

The contract `redact(text: str) -> str` is preserved as a module-level wrapper
around `PIIRedactor` so existing references (structlog processors, repository
write paths) keep working. New code should prefer `PIIRedactor.redact_text`.
"""

from __future__ import annotations

import re
from typing import Pattern

# Order matters: API-key / bearer patterns are applied first so the secret value
# is not later partially-matched by the looser email or phone regexes.
_PATTERNS: list[tuple[Pattern[str], str]] = [
    (re.compile(r"sk_(?:live|test)_[A-Za-z0-9]+"), "[REDACTED_API_KEY]"),
    (re.compile(r"\b[Bb]earer\s+[A-Za-z0-9._\-]+"), "[REDACTED_API_KEY]"),
    (re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}"), "[REDACTED_EMAIL]"),
    (re.compile(r"\+?\d[\d\s().\-]{7,}\d"), "[REDACTED_PHONE]"),
]


class PIIRedactor:
    """Regex-based redactor for emails, phone numbers, and API-key-like secrets."""

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
    """Module-level convenience wrapper around the default `PIIRedactor`."""
    return _default_redactor.redact_text(text)
