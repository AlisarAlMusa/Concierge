"""Platform tenant management routes — accessible only by tenant_manager.

All routes under /platform/tenants require the tenant_manager role.
tenant_admin → 403.  member → 403.  unauthenticated → 401.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import get_user_manager
from app.dependencies import get_session, require_tenant_manager
from app.models.user import User
from app.schemas.auth import InviteAdminRequest, UserRead
from app.services.auth_service import invite_admin as _invite_admin

router = APIRouter(tags=["platform-tenants"])


# ──────────────────────────────────────────────────────────────────────────────
# GET /platform/tenants  (stub)
# ──────────────────────────────────────────────────────────────────────────────


@router.get(
    "/",
    summary="List all tenants (tenant_manager only)",
)
async def list_tenants(
    _manager: User = Depends(require_tenant_manager),
):
    """Return a list of all tenants.  Stub — returns empty list for now."""
    return []


# ──────────────────────────────────────────────────────────────────────────────
# POST /platform/tenants/{tenant_id}/invite-admin  (T022)
# ──────────────────────────────────────────────────────────────────────────────


@router.post(
    "/{tenant_id}/invite-admin",
    response_model=UserRead,
    status_code=status.HTTP_201_CREATED,
    summary="Invite a tenant_admin for the specified tenant (tenant_manager only)",
)
async def invite_admin_route(
    tenant_id: UUID,
    body: InviteAdminRequest,
    manager: User = Depends(require_tenant_manager),
    session: AsyncSession = Depends(get_session),
    user_manager=Depends(get_user_manager),
):
    """Create a tenant_admin user for the given tenant.

    • Caller must have role=tenant_manager.
    • Returns 404 if the tenant does not exist or is not active.
    • Returns 409 if the email is already registered.
    • Returns 201 UserRead with role=tenant_admin and correct tenant_id.
    """
    new_user = await _invite_admin(tenant_id, body.email, session, user_manager)
    return UserRead.model_validate(new_user)
