"""Admin escalations routes — ``GET /escalations`` + ``PATCH /escalations/{id}``.

Authoring surface for tenant admins (Spec 012 FR-010 / FR-011). The
``escalate`` agent tool writes escalations via ``EscalationService.create``
from the widget-token path; this router is the *admin* surface and never
creates escalations itself. Per the Spec 012 Assumptions, the admin
surface intentionally does not expose DELETE — escalation removal happens
through the tenant erasure flow (feature 015).

Authentication (transitional, mirrors ``/cms`` and ``/leads`` exactly):

1. ``X-Service-Token`` — shared secret from ``Settings.SERVICE_AUTH_SECRET``.
2. ``X-Tenant-Id`` — the tenant the caller is operating on.

Owner: Person B.
"""

from __future__ import annotations

from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query

from app.core.security import require_service_token
from app.dependencies import get_admin_escalation_service, get_admin_tenant_id
from app.schemas.escalation import EscalationList, EscalationRead, EscalationUpdate
from app.services.escalation_service import EscalationService

logger = structlog.get_logger(__name__)

router = APIRouter(tags=["escalations"], dependencies=[Depends(require_service_token)])


def _to_read(escalation) -> EscalationRead:
    """Build the public response from an ORM row."""
    return EscalationRead.model_validate(escalation)


def _not_found() -> HTTPException:
    return HTTPException(
        status_code=404,
        detail={"detail": "escalation not found", "code": "not_found"},
    )


@router.get(
    "",
    response_model=EscalationList,
    summary="List escalations for the caller's tenant (newest first)",
)
async def list_escalations(
    tenant_id: UUID = Depends(get_admin_tenant_id),
    service: EscalationService = Depends(get_admin_escalation_service),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> EscalationList:
    """Paginated list. Spec 012 FR-010 + SC-004."""
    items, total = await service.list_escalations(tenant_id=tenant_id, limit=limit, offset=offset)
    return EscalationList(items=[_to_read(escalation) for escalation in items], total=total)


@router.patch(
    "/{escalation_id}",
    response_model=EscalationRead,
    summary="Update escalation status (open / in_progress / resolved / dismissed)",
)
async def patch_escalation(
    escalation_id: UUID,
    payload: EscalationUpdate,
    tenant_id: UUID = Depends(get_admin_tenant_id),
    service: EscalationService = Depends(get_admin_escalation_service),
) -> EscalationRead:
    """Status transition (Spec 012 FR-011)."""
    escalation = await service.update_escalation(
        tenant_id=tenant_id,
        escalation_id=escalation_id,
        status=payload.status,
    )
    if escalation is None:
        raise _not_found()
    return _to_read(escalation)
