"""HTTP contract for ``POST /widgets/session`` (Spec 011 FR-002 / FR-005).

The request body carries the host page's public widget id plus the
host-site origin. The response is the bearer token the widget runtime
sends on every subsequent ``/chat`` call.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


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
