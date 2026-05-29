"""Integration tests for public tenant website — requires live PostgreSQL.

Run with:
    RUN_INTEGRATION=1 uv run pytest tests/integration/test_public_site.py -v

Verifies:
- /sites/{slug} returns 200 HTML with correct tenant content
- Tenant isolation: abc-gym page contains no green-clinic content and vice versa
- Draft pages are never shown
- Unknown slug returns 404
- Suspended tenant returns 403
"""

from __future__ import annotations

import os
import uuid

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.cms import CmsPage, CmsPageStatus
from app.models.tenant import Tenant, TenantStatus
from app.models.tenant_config import TenantConfig
from app.models.widget import Widget

pytestmark = pytest.mark.integration

_RUN = os.getenv("RUN_INTEGRATION", "").lower() in ("1", "true", "yes")


def _skip_if_no_db():
    if not _RUN:
        pytest.skip("Set RUN_INTEGRATION=1 to run public-site integration tests")


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest_asyncio.fixture()
async def abc_gym_tenant(db_session: AsyncSession) -> Tenant:
    t = Tenant(id=uuid.uuid4(), name="ABC Gym", slug="abc-gym-test", status=TenantStatus.active)
    db_session.add(t)
    await db_session.flush()
    cfg = TenantConfig(
        tenant_id=t.id,
        brand_name="ABC Gym",
        public_description="A fitness center.",
        contact_email="hello@abcgym.example",
    )
    db_session.add(cfg)
    p1 = CmsPage(
        id=uuid.uuid4(), tenant_id=t.id, title="Opening Hours",
        slug="opening-hours", body="8 AM to 10 PM.", status=CmsPageStatus.published,
    )
    p2 = CmsPage(
        id=uuid.uuid4(), tenant_id=t.id, title="ABC Draft",
        slug="abc-draft", body="Should not appear.", status=CmsPageStatus.draft,
    )
    w = Widget(
        id=uuid.uuid4(), tenant_id=t.id, public_widget_id="pub_wid_abc_test",
        name="default", allowed_origins=[], theme={}, greeting="Hi!", enabled=True,
    )
    db_session.add_all([p1, p2, w])
    await db_session.flush()
    return t


@pytest_asyncio.fixture()
async def green_clinic_tenant(db_session: AsyncSession) -> Tenant:
    t = Tenant(
        id=uuid.uuid4(), name="Green Clinic", slug="green-clinic-test", status=TenantStatus.active
    )
    db_session.add(t)
    await db_session.flush()
    p1 = CmsPage(
        id=uuid.uuid4(), tenant_id=t.id, title="Our Services",
        slug="services", body="Pediatrics and physiotherapy.", status=CmsPageStatus.published,
    )
    db_session.add(p1)
    await db_session.flush()
    return t


@pytest_asyncio.fixture()
async def suspended_tenant(db_session: AsyncSession) -> Tenant:
    t = Tenant(
        id=uuid.uuid4(), name="Suspended Co", slug="suspended-co-test",
        status=TenantStatus.suspended,
    )
    db_session.add(t)
    await db_session.flush()
    return t


# ── Tests ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_public_page_200(http_client: AsyncClient, abc_gym_tenant: Tenant):
    _skip_if_no_db()
    resp = await http_client.get(f"/sites/{abc_gym_tenant.slug}")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "ABC Gym" in resp.text
    assert "Opening Hours" in resp.text
    assert "8 AM to 10 PM" in resp.text
    assert "pub_wid_abc_test" in resp.text


@pytest.mark.asyncio
async def test_draft_page_not_shown(http_client: AsyncClient, abc_gym_tenant: Tenant):
    _skip_if_no_db()
    resp = await http_client.get(f"/sites/{abc_gym_tenant.slug}")
    assert resp.status_code == 200
    assert "ABC Draft" not in resp.text
    assert "Should not appear" not in resp.text


@pytest.mark.asyncio
async def test_tenant_isolation_abc_gym(
    http_client: AsyncClient,
    abc_gym_tenant: Tenant,
    green_clinic_tenant: Tenant,
):
    _skip_if_no_db()
    resp = await http_client.get(f"/sites/{abc_gym_tenant.slug}")
    assert resp.status_code == 200
    assert "Our Services" not in resp.text
    assert "Pediatrics" not in resp.text
    assert "Green Clinic" not in resp.text


@pytest.mark.asyncio
async def test_tenant_isolation_green_clinic(
    http_client: AsyncClient,
    abc_gym_tenant: Tenant,
    green_clinic_tenant: Tenant,
):
    _skip_if_no_db()
    resp = await http_client.get(f"/sites/{green_clinic_tenant.slug}")
    assert resp.status_code == 200
    assert "Opening Hours" not in resp.text
    assert "ABC Gym" not in resp.text
    assert "pub_wid_abc_test" not in resp.text


@pytest.mark.asyncio
async def test_unknown_slug_returns_404(http_client: AsyncClient):
    _skip_if_no_db()
    resp = await http_client.get("/sites/this-tenant-does-not-exist")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_suspended_tenant_returns_403(
    http_client: AsyncClient, suspended_tenant: Tenant
):
    _skip_if_no_db()
    resp = await http_client.get(f"/sites/{suspended_tenant.slug}")
    assert resp.status_code == 403
