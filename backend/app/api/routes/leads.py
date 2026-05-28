"""Admin leads routes — ``GET /leads`` + ``PATCH/DELETE /leads/{lead_id}``.

Authoring surface for tenant admins (Spec 012 FR-005…FR-007). The
``capture_lead`` agent tool writes leads via ``LeadService.capture`` from
the widget-token path; this router is the *admin* surface and never
captures leads itself.

Authentication (transitional, mirrors ``/cms`` exactly):

1. ``X-Service-Token`` — shared secret from ``Settings.SERVICE_AUTH_SECRET``.
2. ``X-Tenant-Id`` — the tenant the caller is operating on. Read once into
   the RLS session variable and the ``Lead.tenant_id`` filter so the two
   walls never disagree.

Once Owner A's admin JWT lands, this gate is replaced with
``require_tenant_admin`` and the tenant comes from the verified user —
the service layer below does not change.

Owner: Person B.
"""

from __future__ import annotations

from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import JSONResponse

from app.core.security import require_service_token
from app.dependencies import get_admin_lead_service, get_admin_tenant_id
from app.schemas.lead import LeadList, LeadRead, LeadUpdate
from app.services.lead_service import LeadService

logger = structlog.get_logger(__name__)

router = APIRouter(tags=["leads"], dependencies=[Depends(require_service_token)])


def _to_read(lead) -> LeadRead:
    """Build the public response from an ORM row."""
    return LeadRead.model_validate(lead)


def _not_found() -> HTTPException:
    return HTTPException(
        status_code=404,
        detail={"detail": "lead not found", "code": "not_found"},
    )


@router.get(
    "",
    response_model=LeadList,
    summary="List leads for the caller's tenant (newest first)",
)
async def list_leads(
    tenant_id: UUID = Depends(get_admin_tenant_id),
    service: LeadService = Depends(get_admin_lead_service),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> LeadList:
    """Paginated list. Spec 012 FR-005 + SC-004."""
    items, total = await service.list_leads(tenant_id=tenant_id, limit=limit, offset=offset)
    return LeadList(items=[_to_read(lead) for lead in items], total=total)


@router.patch(
    "/{lead_id}",
    response_model=LeadRead,
    summary="Update lead status and/or notes (admin-only fields)",
)
async def patch_lead(
    lead_id: UUID,
    payload: LeadUpdate,
    tenant_id: UUID = Depends(get_admin_tenant_id),
    service: LeadService = Depends(get_admin_lead_service),
) -> LeadRead:
    """Partial update (Spec 012 FR-006)."""
    try:
        lead = await service.update_lead(
            tenant_id=tenant_id,
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
    summary="Hard-delete a lead (tenant-scoped, 404 if absent)",
)
async def delete_lead(
    lead_id: UUID,
    tenant_id: UUID = Depends(get_admin_tenant_id),
    service: LeadService = Depends(get_admin_lead_service),
) -> JSONResponse:
    """Hard delete (Spec 012 FR-007).

    Returns 204 on success, 404 if the lead does not exist for the
    caller's tenant. Cross-tenant access is indistinguishable from
    "not found" by design — never leak existence across tenants.
    """
    deleted = await service.delete_lead(tenant_id=tenant_id, lead_id=lead_id)
    if not deleted:
        raise _not_found()
    return JSONResponse(status_code=status.HTTP_204_NO_CONTENT, content=None)
