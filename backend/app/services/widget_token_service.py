"""WidgetTokenService — signed short-lived session tokens (Spec 011 FR-005/FR-012).

HS256 JWTs signed with ``Settings.WIDGET_TOKEN_SECRET``. The token carries:

* ``tenant_id``   — the authoritative tenant for every downstream call.
  The chat route reads this **only** from the verified token; the request
  body's ``tenant_id``, if any, is ignored (PDF: "Trusting a tenant_id in
  the request body is a one-line cross-tenant breach.").
* ``widget_id``   — internal Widget primary key.
* ``visitor_session_id`` — anonymous per-tab identifier; used by
  ``LeadService`` for per-session rate limiting (Spec 012 FR-003).
* ``origin``      — the host-site origin that was server-side-validated at
  mint time. Carried so audit/logging can show where the chat originated.
* ``iat`` / ``exp`` — issued-at and expiry, both in epoch seconds.
* ``iss`` / ``typ`` — issuer and a fixed type discriminator so generic JWTs
  from other parts of the stack (admin auth, etc.) cannot be confused for
  widget sessions.

Verification failures are translated into ``WidgetTokenError`` instances
whose ``.code`` field drives the HTTP 401 response shape (the chat route
uses this so the widget runtime can distinguish ``token_expired`` from
``invalid_token``).

Owner: Person B.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from jose import JWTError, jwt
from jose.exceptions import ExpiredSignatureError
from pydantic import BaseModel, Field

_ALGORITHM = "HS256"
_TOKEN_TYPE = "widget_session"
_ISSUER = "concierge"

_ErrorCode = Literal[
    "invalid_token",
    "malformed_token",
    "token_expired",
    "wrong_token_type",
]


@dataclass(frozen=True)
class WidgetTokenError(Exception):
    """Raised by ``WidgetTokenService.verify`` on any verification failure."""

    code: _ErrorCode
    reason: str

    def __str__(self) -> str:
        return f"{self.code}: {self.reason}"


class WidgetTokenClaims(BaseModel):
    """Verified, parsed claims. Only this is allowed to flow into the app."""

    tenant_id: UUID
    widget_id: UUID
    visitor_session_id: UUID
    origin: str
    issued_at: int = Field(..., description="iat, epoch seconds")
    expires_at: int = Field(..., description="exp, epoch seconds")


class WidgetTokenService:
    """Sign + verify the short-lived JWT carried by every chat request."""

    def __init__(self, *, secret: str, ttl_seconds: int = 900) -> None:
        if not secret:
            raise ValueError("WidgetTokenService: secret must be non-empty")
        if ttl_seconds <= 0:
            raise ValueError("WidgetTokenService: ttl_seconds must be > 0")
        self._secret = secret
        self._ttl = ttl_seconds

    @property
    def ttl_seconds(self) -> int:
        return self._ttl

    def issue(
        self,
        *,
        tenant_id: UUID,
        widget_id: UUID,
        visitor_session_id: UUID,
        origin: str,
        now: int | None = None,
    ) -> str:
        """Mint a signed JWT. ``now`` is injectable so tests can pin the clock."""
        if not origin:
            raise ValueError("WidgetTokenService.issue: origin must be non-empty")
        iat = int(now if now is not None else time.time())
        exp = iat + self._ttl
        payload = {
            "iss": _ISSUER,
            "typ": _TOKEN_TYPE,
            "iat": iat,
            "exp": exp,
            "tenant_id": str(tenant_id),
            "widget_id": str(widget_id),
            "visitor_session_id": str(visitor_session_id),
            "origin": origin,
        }
        return jwt.encode(payload, self._secret, algorithm=_ALGORITHM)

    def verify(self, token: str) -> WidgetTokenClaims:
        """Decode + validate the token. Raises ``WidgetTokenError`` on failure.

        ``jose`` raises ``ExpiredSignatureError`` for expired tokens and
        ``JWTError`` for everything else. We split them so the chat route
        can surface a distinct ``token_expired`` code to the widget.
        """
        if not token:
            raise WidgetTokenError(code="malformed_token", reason="empty token")
        try:
            payload = jwt.decode(
                token,
                self._secret,
                algorithms=[_ALGORITHM],
                issuer=_ISSUER,
                options={"require": ["exp", "iat", "iss"]},
            )
        except ExpiredSignatureError as exc:
            raise WidgetTokenError(code="token_expired", reason=str(exc)) from exc
        except JWTError as exc:
            raise WidgetTokenError(code="invalid_token", reason=str(exc)) from exc

        if payload.get("typ") != _TOKEN_TYPE:
            raise WidgetTokenError(
                code="wrong_token_type",
                reason=f"expected {_TOKEN_TYPE}, got {payload.get('typ')!r}",
            )

        try:
            return WidgetTokenClaims(
                tenant_id=UUID(payload["tenant_id"]),
                widget_id=UUID(payload["widget_id"]),
                visitor_session_id=UUID(payload["visitor_session_id"]),
                origin=payload["origin"],
                issued_at=int(payload["iat"]),
                expires_at=int(payload["exp"]),
            )
        except (KeyError, ValueError, TypeError) as exc:
            raise WidgetTokenError(
                code="malformed_token", reason=f"claim parse error: {exc}"
            ) from exc
