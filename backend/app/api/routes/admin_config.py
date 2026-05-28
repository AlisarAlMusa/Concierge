"""Tenant admin config routes — accessible only by tenant_admin.

Tenant context (RLS) is set automatically by require_tenant_admin.
tenant_manager → 403.  member → 403.  unauthenticated → 401.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.dependencies import require_tenant_admin
from app.models.user import User

router = APIRouter(tags=["admin_config"])


# ──────────────────────────────────────────────────────────────────────────────
# GET /tenant/config  (stub — Person A Day 3)
# ──────────────────────────────────────────────────────────────────────────────


@router.get(
    "/config",
    summary="Get tenant configuration (tenant_admin only)",
)
async def get_tenant_config(
    current_user: User = Depends(require_tenant_admin),
):
    """Return tenant configuration.  Stub — returns empty dict for now.

    tenant_id is derived from current_user.tenant_id (never from body).
    RLS context is set by require_tenant_admin before this handler runs.
    """
    return {"tenant_id": str(current_user.tenant_id), "config": {}}
