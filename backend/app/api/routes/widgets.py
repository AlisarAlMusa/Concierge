"""Widget routes — admin lifecycle + public session issuance (Spec 011).

Admin surface (``require_tenant_admin``):

* ``GET    /widgets/``           — list this tenant's widgets.
* ``POST   /widgets/``           — create a widget; server picks
                                   ``public_widget_id``.
* ``PATCH  /widgets/{id}``        — partial update (name, greeting,
                                   ``allowed_origins``, theme, enabled).
* ``DELETE /widgets/{id}``        — hard delete.

Public surface (no auth):

* ``POST   /widgets/session``    — exchange a public widget id + origin
                                   for a short-lived signed JWT.

Failure modes for ``/widgets/session``:

* 404 — public widget id does not resolve to an enabled widget.
* 403 — origin is not in ``widget.allowed_origins`` (server-side check,
  not CORS).
* 422 — malformed body (pydantic).

Spec 011 FR-003 / FR-004 are deliberate: a curl from a server with a
copied ``public_widget_id`` ignores CORS entirely, so the origin check
must happen server-side here too.

CLAUDE.md non-negotiable rule honored everywhere: ``tenant_id`` is read
from the authenticated user, never from the request body.

Owner: Person B.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import structlog
from fastapi import APIRouter, Depends, HTTPException, Response, status

from app.dependencies import (
    get_admin_widget_service,
    get_widget_service,
    get_widget_token_service,
    require_tenant_admin,
)
from app.models.user import User
from app.schemas.widget import (
    WidgetAdminRead,
    WidgetCreate,
    WidgetSessionRequest,
    WidgetSessionResponse,
    WidgetUpdate,
)
from app.services.widget_service import WidgetService
from app.services.widget_token_service import WidgetTokenService

logger = structlog.get_logger(__name__)

router = APIRouter(tags=["widgets"])


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
    return [WidgetAdminRead.model_validate(w) for w in widgets]


@router.post(
    "/",
    response_model=WidgetAdminRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a widget for the calling tenant (tenant_admin only)",
)
async def create_widget(
    body: WidgetCreate,
    current_user: User = Depends(require_tenant_admin),
    service: WidgetService = Depends(get_admin_widget_service),
) -> WidgetAdminRead:
    widget = await service.create_widget(
        tenant_id=current_user.tenant_id,
        name=body.name,
        allowed_origins=body.allowed_origins,
        greeting=body.greeting,
        theme=body.theme,
        enabled=body.enabled,
    )
    return WidgetAdminRead.model_validate(widget)


@router.patch(
    "/{widget_id}",
    response_model=WidgetAdminRead,
    summary="Update a widget owned by the calling tenant (tenant_admin only)",
)
async def patch_widget(
    widget_id: UUID,
    body: WidgetUpdate,
    current_user: User = Depends(require_tenant_admin),
    service: WidgetService = Depends(get_admin_widget_service),
) -> WidgetAdminRead:
    # Pydantic's ``exclude_unset`` is what carries "field was omitted" all
    # the way to the service — ``None`` from the body would otherwise
    # clobber stored values with NULL.
    updates = body.model_dump(exclude_unset=True)
    widget = await service.update_widget(
        widget_id=widget_id,
        tenant_id=current_user.tenant_id,
        **updates,
    )
    if widget is None:
        raise HTTPException(
            status_code=404,
            detail="widget not found",
            headers={"X-Error-Code": "not_found"},
        )
    return WidgetAdminRead.model_validate(widget)


@router.delete(
    "/{widget_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a widget owned by the calling tenant (tenant_admin only)",
)
async def delete_widget(
    widget_id: UUID,
    current_user: User = Depends(require_tenant_admin),
    service: WidgetService = Depends(get_admin_widget_service),
) -> Response:
    removed = await service.delete_widget(
        widget_id=widget_id, tenant_id=current_user.tenant_id
    )
    if not removed:
        raise HTTPException(
            status_code=404,
            detail="widget not found",
            headers={"X-Error-Code": "not_found"},
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


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
