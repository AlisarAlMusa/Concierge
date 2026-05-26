"""Unit tests for RagService.

Tests the behavioral contracts from ``specs/rag-service/spec.md §9.A`` against
a fake AsyncSession and a fake embedding client. No real Postgres, no pgvector,
no network. Integration tests against real pgvector live in
``backend/tests/integration/test_rag_pgvector.py`` and are opt-in.

The fakes only model what RagService actually touches: ``execute``, ``add``,
``add_all``, ``flush`` on the session; ``embed_query`` / ``embed_documents``
on the embedding client. Anything else would be over-fitting tests to
implementation details.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

import pytest
from app.services.tools.rag_search import RagSearchResult

from app.core.errors import ExternalServiceError
from app.models.chunk import CmsChunk
from app.services.rag_service import (
    DEFAULT_MAX_CHUNKS,
    RagService,
    _distance_to_score,
)


# ----- Fake session ----------------------------------------------------------
class _FakeResult:
    """Mimics the slice of ``sqlalchemy.engine.Result`` we touch.

    SQLAlchemy's ``.all()`` is sync; ``.rowcount`` is a plain attribute.
    """

    def __init__(self, rows: list[Any], rowcount: int) -> None:
        self._rows = rows
        self.rowcount = rowcount

    def all(self) -> list[Any]:
        return list(self._rows)


class _FakeSession:
    """Captures every ``execute``/``add_all``/``flush`` call.

    Queue results with ``queue_result`` in FIFO order. Anything not queued
    returns an empty result with rowcount 0 — that matches Postgres semantics
    for "deleted nothing" and is the right default for the few execute calls
    we don't explicitly stage.
    """

    def __init__(self) -> None:
        self.executed: list[Any] = []
        self._results: list[_FakeResult] = []
        self.added: list[CmsChunk] = []
        self.flush_count = 0

    def queue_result(self, *, rows: list[Any] | None = None, rowcount: int = 0) -> None:
        self._results.append(_FakeResult(rows or [], rowcount))

    async def execute(self, stmt: Any, *args: Any, **kwargs: Any) -> _FakeResult:
        self.executed.append(stmt)
        if self._results:
            return self._results.pop(0)
        return _FakeResult([], 0)

    def add(self, item: CmsChunk) -> None:
        self.added.append(item)

    def add_all(self, items: list[CmsChunk]) -> None:
        self.added.extend(items)

    async def flush(self) -> None:
        self.flush_count += 1


# ----- Fake embedding client -------------------------------------------------
class _FakeEmbeddingClient:
    """Deterministic stand-in for ``CohereEmbeddingClient``.

    ``embed_query`` records the query and returns a configurable vector.
    ``embed_documents`` returns one canned vector per input by default — tests
    can override either via ``queue_documents_response``.
    """

    def __init__(
        self,
        *,
        query_vector: list[float] | None = None,
        documents_response: list[list[float]] | None = None,
        documents_error: Exception | None = None,
        query_error: Exception | None = None,
    ) -> None:
        self.query_vector = query_vector or [0.0] * 1024
        self.documents_response = documents_response
        self.documents_error = documents_error
        self.query_error = query_error
        self.query_calls: list[str] = []
        self.document_calls: list[list[str]] = []

    async def embed_query(self, text: str) -> list[float]:
        self.query_calls.append(text)
        if self.query_error is not None:
            raise self.query_error
        return list(self.query_vector)

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        self.document_calls.append(list(texts))
        if self.documents_error is not None:
            raise self.documents_error
        if self.documents_response is not None:
            return self.documents_response
        return [[0.1] * 1024 for _ in texts]


# ----- Helpers ---------------------------------------------------------------
def _build(
    *,
    query_vector: list[float] | None = None,
    documents_response: list[list[float]] | None = None,
    documents_error: Exception | None = None,
    query_error: Exception | None = None,
) -> tuple[RagService, _FakeSession, _FakeEmbeddingClient]:
    session = _FakeSession()
    client = _FakeEmbeddingClient(
        query_vector=query_vector,
        documents_response=documents_response,
        documents_error=documents_error,
        query_error=query_error,
    )
    service = RagService(
        session=session,  # type: ignore[arg-type]
        embedding_client=client,  # type: ignore[arg-type]
    )
    return service, session, client


def _row(text: str, page_id: UUID, distance: float) -> Any:
    """One row out of the SELECT — attribute access matches RagService's reads."""
    return SimpleNamespace(text=text, page_id=page_id, distance=distance)


# ----- search ---------------------------------------------------------------
async def test_empty_query_returns_empty_no_embedding():
    """search('') short-circuits — zero embedding calls, zero DB calls."""
    service, session, client = _build()

    result = await service.search(query="", tenant_id=uuid4())

    assert result == RagSearchResult(chunks=[], total_found=0)
    assert client.query_calls == []
    assert session.executed == []


async def test_whitespace_only_query_returns_empty_no_embedding():
    """search('   ') is treated the same as empty — visitors send junk; we don't bill it."""
    service, session, client = _build()

    result = await service.search(query="   \n\t", tenant_id=uuid4())

    assert result.chunks == []
    assert client.query_calls == []
    assert session.executed == []


async def test_search_calls_embed_query_once():
    service, session, client = _build()
    session.queue_result(rows=[])

    await service.search(query="what are your hours", tenant_id=uuid4())

    assert client.query_calls == ["what are your hours"]
    assert len(session.executed) == 1


async def test_search_returns_chunks_from_rows():
    """Each row produces one RagChunk with the right page_id and a normalized score."""
    service, session, _ = _build()
    page_a, page_b = uuid4(), uuid4()
    session.queue_result(
        rows=[
            _row("hello world", page_a, 0.4),
            _row("second hit", page_b, 1.0),
        ]
    )

    result = await service.search(query="hello", tenant_id=uuid4())

    assert len(result.chunks) == 2
    assert result.total_found == 2
    assert result.chunks[0].source_page_id == page_a
    assert result.chunks[0].text == "hello world"
    assert result.chunks[1].source_page_id == page_b


async def test_score_normalization():
    """Distance 0 → score 1; distance 2 → score 0; distance 1 → score 0.5."""
    assert _distance_to_score(0.0) == 1.0
    assert _distance_to_score(2.0) == 0.0
    assert _distance_to_score(1.0) == pytest.approx(0.5)
    # Clipping — defensive against non-normalized vectors at the boundary.
    assert _distance_to_score(-0.1) == 1.0
    assert _distance_to_score(2.5) == 0.0


async def test_search_score_is_in_unit_interval():
    """Every returned chunk's score is in [0, 1], higher = better."""
    service, session, _ = _build()
    session.queue_result(
        rows=[
            _row("a", uuid4(), 0.0),  # perfect match
            _row("b", uuid4(), 1.0),  # orthogonal
            _row("c", uuid4(), 2.0),  # opposite
        ]
    )

    result = await service.search(query="anything", tenant_id=uuid4())

    scores = [c.score for c in result.chunks]
    assert scores == [1.0, pytest.approx(0.5), 0.0]
    assert scores == sorted(scores, reverse=True)  # higher = better


async def test_embedding_error_propagates():
    """ExternalServiceError from the embedding client bubbles — the agent /
    tool wrapper decides whether it becomes a ToolError."""
    service, session, _ = _build(query_error=ExternalServiceError("cohere", "boom"))

    with pytest.raises(ExternalServiceError):
        await service.search(query="anything", tenant_id=uuid4())

    assert session.executed == []  # no DB call after embedding failure


async def test_search_invalid_max_chunks_raises():
    service, _, _ = _build()

    with pytest.raises(ValueError):
        await service.search(query="x", tenant_id=uuid4(), max_chunks=0)


async def test_search_default_max_chunks_matches_spec():
    """The default mirrors RagSearchArgs.max_chunks (= 5)."""
    assert DEFAULT_MAX_CHUNKS == 5


# ----- index_page -----------------------------------------------------------
async def test_index_empty_content_writes_nothing_but_clears_existing():
    """Empty content → no embedding call, no inserts; delete still runs so the
    final state matches the spec's idempotency invariant."""
    service, session, client = _build()

    written = await service.index_page(tenant_id=uuid4(), page_id=uuid4(), content="")

    assert written == 0
    assert client.document_calls == []  # no embedding for empty content
    assert session.added == []  # no rows added
    # Delete is still issued to wipe any stale chunks for this page.
    assert len(session.executed) == 1


async def test_index_page_writes_one_row_per_chunk():
    service, session, client = _build()
    content = "First paragraph.\n\nSecond paragraph."

    written = await service.index_page(tenant_id=uuid4(), page_id=uuid4(), content=content)

    assert written == len(session.added)
    assert written >= 1
    assert len(client.document_calls) == 1  # one embed_documents call
    assert len(client.document_calls[0]) == written


async def test_index_page_uses_one_embedding_call_per_index():
    """Spec §4: 'One call per index_page (batched internally)'. We never embed
    chunks one-by-one — that would defeat Cohere's batching."""
    service, _, client = _build()

    await service.index_page(
        tenant_id=uuid4(), page_id=uuid4(), content="Para 1.\n\nPara 2.\n\nPara 3."
    )

    assert len(client.document_calls) == 1


async def test_index_page_idempotent_delete_then_insert_order():
    """Spec §6.6: delete before insert in the same transaction. The execute()
    call for DELETE must precede ``add_all``."""
    service, session, _ = _build()

    await service.index_page(
        tenant_id=uuid4(),
        page_id=uuid4(),
        content="Some paragraph.",
    )

    # Delete is executed (one execute call); rows are then added; flush at end.
    assert len(session.executed) == 1, "exactly one DELETE statement"
    assert len(session.added) >= 1
    assert session.flush_count == 1


async def test_index_page_rejects_wrong_dim_vectors():
    """If the embedding client ever returns a non-1024-dim vector, RagService
    refuses to insert. Defense-in-depth against silent model swaps."""
    bad_vectors = [[0.0] * 768]  # 768 ≠ 1024
    service, session, _ = _build(documents_response=bad_vectors)

    with pytest.raises(ValueError) as excinfo:
        await service.index_page(tenant_id=uuid4(), page_id=uuid4(), content="Anything.")

    assert "1024" in str(excinfo.value)
    # The defensive guard fires before any INSERT or DELETE.
    assert session.added == []
    assert session.executed == []


async def test_index_page_propagates_embedding_error():
    """A Cohere failure during embedding does NOT leave a half-deleted page."""
    service, session, _ = _build(documents_error=ExternalServiceError("cohere", "down"))

    with pytest.raises(ExternalServiceError):
        await service.index_page(tenant_id=uuid4(), page_id=uuid4(), content="Real content here.")

    # No SQL ran — embedding failure happens before delete-then-insert.
    assert session.executed == []
    assert session.added == []


async def test_index_page_each_inserted_row_carries_tenant_and_page_ids():
    """No row may be inserted under a different tenant_id than the caller's."""
    service, session, _ = _build()
    tenant = uuid4()
    page = uuid4()

    await service.index_page(tenant_id=tenant, page_id=page, content="One.\n\nTwo.\n\nThree.")

    for row in session.added:
        assert row.tenant_id == tenant
        assert row.page_id == page


# ----- delete_page ----------------------------------------------------------
async def test_delete_page_returns_rowcount():
    service, session, _ = _build()
    session.queue_result(rowcount=4)

    deleted = await service.delete_page(tenant_id=uuid4(), page_id=uuid4())

    assert deleted == 4


async def test_delete_page_with_no_existing_rows_returns_zero():
    service, session, _ = _build()
    session.queue_result(rowcount=0)

    deleted = await service.delete_page(tenant_id=uuid4(), page_id=uuid4())

    assert deleted == 0


async def test_delete_page_issues_one_statement_and_flushes():
    service, session, _ = _build()
    session.queue_result(rowcount=2)

    await service.delete_page(tenant_id=uuid4(), page_id=uuid4())

    assert len(session.executed) == 1
    assert session.flush_count == 1


# ----- Constructor validation ----------------------------------------------
def test_constructor_rejects_invalid_default_max_chunks():
    with pytest.raises(ValueError):
        RagService(
            session=_FakeSession(),  # type: ignore[arg-type]
            embedding_client=_FakeEmbeddingClient(),  # type: ignore[arg-type]
            default_max_chunks=0,
        )
