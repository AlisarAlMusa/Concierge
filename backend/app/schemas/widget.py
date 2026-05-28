"""HTTP contracts for the public widget runtime (Spec 011).

Three messages cross the wire:

* ``WidgetSessionRequest`` / ``WidgetSessionResponse`` —
  ``POST /widgets/session`` (also exposed at ``/public/widgets/session``).
  The request carries the host-page ``public_widget_id`` + the browser
  origin; the response is the bearer token every subsequent ``/chat``
  call sends.
* ``WidgetConfigResponse`` —
  ``GET /public/widgets/config``. Returned to the widget runtime after
  the session token is minted so the bundle can paint the tenant's
  greeting and theme on load.

Both endpoints derive tenancy from the verified token (FR-005), never
from the request body or query string.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


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
