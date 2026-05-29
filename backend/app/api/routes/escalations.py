"""Admin escalations routes — ``GET /escalations`` + ``PATCH /escalations/{id}``.

JWT-authenticated via require_tenant_admin. Tenant id is derived from the
verified user — never from the request body (CLAUDE.md rule 5).
"""

from __future__ import annotations

from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query

from app.dependencies import get_jwt_escalation_service, require_tenant_admin
from app.models.user import User
from app.schemas.escalation import EscalationList, EscalationRead, EscalationUpdate
from app.services.escalation_service import EscalationService

logger = structlog.get_logger(__name__)

router = APIRouter(tags=["escalations"])


def _to_read(escalation) -> EscalationRead:
    return EscalationRead.model_validate(escalation)


def _not_found() -> HTTPException:
    return HTTPException(
        status_code=404,
        detail={"detail": "escalation not found", "code": "not_found"},
    )


@router.get(
    "",
    response_model=EscalationList,
    summary="List escalations for the calling tenant (newest first)",
)
async def list_escalations(
    current_user: User = Depends(require_tenant_admin),
    service: EscalationService = Depends(get_jwt_escalation_service),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> EscalationList:
    items, total = await service.list_escalations(
        tenant_id=current_user.tenant_id, limit=limit, offset=offset
    )
    return EscalationList(items=[_to_read(escalation) for escalation in items], total=total)


@router.patch(
    "/{escalation_id}",
    response_model=EscalationRead,
    summary="Update escalation status (open / in_progress / resolved / dismissed)",
)
async def patch_escalation(
    escalation_id: UUID,
    payload: EscalationUpdate,
    current_user: User = Depends(require_tenant_admin),
    service: EscalationService = Depends(get_jwt_escalation_service),
) -> EscalationRead:
    escalation = await service.update_escalation(
        tenant_id=current_user.tenant_id,
        escalation_id=escalation_id,
        status=payload.status,
    )
    if escalation is None:
        raise _not_found()
    return _to_read(escalation)
