"""Redis-backed fixed-window rate limiter (Spec 013 FR-007 – FR-012).

Window strategy: fixed window using atomic INCR + EXPIRE NX. The EXPIRE NX
pattern means the TTL is set ONLY on the first increment within a window,
so subsequent increments do not reset it — identical to the login rate limiter
in auth_service.

Key format: ``ratelimit:{scope}:{id}``
  scope = ``tenant`` | ``widget`` | ``session``
  id    = UUID (tenant_id, widget_id, or visitor_session_id)

Fail-open (FR-010 edge case): if Redis is unreachable, a warning is logged and
the request proceeds. Availability beats a false 429.

Tenant isolation (FR-012): each tenant's key is independent. Tenant A
exhausting its counter has zero effect on Tenant B's keys.
"""

from __future__ import annotations

from uuid import UUID

import redis.asyncio as aioredis
import structlog
from fastapi import HTTPException

log = structlog.get_logger(__name__)


class RateLimitService:
    """Fixed-window rate limiter. One instance per request (stateless beyond Redis)."""

    def __init__(
        self,
        redis: aioredis.Redis,
        *,
        tenant_limit: int,
        widget_limit: int,
        window_seconds: int,
        session_lead_limit: int,
        session_lead_window_seconds: int,
    ) -> None:
        self._redis = redis
        self._tenant_limit = tenant_limit
        self._widget_limit = widget_limit
        self._window_seconds = window_seconds
        self._session_lead_limit = session_lead_limit
        self._session_lead_window_seconds = session_lead_window_seconds

    # ------------------------------------------------------------------
    # Chat rate limits (FR-007 per-tenant, FR-008 per-widget)
    # ------------------------------------------------------------------

    async def check_tenant_chat_limit(self, tenant_id: UUID) -> None:
        """Raise HTTP 429 if the tenant has exceeded their chat rate limit.

        Fail-open: if Redis is unavailable the check passes with a warning.
        """
        await self._check("tenant", str(tenant_id), self._tenant_limit, self._window_seconds)

    async def check_widget_chat_limit(self, widget_id: UUID) -> None:
        """Raise HTTP 429 if the widget has exceeded its chat rate limit."""
        await self._check("widget", str(widget_id), self._widget_limit, self._window_seconds)

    # ------------------------------------------------------------------
    # Lead capture rate limit (FR-009 per-visitor-session)
    # ------------------------------------------------------------------

    async def check_session_lead_limit(self, visitor_session_id: UUID) -> None:
        """Raise HTTP 429 if the visitor session has exceeded the lead capture limit."""
        await self._check(
            "session",
            str(visitor_session_id),
            self._session_lead_limit,
            self._session_lead_window_seconds,
        )

    # ------------------------------------------------------------------
    # Internal helper
    # ------------------------------------------------------------------

    async def _check(
        self,
        scope: str,
        identifier: str,
        limit: int,
        window_seconds: int,
    ) -> None:
        """Atomic INCR + EXPIRE NX. Raises 429 with Retry-After if exceeded.

        Two Redis commands are pipelined so they execute atomically from the
        perspective of this client. The EXPIRE NX ensures the window starts on
        the first request and expires exactly ``window_seconds`` later,
        regardless of how many increments happen in between.
        """
        key = f"ratelimit:{scope}:{identifier}"
        try:
            pipe = self._redis.pipeline()
            pipe.incr(key)
            pipe.expire(key, window_seconds, nx=True)
            results = await pipe.execute()
            count: int = results[0]
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "rate_limit.redis_unavailable",
                scope=scope,
                error=str(exc),
            )
            return  # fail-open

        if count > limit:
            try:
                ttl: int = await self._redis.ttl(key)
            except Exception:  # noqa: BLE001
                ttl = window_seconds
            retry_after = max(1, ttl)
            log.info(
                "rate_limit.exceeded",
                scope=scope,
                identifier=identifier,
                count=count,
                limit=limit,
                retry_after=retry_after,
            )
            raise HTTPException(
                status_code=429,
                detail=f"Rate limit exceeded for {scope}. Try again in {retry_after}s.",
                headers={"Retry-After": str(retry_after)},
            )
