"""Public widget runtime surface (Spec 011 — ``/public/*`` trio).

Three endpoints the browser-side widget loader / runtime calls directly:

* ``POST /public/widgets/session`` — alias for ``POST /widgets/session``
  (same handler, identical contract). Mints the short-lived session JWT
  after server-side validation of ``public_widget_id`` + ``origin``.
* ``POST /public/chat`` — alias for ``POST /chat`` (same handler).
  Requires a valid widget session token; one chat turn per call.
* ``GET  /public/widgets/config`` — new. Returns the verified widget's
  ``greeting`` + ``theme`` so the bundle can paint on load (FR-006 /
  FR-011). Tenancy is derived from the token, not the URL.

Why aliases rather than re-mounting the existing routers under
``/public``: keeping the public surface in its own module lets the
config endpoint stay scoped to ``/public/*`` only (we explicitly do
*not* want ``GET /widgets/config`` to leak under the admin prefix),
avoids ambiguous OpenAPI duplication, and lets each public route carry
its own ``"public-widget-runtime"`` tag while reusing the underlying
handlers verbatim via ``add_api_route``.

Security invariants preserved from the underlying handlers
(``app.api.routes.widgets`` and ``app.api.routes.chat``):

* ``tenant_id`` is sourced only from the verified widget JWT — never the
  request body, query string, or any header the caller controls.
* The origin allowlist check is performed server-side in the session
  handler; CORS/CSP would be layered defense, not a substitute.
* ``/public/chat`` and ``/public/widgets/config`` both require a valid
  ``Authorization: Bearer …`` token and surface 401 on missing /
  invalid / expired tokens.
* The ``widgets`` table has RLS enabled; ``/public/widgets/config``
  reads under the tenant-scoped session via
  ``get_runtime_widget_service``.

Owner: Person B.
"""

from __future__ import annotations

from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException

from app.api.routes.chat import post_chat
from app.api.routes.widgets import post_widget_session
from app.dependencies import (
    get_runtime_widget_service,
    get_tenant_id,
    get_widget_id,
)
from app.schemas.chat import ChatResponse
from app.schemas.widget import (
    WidgetConfigResponse,
    WidgetSessionResponse,
)
from app.services.widget_service import WidgetService

logger = structlog.get_logger(__name__)

router = APIRouter(tags=["public-widget-runtime"])


# ``/public/widgets/session`` — direct alias for ``POST /widgets/session``.
# The handler is reused verbatim so the origin allowlist check, token-mint
# claims, and failure-mode contract are guaranteed identical.
router.add_api_route(
    "/widgets/session",
    post_widget_session,
    methods=["POST"],
    response_model=WidgetSessionResponse,
    status_code=200,
    name="post_public_widget_session",
    summary="Public alias: exchange public_widget_id + origin for a signed session token",
    tags=["public-widget-runtime"],
)


# ``/public/chat`` — direct alias for ``POST /chat``. Reuses the same auth
# chain (``get_widget_claims`` → tenant/widget/visitor) and orchestrator
# wiring so behaviour is byte-identical to the legacy admin path.
router.add_api_route(
    "/chat",
    post_chat,
    methods=["POST"],
    response_model=ChatResponse,
    status_code=200,
    name="post_public_chat",
    summary="Public alias: one chat turn with a verified widget session token",
    tags=["public-widget-runtime"],
)


@router.get(
    "/widgets/config",
    response_model=WidgetConfigResponse,
    summary="Return greeting + theme for the verified widget session",
)
async def get_widget_config(
    tenant_id: UUID = Depends(get_tenant_id),
    widget_id: UUID = Depends(get_widget_id),
    widget_service: WidgetService = Depends(get_runtime_widget_service),
) -> WidgetConfigResponse:
    """Runtime config the widget bundle paints on load.

    Both ``widget_id`` and ``tenant_id`` come from the verified JWT — the
    route has no URL parameter the caller can manipulate. The service
    method re-asserts ``WHERE tenant_id = …`` so even a future refactor
    that drops RLS cannot return another tenant's widget.

    Failure modes:

    * 401 — missing / invalid / expired session token (handled in
      ``get_widget_claims`` before this function runs).
    * 404 — token is valid but the widget has been disabled or deleted
      since the session was issued. Returning 404 (rather than 401) lets
      the runtime know the token itself is fine; refreshing it would
      yield the same result.
    """
    widget = await widget_service.get_by_id(widget_id, tenant_id=tenant_id)
    if widget is None:
        logger.info(
            "widget.config.not_found",
            widget_id=str(widget_id),
            tenant_id=str(tenant_id),
        )
        raise HTTPException(status_code=404, detail="widget not found or disabled")
    return WidgetConfigResponse.model_validate(widget)
