"""Tenant admin config routes — accessible only by tenant_admin.

Tenant context (RLS) is set automatically by require_tenant_admin.
tenant_manager → 403.  member → 403.  unauthenticated → 401.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db_session
from app.dependencies import require_tenant_admin
from app.models.user import User
from app.repositories import tenant_repository
from app.schemas.guardrails import GuardrailsConfigRead, GuardrailsConfigUpdate
from app.schemas.tenant import OperationUsage, TenantUsageSummary
from app.services import cost_service

router = APIRouter(tags=["admin_config"])


# ──────────────────────────────────────────────────────────────────────────────
# GET /tenant/config  (stub — Person A Day 3)
# ──────────────────────────────────────────────────────────────────────────────


@router.get(
    "/config",
    summary="Get tenant configuration (tenant_admin only)",
)
async def get_tenant_config(
    current_user: User = Depends(require_tenant_admin),
):
    """Return tenant configuration.  Stub — returns empty dict for now.

    tenant_id is derived from current_user.tenant_id (never from body).
    RLS context is set by require_tenant_admin before this handler runs.
    """
    return {"tenant_id": str(current_user.tenant_id), "config": {}}


# ──────────────────────────────────────────────────────────────────────────────
# GET /tenant/usage-summary  (Spec 013 FR-005)
# ──────────────────────────────────────────────────────────────────────────────


@router.get(
    "/usage-summary",
    response_model=TenantUsageSummary,
    summary="Get cost usage summary for the calling tenant (tenant_admin only)",
)
async def get_tenant_usage_summary(
    current_user: User = Depends(require_tenant_admin),
    session: AsyncSession = Depends(get_db_session),
) -> TenantUsageSummary:
    """Return aggregate cost metrics for the calling tenant.

    tenant_id is derived from the authenticated user's JWT — never from the
    request body (CLAUDE.md non-negotiable rule 5).

    The session shared with require_tenant_admin already has app.tenant_id RLS
    context set, so the DB-level policy provides a second enforcement layer on
    top of the explicit tenant_id filter inside cost_service.get_tenant_usage_summary.

    Returns numeric aggregates only — no conversation content, lead records, or
    CMS body text (Spec 013 SC-005).
    """
    summary = await cost_service.get_tenant_usage_summary(session, current_user.tenant_id)
    return TenantUsageSummary(
        tenant_id=summary["tenant_id"],
        total_input_tokens=summary["total_input_tokens"],
        total_output_tokens=summary["total_output_tokens"],
        total_cost_usd=summary["total_cost_usd"],
        llm=OperationUsage(**summary["llm"]),
        embedding=OperationUsage(**summary["embedding"]),
        classifier=OperationUsage(**summary["classifier"]),
        rerank=OperationUsage(**summary["rerank"]),
    )


# ──────────────────────────────────────────────────────────────────────────────
# Guardrails config — GET + PATCH /config/guardrails  (Spec 010 FR-023 / US7)
# ──────────────────────────────────────────────────────────────────────────────


@router.get(
    "/config/guardrails",
    response_model=GuardrailsConfigRead,
    summary="Get the tenant's guardrails config (tenant_admin only)",
)
async def get_guardrails_config(
    current_user: User = Depends(require_tenant_admin),
    session: AsyncSession = Depends(get_db_session),
) -> GuardrailsConfigRead:
    raw = await tenant_repository.get_guardrails_config(session, current_user.tenant_id)
    return GuardrailsConfigRead.model_validate(raw)


@router.patch(
    "/config/guardrails",
    response_model=GuardrailsConfigRead,
    summary="Update the tenant's guardrails config (tenant_admin only)",
)
async def patch_guardrails_config(
    payload: GuardrailsConfigUpdate,
    current_user: User = Depends(require_tenant_admin),
    session: AsyncSession = Depends(get_db_session),
) -> GuardrailsConfigRead:
    """Partial-merge update for `tenants.guardrails_config`.

    Strict Pydantic limits at the boundary (spec 010 FR-023): max 10 topics,
    each 1..30 chars, case-insensitive deduped. Persona ≤500, tone ≤100.
    `tenant_id` is derived from the authenticated user — never from the body.
    Missing fields = "no change"; `blocked_topics=[]` clears the list.
    """
    partial = payload.model_dump(exclude_unset=True)
    tenant = await tenant_repository.update_guardrails_config(
        session, current_user.tenant_id, partial
    )
    return GuardrailsConfigRead.model_validate(tenant.guardrails_config)
