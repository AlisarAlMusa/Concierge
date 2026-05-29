"""Widget session endpoint — token issuance (Spec 011).

``POST /widgets/session`` exchanges a public widget id + origin for a
short-lived signed JWT. The widget runtime stores that JWT in memory and
includes it as ``Authorization: Bearer …`` on every subsequent ``/chat``
call.

Failure modes:

* 404 — public widget id does not resolve to an enabled widget.
* 403 — origin is not in ``widget.allowed_origins`` (server-side check,
  not CORS).
* 422 — malformed body (pydantic).

Spec 011 FR-003 / FR-004 are deliberate: a curl from a server with a
copied ``public_widget_id`` ignores CORS entirely, so the origin check
must happen server-side here too.

Owner: Person B.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import structlog
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.dependencies import (
    get_admin_widget_service,
    get_widget_service,
    get_widget_token_service,
    require_tenant_admin,
)
from app.models.user import User
from app.schemas.widget import WidgetSessionRequest, WidgetSessionResponse
from app.services.widget_service import WidgetService
from app.services.widget_token_service import WidgetTokenService

logger = structlog.get_logger(__name__)

router = APIRouter(tags=["widgets"])


class WidgetAdminRead(BaseModel):
    id: str
    public_widget_id: str
    name: str | None
    greeting: str | None
    enabled: bool
    theme: dict[str, Any]


@router.get(
    "/",
    response_model=list[WidgetAdminRead],
    summary="List widgets for the calling tenant (tenant_admin only)",
)
async def list_widgets(
    current_user: User = Depends(require_tenant_admin),
    service: WidgetService = Depends(get_admin_widget_service),
) -> list[WidgetAdminRead]:
    widgets = await service.list_by_tenant(current_user.tenant_id)
    return [
        WidgetAdminRead(
            id=str(w.id),
            public_widget_id=w.public_widget_id,
            name=w.name,
            greeting=w.greeting,
            enabled=w.enabled,
            theme=w.theme or {},
        )
        for w in widgets
    ]


@router.post(
    "/session",
    response_model=WidgetSessionResponse,
    status_code=200,
    summary="Exchange a public widget id + origin for a short-lived session token",
)
async def post_widget_session(
    request: WidgetSessionRequest,
    widget_service: WidgetService = Depends(get_widget_service),
    token_service: WidgetTokenService = Depends(get_widget_token_service),
) -> WidgetSessionResponse:
    widget = await widget_service.get_by_public_id(request.public_widget_id)
    if widget is None:
        logger.info("widget.session.not_found", public_widget_id=request.public_widget_id)
        raise HTTPException(status_code=404, detail="widget not found")

    if not WidgetService.validate_origin(widget, request.origin):
        # Don't reveal whether the widget exists vs the origin is wrong —
        # both failures are user-facing identical to limit probing.
        logger.warning(
            "widget.session.origin_rejected",
            widget_id=str(widget.id),
            tenant_id=str(widget.tenant_id),
            origin=request.origin,
        )
        raise HTTPException(status_code=403, detail="origin not allowed")

    visitor_session_id = uuid4()
    token = token_service.issue(
        tenant_id=widget.tenant_id,
        widget_id=widget.id,
        visitor_session_id=visitor_session_id,
        origin=request.origin,
    )
    logger.info(
        "widget.session.issued",
        tenant_id=str(widget.tenant_id),
        widget_id=str(widget.id),
        visitor_session_id=str(visitor_session_id),
        ttl_seconds=token_service.ttl_seconds,
    )
    return WidgetSessionResponse(
        token=token,
        token_type="Bearer",
        expires_in=token_service.ttl_seconds,
    )
