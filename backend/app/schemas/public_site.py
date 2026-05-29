from __future__ import annotations

from pydantic import BaseModel


class PublicCmsSection(BaseModel):
    title: str
    body: str


class PublicTenantConfig(BaseModel):
    brand_name: str
    theme_color: str
    greeting: str | None
    public_description: str | None
    contact_email: str | None


class PublicWidgetInfo(BaseModel):
    widget_id: str


class PublicSiteContext(BaseModel):
    tenant_name: str
    tenant_slug: str
    config: PublicTenantConfig
    pages: list[PublicCmsSection]
    widget: PublicWidgetInfo | None
