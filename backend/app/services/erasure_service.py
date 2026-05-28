"""Tenant erasure service — full implementation in spec 015.

This stub allows delete_tenant to fire asyncio.create_task(purge_tenant(...))
without crashing. When spec 015 is implemented, replace this body with the
real purge logic (Postgres rows, pgvector embeddings, MinIO blobs, Redis sessions).
"""

from __future__ import annotations

from uuid import UUID

import structlog

log = structlog.get_logger(__name__)


async def purge_tenant(tenant_id: UUID) -> None:
    """Stub — logs intent. Real purge implemented in spec 015."""
    log.info("erasure_service.purge_tenant.stub", tenant_id=str(tenant_id))
