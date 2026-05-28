"""Platform audit log routes — accessible only by tenant_manager."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_session, require_tenant_manager
from app.models.user import User
from app.repositories import audit_repository
from app.schemas.audit_log import AuditLogRead

router = APIRouter(tags=["platform-audit-logs"])


@router.get(
    "/audit-logs",
    response_model=list[AuditLogRead],
    summary="Paginated audit log (tenant_manager only)",
)
async def list_audit_logs(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    tenant_id: UUID | None = Query(default=None),
    _manager: User = Depends(require_tenant_manager),
    session: AsyncSession = Depends(get_session),
):
    logs = await audit_repository.list_audit_logs(
        session, limit=limit, offset=offset, tenant_id=tenant_id
    )
    return [AuditLogRead.model_validate(entry) for entry in logs]
