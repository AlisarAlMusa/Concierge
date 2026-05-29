"""PublicSiteService — assembles PublicSiteContext for a given tenant slug.

Raises HTTP exceptions directly so the route handler stays thin. The service
never accepts tenant_id from a caller — it resolves it from the slug itself.
"""

from __future__ import annotations

import structlog
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tenant import TenantStatus
from app.repositories import public_site_repository as repo
from app.schemas.public_site import (
    PublicCmsSection,
    PublicSiteContext,
    PublicTenantConfig,
    PublicWidgetInfo,
)

log = structlog.get_logger(__name__)

_DEFAULT_THEME_COLOR = "#1f2937"


async def get_site_context(session: AsyncSession, slug: str) -> PublicSiteContext:
    tenant = await repo.get_tenant_by_slug(session, slug)

    if tenant is None:
        log.info("public_site.tenant_not_found", slug=slug)
        raise HTTPException(status_code=404, detail="Tenant not found")

    if tenant.status == TenantStatus.suspended:
        log.info("public_site.tenant_suspended", slug=slug, tenant_id=str(tenant.id))
        raise HTTPException(status_code=403, detail="This site is temporarily unavailable")

    config_row, cms_pages, widget_row = await repo.get_site_data(session, tenant.id)

    log.info(
        "public_site.tenant_resolved",
        slug=slug,
        tenant_id=str(tenant.id),
        page_count=len(cms_pages),
        has_widget=widget_row is not None,
    )

    config = PublicTenantConfig(
        brand_name=config_row.brand_name if config_row and config_row.brand_name else tenant.name,
        theme_color=(
            config_row.theme_color
            if config_row and config_row.theme_color
            else _DEFAULT_THEME_COLOR
        ),
        greeting=config_row.greeting if config_row else None,
        public_description=config_row.public_description if config_row else None,
        contact_email=config_row.contact_email if config_row else None,
    )

    pages = [PublicCmsSection(title=p.title, body=p.body) for p in cms_pages]

    widget = PublicWidgetInfo(widget_id=widget_row.public_widget_id) if widget_row else None

    return PublicSiteContext(
        tenant_name=tenant.name,
        tenant_slug=tenant.slug,
        config=config,
        pages=pages,
        widget=widget,
    )
