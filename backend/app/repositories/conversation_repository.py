"""Conversation/Message repository — tenant-scoped data access.

Every read carries an explicit ``WHERE tenant_id = $1`` clause; RLS on
``conversations`` / ``messages`` is the second wall. Writes flush once so
the caller's transaction boundary stays predictable.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.conversation import Conversation, Message


async def get_for_tenant(
    session: AsyncSession, *, tenant_id: UUID, conversation_id: UUID
) -> Conversation | None:
    """Return one conversation or ``None`` if absent / cross-tenant."""
    stmt = select(Conversation).where(
        Conversation.tenant_id == tenant_id,
        Conversation.id == conversation_id,
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def add(session: AsyncSession, conversation: Conversation) -> None:
    """Stage a new ``Conversation`` row and flush."""
    session.add(conversation)
    await session.flush()


async def add_message(session: AsyncSession, message: Message) -> None:
    """Stage a new ``Message`` row and flush."""
    session.add(message)
    await session.flush()


async def flush_pending(session: AsyncSession) -> None:
    """Flush in-place mutations on a row previously fetched through the repo."""
    await session.flush()
