"""Service-to-service authentication for `guardrails_sidecar`.

Mirrors `backend/app/core/security.py`. The shared service token is read from
the settings singleton (populated from Vault in non-local envs — see spec 018).
"""

from __future__ import annotations

import hmac

from fastapi import Header, HTTPException

from app.core.config import get_settings


def verify_service_token(token: str | None) -> bool:
    """Constant-time comparison; falsy inputs return False so the dependency
    emits the same 403 for missing / empty / wrong tokens (FR-007).
    """
    if not token:
        return False
    expected = get_settings().SERVICE_AUTH_SECRET
    if not expected:
        return False
    return hmac.compare_digest(token, expected)


async def require_service_token(
    x_service_token: str | None = Header(default=None, alias="X-Service-Token"),
) -> None:
    """FastAPI dependency. Use as `dependencies=[Depends(require_service_token)]`
    on every business endpoint. Do NOT apply to `/health` — Docker healthchecks
    must remain reachable.
    """
    if not verify_service_token(x_service_token):
        raise HTTPException(status_code=403, detail="Invalid service token")
