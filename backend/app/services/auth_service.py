"""Auth service: audit logging, invite flow, and login rate limiting.

All DB writes are fire-and-forget (asyncio.create_task) per FR-021 — the
request path must not block on audit log writes.

Rate-limit counters use Redis atomic INCR + EXPIRE NX so that the 15-minute
window is set only on the first increment, not reset on each subsequent one.
"""

from __future__ import annotations

import asyncio
import secrets
import uuid
from typing import Any
from uuid import UUID

import redis.asyncio as aioredis
import structlog
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

log = structlog.get_logger(__name__)


# ──────────────────────────────────────────────────────────────────────────────
# Audit logging
# ──────────────────────────────────────────────────────────────────────────────


async def _write_audit_event_task(
    action: str,
    actor_role: str,
    actor_user_id: UUID | None = None,
    tenant_id: UUID | None = None,
    metadata_: dict | None = None,
    session: AsyncSession | None = None,
) -> None:
    """Inner coroutine — runs in a background task so the request path never awaits it."""
    if session is None:
        # No session provided; obtain a fresh one.
        from app.db.session import get_session_factory

        factory = get_session_factory()
        async with factory() as _session:
            await _insert_audit_log(
                _session, action, actor_role, actor_user_id, tenant_id, metadata_
            )
    else:
        await _insert_audit_log(session, action, actor_role, actor_user_id, tenant_id, metadata_)


async def _insert_audit_log(
    session: AsyncSession,
    action: str,
    actor_role: str,
    actor_user_id: UUID | None,
    tenant_id: UUID | None,
    metadata_: dict | None,
) -> None:
    """Insert one audit_logs row. Errors are caught and logged, never raised."""
    from app.models.audit_log import AuditLog

    try:
        audit = AuditLog(
            id=uuid.uuid4(),
            actor_user_id=actor_user_id,
            actor_role=actor_role,
            tenant_id=tenant_id,
            action=action,
            metadata_=metadata_,
        )
        session.add(audit)
        await session.commit()
    except Exception as exc:  # noqa: BLE001
        log.error("audit_log.write_failed", action=action, error=str(exc))


def write_audit_event(
    action: str,
    actor_role: str,
    actor_user_id: UUID | None = None,
    tenant_id: UUID | None = None,
    metadata_: dict | None = None,
    session: AsyncSession | None = None,
) -> None:
    """Fire-and-forget audit event write.

    Uses asyncio.create_task so it never blocks the calling request path.
    DB errors are caught inside _write_audit_event_task and logged — they
    will never propagate to callers.
    """
    asyncio.create_task(
        _write_audit_event_task(
            action=action,
            actor_role=actor_role,
            actor_user_id=actor_user_id,
            tenant_id=tenant_id,
            metadata_=metadata_,
            session=session,
        )
    )


# ──────────────────────────────────────────────────────────────────────────────
# Login rate limiting
# ──────────────────────────────────────────────────────────────────────────────

LOGIN_RATE_LIMIT_MAX: int = 10
LOGIN_RATE_LIMIT_WINDOW_SECONDS: int = 900  # 15 minutes


async def check_login_rate_limit(ip: str, redis_client: aioredis.Redis) -> None:
    """Raise 429 if the IP has exceeded LOGIN_RATE_LIMIT_MAX attempts.

    Uses an atomic INCR + EXPIRE NX pipeline so:
    - The counter is incremented on every call (success or failure).
    - The 15-minute TTL is set ONLY on the first increment (NX).
    - Subsequent increments within the window do NOT reset the TTL.

    The caller is responsible for deleting the key on a successful login
    to reset the counter (call reset_login_rate_limit after auth succeeds).
    """
    key = f"login_attempts:{ip}"

    pipe = redis_client.pipeline()
    pipe.incr(key)
    pipe.expire(key, LOGIN_RATE_LIMIT_WINDOW_SECONDS, nx=True)
    results = await pipe.execute()
    count: int = results[0]

    if count > LOGIN_RATE_LIMIT_MAX:
        ttl: int = await redis_client.ttl(key)
        raise HTTPException(
            status_code=429,
            detail="Too many failed login attempts. Please try again later.",
            headers={"Retry-After": str(max(1, ttl))},
        )


async def reset_login_rate_limit(ip: str, redis_client: aioredis.Redis) -> None:
    """Delete the rate-limit counter after a successful login."""
    await redis_client.delete(f"login_attempts:{ip}")


# ──────────────────────────────────────────────────────────────────────────────
# Invite admin
# ──────────────────────────────────────────────────────────────────────────────


async def invite_admin(
    tenant_id: UUID,
    email: str,
    session: AsyncSession,
    user_manager: Any,
) -> Any:
    """Create a tenant_admin user associated with tenant_id.

    • Raises 404 if the tenant does not exist or is not active.
    • Raises 409 if the email is already registered.
    • Writes an `invite_admin` audit event.
    • Returns a User ORM object with role=tenant_admin and correct tenant_id.

    The caller (tenant_manager via the platform route) is responsible for
    communicating the generated credentials to the new admin out of band.
    In Week 8 no email flow is implemented; the seeded admin credentials
    are known from the seed script.
    """

    # Validate tenant exists and is active.
    from sqlalchemy import select

    from app.models.tenant import Tenant, TenantStatus
    from app.models.user import User, UserRole

    result = await session.execute(select(Tenant).where(Tenant.id == tenant_id))
    tenant = result.scalar_one_or_none()
    if tenant is None or tenant.status in (TenantStatus.deleting, TenantStatus.deleted):
        raise HTTPException(
            status_code=404,
            detail="Tenant not found",
            headers={"X-Error-Code": "not_found"},
        )
    if tenant.status == TenantStatus.suspended:
        raise HTTPException(
            status_code=422,
            detail="Cannot invite admin for a suspended tenant",
            headers={"X-Error-Code": "tenant_not_active"},
        )

    # Check for duplicate email.
    existing = await user_manager.user_db.get_by_email(email)
    if existing is not None:
        raise HTTPException(
            status_code=409,
            detail="Email is already registered",
            headers={"X-Error-Code": "conflict"},
        )

    # Generate a cryptographically secure temporary password.
    # The new admin must use a password-reset flow (not implemented in Week 8).
    temp_password = secrets.token_urlsafe(24)
    hashed = user_manager.password_helper.hash(temp_password)

    new_user = User(
        id=uuid.uuid4(),
        email=email,
        hashed_password=hashed,
        is_active=True,
        is_superuser=False,
        is_verified=True,
        role=UserRole.tenant_admin,
        tenant_id=tenant_id,
    )
    session.add(new_user)
    await session.commit()
    await session.refresh(new_user)

    # Fire-and-forget audit event.
    write_audit_event(
        action="invite_admin",
        actor_role=UserRole.tenant_manager.value,
        tenant_id=tenant_id,
        metadata_={"invited_email": email},
    )
    log.info("invite_admin.created", email=email, tenant_id=str(tenant_id))
    return new_user
