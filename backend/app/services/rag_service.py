"""RagService — dense pgvector retrieval over tenant-isolated CMS chunks.

This is the single source of truth for the read side of RAG (the ``rag_search``
tool's bound service) and for the write side (CMS publishing's ``index_page`` /
``delete_page`` hooks). One service owns both paths so the corpus is shaped,
scored, and kept consistent in one place. See ``specs/rag-service/spec.md``.

Architectural invariants (frozen):

* Every SQL statement carries an explicit ``WHERE tenant_id = $1`` clause.
  RLS is defense-in-depth, not the primary mechanism. (Spec §6.1, §6.9.)
* ``search`` runs exactly one ``embed_query`` and one pgvector statement;
  no hybrid retrieval, no re-ranking, no query rewriting at MVP.
* As of the v1 retrieval improvement (``docs/EVALS.md``), the pgvector
  query also JOINs ``cms_pages`` and filters ``status = 'published'`` so
  the widget runtime never surfaces draft / unpublished chunks. The
  filter is on by default; ``published_only=False`` preserves the
  pre-improvement behavior for A/B comparison and admin debugging.
  (Spec 005 §publication semantics.)
* ``index_page`` is idempotent: delete-then-insert in the same transaction so
  re-indexing the same ``(tenant_id, page_id, content)`` yields the same final
  state. Empty content still issues the delete so previously-indexed chunks
  are wiped. (Spec §6.6, §6.7.)
* Vectors are always supplied by ``CohereEmbeddingClient``. RagService never
  accepts caller-provided embeddings. (Spec §6.12.)
* The cosine-distance → score formula is fixed: ``score = 1 - distance/2``,
  yielding ``[0, 1]`` higher-is-better for the normalized vectors Cohere
  returns. (Spec §5 "Distance and score".)
* Embedding dimension is asserted to be 1024 at insert time so a silent
  model swap surfaces immediately as ``ValueError``, not as a vector column
  type error deep inside Postgres. (Spec §6.10.)

Owner: Person B.
"""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

import structlog
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chunk import CMS_CHUNK_EMBEDDING_DIM, CmsChunk
from app.models.cms import CmsPage, CmsPageStatus
from app.services.chunking import chunk_page
from app.services.tools.rag_search import RagChunk, RagSearchResult

logger = structlog.get_logger(__name__)

DEFAULT_MAX_CHUNKS = 5
# pgvector's cosine distance is in [0, 2] for any unit vectors; Cohere v3
# returns normalized vectors so the bound holds and the score is well defined.
_COSINE_DISTANCE_MAX = 2.0


class EmbeddingClient(Protocol):
    """Structural type RagService needs from the embedding boundary.

    Defined locally so RagService doesn't import the concrete
    ``CohereEmbeddingClient`` and unit tests can pass a fake without
    subclassing. See ``specs/embedding-service/spec.md``.
    """

    async def embed_query(self, text: str) -> list[float]: ...

    async def embed_documents(self, texts: list[str]) -> list[list[float]]: ...


class RagService:
    """Dense retrieval and CMS-chunk lifecycle for one tenant-scoped session.

    Construction shape mirrors ``specs/rag-service/spec.md §4``. ``session`` is
    request-scoped (provided by ``get_tenant_db_session`` once DI lands);
    ``embedding_client`` is a process-singleton.
    """

    def __init__(
        self,
        *,
        session: AsyncSession,
        embedding_client: EmbeddingClient,
        default_max_chunks: int = DEFAULT_MAX_CHUNKS,
    ) -> None:
        if default_max_chunks < 1:
            raise ValueError("default_max_chunks must be >= 1")
        self._session = session
        self._embedding = embedding_client
        self._default_max_chunks = default_max_chunks

    # ----- read path --------------------------------------------------------
    async def search(
        self,
        *,
        query: str,
        tenant_id: UUID,
        max_chunks: int = DEFAULT_MAX_CHUNKS,
        published_only: bool = True,
    ) -> RagSearchResult:
        """Top-K cosine-similar chunks for ``query`` within ``tenant_id``.

        With ``published_only=True`` (the default and the production path)
        the query JOINs ``cms_pages`` and discards chunks whose source page
        is in ``draft`` status. This is the v1 retrieval improvement
        described in ``docs/EVALS.md``: it ensures visitor-facing answers
        never quote half-finished or retracted pages, even if those pages
        were previously published and still have indexed chunks. The
        composite index ``ix_cms_pages_tenant_status`` keeps the JOIN
        cheap.

        ``published_only=False`` preserves the pre-improvement behavior;
        it exists only for the evaluation script's A/B comparison and for
        admin debugging — production callers (the agent's ``rag_search``
        tool) always use the default.

        Returns an empty ``RagSearchResult`` for empty/whitespace-only queries
        without calling the embedding client or the database — the agent's
        rag_search tool relies on this short-circuit. (Spec §6.4.)
        """
        if not query or not query.strip():
            return RagSearchResult(chunks=[], total_found=0)
        if max_chunks < 1:
            raise ValueError("max_chunks must be >= 1")

        query_vec = await self._embedding.embed_query(query)

        # Explicit tenant filter at the SQL layer — RLS is the second wall.
        distance_col = CmsChunk.embedding.cosine_distance(query_vec).label("distance")
        stmt = (
            select(CmsChunk.text, CmsChunk.page_id, distance_col)
            .where(CmsChunk.tenant_id == tenant_id)
            .order_by(distance_col)
            .limit(max_chunks)
        )
        if published_only:
            # JOIN against cms_pages so retrieval reflects the live
            # publication state, not the historical "was once published"
            # state baked into cms_chunks. The composite index
            # (tenant_id, status) makes this a cheap index-only filter.
            stmt = stmt.join(CmsPage, CmsPage.id == CmsChunk.page_id).where(
                CmsPage.status == CmsPageStatus.published
            )

        rows = (await self._session.execute(stmt)).all()
        chunks = [
            RagChunk(
                text=row.text,
                source_page_id=row.page_id,
                score=_distance_to_score(row.distance),
            )
            for row in rows
        ]

        logger.info(
            "rag.search.completed",
            tenant_id=str(tenant_id),
            query_chars=len(query),
            chunks_returned=len(chunks),
            max_chunks=max_chunks,
            published_only=published_only,
        )
        return RagSearchResult(chunks=chunks, total_found=len(chunks))

    # ----- write path -------------------------------------------------------
    async def index_page(
        self,
        *,
        tenant_id: UUID,
        page_id: UUID,
        content: str,
    ) -> int:
        """Idempotently (re)index a CMS page.

        Always deletes any existing chunks for ``(tenant_id, page_id)`` before
        inserting the new set — this preserves the spec's idempotency invariant
        even when ``content`` is cleared to empty (the final state must be zero
        chunks for the page, not stale leftover rows).

        Returns the number of chunks written (zero for empty content).
        """
        chunks = chunk_page(page_id=page_id, content=content)

        embeddings: list[list[float]] = []
        if chunks:
            embeddings = await self._embedding.embed_documents([c.text for c in chunks])
            _assert_embedding_dims(embeddings)

        # Delete-then-insert, in the caller's transaction. The caller (route /
        # CMS publishing flow) commits when the request completes.
        await self._session.execute(
            delete(CmsChunk).where(
                CmsChunk.tenant_id == tenant_id,
                CmsChunk.page_id == page_id,
            )
        )

        if chunks:
            rows = [
                CmsChunk(
                    tenant_id=tenant_id,
                    page_id=page_id,
                    chunk_index=chunk.chunk_index,
                    text=chunk.text,
                    embedding=vec,
                )
                for chunk, vec in zip(chunks, embeddings, strict=True)
            ]
            self._session.add_all(rows)

        await self._session.flush()
        logger.info(
            "rag.index_page.completed",
            tenant_id=str(tenant_id),
            page_id=str(page_id),
            chunks_written=len(chunks),
        )
        return len(chunks)

    async def delete_page(
        self,
        *,
        tenant_id: UUID,
        page_id: UUID,
    ) -> int:
        """Remove every chunk for ``(tenant_id, page_id)``.

        Used by the CMS publishing flow on unpublish. Returns ``rowcount`` so
        callers (and admin tooling) can confirm whether anything was deleted.
        """
        result = await self._session.execute(
            delete(CmsChunk).where(
                CmsChunk.tenant_id == tenant_id,
                CmsChunk.page_id == page_id,
            )
        )
        await self._session.flush()
        deleted = result.rowcount or 0
        logger.info(
            "rag.delete_page.completed",
            tenant_id=str(tenant_id),
            page_id=str(page_id),
            chunks_deleted=deleted,
        )
        return deleted


# ----- helpers ---------------------------------------------------------------
def _distance_to_score(distance: float) -> float:
    """Map pgvector cosine distance to a higher-is-better score in ``[0, 1]``.

    Cohere v3 returns normalized vectors, so distance is in ``[0, 2]``.
    We clip defensively in case a vector ever isn't perfectly normalized.
    """
    if distance is None:  # pragma: no cover — defensive
        return 0.0
    clipped = max(0.0, min(float(distance), _COSINE_DISTANCE_MAX))
    return 1.0 - (clipped / _COSINE_DISTANCE_MAX)


def _assert_embedding_dims(embeddings: list[list[float]]) -> None:
    """Defense-in-depth check before INSERT.

    The embedding client already validates dimensions, but we re-check here so
    a future change in the client (or a different client implementation) can't
    silently corrupt the corpus with off-dimension vectors.
    """
    for i, vec in enumerate(embeddings):
        if len(vec) != CMS_CHUNK_EMBEDDING_DIM:
            raise ValueError(
                f"embedding {i} has dim {len(vec)}; "
                f"expected {CMS_CHUNK_EMBEDDING_DIM} (model swap?)"
            )
