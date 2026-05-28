from collections.abc import AsyncGenerator

import httpx
import redis.asyncio as aioredis
from fastapi import Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db_session
from app.models.user import User, UserRole


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


# Placeholder until Person A adds fastapi-users auth on Day 2.
# Routes that need auth should Depends() on one of these.
async def get_current_user() -> User:
    raise HTTPException(status_code=501, detail="Auth not yet implemented")


async def require_tenant_manager(
    user: User = Depends(get_current_user),
) -> User:
    if user.role != UserRole.tenant_manager:
        raise HTTPException(status_code=403, detail="Tenant manager role required")
    return user


async def require_tenant_admin(
    user: User = Depends(get_current_user),
) -> User:
    if user.role not in (UserRole.tenant_admin, UserRole.tenant_manager):
        raise HTTPException(status_code=403, detail="Tenant admin role required")
    return user
