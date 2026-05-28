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
from functools import lru_cache
from typing import TYPE_CHECKING
from uuid import UUID

import httpx
import redis.asyncio as aioredis
import structlog
from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.errors import ExternalServiceError
from app.core.security import fastapi_users_instance
from app.db.rls import reset_tenant_context, set_tenant_context
from app.db.session import get_db_session, get_session_factory
from app.models.tenant import Tenant, TenantStatus
from app.models.user import User, UserRole
from app.services.agent_service import AgentService
from app.services.chat_orchestrator import (
    ChatOrchestrator,
    PassthroughGuardrailClient,
)
from app.services.classifier_client import (
    HttpClassifierClient,
    UnavailableClassifierClient,
)
from app.services.cms_page_service import CmsPageService
from app.services.conversation_service import ConversationService
from app.services.embedding_client import CohereEmbeddingClient
from app.services.escalation_service import EscalationService
from app.services.lead_service import LeadService
from app.services.llm_client import GroqLLMClient
from app.services.memory_service import MemoryService
from app.services.rag_service import RagService
from app.services.router_service import ClassifierClient, RouterService
from app.services.tools import ToolRegistry, build_registry
from app.services.widget_service import WidgetService
from app.services.widget_token_service import (
    WidgetTokenClaims,
    WidgetTokenError,
    WidgetTokenService,
)
from app.services.workflows import (
    FaqWorkflow,
    HumanWorkflow,
    SalesWorkflow,
)

from sqlalchemy import select

log = structlog.get_logger(__name__)


# ──────────────────────────────────────────────────────────────────────────────
# Infrastructure dependencies
# ──────────────────────────────────────────────────────────────────────────────


# ----- Auth placeholders (kept for compat with existing routes) --------------
async def get_redis(request: Request) -> aioredis.Redis:
    """Return the process-singleton Redis client attached by the lifespan."""
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
    """Require the caller to have at least the tenant_admin role."""

    if user.role not in (UserRole.tenant_admin, UserRole.tenant_manager):
        raise HTTPException(
            status_code=403,
            detail="Tenant admin role required",
        )

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
            detail="User has no tenant_id – data integrity error",
        )

    result = await session.execute(
        select(Tenant).where(Tenant.id == user.tenant_id)
    )

    tenant = result.scalar_one_or_none()

    if tenant is None or tenant.status == TenantStatus.deleted:
        raise HTTPException(
            status_code=403,
            detail="Tenant not found or deleted",
            headers={"X-Error-Code": "permission_denied"},
        )

    await set_tenant_context(session, user.tenant_id)

    try:
        yield user
    finally:
        await reset_tenant_context(session)

# ----- Process-singleton infrastructure clients ------------------------------
@lru_cache(maxsize=1)
def _get_llm_client_singleton() -> GroqLLMClient:
    """Build the GroqLLMClient once per process."""
    settings = get_settings()
    if not settings.GROQ_API_KEY:
        raise ExternalServiceError(
            service="groq",
            reason="GROQ_API_KEY is not set; cannot construct LLM client",
        )
    return GroqLLMClient.from_api_key(
        api_key=settings.GROQ_API_KEY,
        model=settings.LLM_MODEL,
    )


@lru_cache(maxsize=1)
def _get_embedding_client_singleton() -> CohereEmbeddingClient:
    """Build the CohereEmbeddingClient once per process."""
    settings = get_settings()
    if not settings.COHERE_API_KEY:
        raise ExternalServiceError(
            service="cohere",
            reason="COHERE_API_KEY is not set; cannot construct embedding client",
        )
    return CohereEmbeddingClient.from_api_key(
        api_key=settings.COHERE_API_KEY,
        model=settings.EMBEDDING_MODEL,
    )


@lru_cache(maxsize=1)
def _get_classifier_client_singleton() -> ClassifierClient:
    """Classifier adapter, env-conditional.

    ``Settings.CLASSIFIER_ENABLED == True`` (production, once ``model_server``
    is reachable) → ``HttpClassifierClient``. Otherwise (default, CI, tests)
    → ``UnavailableClassifierClient`` so ``RouterService`` fails open to the
    agent. The fail-open posture is the correct safety net: a missing
    classifier must NOT drop user messages.
    """
    settings = get_settings()
    if settings.CLASSIFIER_ENABLED:
        return HttpClassifierClient(
            base_url=settings.MODEL_SERVER_URL,
            service_token=settings.SERVICE_AUTH_SECRET,
        )
    return UnavailableClassifierClient()


@lru_cache(maxsize=1)
def _get_widget_token_service_singleton() -> WidgetTokenService:
    """One ``WidgetTokenService`` per process. Pure HMAC, no I/O.

    Constructed lazily so tests can swap the singleton via
    ``reset_singletons`` after monkeypatching ``Settings``.
    """
    settings = get_settings()
    return WidgetTokenService(
        secret=settings.WIDGET_TOKEN_SECRET,
        ttl_seconds=settings.WIDGET_TOKEN_TTL_SECONDS,
    )


def get_llm_client() -> GroqLLMClient:
    """FastAPI dependency for the LLM client."""
    return _get_llm_client_singleton()


def get_embedding_client() -> CohereEmbeddingClient:
    """FastAPI dependency for the embedding client."""
    return _get_embedding_client_singleton()


def get_classifier_client() -> ClassifierClient:
    """FastAPI dependency for the intent classifier client."""
    return _get_classifier_client_singleton()


def get_widget_token_service() -> WidgetTokenService:
    """FastAPI dependency for the widget token service."""
    return _get_widget_token_service_singleton()


def warm_singletons() -> None:
    """Construct singletons up front during app startup.

    Called from ``main.py`` lifespan. Failures are swallowed at startup
    (so the app can boot without API keys for unrelated routes) and
    re-raised on first use via the same getters above.
    """
    try:
        _get_llm_client_singleton()
    except ExternalServiceError:
        pass
    try:
        _get_embedding_client_singleton()
    except ExternalServiceError:
        pass
    _get_classifier_client_singleton()
    try:
        _get_widget_token_service_singleton()
    except ValueError:
        # Missing/empty WIDGET_TOKEN_SECRET — surfaced on first /chat or
        # /widgets/session call. App still boots so unrelated routes work.
        pass


def reset_singletons() -> None:
    """Clear singleton caches. Used by tests; not called at runtime."""
    _get_llm_client_singleton.cache_clear()
    _get_embedding_client_singleton.cache_clear()
    _get_classifier_client_singleton.cache_clear()
    _get_widget_token_service_singleton.cache_clear()


# ----- Database session providers --------------------------------------------
# Pattern: yield a session, commit on success, rollback on exception. This is
# the fix for the silent-rollback bug — the previous implementation never
# committed, so every persistence INSERT was dropped at request end.
async def get_plain_db_session() -> AsyncGenerator[AsyncSession, None]:
    """Plain (non-RLS) session for pre-auth routes (e.g. widget token mint).

    Commits on clean exit, rolls back on exception. Use only for endpoints
    that don't yet have a verified tenant context. The widgets RLS policy
    is intentionally relaxed for this case.
    """
    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


# ----- Per-request services --------------------------------------------------
async def get_memory_service(
    redis: aioredis.Redis = Depends(get_redis),
) -> MemoryService:
    settings = get_settings()
    return MemoryService(
        redis=redis,
        ttl_seconds=settings.MEMORY_TTL_SECONDS,
        max_entries=settings.MEMORY_MAX_ENTRIES,
    )


async def get_widget_claims(
    authorization: str | None = Header(
        default=None,
        alias="Authorization",
        description="Bearer widget session token from POST /widgets/session.",
    ),
    token_service: WidgetTokenService = Depends(get_widget_token_service),
) -> WidgetTokenClaims:
    """Verify a Bearer JWT and return the validated claims.

    Maps every token failure to HTTP 401 with a machine-readable ``code`` so
    the widget runtime can distinguish ``token_expired`` (refresh and retry)
    from ``invalid_token`` (re-issue a fresh session).
    """
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(
            status_code=401,
            detail={"detail": "Missing bearer token", "code": "auth_required"},
        )
    token = authorization.split(" ", 1)[1].strip()

    try:
        return token_service.verify(token)
    except WidgetTokenError as exc:
        raise HTTPException(
            status_code=401, detail={"detail": exc.reason, "code": exc.code}
        ) from exc


async def get_tenant_id(
    claims: WidgetTokenClaims = Depends(get_widget_claims),
) -> UUID:
    """Authoritative tenant id for the request. Sourced ONLY from the token."""
    return claims.tenant_id


async def get_visitor_session_id(
    claims: WidgetTokenClaims = Depends(get_widget_claims),
) -> UUID:
    return claims.visitor_session_id


async def get_widget_id(
    claims: WidgetTokenClaims = Depends(get_widget_claims),
) -> UUID:
    return claims.widget_id


# Concrete RLS-scoped session for chat-path requests. Routed through
# ``get_tenant_id`` so the dependency tree shares a single token verification.
async def get_tenant_rls_session(
    tenant_id: UUID = Depends(get_tenant_id),
) -> AsyncGenerator[AsyncSession, None]:
    factory = get_session_factory()
    async with factory() as session:
        await set_tenant_context(session, tenant_id)
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await reset_tenant_context(session)


async def get_admin_tenant_id(
    x_tenant_id: str = Header(
        ...,
        alias="X-Tenant-Id",
        description="Tenant id the admin caller is operating on. Transitional — "
        "replaced by user-derived tenant once Owner A admin auth ships.",
    ),
) -> UUID:
    """Parse the admin ``X-Tenant-Id`` header into a UUID.

    Single source of truth for the admin tenant within a request. Both
    the RLS session and the route handlers read it from here, so the
    session's RLS variable and the service's ``tenant_id`` argument can
    never disagree.
    """
    try:
        return UUID(x_tenant_id)
    except (ValueError, TypeError) as exc:
        raise HTTPException(
            status_code=400,
            detail={"detail": "X-Tenant-Id must be a UUID", "code": "invalid_tenant"},
        ) from exc


async def get_admin_rls_session(
    tenant_id: UUID = Depends(get_admin_tenant_id),
) -> AsyncGenerator[AsyncSession, None]:
    """RLS-scoped session for the admin CMS surface.

    Transitional dependency: tenant id comes from ``X-Tenant-Id``
    (paired with ``require_service_token`` at the route layer). Mirrors
    ``get_tenant_rls_session`` in every other respect — commits on clean
    exit, rolls back on exception, resets the RLS session variable on
    the way out. Replaced by a verified-user-derived tenant once Owner
    A ships ``/auth``.
    """
    factory = get_session_factory()
    async with factory() as session:
        await set_tenant_context(session, tenant_id)
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await reset_tenant_context(session)


def get_rag_service(
    session: AsyncSession = Depends(get_tenant_rls_session),
    embedding_client: CohereEmbeddingClient = Depends(get_embedding_client),
) -> RagService:
    return RagService(session=session, embedding_client=embedding_client)


def get_conversation_service(
    session: AsyncSession = Depends(get_tenant_rls_session),
) -> ConversationService:
    return ConversationService(session=session)


def get_lead_service(
    session: AsyncSession = Depends(get_tenant_rls_session),
    settings: Settings = Depends(get_settings),
) -> LeadService:
    return LeadService(session=session, settings=settings)


def get_escalation_service(
    session: AsyncSession = Depends(get_tenant_rls_session),
    conversations: ConversationService = Depends(get_conversation_service),
) -> EscalationService:
    return EscalationService(session=session, conversation_service=conversations)


def get_admin_rag_service(
    session: AsyncSession = Depends(get_admin_rls_session),
    embedding_client: CohereEmbeddingClient = Depends(get_embedding_client),
) -> RagService:
    """RagService scoped to an admin-tenant session.

    Distinct from ``get_rag_service`` (widget-auth path) so the admin
    CMS routes don't pull in the widget token dependency chain. The
    underlying ``RagService`` is identical — only the session source
    differs.
    """
    return RagService(session=session, embedding_client=embedding_client)


def get_cms_page_service(
    session: AsyncSession = Depends(get_admin_rls_session),
    rag_service: RagService = Depends(get_admin_rag_service),
) -> CmsPageService:
    """Admin CMS ingestion. Page + chunks written under the same session
    so a failed embedding pass rolls the page row back with it."""
    return CmsPageService(session=session, rag_service=rag_service)


def get_admin_conversation_service(
    session: AsyncSession = Depends(get_admin_rls_session),
) -> ConversationService:
    """ConversationService scoped to an admin-tenant session.

    Distinct from ``get_conversation_service`` (widget-auth path) so the
    admin escalations route doesn't pull in the widget token dependency
    chain. The underlying service is identical — only the session source
    differs.
    """
    return ConversationService(session=session)


def get_admin_lead_service(
    session: AsyncSession = Depends(get_admin_rls_session),
    settings: Settings = Depends(get_settings),
) -> LeadService:
    """LeadService bound to the admin RLS session for ``/leads`` admin routes.

    Same business logic as ``get_lead_service`` (widget path); only the
    session source differs. The route layer is the one that gates on
    ``X-Service-Token`` + ``X-Tenant-Id``.
    """
    return LeadService(session=session, settings=settings)


def get_admin_escalation_service(
    session: AsyncSession = Depends(get_admin_rls_session),
    conversations: ConversationService = Depends(get_admin_conversation_service),
) -> EscalationService:
    """EscalationService bound to the admin RLS session for ``/escalations`` routes.

    The injected ``ConversationService`` is unused by ``list_escalations`` /
    ``update_escalation`` — it's a constructor requirement because the same
    service also exposes ``create`` (which does flip the conversation
    status). Keeping one service type avoids duplicating the model.
    """
    return EscalationService(session=session, conversation_service=conversations)

def get_widget_service(
    session: AsyncSession = Depends(get_plain_db_session),
) -> WidgetService:
    """Widget lookup. NOT RLS-scoped — token issuance happens before the
    request has an authenticated ``tenant_id``. The ``widgets`` RLS policy
    permits reads when ``app.tenant_id`` is unset; see migration 0003.
    """
    return WidgetService(session=session)


def get_tool_registry(
    rag_service: RagService = Depends(get_rag_service),
    lead_service: LeadService = Depends(get_lead_service),
    escalation_service: EscalationService = Depends(get_escalation_service),
) -> ToolRegistry:
    return build_registry(
        rag_service=rag_service,
        lead_service=lead_service,
        escalation_service=escalation_service,
    )


def get_router_service(
    classifier: ClassifierClient = Depends(get_classifier_client),
    settings: Settings = Depends(get_settings),
) -> RouterService:
    return RouterService(
        classifier_client=classifier,
        confidence_threshold=settings.ROUTER_CONFIDENCE_THRESHOLD,
    )


def get_agent_service(
    llm: GroqLLMClient = Depends(get_llm_client),
    memory: MemoryService = Depends(get_memory_service),
    tools: ToolRegistry = Depends(get_tool_registry),
    settings: Settings = Depends(get_settings),
) -> AgentService:
    return AgentService(
        llm_client=llm,
        memory_service=memory,
        tool_registry=tools,
        max_iterations=settings.AGENT_MAX_TOOL_ITERATIONS,
        max_output_tokens=settings.AGENT_MAX_OUTPUT_TOKENS,
    )


def get_faq_workflow(
    rag: RagService = Depends(get_rag_service),
    llm: GroqLLMClient = Depends(get_llm_client),
) -> FaqWorkflow:
    return FaqWorkflow(rag_service=rag, llm_client=llm)


def get_sales_workflow(
    rag: RagService = Depends(get_rag_service),
    llm: GroqLLMClient = Depends(get_llm_client),
) -> SalesWorkflow:
    return SalesWorkflow(rag_service=rag, llm_client=llm)


def get_human_workflow(
    escalation: EscalationService = Depends(get_escalation_service),
) -> HumanWorkflow:
    return HumanWorkflow(escalation_service=escalation)


def get_chat_orchestrator(
    router: RouterService = Depends(get_router_service),
    agent: AgentService = Depends(get_agent_service),
    memory: MemoryService = Depends(get_memory_service),
    escalation: EscalationService = Depends(get_escalation_service),
    conversations: ConversationService = Depends(get_conversation_service),
    faq: FaqWorkflow = Depends(get_faq_workflow),
    sales: SalesWorkflow = Depends(get_sales_workflow),
    human: HumanWorkflow = Depends(get_human_workflow),
) -> ChatOrchestrator:
    return ChatOrchestrator(
        router_service=router,
        agent_service=agent,
        memory_service=memory,
        escalation_service=escalation,
        conversation_service=conversations,
        faq_workflow=faq,
        sales_workflow=sales,
        human_workflow=human,
        guardrail_client=PassthroughGuardrailClient(),
    )
