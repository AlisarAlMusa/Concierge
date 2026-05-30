from fastapi import APIRouter

from app.api.routes import (
    admin_cms,
    admin_config,
    audit_logs,
    auth,
    chat,
    cms,
    costs,
    escalations,
    health,
    leads,
    public,
    public_site,
    tenants,
    widget_asset,
    widgets,
)

api_router = APIRouter()

api_router.include_router(health.router)
# Custom auth routes (register, login, logout, /me) — replaces the fastapi-users
# default routers so we can enforce rate limiting, audit logging, and our
# platform error contract.
api_router.include_router(auth.router, prefix="/auth")
api_router.include_router(tenants.router, prefix="/platform/tenants")
api_router.include_router(audit_logs.router, prefix="/platform")
api_router.include_router(admin_config.router, prefix="/tenant")
api_router.include_router(admin_cms.router, prefix="/tenant/cms")
api_router.include_router(cms.router, prefix="/cms")
api_router.include_router(widgets.router, prefix="/widgets")
api_router.include_router(chat.router, prefix="/chat")
api_router.include_router(leads.router, prefix="/leads")
api_router.include_router(escalations.router, prefix="/escalations")
api_router.include_router(costs.router, prefix="/costs")
# Public widget runtime surface (Spec 011). Aliases the session + chat
# handlers under ``/public/*`` and adds ``GET /public/widgets/config``.
# The original ``/widgets/session`` and ``/chat`` routes are intentionally
# retained as backward-compatible duplicates.
api_router.include_router(public.router, prefix="/public")
# Public tenant website — unauthenticated, slug-based, HTML + optional JSON.
api_router.include_router(public_site.router, prefix="/sites")
# Embeddable widget bundle — single ``GET /widget.js`` static asset honored by
# the Admin panel's embed snippet. No prefix; mounted at the root.
api_router.include_router(widget_asset.router)
