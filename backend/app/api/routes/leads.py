"""Admin leads routes — ``GET /leads`` + ``PATCH/DELETE /leads/{lead_id}``.

JWT-authenticated via require_tenant_admin. Tenant id is derived from the
verified user — never from the request body (CLAUDE.md rule 5).
"""

from __future__ import annotations

from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import JSONResponse

from app.dependencies import get_jwt_lead_service, require_tenant_admin
from app.models.user import User
from app.schemas.lead import LeadList, LeadRead, LeadUpdate
from app.services.lead_service import LeadService

logger = structlog.get_logger(__name__)

router = APIRouter(tags=["leads"])


def _to_read(lead) -> LeadRead:
    return LeadRead.model_validate(lead)


def _not_found() -> HTTPException:
    return HTTPException(
        status_code=404,
        detail={"detail": "lead not found", "code": "not_found"},
    )


@router.get(
    "",
    response_model=LeadList,
    summary="List leads for the calling tenant (newest first)",
)
async def list_leads(
    current_user: User = Depends(require_tenant_admin),
    service: LeadService = Depends(get_jwt_lead_service),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> LeadList:
    items, total = await service.list_leads(
        tenant_id=current_user.tenant_id, limit=limit, offset=offset
    )
    return LeadList(items=[_to_read(lead) for lead in items], total=total)


@router.patch(
    "/{lead_id}",
    response_model=LeadRead,
    summary="Update lead status and/or notes",
)
async def patch_lead(
    lead_id: UUID,
    payload: LeadUpdate,
    current_user: User = Depends(require_tenant_admin),
    service: LeadService = Depends(get_jwt_lead_service),
) -> LeadRead:
    try:
        lead = await service.update_lead(
            tenant_id=current_user.tenant_id,
            lead_id=lead_id,
            status=payload.status,
            notes=payload.notes,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={"detail": str(exc), "code": "invalid_payload"},
        ) from exc

    if lead is None:
        raise _not_found()
    return _to_read(lead)


@router.delete(
    "/{lead_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Hard-delete a lead (tenant-scoped)",
)
async def delete_lead(
    lead_id: UUID,
    current_user: User = Depends(require_tenant_admin),
    service: LeadService = Depends(get_jwt_lead_service),
) -> JSONResponse:
    deleted = await service.delete_lead(tenant_id=current_user.tenant_id, lead_id=lead_id)
    if not deleted:
        raise _not_found()
    return JSONResponse(status_code=status.HTTP_204_NO_CONTENT, content=None)
