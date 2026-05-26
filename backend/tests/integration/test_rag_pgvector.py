"""End-to-end RagService tests against a real Postgres+pgvector.

Opt-in via ``RUN_INTEGRATION=1`` so the default ``uv run pytest`` invocation
stays fast and database-free. Required env vars when opted in:

* ``RUN_INTEGRATION=1`` — gate.
* ``INTEGRATION_DATABASE_URL`` — async SQLAlchemy URL pointing at a Postgres
  instance with the ``vector`` extension available. The tests create
  ``cms_chunks`` (and the ``tenants`` parent it FK's to) inside a temporary
  schema and drop it at teardown, so this DB does NOT need pre-existing
  migrations.

These tests use the SAME ``RagService`` and ``CmsChunk`` model as production
code; they only swap the embedding client for a deterministic in-memory fake
because real Cohere calls are tested in ``test_embedding_client.py`` and
would only add cost + flakiness here.

Coverage mirrors ``specs/rag-service/spec.md §9.B``:

* end-to-end index → search returns the right chunks
* cross-tenant isolation
* top-K ordering by cosine similarity
* ``delete_page`` removes only the named page
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.db.base import Base
from app.services.rag_service import RagService

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_INTEGRATION") != "1",
    reason="opt-in pgvector integration tests (set RUN_INTEGRATION=1)",
)

_INTEGRATION_URL = os.environ.get(
    "INTEGRATION_DATABASE_URL",
    "postgresql+asyncpg://postgres:postgres@localhost:5432/concierge_test",
)


# ----- Deterministic embedding client ----------------------------------------
def _vec(*, dim0: float = 0.0, dim1: float = 0.0) -> list[float]:
    """Build a 1024-dim sparse vector with controlled values on dims 0 and 1.

    Lets a test set cosine similarity precisely. For unit vectors
    ``q = [1, 0, ...]`` and ``d = [a, b, ...]`` with ``a² + b² = 1``, cosine
    distance is ``1 - a`` — directly tunable via the ``dim0`` parameter.
    """
    v = [0.0] * 1024
    v[0] = dim0
    v[1] = dim1
    return v


class _StaticEmbeddingClient:
    """Maps known texts → known 1024-dim vectors for predictable retrieval.

    Anything not in the map gets a zero vector. ``embed_query`` and
    ``embed_documents`` share the same dictionary so a query for the same text
    a document was indexed under retrieves it at distance 0.
    """

    def __init__(self, mapping: dict[str, list[float]]) -> None:
        self._mapping = mapping

    async def embed_query(self, text: str) -> list[float]:
        return list(self._mapping.get(text, [0.0] * 1024))

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [list(self._mapping.get(t, [0.0] * 1024)) for t in texts]


# ----- Fixtures --------------------------------------------------------------
@pytest_asyncio.fixture
async def engine():
    """Create engine, install pgvector + pgcrypto, create cms_chunks + tenants."""
    eng = create_async_engine(_INTEGRATION_URL)
    async with eng.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS pgcrypto"))
        # Drop FK from cms_chunks to tenants temporarily — easier to manage
        # schema lifecycle in tests by creating both tables from scratch.
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await eng.dispose()


@pytest_asyncio.fixture
async def session(engine) -> AsyncIterator[AsyncSession]:
    factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    async with factory() as s:
        # Insert a few tenants so cms_chunks' FK doesn't reject inserts.
        # We bypass the Tenant model to keep this file self-contained.
        await s.execute(
            text(
                "INSERT INTO tenants (id, name, slug, status) VALUES "
                "(:a, 'A', 'a', 'active'), (:b, 'B', 'b', 'active') "
                "ON CONFLICT DO NOTHING"
            ),
            {"a": str(TENANT_A), "b": str(TENANT_B)},
        )
        await s.commit()
        yield s
        await s.rollback()


TENANT_A = UUID("00000000-0000-0000-0000-00000000aaaa")
TENANT_B = UUID("00000000-0000-0000-0000-00000000bbbb")

# Unit query vector along axis 0. Documents control their cosine similarity
# with this by choosing how much mass they put on dim 0 vs dim 1.
_Q_AXIS = _vec(dim0=1.0)


# ----- Tests ----------------------------------------------------------------
async def test_end_to_end_index_then_search(session: AsyncSession) -> None:
    """Index a page, query for it, get the right chunks back."""
    content_text = "We are open from 9 to 5 Monday through Friday."
    client = _StaticEmbeddingClient(
        {
            "what are your hours": _Q_AXIS,
            content_text: _Q_AXIS,  # cos_sim = 1, distance = 0
        }
    )
    svc = RagService(session=session, embedding_client=client)

    page = uuid4()
    written = await svc.index_page(tenant_id=TENANT_A, page_id=page, content=content_text)
    await session.commit()
    assert written >= 1

    result = await svc.search(query="what are your hours", tenant_id=TENANT_A)
    assert len(result.chunks) >= 1
    assert any(c.source_page_id == page for c in result.chunks)
    assert result.chunks[0].score >= 0.99  # cos_sim=1 → score=1


async def test_cross_tenant_isolation(session: AsyncSession) -> None:
    """T1's chunks must be invisible to T2's search."""
    client = _StaticEmbeddingClient({"secret content for tenant a": _Q_AXIS, "anything": _Q_AXIS})
    svc = RagService(session=session, embedding_client=client)

    await svc.index_page(
        tenant_id=TENANT_A,
        page_id=uuid4(),
        content="secret content for tenant a",
    )
    await session.commit()

    # B searches — must get zero results, because tenant_id filter is explicit.
    result = await svc.search(query="anything", tenant_id=TENANT_B)
    assert result.chunks == []
    assert result.total_found == 0


async def test_top_k_ordering(session: AsyncSession) -> None:
    """Closer cosine distance → earlier in the result list → higher score.

    Three unit vectors at angles 0°, 60°, 180° from the query give cosine
    distances 0, 0.5, and 2 respectively. The ordering returned by pgvector's
    ``<=>`` should match exactly.
    """
    half_root3 = 0.8660254037844386  # sqrt(3) / 2
    client = _StaticEmbeddingClient(
        {
            "q": _Q_AXIS,
            "best match": _vec(dim0=1.0),  # cos_sim=1 → dist=0
            "ok match": _vec(dim0=0.5, dim1=half_root3),  # unit, cos_sim=0.5 → dist=0.5
            "far match": _vec(dim0=-1.0),  # cos_sim=-1 → dist=2
        }
    )
    svc = RagService(session=session, embedding_client=client)

    for content in ("best match", "ok match", "far match"):
        await svc.index_page(tenant_id=TENANT_A, page_id=uuid4(), content=content)
    await session.commit()

    result = await svc.search(query="q", tenant_id=TENANT_A, max_chunks=3)
    assert [c.text for c in result.chunks] == ["best match", "ok match", "far match"]
    assert result.chunks[0].score > result.chunks[1].score > result.chunks[2].score


async def test_delete_page_removes_only_named_page(session: AsyncSession) -> None:
    """``delete_page(page_a)`` does not touch ``page_b``."""
    client = _StaticEmbeddingClient(
        {"q": _Q_AXIS, "page a content": _Q_AXIS, "page b content": _Q_AXIS}
    )
    svc = RagService(session=session, embedding_client=client)

    page_a, page_b = uuid4(), uuid4()
    await svc.index_page(tenant_id=TENANT_A, page_id=page_a, content="page a content")
    await svc.index_page(tenant_id=TENANT_A, page_id=page_b, content="page b content")
    await session.commit()

    deleted = await svc.delete_page(tenant_id=TENANT_A, page_id=page_a)
    await session.commit()
    assert deleted >= 1

    result = await svc.search(query="q", tenant_id=TENANT_A, max_chunks=5)
    page_ids = {c.source_page_id for c in result.chunks}
    assert page_a not in page_ids
    assert page_b in page_ids


async def test_index_page_idempotent_against_real_db(session: AsyncSession) -> None:
    """Re-indexing the same page twice yields the same final state, not duplicates."""
    client = _StaticEmbeddingClient({"q": _Q_AXIS, "stable content": _Q_AXIS})
    svc = RagService(session=session, embedding_client=client)
    page = uuid4()

    n1 = await svc.index_page(tenant_id=TENANT_A, page_id=page, content="stable content")
    await session.commit()

    n2 = await svc.index_page(tenant_id=TENANT_A, page_id=page, content="stable content")
    await session.commit()

    assert n1 == n2

    count = (
        await session.execute(
            text("SELECT COUNT(*) FROM cms_chunks " "WHERE tenant_id = :t AND page_id = :p"),
            {"t": str(TENANT_A), "p": str(page)},
        )
    ).scalar_one()
    assert count == n2
