"""Cost tracking service — records per-call cost events for tenants.

FR-001: Every LLM API call produces a cost_event row.
FR-002: Every embedding API call produces a cost_event row.
FR-003: Every classifier call produces a cost_event row (cost=0, self-hosted).
FR-004: Writes are async and non-blocking. A failed write warns and continues.

Fire-and-forget pattern mirrors auth_service.write_audit_event: the public
entry point is a sync function that schedules an asyncio.Task, so callers
never await cost recording and the request path is never delayed by a DB write.

Static pricing table is read from Settings (Spec 013 assumption — no live API).
"""

from __future__ import annotations

import asyncio
from decimal import Decimal
from uuid import UUID

import structlog

from app.core.config import get_settings
from app.db.session import get_session_factory
from app.models.cost_event import CostEvent, CostOperation
from app.repositories import cost_repository

log = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Cost estimation
# ---------------------------------------------------------------------------


def _estimate_cost(
    provider: str,
    input_tokens: int,
    output_tokens: int,
) -> Decimal:
    """Return estimated USD cost for a provider call using Settings pricing table.

    Reads from the process-wide Settings singleton so the table is consistent
    with whatever is in .env / Vault. Self-hosted providers (model_server)
    always return zero.
    """
    s = get_settings()
    p = provider.lower()

    if p == "groq":
        return (
            Decimal(str(s.COST_GROQ_INPUT_PER_TOKEN)) * input_tokens
            + Decimal(str(s.COST_GROQ_OUTPUT_PER_TOKEN)) * output_tokens
        )
    if p == "cohere":
        # Cohere charges on input tokens only for embed.
        return Decimal(str(s.COST_COHERE_INPUT_PER_TOKEN)) * input_tokens

    # Self-hosted / unknown provider — cost is zero.
    return Decimal("0")


# ---------------------------------------------------------------------------
# Inner async coroutines
# ---------------------------------------------------------------------------


async def _write_cost_event(
    *,
    tenant_id: UUID,
    provider: str,
    model: str,
    operation: CostOperation,
    input_tokens: int,
    output_tokens: int,
) -> None:
    """Insert one cost_event row using a fresh session. Never raises."""
    estimated_cost = _estimate_cost(provider, input_tokens, output_tokens)
    event = CostEvent(
        tenant_id=tenant_id,
        provider=provider,
        model=model,
        operation=operation,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        estimated_cost_usd=estimated_cost,
    )
    try:
        factory = get_session_factory()
        async with factory() as session:
            await cost_repository.insert_cost_event(session, event)
            await session.commit()
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "cost_event.write_failed",
            tenant_id=str(tenant_id),
            operation=operation.value,
            error=str(exc),
        )


# ---------------------------------------------------------------------------
# Public fire-and-forget entry point
# ---------------------------------------------------------------------------


def record_event(
    *,
    tenant_id: UUID,
    provider: str,
    model: str,
    operation: CostOperation,
    input_tokens: int,
    output_tokens: int = 0,
) -> None:
    """Fire-and-forget cost event recording.

    Schedules an asyncio.Task so the calling request path never awaits it.
    Any DB failure is caught inside ``_write_cost_event`` and logged as a
    warning — it never propagates to callers (FR-004).
    """
    asyncio.create_task(
        _write_cost_event(
            tenant_id=tenant_id,
            provider=provider,
            model=model,
            operation=operation,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )
    )


# ---------------------------------------------------------------------------
# Usage summary (awaitable — used by API routes)
# ---------------------------------------------------------------------------


async def get_tenant_usage_summary(
    session,  # AsyncSession — typed loosely to avoid import cycle
    tenant_id: UUID,
) -> dict:
    """Return per-operation aggregate usage for a tenant.

    Returns a dict with keys matching TenantUsageSummary fields. The session
    should have RLS context set to the tenant so RLS provides a second
    enforcement layer on top of the explicit tenant_id filter in the query.
    """
    rows = await cost_repository.get_usage_summary_by_operation(session, tenant_id)

    # Collect per-operation totals.
    by_op: dict[str, dict] = {}
    for row in rows:
        op = row["operation"]
        op_key = op.value if hasattr(op, "value") else str(op)
        by_op[op_key] = {
            "input_tokens": row["input_tokens"],
            "output_tokens": row["output_tokens"],
            "cost_usd": row["cost_usd"],
        }

    def _op(key: str) -> dict:
        return by_op.get(key, {"input_tokens": 0, "output_tokens": 0, "cost_usd": Decimal("0")})

    llm = _op("llm")
    embedding = _op("embedding")
    classifier = _op("classifier")
    rerank = _op("rerank")

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
        "tenant_id": tenant_id,
        "total_input_tokens": total_input,
        "total_output_tokens": total_output,
        "total_cost_usd": total_cost,
        "llm": llm,
        "embedding": embedding,
        "classifier": classifier,
        "rerank": rerank,
    }
