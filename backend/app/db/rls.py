from collections.abc import AsyncGenerator
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session_factory


async def set_tenant_context(session: AsyncSession, tenant_id: UUID) -> None:
    """Set the Postgres RLS session variable for the current transaction."""
    await session.execute(
        text("SELECT set_config('app.tenant_id', :tid, true)"),
        {"tid": str(tenant_id)},
    )


async def reset_tenant_context(session: AsyncSession) -> None:
    """Clear the Postgres RLS session variable.

    Must be called after every request that set the context — pooled connections
    persist session variables, and a leftover value is a cross-tenant leak.
    """
    await session.execute(
        text("SELECT set_config('app.tenant_id', '', true)"),
    )


async def get_tenant_db_session(tenant_id: UUID) -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency: yields an AsyncSession with RLS context set + always reset."""
    factory = get_session_factory()
    async with factory() as session:
        await set_tenant_context(session, tenant_id)
        try:
            yield session
        finally:
            await reset_tenant_context(session)
