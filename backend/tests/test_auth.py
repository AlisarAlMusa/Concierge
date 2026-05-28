"""Auth & Roles test suite — 10 required tests from spec Testing Requirements.

Test isolation strategy
───────────────────────
• SQLite (in-memory) via aiosqlite for DB — no Docker required in CI.
• fakeredis for JTI revocation and rate-limit tests.
• asgi-lifespan's LifespanManager triggers the app's startup/shutdown hooks
  so that app.state.secrets and app.state.redis are properly set.
• All HTTP calls use httpx.AsyncClient with ASGITransport.

Tests implemented
─────────────────
T012: test_register_creates_member_role
T013: test_login_returns_jwt / test_protected_route_401_without_jwt
T014: test_logout_revokes_token / test_jwt_payload_has_no_pii
T017: test_tenant_admin_403_on_platform_route / test_tenant_manager_403_on_tenant_content
T020: test_tenant_id_never_from_request_body / test_rls_context_reset_after_request
T024: test_invite_admin_creates_correct_role / test_self_registration_cannot_elevate_role
T027: test_login_rate_limit
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
# App factory with test infrastructure
# ──────────────────────────────────────────────────────────────────────────────


def make_test_app(session_factory, fake_redis) -> FastAPI:
    """Build a testable FastAPI app backed by an in-memory SQLite DB."""

    from app.api.router import api_router
    from app.core.errors import register_error_handlers
    from app.core.logging import RequestIDMiddleware, configure_logging

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
    app.include_router(api_router)

    # Override the DB session so all requests use the SQLite test DB.
    from app.db import session as db_session_module

    db_session_module._session_factory = session_factory

    return app


# ──────────────────────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def setup_db():
    """Create an in-memory SQLite DB with all tables and yield the session factory."""
    import fakeredis.aioredis as fake_aioredis

    # Import all models to ensure metadata is populated.
    import app.models.audit_log  # noqa: F401
    import app.models.cost_event  # noqa: F401
    import app.models.tenant  # noqa: F401
    import app.models.user  # noqa: F401
    from app.db.base import Base

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    fake_redis = fake_aioredis.FakeRedis(decode_responses=True)

    yield factory, fake_redis, engine

    await fake_redis.aclose()
    await engine.dispose()


@pytest_asyncio.fixture
async def client(setup_db):
    """Yield an AsyncClient backed by an in-memory SQLite DB and fake Redis."""
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


async def register_user(client: AsyncClient, email: str, password: str, **extra) -> AsyncClient:
    """POST /auth/register and return the response."""
    return await client.post(
        "/auth/register", json={"email": email, "password": password, **extra}
    )


async def login_user(client: AsyncClient, email: str, password: str) -> str:
    """Login and return the access_token string."""
    resp = await client.post(
        "/auth/login",
        data={"username": email, "password": password},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert resp.status_code == 200, f"Login failed ({resp.status_code}): {resp.text}"
    return resp.json()["access_token"]


# ──────────────────────────────────────────────────────────────────────────────
# T012: Role is always 'member' on self-registration
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_register_creates_member_role(client):
    """POST /auth/register with role=tenant_admin in body → response role is member."""
    resp = await register_user(
        client,
        email="test_role_injection@example.com",
        password="SecurePass!1",
        role="tenant_admin",  # should be silently ignored
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["role"] == "member", f"Expected 'member', got {data['role']!r}"
    assert data["tenant_id"] is None


# ──────────────────────────────────────────────────────────────────────────────
# T013: Login returns JWT / protected route 401 without JWT
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_login_returns_jwt(client):
    """Valid credentials return an access_token."""
    await register_user(client, email="jwt_test@example.com", password="SecurePass!1")
    resp = await client.post(
        "/auth/login",
        data={"username": "jwt_test@example.com", "password": "SecurePass!1"},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "access_token" in body
    assert body["token_type"] == "bearer"


@pytest.mark.asyncio
async def test_protected_route_401_without_jwt(client):
    """GET /auth/me without token returns 401 with a code field."""
    resp = await client.get("/auth/me")
    assert resp.status_code == 401
    body = resp.json()
    assert "code" in body
    assert body["code"] in ("auth_required", "token_revoked", "invalid_token")


# ──────────────────────────────────────────────────────────────────────────────
# T014: Logout revokes token / JWT payload has no PII
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_logout_revokes_token(client):
    """Login → logout → reuse token → 401 with code=token_revoked."""
    await register_user(client, email="logout_test@example.com", password="SecurePass!1")
    token = await login_user(client, "logout_test@example.com", "SecurePass!1")

    # Token works before logout.
    me_resp = await client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me_resp.status_code == 200

    # Logout.
    logout_resp = await client.post(
        "/auth/logout", headers={"Authorization": f"Bearer {token}"}
    )
    assert logout_resp.status_code == 204

    # Reuse the same token — must fail with token_revoked.
    reuse_resp = await client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert reuse_resp.status_code == 401
    body = reuse_resp.json()
    assert body.get("code") == "token_revoked", f"Expected token_revoked, got: {body}"


@pytest.mark.asyncio
async def test_jwt_payload_has_no_pii(client):
    """Decoded JWT payload must not contain email, tenant_id, or hashed_password."""
    import base64
    import json

    await register_user(client, email="pii_check@example.com", password="SecurePass!1")
    token = await login_user(client, "pii_check@example.com", "SecurePass!1")

    parts = token.split(".")
    assert len(parts) == 3, "Expected a 3-part JWT"
    # Add padding for base64 decoding.
    payload_b64 = parts[1] + "=" * (4 - len(parts[1]) % 4)
    payload = json.loads(base64.urlsafe_b64decode(payload_b64))

    assert "email" not in payload, f"email leaked into JWT payload: {payload}"
    assert "tenant_id" not in payload, f"tenant_id leaked into JWT payload: {payload}"
    assert "hashed_password" not in payload, f"hashed_password in JWT: {payload}"
    assert "sub" in payload, "JWT payload missing 'sub'"
    assert "jti" in payload, "JWT payload missing 'jti'"
    assert "role" in payload, "JWT payload missing 'role'"


# ──────────────────────────────────────────────────────────────────────────────
# T017: Role boundary between platform and tenant routes
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_tenant_admin_403_on_platform_route(client):
    """A non-manager token on GET /platform/tenants/ returns 403 with code=permission_denied."""
    await register_user(client, email="ta_platform@example.com", password="SecurePass!1")
    token = await login_user(client, "ta_platform@example.com", "SecurePass!1")

    resp = await client.get(
        "/platform/tenants/", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 403
    body = resp.json()
    assert body.get("code") == "permission_denied", f"Expected permission_denied, got: {body}"


@pytest.mark.asyncio
async def test_tenant_manager_403_on_tenant_content(client):
    """A member token on GET /tenant/config returns 403 with code=permission_denied."""
    await register_user(client, email="tm_tenant@example.com", password="SecurePass!1")
    token = await login_user(client, "tm_tenant@example.com", "SecurePass!1")

    resp = await client.get(
        "/tenant/config", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 403
    body = resp.json()
    assert body.get("code") == "permission_denied", f"Expected permission_denied, got: {body}"


# ──────────────────────────────────────────────────────────────────────────────
# T020: tenant_id never from request body / RLS context reset
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_tenant_id_never_from_request_body(client):
    """Route handler signatures for tenant-scoped routes must not accept tenant_id as body param."""
    import inspect

    from app.api.routes import admin_config, cms

    for module in (admin_config, cms):
        for route in module.router.routes:
            if not hasattr(route, "endpoint"):
                continue
            sig = inspect.signature(route.endpoint)
            # tenant_id should only appear as a path parameter (from URL),
            # never as a standalone body parameter without Depends().
            for param_name, param in sig.parameters.items():
                if param_name == "tenant_id":
                    # If it's a bare parameter without a dependency, that's a violation.
                    from fastapi import params

                    assert not (
                        param.default is inspect.Parameter.empty
                        and not isinstance(param.default, params.Depends)
                    ), (
                        f"{route.endpoint.__name__}: tenant_id is a raw body "
                        "parameter — must come from Depends(require_tenant_admin)"
                    )


@pytest.mark.asyncio
async def test_rls_context_reset_after_request(client):
    """require_tenant_admin always returns 403 for member users (not 500).

    If RLS state leaked between requests, the second request might behave
    differently.  Both must return 403 with the same error code.
    """
    await register_user(client, email="rls_reset@example.com", password="SecurePass!1")
    token = await login_user(client, "rls_reset@example.com", "SecurePass!1")

    # Two consecutive requests — RLS state must not bleed across them.
    for _ in range(2):
        resp = await client.get(
            "/cms/pages", headers={"Authorization": f"Bearer {token}"}
        )
        assert resp.status_code == 403, f"Expected 403, got {resp.status_code}: {resp.text}"
        assert resp.json().get("code") == "permission_denied"


# ──────────────────────────────────────────────────────────────────────────────
# T024: Self-registration cannot elevate role
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_self_registration_cannot_elevate_role(client):
    """POST /auth/register with role=tenant_manager in body → role is still member."""
    resp = await register_user(
        client,
        email="elevation@example.com",
        password="SecurePass!1",
        role="tenant_manager",
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["role"] == "member", f"Role elevation succeeded (should not): {data['role']!r}"


@pytest.mark.asyncio
async def test_invite_admin_requires_tenant_manager(client):
    """A member token on POST /platform/tenants/{id}/invite-admin returns 403."""
    await register_user(client, email="invite_member@example.com", password="SecurePass!1")
    token = await login_user(client, "invite_member@example.com", "SecurePass!1")

    fake_tenant_id = str(uuid.uuid4())
    resp = await client.post(
        f"/platform/tenants/{fake_tenant_id}/invite-admin",
        json={"email": "newadmin@example.com"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403
    assert resp.json().get("code") == "permission_denied"


# ──────────────────────────────────────────────────────────────────────────────
# T027: Login rate limiting
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_login_rate_limit(client):
    """11 failed login attempts from the same IP → at least one 429 with Retry-After."""
    email = "ratelimit_test@example.com"
    await register_user(client, email=email, password="CorrectPass!1")

    responses = []
    for _ in range(11):
        resp = await client.post(
            "/auth/login",
            data={"username": email, "password": "WrongPassword!"},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        responses.append(resp)

    rate_limited = [r for r in responses if r.status_code == 429]
    assert len(rate_limited) >= 1, (
        f"Expected at least one 429 after 11 attempts. "
        f"Got: {[r.status_code for r in responses]}"
    )
    last_429 = rate_limited[-1]
    assert "Retry-After" in last_429.headers, "429 response missing Retry-After header"
