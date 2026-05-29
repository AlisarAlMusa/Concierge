"""Public tenant website route.

GET /sites/{tenant_slug} — returns a server-rendered HTML page for the tenant.

tenant_id is NEVER accepted from the request. It is resolved from tenant_slug
inside the service layer. CORS is not used as authentication.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db_session
from app.schemas.public_site import PublicSiteContext
from app.services import public_site_service

_TEMPLATES_DIR = Path(__file__).parent.parent.parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

router = APIRouter(tags=["public_site"])


@router.get(
    "/{tenant_slug}",
    response_class=HTMLResponse,
    summary="Public tenant website page",
)
async def get_public_site(
    request: Request,
    tenant_slug: str,
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    """Render the public-facing page for a tenant.

    Resolves tenant from slug only — never from request body or query params.
    Returns 404 for unknown slugs, 403 for suspended tenants.
    Only published CMS content is shown.
    """
    ctx: PublicSiteContext = await public_site_service.get_site_context(session, tenant_slug)
    return templates.TemplateResponse(
        request=request,
        name="public_site.html",
        context={"ctx": ctx},
    )


@router.get(
    "/api/{tenant_slug}",
    response_model=PublicSiteContext,
    summary="Public tenant site data (JSON)",
)
async def get_public_site_json(
    tenant_slug: str,
    session: AsyncSession = Depends(get_db_session),
) -> PublicSiteContext:
    """Return the same data as the HTML page but as JSON.

    Useful for React frontends or external integrations.
    """
    return await public_site_service.get_site_context(session, tenant_slug)
