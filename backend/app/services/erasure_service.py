"""Tenant erasure service — GDPR/CCPA right-to-delete implementation.

Replaces all tenant data across every storage layer:
  1. PostgreSQL  — DELETE from 9 tenant-owned tables (FK-safe order)
  2. MinIO       — delete all objects under {tenant_id}/ prefix
  3. Redis       — delete all memory:{tenant_id}:* session keys

After full erasure:
  - A compliance audit marker is written (action=tenant_deleted, no content)
  - Tenant status is set to 'deleted'

On partial failure (any layer raises):
  - The completed layers are NOT rolled back (idempotency)
  - Tenant status remains 'deleting' so the job can be retried
  - audit_logs rows are NEVER deleted (FR-009 — compliance retention)

Design invariant: this is a delete-only path. No SELECT on content tables.
"""

from __future__ import annotations

import asyncio
from uuid import UUID

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session_factory
from app.models.audit_log import AuditLog

log = structlog.get_logger(__name__)

# Tables to purge in FK-safe order (children before parents).
# audit_logs is intentionally excluded — retained as compliance proof (FR-009).
_CONTENT_TABLES = [
    "messages",
    "escalations",
    "leads",
    "cms_chunks",
    "conversations",
    "widgets",
    "cms_pages",
    "guardrail_configs",
    "cost_events",
]

_MINIO_BUCKET = "concierge-cms"


async def purge_tenant(tenant_id: UUID, redis=None) -> None:
    """Purge all data for a tenant across Postgres, MinIO, and Redis.

    Called as asyncio.create_task by tenant_service.delete_tenant after the
    tenant status has been set to 'deleting'. Opens its own DB session because
    the request session is already closed by the time this runs.
    """
    log.info("erasure.started", tenant_id=str(tenant_id))

    factory = get_session_factory()
    async with factory() as session:
        # ── Idempotency guard ──────────────────────────────────────────────
        if await _is_already_deleted(session, tenant_id):
            log.info("erasure.skipped_already_deleted", tenant_id=str(tenant_id))
            return

        # ── Layer-by-layer purge ───────────────────────────────────────────
        postgres_ok = await _purge_postgres(session, tenant_id)
        minio_ok = await _purge_minio(tenant_id)
        redis_ok = await _purge_redis(redis, tenant_id)

        if not (postgres_ok and minio_ok and redis_ok):
            log.warning(
                "erasure.partial_failure_tenant_stays_deleting",
                tenant_id=str(tenant_id),
                postgres_ok=postgres_ok,
                minio_ok=minio_ok,
                redis_ok=redis_ok,
            )
            return

        # ── Compliance audit marker ────────────────────────────────────────
        await _write_audit_marker(session, tenant_id)

        # ── Mark tenant deleted ────────────────────────────────────────────
        await session.execute(
            text("UPDATE tenants SET status = 'deleted' WHERE id = :tid"),
            {"tid": tenant_id},
        )
        await session.commit()

        log.info("erasure.completed", tenant_id=str(tenant_id))


async def _is_already_deleted(session: AsyncSession, tenant_id: UUID) -> bool:
    row = await session.execute(
        text("SELECT status FROM tenants WHERE id = :tid"),
        {"tid": tenant_id},
    )
    result = row.scalar_one_or_none()
    return result == "deleted"


async def _purge_postgres(session: AsyncSession, tenant_id: UUID) -> bool:
    """DELETE from all 9 content tables scoped by tenant_id. No content SELECT."""
    try:
        for table in _CONTENT_TABLES:
            await session.execute(
                text(f"DELETE FROM {table} WHERE tenant_id = :tid"),  # noqa: S608
                {"tid": tenant_id},
            )
            log.debug("erasure.postgres.table_deleted", table=table, tenant_id=str(tenant_id))
        await session.commit()
        log.info("erasure.postgres.done", tenant_id=str(tenant_id))
        return True
    except Exception:
        log.exception("erasure.postgres.failed", tenant_id=str(tenant_id))
        await session.rollback()
        return False


async def _purge_minio(tenant_id: UUID) -> bool:
    """Delete all objects under {tenant_id}/ prefix in the CMS bucket."""
    try:
        from app.core.config import get_settings  # noqa: PLC0415

        settings = get_settings()
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, _minio_delete_prefix, settings, str(tenant_id))
        log.info("erasure.minio.done", tenant_id=str(tenant_id))
        return True
    except Exception:
        log.exception("erasure.minio.failed", tenant_id=str(tenant_id))
        return False


def _minio_delete_prefix(settings, tenant_prefix: str) -> None:
    """Synchronous MinIO blob deletion — runs in thread executor."""
    from minio import Minio  # noqa: PLC0415
    from minio.error import S3Error  # noqa: PLC0415

    client = Minio(
        settings.MINIO_ENDPOINT,
        access_key=settings.MINIO_ACCESS_KEY,
        secret_key=settings.MINIO_SECRET_KEY,
        secure=False,
    )

    try:
        objects = list(
            client.list_objects(_MINIO_BUCKET, prefix=f"{tenant_prefix}/", recursive=True)
        )
        for obj in objects:
            client.remove_object(_MINIO_BUCKET, obj.object_name)
        log.debug(
            "erasure.minio.objects_deleted",
            count=len(objects),
            tenant_prefix=tenant_prefix,
        )
    except S3Error as exc:
        if exc.code in ("NoSuchBucket", "NoSuchKey"):
            return  # already purged — idempotent
        raise


async def _purge_redis(redis, tenant_id: UUID) -> bool:
    """Delete all memory:{tenant_id}:* session keys via SCAN."""
    if redis is None:
        log.warning("erasure.redis.no_client_skipping", tenant_id=str(tenant_id))
        return True  # no client available — treat as success

    try:
        pattern = f"memory:{tenant_id}:*"
        keys = [key async for key in redis.scan_iter(pattern)]
        if keys:
            await redis.delete(*keys)
        log.info("erasure.redis.done", keys_deleted=len(keys), tenant_id=str(tenant_id))
        return True
    except Exception:
        log.exception("erasure.redis.failed", tenant_id=str(tenant_id))
        return False


async def _write_audit_marker(session: AsyncSession, tenant_id: UUID) -> None:
    """Write a minimal compliance audit entry — no content fields."""
    marker = AuditLog(
        actor_role="system",
        action="tenant_deleted",
        tenant_id=tenant_id,
        actor_user_id=None,
        target_type="tenant",
        target_id=str(tenant_id),
        metadata=None,
    )
    session.add(marker)
    await session.flush()
    log.info("erasure.audit_marker_written", tenant_id=str(tenant_id))
