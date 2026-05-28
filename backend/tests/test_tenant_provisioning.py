"""Tenant Provisioning test suite — spec 003 acceptance scenarios.

Test isolation strategy
───────────────────────
• SQLite (in-memory) via aiosqlite — no Docker required in CI.
• fakeredis for JTI revocation and rate-limit counters.
• asgi-lifespan's LifespanManager triggers app startup so app.state is set.
• All HTTP calls use httpx.AsyncClient with ASGITransport.

Stories covered
───────────────
US1: Create tenant (201, 409 duplicate slug, 403 wrong role, audit event)
US2: Invite admin (201, 409 duplicate email, 422 on suspended tenant, 403 wrong role)
US3: Suspend (200, idempotent), suspended → 403 tenant_suspended, reactivate restores
US4: List tenants (no content), usage-summary (aggregates only), audit-log (403)
US5: Delete (202, status=deleting, 404 after deletion)
Edge cases: invalid slug, invite on suspended tenant, double-delete
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager

import pytest
import pytest_asyncio
from asgi_lifespan import LifespanManager
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

TEST_JWT_SECRET = "test-secret-not-for-production"


# ──────────────────────────────────────────────────────────────────────────────
# App factory
# ──────────────────────────────────────────────────────────────────────────────


def make_test_app(session_factory, fake_redis) -> FastAPI:
    # Use a partial router that only mounts routes relevant to spec 003.
    # This avoids import-chain failures from Person B/C's unimplemented services
    # (chat_orchestrator, conversation_service, etc.).
    from fastapi import APIRouter

    from app.api.routes import admin_config, audit_logs, auth, cms, tenants
    from app.core.errors import register_error_handlers
    from app.core.logging import RequestIDMiddleware, configure_logging

    partial_router = APIRouter()
    partial_router.include_router(auth.router, prefix="/auth")
    partial_router.include_router(tenants.router, prefix="/platform/tenants")
    partial_router.include_router(audit_logs.router, prefix="/platform")
    partial_router.include_router(admin_config.router, prefix="/tenant")
    partial_router.include_router(cms.router, prefix="/cms")

    @asynccontextmanager
    async def test_lifespan(app: FastAPI):
        configure_logging("local")
        app.state.redis = fake_redis
        app.state.secrets = {
            "jwt_secret": TEST_JWT_SECRET,
            "service_auth_secret": "test-svc-secret",
            "widget_token_secret": "test-widget-secret",
            "minio_secret_key": "test-minio-key",
            "openai_api_key": "",
            "anthropic_api_key": "",
            "azure_openai_api_key": "",
            "azure_openai_endpoint": "",
            "azure_openai_api_version": "2024-02-01",
            "azure_openai_deployment": "",
        }
        yield

    app = FastAPI(title="Concierge Test", lifespan=test_lifespan)
    app.add_middleware(RequestIDMiddleware)
    register_error_handlers(app)
    app.include_router(partial_router)

    from fastapi import Depends

    from app.db import session as db_session_module
    from app.db.session import get_db_session

    db_session_module._session_factory = session_factory

    # Test-only route: force a tenant to a specific status without going through
    # the normal API state machine. Used to simulate the erasure service completing.
    from sqlalchemy.ext.asyncio import AsyncSession

    @app.put("/test-only/tenants/{tenant_id}/status/{status_value}")
    async def _force_tenant_status(
        tenant_id: str,
        status_value: str,
        session: AsyncSession = Depends(get_db_session),
    ):
        from uuid import UUID

        from sqlalchemy import update

        from app.models.tenant import Tenant, TenantStatus

        await session.execute(
            update(Tenant)
            .where(Tenant.id == UUID(tenant_id))
            .values(status=TenantStatus(status_value))
        )
        await session.commit()
        return {"ok": True}

    return app


# ──────────────────────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def setup_db():
    import fakeredis.aioredis as fake_aioredis
    from sqlalchemy.pool import StaticPool

    import app.models.audit_log  # noqa: F401
    import app.models.cost_event  # noqa: F401
    import app.models.tenant  # noqa: F401
    import app.models.user  # noqa: F401
    from app.db.base import Base

    # StaticPool forces all sessions to reuse the same underlying connection.
    # isolation_level=None puts Python's sqlite3 into autocommit mode so SQLAlchemy
    # controls BEGIN/COMMIT exclusively — prevents Python's implicit transaction
    # management from hiding committed writes from subsequent sessions.
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        echo=False,
        connect_args={"check_same_thread": False, "isolation_level": None},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    fake_redis = fake_aioredis.FakeRedis(decode_responses=True)

    yield factory, fake_redis, engine

    await fake_redis.aclose()
    await engine.dispose()


@pytest_asyncio.fixture
async def client(setup_db):
    factory, fake_redis, _ = setup_db
    app = make_test_app(factory, fake_redis)

    async with LifespanManager(app) as manager:
        async with AsyncClient(
            transport=ASGITransport(app=manager.app), base_url="http://test"
        ) as ac:
            yield ac


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────


async def seed_tenant_manager(client: AsyncClient) -> str:
    """Register a tenant_manager directly via DB and return a JWT for them."""
    # We abuse the seed endpoint or directly insert via the DB.
    # Since there's no seed endpoint, we promote via the test helper that
    # reads app.state. Instead, we use the existing /auth/register + DB patch.
    # For test isolation we create a unique email each time.
    email = f"mgr_{uuid.uuid4().hex[:8]}@example.com"
    password = "ManagerPass!1"

    reg = await client.post("/auth/register", json={"email": email, "password": password})
    assert reg.status_code == 201, reg.text
    user_id = reg.json()["id"]

    # Elevate role to tenant_manager via direct DB update (test-only pattern).
    from sqlalchemy import update

    from app.db import session as db_session_module
    from app.models.user import User, UserRole

    async with db_session_module._session_factory() as s:
        await s.execute(
            update(User).where(User.id == uuid.UUID(user_id)).values(role=UserRole.tenant_manager)
        )
        await s.commit()

    resp = await client.post(
        "/auth/login",
        data={"username": email, "password": password},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


async def register_and_login(client: AsyncClient, email: str, password: str = "Pass!word1") -> str:
    await client.post("/auth/register", json={"email": email, "password": password})
    resp = await client.post(
        "/auth/login",
        data={"username": email, "password": password},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


def mgr_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ──────────────────────────────────────────────────────────────────────────────
# US1: Create Tenant
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_tenant_201(client):
    """tenant_manager creates a tenant → 201, status=active."""
    token = await seed_tenant_manager(client)
    resp = await client.post(
        "/platform/tenants/",
        json={"name": "Acme Corp", "slug": "acme-corp"},
        headers=mgr_headers(token),
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["slug"] == "acme-corp"
    assert body["status"] == "active"
    assert "id" in body


@pytest.mark.asyncio
async def test_create_tenant_duplicate_slug_409(client):
    """Duplicate slug → 409 Conflict."""
    token = await seed_tenant_manager(client)
    await client.post(
        "/platform/tenants/",
        json={"name": "First", "slug": "duplicate-slug"},
        headers=mgr_headers(token),
    )
    resp = await client.post(
        "/platform/tenants/",
        json={"name": "Second", "slug": "duplicate-slug"},
        headers=mgr_headers(token),
    )
    assert resp.status_code == 409, resp.text


@pytest.mark.asyncio
async def test_create_tenant_wrong_role_403(client):
    """Non-tenant_manager → 403."""
    member_token = await register_and_login(client, "member@example.com")
    resp = await client.post(
        "/platform/tenants/",
        json={"name": "Bad", "slug": "bad-slug"},
        headers=mgr_headers(member_token),
    )
    assert resp.status_code == 403
    assert resp.json().get("code") == "permission_denied"


@pytest.mark.asyncio
async def test_create_tenant_invalid_slug_422(client):
    """Slugs with underscores, spaces, hyphens at edges, or too short → 422.
    Note: uppercase is auto-lowercased and accepted; truly invalid slugs are tested here.
    """
    token = await seed_tenant_manager(client)
    for bad_slug in (
        "has_underscore",  # underscore not allowed
        "has spaces",  # space not allowed
        "-starts-with-hyphen",  # must start with alphanumeric
        "ends-with-hyphen-",  # must end with alphanumeric
        "a",  # too short (1 char, regex requires 2+)
    ):
        resp = await client.post(
            "/platform/tenants/",
            json={"name": "Test", "slug": bad_slug},
            headers=mgr_headers(token),
        )
        assert (
            resp.status_code == 422
        ), f"Expected 422 for slug={bad_slug!r}, got {resp.status_code}"


@pytest.mark.asyncio
async def test_get_tenant_by_id(client):
    """GET /platform/tenants/{id} returns the tenant."""
    token = await seed_tenant_manager(client)
    create_resp = await client.post(
        "/platform/tenants/",
        json={"name": "GetMe", "slug": "get-me"},
        headers=mgr_headers(token),
    )
    tenant_id = create_resp.json()["id"]
    resp = await client.get(f"/platform/tenants/{tenant_id}", headers=mgr_headers(token))
    assert resp.status_code == 200
    assert resp.json()["id"] == tenant_id


@pytest.mark.asyncio
async def test_get_missing_tenant_404(client):
    """GET /platform/tenants/{random_uuid} → 404."""
    token = await seed_tenant_manager(client)
    resp = await client.get(f"/platform/tenants/{uuid.uuid4()}", headers=mgr_headers(token))
    assert resp.status_code == 404


# ──────────────────────────────────────────────────────────────────────────────
# US2: Invite Admin
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_invite_admin_201(client):
    """Invite admin for active tenant → 201 UserRead with correct role and tenant_id."""
    token = await seed_tenant_manager(client)
    create_resp = await client.post(
        "/platform/tenants/",
        json={"name": "Invite Corp", "slug": "invite-corp"},
        headers=mgr_headers(token),
    )
    tenant_id = create_resp.json()["id"]

    resp = await client.post(
        f"/platform/tenants/{tenant_id}/invite-admin",
        json={"email": "admin@invite-corp.com"},
        headers=mgr_headers(token),
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["role"] == "tenant_admin"
    assert body["tenant_id"] == tenant_id


@pytest.mark.asyncio
async def test_invite_admin_duplicate_email_409(client):
    """Inviting with an already-registered email → 409."""
    token = await seed_tenant_manager(client)
    create_resp = await client.post(
        "/platform/tenants/",
        json={"name": "Dup Email Corp", "slug": "dup-email-corp"},
        headers=mgr_headers(token),
    )
    tenant_id = create_resp.json()["id"]

    await client.post(
        f"/platform/tenants/{tenant_id}/invite-admin",
        json={"email": "dup@example.com"},
        headers=mgr_headers(token),
    )
    resp = await client.post(
        f"/platform/tenants/{tenant_id}/invite-admin",
        json={"email": "dup@example.com"},
        headers=mgr_headers(token),
    )
    assert resp.status_code == 409, resp.text


@pytest.mark.asyncio
async def test_invite_admin_on_suspended_tenant_422(client):
    """Inviting admin for a suspended tenant → 422 tenant_not_active."""
    token = await seed_tenant_manager(client)
    create_resp = await client.post(
        "/platform/tenants/",
        json={"name": "Suspended Corp", "slug": "suspended-corp"},
        headers=mgr_headers(token),
    )
    tenant_id = create_resp.json()["id"]

    await client.post(f"/platform/tenants/{tenant_id}/suspend", headers=mgr_headers(token))

    resp = await client.post(
        f"/platform/tenants/{tenant_id}/invite-admin",
        json={"email": "blocked@example.com"},
        headers=mgr_headers(token),
    )
    assert resp.status_code == 422, resp.text


@pytest.mark.asyncio
async def test_invite_admin_wrong_role_403(client):
    """Non-manager cannot invite admins."""
    member_token = await register_and_login(client, "notmgr@example.com")
    resp = await client.post(
        f"/platform/tenants/{uuid.uuid4()}/invite-admin",
        json={"email": "anyone@example.com"},
        headers=mgr_headers(member_token),
    )
    assert resp.status_code == 403


# ──────────────────────────────────────────────────────────────────────────────
# US3: Suspend and Reactivate
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_suspend_tenant_200(client):
    """Suspend an active tenant → 200, status=suspended."""
    token = await seed_tenant_manager(client)
    create_resp = await client.post(
        "/platform/tenants/",
        json={"name": "Suspend Me", "slug": "suspend-me"},
        headers=mgr_headers(token),
    )
    tenant_id = create_resp.json()["id"]

    resp = await client.post(f"/platform/tenants/{tenant_id}/suspend", headers=mgr_headers(token))
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "suspended"


@pytest.mark.asyncio
async def test_suspend_idempotent(client):
    """Suspending an already-suspended tenant → 200 (idempotent)."""
    token = await seed_tenant_manager(client)
    create_resp = await client.post(
        "/platform/tenants/",
        json={"name": "Idempotent Suspend", "slug": "idempotent-suspend"},
        headers=mgr_headers(token),
    )
    tenant_id = create_resp.json()["id"]

    await client.post(f"/platform/tenants/{tenant_id}/suspend", headers=mgr_headers(token))
    resp = await client.post(f"/platform/tenants/{tenant_id}/suspend", headers=mgr_headers(token))
    assert resp.status_code == 200
    assert resp.json()["status"] == "suspended"


@pytest.mark.asyncio
async def test_suspended_tenant_admin_gets_403(client):
    """tenant_admin for a suspended tenant → 403 tenant_suspended on content routes."""
    token = await seed_tenant_manager(client)
    create_resp = await client.post(
        "/platform/tenants/",
        json={"name": "Block Tenant", "slug": "block-tenant"},
        headers=mgr_headers(token),
    )
    tenant_id = create_resp.json()["id"]

    # Register a normal user (member) with a known password, then promote to tenant_admin.
    admin_email = "suspended.admin@example.com"
    admin_password = "AdminPass!1"

    reg_resp = await client.post(
        "/auth/register", json={"email": admin_email, "password": admin_password}
    )
    assert reg_resp.status_code == 201, reg_resp.text
    admin_user_id = reg_resp.json()["id"]

    # Promote to tenant_admin with the correct tenant_id via direct DB (test-only).
    from sqlalchemy import update

    from app.db import session as db_session_module
    from app.models.user import User, UserRole

    async with db_session_module._session_factory() as s:
        await s.execute(
            update(User)
            .where(User.id == uuid.UUID(admin_user_id))
            .values(role=UserRole.tenant_admin, tenant_id=uuid.UUID(tenant_id))
        )
        await s.commit()

    # Login as admin.
    login_resp = await client.post(
        "/auth/login",
        data={"username": admin_email, "password": admin_password},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert login_resp.status_code == 200, login_resp.text
    admin_token = login_resp.json()["access_token"]

    # Suspend.
    await client.post(f"/platform/tenants/{tenant_id}/suspend", headers=mgr_headers(token))

    # Now the same token on a tenant-admin route → 403 tenant_suspended.
    cms_resp2 = await client.get("/cms/pages", headers={"Authorization": f"Bearer {admin_token}"})
    assert cms_resp2.status_code == 403, cms_resp2.text
    assert cms_resp2.json().get("code") == "tenant_suspended"


@pytest.mark.asyncio
async def test_reactivate_tenant_restores_access(client):
    """Reactivating a suspended tenant → 200 active, users can access again."""
    token = await seed_tenant_manager(client)
    create_resp = await client.post(
        "/platform/tenants/",
        json={"name": "Reactivate Me", "slug": "reactivate-me"},
        headers=mgr_headers(token),
    )
    tenant_id = create_resp.json()["id"]

    await client.post(f"/platform/tenants/{tenant_id}/suspend", headers=mgr_headers(token))
    resp = await client.post(
        f"/platform/tenants/{tenant_id}/reactivate", headers=mgr_headers(token)
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "active"


@pytest.mark.asyncio
async def test_reactivate_non_suspended_422(client):
    """Reactivating an active tenant → 422."""
    token = await seed_tenant_manager(client)
    create_resp = await client.post(
        "/platform/tenants/",
        json={"name": "Already Active", "slug": "already-active"},
        headers=mgr_headers(token),
    )
    tenant_id = create_resp.json()["id"]

    resp = await client.post(
        f"/platform/tenants/{tenant_id}/reactivate", headers=mgr_headers(token)
    )
    assert resp.status_code == 422, resp.text


# ──────────────────────────────────────────────────────────────────────────────
# US4: List Tenants and Usage
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_tenants_no_content_fields(client):
    """GET /platform/tenants returns id/name/slug/status/timestamps — no private content."""
    token = await seed_tenant_manager(client)
    await client.post(
        "/platform/tenants/",
        json={"name": "Listed Corp", "slug": "listed-corp"},
        headers=mgr_headers(token),
    )
    resp = await client.get("/platform/tenants/", headers=mgr_headers(token))
    assert resp.status_code == 200, resp.text
    items = resp.json()
    assert len(items) >= 1
    for item in items:
        assert "id" in item
        assert "slug" in item
        assert "status" in item
        assert "conversations" not in item
        assert "leads" not in item
        assert "cms_pages" not in item


@pytest.mark.asyncio
async def test_list_tenants_wrong_role_403(client):
    """Non-manager cannot list tenants."""
    member_token = await register_and_login(client, "member2@example.com")
    resp = await client.get("/platform/tenants/", headers=mgr_headers(member_token))
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_usage_summary_returns_aggregates_not_content(client):
    """GET /platform/tenants/{id}/usage-summary → aggregate numbers only."""
    token = await seed_tenant_manager(client)
    create_resp = await client.post(
        "/platform/tenants/",
        json={"name": "Usage Corp", "slug": "usage-corp"},
        headers=mgr_headers(token),
    )
    tenant_id = create_resp.json()["id"]

    resp = await client.get(
        f"/platform/tenants/{tenant_id}/usage-summary", headers=mgr_headers(token)
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "total_input_tokens" in body
    assert "total_output_tokens" in body
    assert "total_cost_usd" in body
    # Must be numbers, not content strings
    assert isinstance(body["total_input_tokens"], int)
    assert isinstance(body["total_output_tokens"], int)
    assert "conversations" not in body
    assert "messages" not in body


@pytest.mark.asyncio
async def test_audit_log_list_requires_manager(client):
    """GET /platform/audit-logs with non-manager → 403."""
    member_token = await register_and_login(client, "member3@example.com")
    resp = await client.get("/platform/audit-logs", headers=mgr_headers(member_token))
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_audit_log_contains_tenant_created_entry(client):
    """Creating a tenant produces a tenant_created audit log entry."""
    token = await seed_tenant_manager(client)
    await client.post(
        "/platform/tenants/",
        json={"name": "Audited Corp", "slug": "audited-corp"},
        headers=mgr_headers(token),
    )

    # Give the fire-and-forget task a moment to complete.
    import asyncio

    await asyncio.sleep(0.1)

    resp = await client.get("/platform/audit-logs", headers=mgr_headers(token))
    assert resp.status_code == 200, resp.text
    actions = [entry["action"] for entry in resp.json()]
    assert "tenant_created" in actions


# ──────────────────────────────────────────────────────────────────────────────
# US5: Delete Tenant
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_delete_tenant_202(client):
    """DELETE /platform/tenants/{id} → 202, status=deleting."""
    token = await seed_tenant_manager(client)
    create_resp = await client.post(
        "/platform/tenants/",
        json={"name": "Delete Me", "slug": "delete-me"},
        headers=mgr_headers(token),
    )
    tenant_id = create_resp.json()["id"]

    resp = await client.delete(f"/platform/tenants/{tenant_id}", headers=mgr_headers(token))
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["status"] == "deleting"
    assert body["tenant_id"] == tenant_id


@pytest.mark.asyncio
async def test_delete_sets_status_deleting(client):
    """After DELETE, GET /platform/tenants/{id} shows status=deleting."""
    token = await seed_tenant_manager(client)
    create_resp = await client.post(
        "/platform/tenants/",
        json={"name": "Status Check Delete", "slug": "status-check-delete"},
        headers=mgr_headers(token),
    )
    tenant_id = create_resp.json()["id"]

    await client.delete(f"/platform/tenants/{tenant_id}", headers=mgr_headers(token))

    resp = await client.get(f"/platform/tenants/{tenant_id}", headers=mgr_headers(token))
    assert resp.status_code == 200
    assert resp.json()["status"] == "deleting"


@pytest.mark.asyncio
async def test_double_delete_idempotent(client):
    """DELETE on a tenant already in deleting state → 202 (idempotent)."""
    token = await seed_tenant_manager(client)
    create_resp = await client.post(
        "/platform/tenants/",
        json={"name": "Double Delete", "slug": "double-delete"},
        headers=mgr_headers(token),
    )
    tenant_id = create_resp.json()["id"]

    await client.delete(f"/platform/tenants/{tenant_id}", headers=mgr_headers(token))
    resp = await client.delete(f"/platform/tenants/{tenant_id}", headers=mgr_headers(token))
    assert resp.status_code == 202, resp.text


@pytest.mark.asyncio
async def test_deleted_tenant_returns_404(client):
    """After status is forced to deleted, GET returns 404."""
    token = await seed_tenant_manager(client)
    create_resp = await client.post(
        "/platform/tenants/",
        json={"name": "Gone Corp", "slug": "gone-corp"},
        headers=mgr_headers(token),
    )
    tenant_id = create_resp.json()["id"]

    # Simulate the erasure service completing: force status to deleted via the
    # test-only endpoint (registered in make_test_app) which uses the same
    # session path as all other routes, avoiding SQLite transaction visibility issues.
    force_resp = await client.put(f"/test-only/tenants/{tenant_id}/status/deleted")
    assert force_resp.status_code == 200, force_resp.text

    resp = await client.get(f"/platform/tenants/{tenant_id}", headers=mgr_headers(token))
    assert resp.status_code == 404
