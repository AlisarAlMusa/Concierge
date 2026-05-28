from __future__ import annotations

from decimal import Decimal
from uuid import UUID

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.cost_event import CostEvent
from app.models.tenant import Tenant, TenantStatus

log = structlog.get_logger(__name__)


async def create_tenant(session: AsyncSession, name: str, slug: str) -> Tenant:
    """Insert a new tenant row. Raises IntegrityError on duplicate slug."""
    tenant = Tenant(name=name, slug=slug, status=TenantStatus.active)
    session.add(tenant)
    await session.flush()
    await session.refresh(tenant)
    return tenant


async def get_tenant(session: AsyncSession, tenant_id: UUID) -> Tenant | None:
    result = await session.execute(select(Tenant).where(Tenant.id == tenant_id))
    return result.scalar_one_or_none()


async def get_all_tenants(session: AsyncSession) -> list[Tenant]:
    """Return all tenants excluding deleted ones."""
    result = await session.execute(
        select(Tenant).where(Tenant.status != TenantStatus.deleted).order_by(Tenant.created_at)
    )
    return list(result.scalars().all())


async def update_tenant_status(
    session: AsyncSession, tenant_id: UUID, status: TenantStatus
) -> Tenant:
    result = await session.execute(select(Tenant).where(Tenant.id == tenant_id))
    tenant = result.scalar_one_or_none()
    if tenant is None:
        raise ValueError(f"Tenant {tenant_id} not found")
    tenant.status = status
    await session.flush()
    await session.refresh(tenant)
    return tenant


async def get_usage_summary(session: AsyncSession, tenant_id: UUID) -> dict:
    """Aggregate cost_events for a tenant. Returns zeros if no events exist."""
    result = await session.execute(
        select(
            func.coalesce(func.sum(CostEvent.input_tokens), 0).label("total_input_tokens"),
            func.coalesce(func.sum(CostEvent.output_tokens), 0).label("total_output_tokens"),
            func.coalesce(func.sum(CostEvent.estimated_cost_usd), Decimal("0")).label(
                "total_cost_usd"
            ),
        ).where(CostEvent.tenant_id == tenant_id)
    )
    row = result.one()
    return {
        "total_input_tokens": int(row.total_input_tokens),
        "total_output_tokens": int(row.total_output_tokens),
        "total_cost_usd": Decimal(str(row.total_cost_usd)),
    }
