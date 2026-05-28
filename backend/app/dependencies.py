"""FastAPI dependencies for authentication, authorisation, and RLS context.

Dependency hierarchy
────────────────────
get_current_user
  └─ fastapi_users_instance.current_user(active=True)
       └─ ConciergeJWTStrategy.read_token()   ← checks Redis JTI blacklist
            └─ UserManager.get()

require_tenant_manager
  └─ get_current_user  → assert role == tenant_manager

require_tenant_admin
  └─ get_current_user  → assert role in (tenant_admin, tenant_manager)
       └─ sets RLS context on the session (try/finally reset)

Non-negotiable rules enforced here
───────────────────────────────────
• tenant_id is NEVER read from the request body — always from user.tenant_id.
• app.tenant_id RLS context is reset unconditionally in a finally block.
• JTI revocation is checked inside ConciergeJWTStrategy.read_token().
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

import httpx
import redis.asyncio as aioredis
import structlog
from fastapi import Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import fastapi_users_instance
from app.db.rls import reset_tenant_context, set_tenant_context
from app.db.session import get_db_session
from app.models.user import User, UserRole

log = structlog.get_logger(__name__)


# ──────────────────────────────────────────────────────────────────────────────
# Infrastructure dependencies
# ──────────────────────────────────────────────────────────────────────────────


async def get_redis(request: Request) -> aioredis.Redis:
    return request.app.state.redis


async def get_service_client(request: Request) -> httpx.AsyncClient:
    """Authenticated shared client for outbound sidecar calls (spec 018).

    The `X-Service-Token` header is pre-attached at lifespan construction —
    service-layer code must NOT add it per call.
    """
    return request.app.state.service_client


async def get_session(
    session: AsyncSession = Depends(get_db_session),
) -> AsyncGenerator[AsyncSession, None]:
    yield session


# ──────────────────────────────────────────────────────────────────────────────
# Authentication dependency (T009)
# ──────────────────────────────────────────────────────────────────────────────


async def get_current_user(
    user: User = Depends(fastapi_users_instance.current_user(active=True)),
) -> User:
    """Return the authenticated User ORM object.

    Delegates to fastapi_users_instance.current_user(active=True) which:
    1. Extracts the Bearer token from the Authorization header.
    2. Calls ConciergeJWTStrategy.read_token() which checks the Redis JTI
       blacklist and raises 401 (code=token_revoked) if the token is revoked.
    3. Loads and returns the User from the database.

    A missing, expired, or invalid token → 401 from the authenticator.
    A revoked JTI → 401 (code=token_revoked) from ConciergeJWTStrategy.
    An inactive user → 401 from the authenticator.
    """
    return user


# ──────────────────────────────────────────────────────────────────────────────
# Role-based authorisation dependencies
# ──────────────────────────────────────────────────────────────────────────────


async def require_tenant_manager(
    user: User = Depends(get_current_user),
) -> User:
    """Require the caller to have the tenant_manager role.

    • tenant_admin → 403 permission_denied
    • member → 403 permission_denied
    • unauthenticated → 401 (from get_current_user)
    """
    if user.role != UserRole.tenant_manager:
        raise HTTPException(
            status_code=403,
            detail="Tenant manager role required",
            headers={"X-Error-Code": "permission_denied"},
        )
    return user


async def require_tenant_admin(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> AsyncGenerator[User, None]:
    """Require the caller to have at least the tenant_admin role.

    Derives tenant_id from user.tenant_id (NEVER from the request body).
    Sets the PostgreSQL RLS context variable app.tenant_id for the duration
    of the request, then resets it unconditionally in a finally block to
    prevent cross-tenant leaks via pooled connections.

    • tenant_manager on tenant routes → 403 (no tenant context)
    • member → 403 permission_denied
    • unauthenticated → 401 (from get_current_user)
    • tenant_admin with NULL tenant_id → 500 (data integrity error)
    """
    if user.role not in (UserRole.tenant_admin, UserRole.tenant_manager):
        raise HTTPException(
            status_code=403,
            detail="Tenant admin role required",
            headers={"X-Error-Code": "permission_denied"},
        )

    # tenant_manager has no RLS context — they must not access content tables.
    if user.role == UserRole.tenant_manager:
        raise HTTPException(
            status_code=403,
            detail="Tenant manager cannot access tenant-scoped routes",
            headers={"X-Error-Code": "permission_denied"},
        )

    if user.tenant_id is None:
        log.error(
            "require_tenant_admin.null_tenant_id",
            user_id=str(user.id),
            role=user.role.value,
        )
        raise HTTPException(
            status_code=500,
            detail="User has no tenant_id — data integrity error",
        )

    try:
        await set_tenant_context(session, user.tenant_id)
        yield user
    finally:
        await reset_tenant_context(session)
