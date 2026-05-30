"""HTTP contracts for the widget surface (Spec 011).

Public runtime contracts:

* ``WidgetSessionRequest`` / ``WidgetSessionResponse`` —
  ``POST /widgets/session`` (also exposed at ``/public/widgets/session``).
  The request carries the host-page ``public_widget_id`` + the browser
  origin; the response is the bearer token every subsequent ``/chat``
  call sends.
* ``WidgetConfigResponse`` —
  ``GET /public/widgets/config``. Returned to the widget runtime after
  the session token is minted so the bundle can paint the tenant's
  greeting and theme on load.

Tenant-admin management contracts:

* ``WidgetCreate``, ``WidgetUpdate`` — request bodies for
  ``POST /widgets/`` and ``PATCH /widgets/{id}`` respectively. Both deny
  ``tenant_id`` — it is derived from the authenticated JWT, never from
  the body (CLAUDE.md non-negotiable rule).
* ``WidgetAdminRead`` — response body for the tenant-admin list / create /
  patch routes. Includes ``allowed_origins`` (admin needs to see/edit it)
  but NEVER appears on the public ``/public/widgets/config`` surface,
  which uses ``WidgetConfigResponse`` and intentionally omits it.

Both public endpoints derive tenancy from the verified widget token
(FR-005); admin endpoints derive it from the JWT-authenticated user.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class WidgetSessionRequest(BaseModel):
    """``POST /widgets/session`` request body."""

    public_widget_id: str = Field(..., min_length=1, max_length=64)
    origin: str = Field(..., min_length=1, max_length=255)


class WidgetSessionResponse(BaseModel):
    """``POST /widgets/session`` response body."""

    token: str
    token_type: str = "Bearer"
    expires_in: int = Field(
        ..., description="Token validity window in seconds (== WIDGET_TOKEN_TTL_SECONDS)"
    )


class WidgetConfigResponse(BaseModel):
    """``GET /public/widgets/config`` response body.

    Only the fields the widget runtime needs to render — secrets such as
    ``allowed_origins`` are intentionally NOT returned. The runtime never
    needs to read its own allowlist; the server enforces it at session
    issuance (FR-003 / FR-004).
    """

    model_config = ConfigDict(from_attributes=True)

    public_widget_id: str = Field(
        ...,
        description="Stable public identifier the host page's <script> tag carries.",
    )
    greeting: str = Field(
        ...,
        description="First-message greeting shown when the widget opens (FR-011).",
    )
    theme: dict[str, Any] = Field(
        default_factory=dict,
        description="Opaque tenant theme blob — colors, font, etc. Shape evolves "
        "without a backend migration; the widget treats unknown keys as no-ops.",
    )
    enabled: bool = Field(
        ...,
        description="Always true in a 200 response (disabled widgets surface as 404).",
    )


# ──────────────────────────────────────────────────────────────────────────────
# Tenant-admin management schemas
# ──────────────────────────────────────────────────────────────────────────────

_MAX_ORIGINS = 25
_MAX_ORIGIN_LEN = 255
_MAX_NAME_LEN = 255
_MAX_GREETING_LEN = 500


def _validate_origin(value: str) -> str:
    """Validate one origin string.

    Spec 011 FR-003 mandates exact-match origin comparison; this validator
    rejects every shape that would either match too broadly (wildcards) or
    fail the runtime equality check (path-only, trailing slash, query,
    fragment, embedded whitespace, missing scheme).

    Returns the canonical lowercased origin.
    """
    if not isinstance(value, str):
        raise ValueError("origin must be a string")
    candidate = value.strip()
    if not candidate:
        raise ValueError("origin must not be empty")
    if len(candidate) > _MAX_ORIGIN_LEN:
        raise ValueError(f"origin must be at most {_MAX_ORIGIN_LEN} characters")
    if "*" in candidate or "?" in candidate:
        raise ValueError("origin must not contain wildcards")
    if any(c.isspace() for c in candidate):
        raise ValueError("origin must not contain whitespace")
    parsed = urlsplit(candidate)
    if parsed.scheme not in ("http", "https"):
        raise ValueError("origin must start with http:// or https://")
    if not parsed.hostname:
        raise ValueError("origin must include a hostname (e.g. http://localhost:5500)")
    if parsed.path not in ("", "/"):
        raise ValueError(
            "origin must not contain a path — submit only scheme://host[:port] "
            f"(got path {parsed.path!r})"
        )
    if parsed.query or parsed.fragment:
        raise ValueError("origin must not contain a query string or fragment")
    # Canonical form: scheme + "://" + lowercased host + optional :port.
    host = parsed.hostname.lower()
    port_suffix = f":{parsed.port}" if parsed.port is not None else ""
    return f"{parsed.scheme}://{host}{port_suffix}"


def _validate_origins_list(values: list[str]) -> list[str]:
    """Validate the full list — bounded length, no duplicates, canonicalised."""
    if len(values) > _MAX_ORIGINS:
        raise ValueError(f"at most {_MAX_ORIGINS} allowed origins")
    canonical: list[str] = []
    seen: set[str] = set()
    for raw in values:
        cleaned = _validate_origin(raw)
        if cleaned not in seen:
            canonical.append(cleaned)
            seen.add(cleaned)
    return canonical


class WidgetCreate(BaseModel):
    """``POST /widgets/`` request body.

    ``tenant_id`` is intentionally absent — derived from the authenticated
    JWT in the route. ``public_widget_id`` is also absent — generated
    server-side by ``WidgetService`` so the admin can't pick a colliding
    or predictable id.
    """

    name: str = Field(..., min_length=1, max_length=_MAX_NAME_LEN)
    allowed_origins: list[str] = Field(default_factory=list)
    greeting: str = Field(default="", max_length=_MAX_GREETING_LEN)
    theme: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True

    @field_validator("allowed_origins")
    @classmethod
    def _check_origins(cls, v: list[str]) -> list[str]:
        return _validate_origins_list(v)


class WidgetUpdate(BaseModel):
    """``PATCH /widgets/{id}`` request body.

    Every field is optional; only fields the client sends are updated.
    ``tenant_id`` and ``public_widget_id`` are intentionally not exposed —
    rotating the public id is a separate (deliberate) operation and
    moving a widget across tenants is never legal.
    """

    name: str | None = Field(default=None, min_length=1, max_length=_MAX_NAME_LEN)
    allowed_origins: list[str] | None = None
    greeting: str | None = Field(default=None, max_length=_MAX_GREETING_LEN)
    theme: dict[str, Any] | None = None
    enabled: bool | None = None

    @field_validator("allowed_origins")
    @classmethod
    def _check_origins(cls, v: list[str] | None) -> list[str] | None:
        if v is None:
            return None
        return _validate_origins_list(v)


class WidgetAdminRead(BaseModel):
    """Response body for tenant-admin list / create / patch routes.

    Includes ``allowed_origins`` because the admin needs to see and edit
    it. Never used as the response model for any public route — those
    use ``WidgetConfigResponse``, which omits the allow-list.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    public_widget_id: str
    name: str
    greeting: str
    theme: dict[str, Any] = Field(default_factory=dict)
    allowed_origins: list[str] = Field(default_factory=list)
    enabled: bool
    created_at: datetime
    updated_at: datetime
