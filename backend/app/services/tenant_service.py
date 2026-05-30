"""Tenant lifecycle service — platform operator operations only.

All functions are called from /platform/* routes (tenant_manager role).
No RLS context is set here — the tenant_manager has no content access.
Audit events are fire-and-forget (write_audit_event never blocks the caller).
"""

from __future__ import annotations

import asyncio
from uuid import UUID

import structlog
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tenant import Tenant, TenantStatus
from app.repositories import tenant_repository
from app.schemas.tenant import TenantUsageSummary
from app.services.auth_service import write_audit_event

log = structlog.get_logger(__name__)


async def create_tenant(
    session: AsyncSession,
    name: str,
    slug: str,
    actor_id: UUID,
    actor_role: str,
    contact_email: str | None = None,
    description: str | None = None,
) -> Tenant:
    """Create a new active tenant. Raises 409 on duplicate slug.

    When ``contact_email`` or ``description`` is supplied, a matching
    ``tenant_configs`` row is also inserted so the public site has
    populated branding/contact info on day one. The config row uses
    ``brand_name = name`` as a sensible default; the tenant admin can
    refine it later via the existing ``/tenant/config`` surface.
    Behaviour is unchanged when both fields are ``None`` (the config
    row stays absent and callers fall back to defaults, as before).
    """
    try:
        tenant = await tenant_repository.create_tenant(session, name=name, slug=slug)

        if contact_email is not None or description is not None:
            # Local import — avoids pulling TenantConfig into the module
            # graph for callers that never need it.
            from app.models.tenant_config import TenantConfig

            session.add(
                TenantConfig(
                    tenant_id=tenant.id,
                    brand_name=name,
                    contact_email=contact_email,
                    public_description=description,
                )
            )

        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(
            status_code=409,
            detail="Slug already exists",
            headers={"X-Error-Code": "conflict"},
        )

    write_audit_event(
        action="tenant_created",
        actor_role=actor_role,
        actor_user_id=actor_id,
        tenant_id=tenant.id,
        metadata_={"name": name, "slug": slug},
    )
    log.info("tenant.created", tenant_id=str(tenant.id), slug=slug)
    return tenant


async def get_tenant_or_404(session: AsyncSession, tenant_id: UUID) -> Tenant:
    tenant = await tenant_repository.get_tenant(session, tenant_id)
    if tenant is None or tenant.status == TenantStatus.deleted:
        raise HTTPException(
            status_code=404,
            detail="Tenant not found",
            headers={"X-Error-Code": "not_found"},
        )
    return tenant


async def list_tenants(session: AsyncSession) -> list[Tenant]:
    return await tenant_repository.get_all_tenants(session)


async def suspend_tenant(
    session: AsyncSession,
    tenant_id: UUID,
    actor_id: UUID,
    actor_role: str,
) -> Tenant:
    """Suspend an active tenant. Idempotent if already suspended."""
    tenant = await get_tenant_or_404(session, tenant_id)

    if tenant.status in (TenantStatus.deleting, TenantStatus.deleted):
        raise HTTPException(
            status_code=422,
            detail="Cannot suspend a tenant that is deleting or deleted",
            headers={"X-Error-Code": "tenant_not_active"},
        )

    if tenant.status == TenantStatus.suspended:
        return tenant  # idempotent

    tenant = await tenant_repository.update_tenant_status(
        session, tenant_id, TenantStatus.suspended
    )
    await session.commit()

    write_audit_event(
        action="tenant_suspended",
        actor_role=actor_role,
        actor_user_id=actor_id,
        tenant_id=tenant_id,
    )
    log.info("tenant.suspended", tenant_id=str(tenant_id))
    return tenant


async def reactivate_tenant(
    session: AsyncSession,
    tenant_id: UUID,
    actor_id: UUID,
    actor_role: str,
) -> Tenant:
    """Restore a suspended tenant to active. Raises 422 if not suspended."""
    tenant = await get_tenant_or_404(session, tenant_id)

    if tenant.status != TenantStatus.suspended:
        raise HTTPException(
            status_code=422,
            detail="Tenant is not suspended",
            headers={"X-Error-Code": "tenant_not_active"},
        )

    tenant = await tenant_repository.update_tenant_status(session, tenant_id, TenantStatus.active)
    await session.commit()

    write_audit_event(
        action="tenant_reactivated",
        actor_role=actor_role,
        actor_user_id=actor_id,
        tenant_id=tenant_id,
    )
    log.info("tenant.reactivated", tenant_id=str(tenant_id))
    return tenant


async def delete_tenant(
    session: AsyncSession,
    tenant_id: UUID,
    actor_id: UUID,
    actor_role: str,
    redis=None,
) -> Tenant:
    """Trigger deletion: set status=deleting, fire erasure async. Idempotent if already deleting."""
    tenant = await get_tenant_or_404(session, tenant_id)

    if tenant.status == TenantStatus.deleting:
        return tenant  # idempotent

    tenant = await tenant_repository.update_tenant_status(session, tenant_id, TenantStatus.deleting)
    await session.commit()

    from app.services.erasure_service import purge_tenant  # noqa: PLC0415

    asyncio.create_task(purge_tenant(tenant_id, redis))

    write_audit_event(
        action="tenant_delete_triggered",
        actor_role=actor_role,
        actor_user_id=actor_id,
        tenant_id=tenant_id,
    )
    log.info("tenant.delete_triggered", tenant_id=str(tenant_id))
    return tenant


async def get_usage_summary(session: AsyncSession, tenant_id: UUID) -> TenantUsageSummary:
    from app.schemas.tenant import OperationUsage

    await get_tenant_or_404(session, tenant_id)
    s = await tenant_repository.get_usage_summary(session, tenant_id)
    return TenantUsageSummary(
        tenant_id=tenant_id,
        total_input_tokens=s["total_input_tokens"],
        total_output_tokens=s["total_output_tokens"],
        total_cost_usd=s["total_cost_usd"],
        llm=OperationUsage(**s["llm"]),
        embedding=OperationUsage(**s["embedding"]),
        classifier=OperationUsage(**s["classifier"]),
        rerank=OperationUsage(**s["rerank"]),
    )
