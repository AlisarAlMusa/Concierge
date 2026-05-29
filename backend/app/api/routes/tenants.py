"""Platform tenant management routes — accessible only by tenant_manager.

All routes under /platform/tenants require the tenant_manager role.
tenant_admin → 403.  member → 403.  unauthenticated → 401.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import get_user_manager
from app.dependencies import get_session, require_tenant_manager
from app.models.user import User
from app.schemas.auth import InviteAdminRequest, UserRead
from app.schemas.tenant import TenantCreate, TenantRead, TenantUsageSummary
from app.services import tenant_service
from app.services.auth_service import invite_admin as _invite_admin

router = APIRouter(tags=["platform-tenants"])


# ──────────────────────────────────────────────────────────────────────────────
# POST /platform/tenants  — create tenant (US1)
# ──────────────────────────────────────────────────────────────────────────────


@router.post(
    "/",
    response_model=TenantRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new tenant (tenant_manager only)",
)
async def create_tenant(
    body: TenantCreate,
    manager: User = Depends(require_tenant_manager),
    session: AsyncSession = Depends(get_session),
):
    tenant = await tenant_service.create_tenant(
        session,
        name=body.name,
        slug=body.slug,
        actor_id=manager.id,
        actor_role=manager.role.value,
    )
    return TenantRead.model_validate(tenant)


# ──────────────────────────────────────────────────────────────────────────────
# GET /platform/tenants  — list all tenants (US4)
# ──────────────────────────────────────────────────────────────────────────────


@router.get(
    "/",
    response_model=list[TenantRead],
    summary="List all tenants (tenant_manager only)",
)
async def list_tenants(
    _manager: User = Depends(require_tenant_manager),
    session: AsyncSession = Depends(get_session),
):
    tenants = await tenant_service.list_tenants(session)
    return [TenantRead.model_validate(t) for t in tenants]


# ──────────────────────────────────────────────────────────────────────────────
# GET /platform/tenants/{tenant_id}  — get one tenant (US1)
# ──────────────────────────────────────────────────────────────────────────────


@router.get(
    "/{tenant_id}",
    response_model=TenantRead,
    summary="Get tenant by ID (tenant_manager only)",
)
async def get_tenant(
    tenant_id: UUID,
    _manager: User = Depends(require_tenant_manager),
    session: AsyncSession = Depends(get_session),
):
    tenant = await tenant_service.get_tenant_or_404(session, tenant_id)
    return TenantRead.model_validate(tenant)


# ──────────────────────────────────────────────────────────────────────────────
# POST /platform/tenants/{tenant_id}/invite-admin  (US2)
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
    new_user = await _invite_admin(tenant_id, body.email, session, user_manager)
    return UserRead.model_validate(new_user)


# ──────────────────────────────────────────────────────────────────────────────
# POST /platform/tenants/{tenant_id}/suspend  (US3)
# ──────────────────────────────────────────────────────────────────────────────


@router.post(
    "/{tenant_id}/suspend",
    response_model=TenantRead,
    summary="Suspend an active tenant (tenant_manager only)",
)
async def suspend_tenant(
    tenant_id: UUID,
    manager: User = Depends(require_tenant_manager),
    session: AsyncSession = Depends(get_session),
):
    tenant = await tenant_service.suspend_tenant(
        session, tenant_id, actor_id=manager.id, actor_role=manager.role.value
    )
    return TenantRead.model_validate(tenant)


# ──────────────────────────────────────────────────────────────────────────────
# POST /platform/tenants/{tenant_id}/reactivate  (US3)
# ──────────────────────────────────────────────────────────────────────────────


@router.post(
    "/{tenant_id}/reactivate",
    response_model=TenantRead,
    summary="Reactivate a suspended tenant (tenant_manager only)",
)
async def reactivate_tenant(
    tenant_id: UUID,
    manager: User = Depends(require_tenant_manager),
    session: AsyncSession = Depends(get_session),
):
    tenant = await tenant_service.reactivate_tenant(
        session, tenant_id, actor_id=manager.id, actor_role=manager.role.value
    )
    return TenantRead.model_validate(tenant)


# ──────────────────────────────────────────────────────────────────────────────
# DELETE /platform/tenants/{tenant_id}  (US5)
# ──────────────────────────────────────────────────────────────────────────────


@router.delete(
    "/{tenant_id}",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Trigger tenant deletion and erasure (tenant_manager only)",
)
async def delete_tenant(
    tenant_id: UUID,
    request: Request,
    manager: User = Depends(require_tenant_manager),
    session: AsyncSession = Depends(get_session),
):
    await tenant_service.delete_tenant(
        session,
        tenant_id,
        actor_id=manager.id,
        actor_role=manager.role.value,
        redis=request.app.state.redis,
    )
    return {"status": "deleting", "tenant_id": str(tenant_id)}


# ──────────────────────────────────────────────────────────────────────────────
# GET /platform/tenants/{tenant_id}/usage-summary  (US4)
# ──────────────────────────────────────────────────────────────────────────────


@router.get(
    "/{tenant_id}/usage-summary",
    response_model=TenantUsageSummary,
    summary="Get aggregate usage metrics for a tenant (tenant_manager only)",
)
async def get_usage_summary(
    tenant_id: UUID,
    _manager: User = Depends(require_tenant_manager),
    session: AsyncSession = Depends(get_session),
):
    return await tenant_service.get_usage_summary(session, tenant_id)
