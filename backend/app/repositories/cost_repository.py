"""Cost event repository — tenant-scoped data access for cost_events.

All inserts are non-RLS-scoped (the session may or may not have app.tenant_id
set). The model's ForeignKey(tenants.id) plus the explicit tenant_id argument
enforce isolation at the application level. RLS on cost_events provides a
second layer when the session has the context variable set.
"""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.cost_event import CostEvent


async def insert_cost_event(session: AsyncSession, event: CostEvent) -> None:
    """Add a cost_event row and flush (caller owns the commit)."""
    session.add(event)
    await session.flush()


async def get_usage_summary_by_operation(session: AsyncSession, tenant_id: UUID) -> list[dict]:
    """Return per-operation aggregate rows for a tenant.

    Each dict has keys: operation, input_tokens, output_tokens, cost_usd.
    Rows are only returned for operations that have at least one event.
    """
    result = await session.execute(
        select(
            CostEvent.operation,
            func.coalesce(func.sum(CostEvent.input_tokens), 0).label("input_tokens"),
            func.coalesce(func.sum(CostEvent.output_tokens), 0).label("output_tokens"),
            func.coalesce(func.sum(CostEvent.estimated_cost_usd), Decimal("0")).label("cost_usd"),
        )
        .where(CostEvent.tenant_id == tenant_id)
        .group_by(CostEvent.operation)
    )
    return [
        {
            "operation": row.operation,
            "input_tokens": int(row.input_tokens),
            "output_tokens": int(row.output_tokens),
            "cost_usd": Decimal(str(row.cost_usd)),
        }
        for row in result
    ]


async def get_total_usage(session: AsyncSession, tenant_id: UUID) -> dict:
    """Return a single-row aggregate for a tenant (all operations combined)."""
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
