"""CMS routes — accessible only by tenant_admin (Person B implements content endpoints).

Tenant context (RLS) is set automatically by require_tenant_admin.
tenant_manager → 403.  member → 403.  unauthenticated → 401.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.dependencies import require_tenant_admin
from app.models.user import User

router = APIRouter(tags=["cms"])


# Stub endpoints — Person B will implement full CMS CRUD here.
# The require_tenant_admin dependency is wired so that when content is added,
# RLS automatically scopes queries to the authenticated tenant.


@router.get(
    "/pages",
    summary="List CMS pages (tenant_admin only)",
)
async def list_pages(
    current_user: User = Depends(require_tenant_admin),
):
    """List CMS pages for the authenticated tenant.  Stub — empty list."""
    return []
