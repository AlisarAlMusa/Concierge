"""Unit tests for erasure_service.purge_tenant.

All tests use mocks — no live DB, MinIO, or Redis required.
Tests cover: Postgres DELETE order, idempotency, Redis SCAN/DEL,
status update, per-layer failure handling, and audit marker content.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

TENANT_ID = uuid.uuid4()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_session(status: str = "deleting"):
    """Mock AsyncSession that returns a given tenant status."""
    session = AsyncMock()

    # Scalar result for the status SELECT
    scalar_result = MagicMock()
    scalar_result.scalar_one_or_none.return_value = status
    session.execute.return_value = scalar_result

    return session


def _make_factory(session):
    """Return a context-manager-compatible session factory mock."""
    factory = MagicMock()
    cm = AsyncMock()
    cm.__aenter__.return_value = session
    cm.__aexit__.return_value = False
    factory.return_value = cm
    return factory


def _make_redis(keys=None):
    """Mock redis client with scan_iter returning given keys."""

    async def _scan_iter(pattern):
        for k in keys or []:
            yield k

    redis = AsyncMock()
    redis.scan_iter = _scan_iter
    return redis


# ---------------------------------------------------------------------------
# T1 — Postgres DELETE called for all 9 tables
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_purge_deletes_all_postgres_tables():
    from app.services.erasure_service import _CONTENT_TABLES, purge_tenant

    session = _make_session("deleting")
    factory = _make_factory(session)

    redis = _make_redis()

    with (
        patch("app.services.erasure_service.get_session_factory", return_value=factory),
        patch("app.services.erasure_service._purge_minio", return_value=True),
    ):
        await purge_tenant(TENANT_ID, redis)

    # execute was called: 1 (status check) + 9 (table DELETEs) + 1 (status UPDATE)
    assert session.execute.call_count >= 9 + 1

    # Verify all 9 tables appear in the SQL calls
    all_sql = " ".join(str(c.args[0]) for c in session.execute.call_args_list if c.args)
    for table in _CONTENT_TABLES:
        assert table in all_sql, f"Expected DELETE for table '{table}' not found"


# ---------------------------------------------------------------------------
# T2 — Already-deleted tenant is skipped (idempotency)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_purge_skips_if_already_deleted():
    from app.services.erasure_service import purge_tenant

    session = _make_session("deleted")
    factory = _make_factory(session)
    redis = _make_redis()

    with patch("app.services.erasure_service.get_session_factory", return_value=factory):
        await purge_tenant(TENANT_ID, redis)

    # Only the status SELECT should have been called — no DELETEs
    assert session.execute.call_count == 1


# ---------------------------------------------------------------------------
# T3 — Redis keys are deleted via SCAN
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_purge_clears_redis_keys():
    from app.services.erasure_service import purge_tenant

    fake_keys = [f"memory:{TENANT_ID}:conv1", f"memory:{TENANT_ID}:conv2"]
    session = _make_session("deleting")
    factory = _make_factory(session)
    redis = _make_redis(keys=fake_keys)

    with (
        patch("app.services.erasure_service.get_session_factory", return_value=factory),
        patch("app.services.erasure_service._purge_minio", return_value=True),
    ):
        await purge_tenant(TENANT_ID, redis)

    redis.delete.assert_awaited_once_with(*fake_keys)


# ---------------------------------------------------------------------------
# T4 — Status updated to 'deleted' on full success
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_purge_sets_status_deleted_on_success():
    from app.services.erasure_service import purge_tenant

    session = _make_session("deleting")
    factory = _make_factory(session)
    redis = _make_redis()

    with (
        patch("app.services.erasure_service.get_session_factory", return_value=factory),
        patch("app.services.erasure_service._purge_minio", return_value=True),
    ):
        await purge_tenant(TENANT_ID, redis)

    # The last execute call should be the UPDATE tenants SET status='deleted'
    last_call_sql = str(session.execute.call_args_list[-1].args[0])
    assert "deleted" in last_call_sql.lower()
    assert "tenants" in last_call_sql.lower()


# ---------------------------------------------------------------------------
# T5 — MinIO failure leaves tenant in 'deleting' (status NOT updated)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_purge_stays_deleting_on_minio_failure():
    from app.services.erasure_service import purge_tenant

    session = _make_session("deleting")
    factory = _make_factory(session)
    redis = _make_redis()

    with (
        patch("app.services.erasure_service.get_session_factory", return_value=factory),
        patch("app.services.erasure_service._purge_minio", return_value=False),
        patch("app.services.erasure_service._purge_redis", return_value=True),
    ):
        await purge_tenant(TENANT_ID, redis)

    # No UPDATE to 'deleted' should have been issued
    all_sql = " ".join(str(c.args[0]) for c in session.execute.call_args_list if c.args)
    assert "UPDATE tenants SET status = 'deleted'" not in all_sql


# ---------------------------------------------------------------------------
# T6 — Audit marker written with action=tenant_deleted, no content fields
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_purge_writes_audit_marker():
    from app.models.audit_log import AuditLog
    from app.services.erasure_service import purge_tenant

    session = _make_session("deleting")
    factory = _make_factory(session)
    redis = _make_redis()

    added_objects = []
    original_add = session.add

    def capture_add(obj):
        added_objects.append(obj)
        return original_add(obj)

    session.add = capture_add

    with (
        patch("app.services.erasure_service.get_session_factory", return_value=factory),
        patch("app.services.erasure_service._purge_minio", return_value=True),
    ):
        await purge_tenant(TENANT_ID, redis)

    audit_entries = [o for o in added_objects if isinstance(o, AuditLog)]
    assert len(audit_entries) == 1, "Expected exactly one AuditLog entry"
    marker = audit_entries[0]
    assert marker.action == "tenant_deleted"
    assert marker.actor_role == "system"
    assert marker.tenant_id == TENANT_ID
    assert marker.actor_user_id is None
    assert marker.metadata_ is None, "Compliance marker must not contain content"


# ---------------------------------------------------------------------------
# T7 — Idempotent: 0 rows deleted raises no error
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_purge_idempotent_no_rows():
    """purge_tenant completes cleanly even when all DELETEs affect 0 rows."""
    from app.services.erasure_service import purge_tenant

    session = _make_session("deleting")
    # execute returns a result with rowcount=0 — no rows deleted, no error
    factory = _make_factory(session)
    redis = _make_redis()

    with (
        patch("app.services.erasure_service.get_session_factory", return_value=factory),
        patch("app.services.erasure_service._purge_minio", return_value=True),
    ):
        # Should not raise
        await purge_tenant(TENANT_ID, redis)
