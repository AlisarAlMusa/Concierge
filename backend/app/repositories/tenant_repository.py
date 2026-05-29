from __future__ import annotations

from decimal import Decimal
from uuid import UUID

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.cost_event import CostEvent, CostOperation
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


async def get_guardrails_config(
    session: AsyncSession, tenant_id: UUID
) -> dict:
    """Return the JSONB `guardrails_config` for a tenant (or `{}`).

    Spec 010 FR-022. Read once per chat turn by GuardrailService.
    """
    result = await session.execute(
        select(Tenant.guardrails_config).where(Tenant.id == tenant_id)
    )
    config = result.scalar_one_or_none()
    return dict(config) if config else {}


async def update_guardrails_config(
    session: AsyncSession, tenant_id: UUID, partial: dict
) -> Tenant:
    """Partial-merge `partial` into the tenant's `guardrails_config` JSONB.

    PATCH semantics: only the keys present in `partial` are overwritten;
    the rest of the JSONB stays untouched. This is the same behaviour as
    Postgres's `jsonb || jsonb` operator — we use that operator at the SQL
    layer so the update is one round-trip.

    Spec 010 FR-023.
    """
    if not partial:
        # No-op PATCH: refresh and return.
        result = await session.execute(select(Tenant).where(Tenant.id == tenant_id))
        tenant = result.scalar_one_or_none()
        if tenant is None:
            raise ValueError(f"Tenant {tenant_id} not found")
        return tenant

    # Avoid loading and re-saving the whole row — let Postgres merge.
    await session.execute(
        Tenant.__table__.update()
        .where(Tenant.id == tenant_id)
        .values(
            guardrails_config=Tenant.guardrails_config.op("||")(partial),
        )
    )
    result = await session.execute(select(Tenant).where(Tenant.id == tenant_id))
    tenant = result.scalar_one_or_none()
    if tenant is None:
        raise ValueError(f"Tenant {tenant_id} not found")
    return tenant


async def get_usage_summary(session: AsyncSession, tenant_id: UUID) -> dict:
    """Return per-operation and total aggregate cost metrics for a tenant.

    Returns zeros for all fields if the tenant has no cost events. The returned
    dict matches the shape expected by TenantUsageSummary (Spec 013 FR-005/006).
    Contains only numeric aggregates — no content fields (SC-005).
    """
    result = await session.execute(
        select(
            CostEvent.operation,
            func.coalesce(func.sum(CostEvent.input_tokens), 0).label("input_tokens"),
            func.coalesce(func.sum(CostEvent.output_tokens), 0).label("output_tokens"),
            func.coalesce(
                func.sum(CostEvent.estimated_cost_usd), Decimal("0")
            ).label("cost_usd"),
        )
        .where(CostEvent.tenant_id == tenant_id)
        .group_by(CostEvent.operation)
    )

    by_op: dict[str, dict] = {}
    for row in result:
        op_key = row.operation.value if hasattr(row.operation, "value") else str(row.operation)
        by_op[op_key] = {
            "input_tokens": int(row.input_tokens),
            "output_tokens": int(row.output_tokens),
            "cost_usd": Decimal(str(row.cost_usd)),
        }

    def _zero() -> dict:
        return {"input_tokens": 0, "output_tokens": 0, "cost_usd": Decimal("0")}

    llm = by_op.get(CostOperation.llm.value, _zero())
    embedding = by_op.get(CostOperation.embedding.value, _zero())
    classifier = by_op.get(CostOperation.classifier.value, _zero())
    rerank = by_op.get(CostOperation.rerank.value, _zero())

    total_input = (
        llm["input_tokens"]
        + embedding["input_tokens"]
        + classifier["input_tokens"]
        + rerank["input_tokens"]
    )
    total_output = (
        llm["output_tokens"]
        + embedding["output_tokens"]
        + classifier["output_tokens"]
        + rerank["output_tokens"]
    )
    total_cost = (
        llm["cost_usd"] + embedding["cost_usd"] + classifier["cost_usd"] + rerank["cost_usd"]
    )

    return {
        "total_input_tokens": total_input,
        "total_output_tokens": total_output,
        "total_cost_usd": total_cost,
        "llm": llm,
        "embedding": embedding,
        "classifier": classifier,
        "rerank": rerank,
    }
