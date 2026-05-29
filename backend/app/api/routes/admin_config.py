"""Tenant admin config routes — GET/PATCH /tenant/config, GET /tenant/usage-summary.

tenant_id is always derived from the authenticated user's JWT, never from the
request body (CLAUDE.md non-negotiable rule 5).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db_session
from app.dependencies import require_tenant_admin
from app.models.tenant_config import TenantConfig
from app.models.user import User
from app.repositories import tenant_repository
from app.schemas.guardrails import GuardrailsConfigRead, GuardrailsConfigUpdate
from app.schemas.tenant import OperationUsage, TenantUsageSummary
from app.services import cost_service

router = APIRouter(tags=["admin_config"])

_DEFAULT_TOOLS = ["rag_search", "capture_lead", "escalate"]


class TenantConfigRead(BaseModel):
    tenant_id: str
    persona: str
    refusal_tone: str
    enabled_tools: list[str]
    allowed_topics: list[str]
    blocked_topics: list[str]


class TenantConfigUpdate(BaseModel):
    persona: str | None = None
    refusal_tone: str | None = None
    enabled_tools: list[str] | None = None
    allowed_topics: list[str] | None = None
    blocked_topics: list[str] | None = None


def _row_to_read(cfg: TenantConfig | None, tenant_id: Any) -> TenantConfigRead:
    if cfg is None:
        return TenantConfigRead(
            tenant_id=str(tenant_id),
            persona="",
            refusal_tone="polite",
            enabled_tools=_DEFAULT_TOOLS,
            allowed_topics=[],
            blocked_topics=[],
        )
    return TenantConfigRead(
        tenant_id=str(tenant_id),
        persona=cfg.persona or "",
        refusal_tone=cfg.refusal_tone or "polite",
        enabled_tools=cfg.enabled_tools if cfg.enabled_tools is not None else _DEFAULT_TOOLS,
        allowed_topics=cfg.allowed_topics or [],
        blocked_topics=cfg.blocked_topics or [],
    )


@router.get(
    "/config",
    response_model=TenantConfigRead,
    summary="Get tenant agent/guardrail configuration (tenant_admin only)",
)
async def get_tenant_config(
    current_user: User = Depends(require_tenant_admin),
    session: AsyncSession = Depends(get_db_session),
) -> TenantConfigRead:
    result = await session.execute(
        select(TenantConfig).where(TenantConfig.tenant_id == current_user.tenant_id)
    )
    cfg = result.scalar_one_or_none()
    return _row_to_read(cfg, current_user.tenant_id)


@router.patch(
    "/config",
    response_model=TenantConfigRead,
    summary="Update tenant agent/guardrail configuration (tenant_admin only)",
)
async def patch_tenant_config(
    payload: TenantConfigUpdate,
    current_user: User = Depends(require_tenant_admin),
    session: AsyncSession = Depends(get_db_session),
) -> TenantConfigRead:
    result = await session.execute(
        select(TenantConfig).where(TenantConfig.tenant_id == current_user.tenant_id)
    )
    cfg = result.scalar_one_or_none()

    if cfg is None:
        cfg = TenantConfig(tenant_id=current_user.tenant_id)
        session.add(cfg)

    if payload.persona is not None:
        cfg.persona = payload.persona
    if payload.refusal_tone is not None:
        cfg.refusal_tone = payload.refusal_tone
    if payload.enabled_tools is not None:
        cfg.enabled_tools = payload.enabled_tools
    if payload.allowed_topics is not None:
        cfg.allowed_topics = payload.allowed_topics
    if payload.blocked_topics is not None:
        cfg.blocked_topics = payload.blocked_topics

    await session.flush()
    return _row_to_read(cfg, current_user.tenant_id)


@router.get(
    "/usage-summary",
    response_model=TenantUsageSummary,
    summary="Get cost usage summary for the calling tenant (tenant_admin only)",
)
async def get_tenant_usage_summary(
    current_user: User = Depends(require_tenant_admin),
    session: AsyncSession = Depends(get_db_session),
) -> TenantUsageSummary:
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
