"""CMS chunk repository — tenant-scoped data access for ``cms_chunks``.

Every statement carries an explicit ``WHERE tenant_id = $1`` clause; RLS
on ``cms_chunks`` is the second wall. The repository owns the raw
pgvector SELECT shape (cosine distance + optional JOIN to ``cms_pages``)
so ``RagService`` only deals with embedding math, scoring, and DTOs.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chunk import CmsChunk
from app.models.cms import CmsPage, CmsPageStatus


async def search_top_k(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    query_vector: list[float],
    limit: int,
    published_only: bool,
) -> list[Any]:
    """Top-K cosine-similar chunks for ``query_vector`` within ``tenant_id``.

    When ``published_only=True`` (production default) the SELECT JOINs
    ``cms_pages`` and discards chunks whose source page is in ``draft``
    status — the v1 retrieval improvement. When ``False`` the chunks-only
    SELECT is preserved for the eval baseline. Returns rows with
    ``text``, ``page_id``, and ``distance`` attributes; the caller maps
    distance to score and builds the DTO.
    """
    distance_col = CmsChunk.embedding.cosine_distance(query_vector).label("distance")
    stmt = (
        select(CmsChunk.text, CmsChunk.page_id, distance_col)
        .where(CmsChunk.tenant_id == tenant_id)
        .order_by(distance_col)
        .limit(limit)
    )
    if published_only:
        stmt = stmt.join(CmsPage, CmsPage.id == CmsChunk.page_id).where(
            CmsPage.status == CmsPageStatus.published
        )
    return list((await session.execute(stmt)).all())


async def delete_for_page(
    session: AsyncSession, *, tenant_id: UUID, page_id: UUID
) -> int:
    """Delete every chunk for ``(tenant_id, page_id)`` and return the rowcount.

    Flushes once so the caller (re)index path has a single, predictable
    DB round-trip for the delete portion — matches the spec's
    delete-then-insert idempotency invariant.
    """
    result = await session.execute(
        delete(CmsChunk).where(
            CmsChunk.tenant_id == tenant_id,
            CmsChunk.page_id == page_id,
        )
    )
    await session.flush()
    return int(result.rowcount or 0)


async def bulk_insert(session: AsyncSession, chunks: list[CmsChunk]) -> None:
    """Stage a batch of ``CmsChunk`` rows for INSERT (no flush).

    The flush is intentionally deferred to the caller (or to the
    enclosing transaction's commit) so ``RagService.index_page`` keeps a
    single flush at the end of its delete-then-insert sequence.
    """
    if not chunks:
        return
    session.add_all(chunks)
