"""Unit tests for PublicSiteService.

Mocks the repository layer so these tests run without a database.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.models.cms import CmsPage, CmsPageStatus
from app.models.tenant import Tenant, TenantStatus
from app.models.tenant_config import TenantConfig
from app.models.widget import Widget
from app.services.public_site_service import get_site_context


def _make_tenant(slug: str = "demo-tenant", status: TenantStatus = TenantStatus.active) -> Tenant:
    t = MagicMock(spec=Tenant)
    t.id = uuid4()
    t.name = slug.replace("-", " ").title()
    t.slug = slug
    t.status = status
    return t


def _make_config(tenant_id, **kwargs) -> TenantConfig:
    cfg = MagicMock(spec=TenantConfig)
    cfg.tenant_id = tenant_id
    cfg.brand_name = kwargs.get("brand_name", "Brand")
    cfg.theme_color = kwargs.get("theme_color", "#111827")
    cfg.greeting = kwargs.get("greeting", None)
    cfg.public_description = kwargs.get("public_description", "A description.")
    cfg.contact_email = kwargs.get("contact_email", "hello@example.com")
    return cfg


def _make_page(tenant_id, title: str = "Opening Hours", body: str = "8am–10pm") -> CmsPage:
    p = MagicMock(spec=CmsPage)
    p.id = uuid4()
    p.tenant_id = tenant_id
    p.title = title
    p.body = body
    p.status = CmsPageStatus.published
    return p


def _make_widget(tenant_id, public_widget_id: str = "pub_wid_abc123") -> Widget:
    w = MagicMock(spec=Widget)
    w.id = uuid4()
    w.tenant_id = tenant_id
    w.public_widget_id = public_widget_id
    w.enabled = True
    return w


@pytest.fixture()
def session():
    return AsyncMock()


@pytest.mark.asyncio
async def test_unknown_slug_returns_404(session):
    with patch("app.services.public_site_service.repo") as mock_repo:
        mock_repo.get_tenant_by_slug = AsyncMock(return_value=None)
        with pytest.raises(HTTPException) as exc_info:
            await get_site_context(session, "unknown-slug")
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_suspended_tenant_returns_403(session):
    tenant = _make_tenant(status=TenantStatus.suspended)
    with patch("app.services.public_site_service.repo") as mock_repo:
        mock_repo.get_tenant_by_slug = AsyncMock(return_value=tenant)
        with pytest.raises(HTTPException) as exc_info:
            await get_site_context(session, "demo-tenant")
    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_active_tenant_returns_context(session):
    tenant = _make_tenant()
    cfg = _make_config(tenant.id)
    page = _make_page(tenant.id)
    widget = _make_widget(tenant.id)

    with patch("app.services.public_site_service.repo") as mock_repo:
        mock_repo.get_tenant_by_slug = AsyncMock(return_value=tenant)
        mock_repo.get_site_data = AsyncMock(return_value=(cfg, [page], widget))
        ctx = await get_site_context(session, "demo-tenant")

    assert ctx.tenant_name == tenant.name
    assert ctx.tenant_slug == "demo-tenant"
    assert ctx.config.brand_name == cfg.brand_name
    assert ctx.config.contact_email == cfg.contact_email
    assert len(ctx.pages) == 1
    assert ctx.pages[0].title == page.title
    assert ctx.pages[0].body == page.body
    assert ctx.widget is not None
    assert ctx.widget.widget_id == widget.public_widget_id


@pytest.mark.asyncio
async def test_missing_config_uses_fallback_defaults(session):
    tenant = _make_tenant()

    with patch("app.services.public_site_service.repo") as mock_repo:
        mock_repo.get_tenant_by_slug = AsyncMock(return_value=tenant)
        mock_repo.get_site_data = AsyncMock(return_value=(None, [], None))
        ctx = await get_site_context(session, "demo-tenant")

    assert ctx.config.brand_name == tenant.name
    assert ctx.config.theme_color == "#1f2937"
    assert ctx.config.greeting is None
    assert ctx.config.public_description is None
    assert ctx.config.contact_email is None


@pytest.mark.asyncio
async def test_no_widget_produces_none_widget_field(session):
    tenant = _make_tenant()
    cfg = _make_config(tenant.id)

    with patch("app.services.public_site_service.repo") as mock_repo:
        mock_repo.get_tenant_by_slug = AsyncMock(return_value=tenant)
        mock_repo.get_site_data = AsyncMock(return_value=(cfg, [], None))
        ctx = await get_site_context(session, "demo-tenant")

    assert ctx.widget is None


@pytest.mark.asyncio
async def test_draft_pages_not_included(session):
    """Service receives only published pages from repo (repo filters; service trusts it)."""
    tenant = _make_tenant()
    cfg = _make_config(tenant.id)
    published = _make_page(tenant.id, title="Published Page")

    with patch("app.services.public_site_service.repo") as mock_repo:
        mock_repo.get_tenant_by_slug = AsyncMock(return_value=tenant)
        mock_repo.get_site_data = AsyncMock(return_value=(cfg, [published], None))
        ctx = await get_site_context(session, "demo-tenant")

    assert len(ctx.pages) == 1
    assert ctx.pages[0].title == "Published Page"
