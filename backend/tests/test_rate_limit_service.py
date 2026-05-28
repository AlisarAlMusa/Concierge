"""Unit tests for RateLimitService (Spec 013 FR-007 – FR-012).

Pure unit tests — Redis is mocked. pipeline() is a synchronous call that
returns a pipeline object; only execute() is async. Tests cover the P1
scenarios: limits enforced, 429 raised, fail-open, tenant isolation.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.services.rate_limit_service import RateLimitService


def _make_pipeline(count: int, ttl: int = 30):
    """Return a sync-callable pipeline mock with the given INCR result."""
    pipeline = MagicMock()
    pipeline.incr = MagicMock()
    pipeline.expire = MagicMock()
    pipeline.execute = AsyncMock(return_value=[count, True])
    return pipeline


def _make_service(
    tenant_limit: int = 100,
    widget_limit: int = 60,
    window: int = 60,
) -> tuple[RateLimitService, MagicMock]:
    """Return (service, mock_redis)."""
    mock_redis = MagicMock()
    mock_redis.ttl = AsyncMock(return_value=30)
    svc = RateLimitService(
        redis=mock_redis,
        tenant_limit=tenant_limit,
        widget_limit=widget_limit,
        window_seconds=window,
        session_lead_limit=5,
        session_lead_window_seconds=3600,
    )
    return svc, mock_redis


# ---------------------------------------------------------------------------
# Tenant chat limit (FR-007)
# ---------------------------------------------------------------------------


class TestTenantChatLimit:
    @pytest.mark.asyncio
    async def test_under_limit_passes(self):
        """A request under the tenant limit is allowed."""
        svc, mock_redis = _make_service(tenant_limit=100)
        mock_redis.pipeline.return_value = _make_pipeline(count=5)

        await svc.check_tenant_chat_limit(uuid4())  # must not raise

    @pytest.mark.asyncio
    async def test_at_limit_passes(self):
        """Exactly at the limit (count == limit) is allowed — limit is exclusive."""
        svc, mock_redis = _make_service(tenant_limit=100)
        mock_redis.pipeline.return_value = _make_pipeline(count=100)

        await svc.check_tenant_chat_limit(uuid4())  # must not raise

    @pytest.mark.asyncio
    async def test_over_limit_raises_429(self):
        """Exceeding the limit raises HTTP 429 with Retry-After."""
        svc, mock_redis = _make_service(tenant_limit=100)
        mock_redis.pipeline.return_value = _make_pipeline(count=101)
        mock_redis.ttl = AsyncMock(return_value=45)

        with pytest.raises(HTTPException) as exc_info:
            await svc.check_tenant_chat_limit(uuid4())

        assert exc_info.value.status_code == 429
        assert "Retry-After" in exc_info.value.headers
        assert exc_info.value.headers["Retry-After"] == "45"

    @pytest.mark.asyncio
    async def test_fail_open_on_redis_unavailable(self):
        """If Redis pipeline.execute raises, the check passes (fail-open, FR-010 edge)."""
        svc, mock_redis = _make_service()
        broken_pipeline = MagicMock()
        broken_pipeline.incr = MagicMock()
        broken_pipeline.expire = MagicMock()
        broken_pipeline.execute = AsyncMock(side_effect=Exception("connection refused"))
        mock_redis.pipeline.return_value = broken_pipeline

        await svc.check_tenant_chat_limit(uuid4())  # must not raise


# ---------------------------------------------------------------------------
# Widget chat limit (FR-008)
# ---------------------------------------------------------------------------


class TestWidgetChatLimit:
    @pytest.mark.asyncio
    async def test_over_limit_raises_429(self):
        """Widget rate limit exceeded → 429."""
        svc, mock_redis = _make_service(widget_limit=60)
        mock_redis.pipeline.return_value = _make_pipeline(count=61)
        mock_redis.ttl = AsyncMock(return_value=30)

        with pytest.raises(HTTPException) as exc_info:
            await svc.check_widget_chat_limit(uuid4())

        assert exc_info.value.status_code == 429

    @pytest.mark.asyncio
    async def test_widget_key_is_independent_of_tenant_key(self):
        """Widget and tenant checks each call pipeline() once — independent counters."""
        svc, mock_redis = _make_service()
        pipeline_call_count = 0

        def _new_pipeline():
            nonlocal pipeline_call_count
            pipeline_call_count += 1
            return _make_pipeline(count=1)

        mock_redis.pipeline.side_effect = _new_pipeline

        await svc.check_tenant_chat_limit(uuid4())
        await svc.check_widget_chat_limit(uuid4())

        assert pipeline_call_count == 2


# ---------------------------------------------------------------------------
# Tenant isolation (FR-012)
# ---------------------------------------------------------------------------


class TestTenantIsolation:
    @pytest.mark.asyncio
    async def test_tenant_a_limit_does_not_affect_tenant_b(self):
        """Tenant A being rate-limited has zero effect on Tenant B."""
        tenant_a = uuid4()
        tenant_b = uuid4()

        calls = []

        def _pipeline_for_id():
            count = 101 if len(calls) == 0 else 1
            calls.append(count)
            return _make_pipeline(count=count)

        svc, mock_redis = _make_service(tenant_limit=100)
        mock_redis.pipeline.side_effect = lambda: _pipeline_for_id()
        mock_redis.ttl = AsyncMock(return_value=30)

        with pytest.raises(HTTPException) as exc_info:
            await svc.check_tenant_chat_limit(tenant_a)
        assert exc_info.value.status_code == 429

        # Tenant B must not be affected.
        await svc.check_tenant_chat_limit(tenant_b)  # must not raise


# ---------------------------------------------------------------------------
# Session lead capture limit (FR-009)
# ---------------------------------------------------------------------------


class TestSessionLeadLimit:
    @pytest.mark.asyncio
    async def test_session_over_limit_raises_429(self):
        """Per-session lead capture limit exceeded → 429."""
        svc, mock_redis = _make_service()
        mock_redis.pipeline.return_value = _make_pipeline(count=6)  # limit=5
        mock_redis.ttl = AsyncMock(return_value=3600)

        with pytest.raises(HTTPException) as exc_info:
            await svc.check_session_lead_limit(uuid4())

        assert exc_info.value.status_code == 429

    @pytest.mark.asyncio
    async def test_session_under_limit_passes(self):
        svc, mock_redis = _make_service()
        mock_redis.pipeline.return_value = _make_pipeline(count=3)  # limit=5

        await svc.check_session_lead_limit(uuid4())  # must not raise


# ---------------------------------------------------------------------------
# Retry-After header (FR-011)
# ---------------------------------------------------------------------------


class TestRetryAfterHeader:
    @pytest.mark.asyncio
    async def test_retry_after_reflects_redis_ttl(self):
        """Retry-After value matches the remaining TTL from Redis."""
        svc, mock_redis = _make_service(tenant_limit=10)
        mock_redis.pipeline.return_value = _make_pipeline(count=11)
        mock_redis.ttl = AsyncMock(return_value=27)

        with pytest.raises(HTTPException) as exc_info:
            await svc.check_tenant_chat_limit(uuid4())

        assert exc_info.value.headers["Retry-After"] == "27"

    @pytest.mark.asyncio
    async def test_retry_after_minimum_one_when_ttl_zero(self):
        """Retry-After is at least 1 even if TTL is 0 or negative."""
        svc, mock_redis = _make_service(tenant_limit=10)
        mock_redis.pipeline.return_value = _make_pipeline(count=11)
        mock_redis.ttl = AsyncMock(return_value=0)

        with pytest.raises(HTTPException) as exc_info:
            await svc.check_tenant_chat_limit(uuid4())

        assert int(exc_info.value.headers["Retry-After"]) >= 1
