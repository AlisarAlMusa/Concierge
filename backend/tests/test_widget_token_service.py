"""Unit tests for WidgetTokenService — HS256 signing + claim verification.

No Postgres, no Redis, no FastAPI. Validates Spec 011 FR-005 (the claims
carried) and FR-012 (short-lived token) in isolation. Time-sensitive tests
inject ``now`` rather than calling ``time.sleep`` or mocking the clock
globally.
"""

from __future__ import annotations

import time
from uuid import uuid4

import pytest

from app.services.widget_token_service import (
    WidgetTokenClaims,
    WidgetTokenError,
    WidgetTokenService,
)

SECRET = "test-widget-secret"


def _service(ttl: int = 600) -> WidgetTokenService:
    return WidgetTokenService(secret=SECRET, ttl_seconds=ttl)


def _claims_kwargs(**overrides):
    base = dict(
        tenant_id=uuid4(),
        widget_id=uuid4(),
        visitor_session_id=uuid4(),
        origin="https://example.com",
    )
    base.update(overrides)
    return base


# ----- happy path -----------------------------------------------------------
def test_issue_then_verify_round_trips_claims():
    svc = _service()
    kwargs = _claims_kwargs()

    token = svc.issue(**kwargs)
    claims = svc.verify(token)

    assert isinstance(claims, WidgetTokenClaims)
    assert claims.tenant_id == kwargs["tenant_id"]
    assert claims.widget_id == kwargs["widget_id"]
    assert claims.visitor_session_id == kwargs["visitor_session_id"]
    assert claims.origin == kwargs["origin"]
    assert claims.expires_at - claims.issued_at == svc.ttl_seconds


def test_ttl_seconds_property_matches_constructor():
    svc = _service(ttl=42)
    assert svc.ttl_seconds == 42


# ----- expiry ---------------------------------------------------------------
def test_expired_token_raises_token_expired_error():
    svc = _service(ttl=1)
    past = int(time.time()) - 3600
    token = svc.issue(**_claims_kwargs(), now=past)

    with pytest.raises(WidgetTokenError) as exc:
        svc.verify(token)
    assert exc.value.code == "token_expired"


# ----- tamper / wrong secret -----------------------------------------------
def test_token_signed_with_different_secret_is_invalid():
    issuer = _service()
    verifier = WidgetTokenService(secret="other-secret", ttl_seconds=600)
    token = issuer.issue(**_claims_kwargs())

    with pytest.raises(WidgetTokenError) as exc:
        verifier.verify(token)
    assert exc.value.code == "invalid_token"


def test_malformed_token_raises_invalid_or_malformed():
    svc = _service()
    with pytest.raises(WidgetTokenError) as exc:
        svc.verify("not.a.jwt")
    # jose treats this as a decode failure → invalid_token.
    assert exc.value.code in ("invalid_token", "malformed_token")


def test_empty_token_raises_malformed():
    svc = _service()
    with pytest.raises(WidgetTokenError) as exc:
        svc.verify("")
    assert exc.value.code == "malformed_token"


# ----- type discriminator ---------------------------------------------------
def test_wrong_token_type_raises():
    """A JWT signed with the right secret but the wrong ``typ`` is rejected."""
    from jose import jwt

    svc = _service()
    now = int(time.time())
    payload = {
        "iss": "concierge",
        "typ": "admin_session",  # not widget_session
        "iat": now,
        "exp": now + 600,
        "tenant_id": str(uuid4()),
        "widget_id": str(uuid4()),
        "visitor_session_id": str(uuid4()),
        "origin": "https://example.com",
    }
    foreign = jwt.encode(payload, SECRET, algorithm="HS256")

    with pytest.raises(WidgetTokenError) as exc:
        svc.verify(foreign)
    assert exc.value.code == "wrong_token_type"


# ----- constructor validation -----------------------------------------------
def test_empty_secret_rejected():
    with pytest.raises(ValueError):
        WidgetTokenService(secret="", ttl_seconds=600)


def test_zero_or_negative_ttl_rejected():
    with pytest.raises(ValueError):
        WidgetTokenService(secret="x", ttl_seconds=0)
    with pytest.raises(ValueError):
        WidgetTokenService(secret="x", ttl_seconds=-1)


def test_issue_rejects_empty_origin():
    svc = _service()
    with pytest.raises(ValueError):
        svc.issue(**_claims_kwargs(origin=""))
