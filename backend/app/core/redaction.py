# TODO: Person C — implement redact() using regex patterns for emails, phone numbers,
# API-key-like strings, and bearer tokens. Tests in backend/tests/test_redaction.py.
#
# Contract (do not change the signature):
#   def redact(text: str) -> str
#       Returns the input with all detected PII/secrets replaced by [REDACTED].
#       Must be synchronous (called in structlog processors and before DB writes).


def redact(text: str) -> str:
    """Placeholder — Person C replaces with real implementation."""
    return text
