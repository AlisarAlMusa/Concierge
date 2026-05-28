from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_log import AuditLog


async def list_audit_logs(
    session: AsyncSession,
    limit: int = 50,
    offset: int = 0,
    tenant_id: UUID | None = None,
) -> list[AuditLog]:
    stmt = select(AuditLog).order_by(AuditLog.created_at.desc())
    if tenant_id is not None:
        stmt = stmt.where(AuditLog.tenant_id == tenant_id)
    stmt = stmt.limit(limit).offset(offset)
    result = await session.execute(stmt)
    return list(result.scalars().all())
