"""Tests for the CMS ingestion surface (POST/GET /cms/pages + CmsPageService).

Validates:

* ``derive_slug`` is pure + URL-safe.
* ``CmsPageService.create_page`` inserts a new page and routes the body
  through ``RagService.index_page`` (real-write contract).
* Re-posting the same slug updates the existing row in place
  (idempotent / upsert).
* Empty input is rejected at the service layer.
* The HTTP surface honors the dual auth gate (``X-Service-Token`` +
  ``X-Tenant-Id``) and round-trips through to the service.
* Routes never leak across tenants: a request for tenant A cannot see
  tenant B's pages.

No real Postgres, no real Cohere — the session and the RagService are
hand-rolled fakes.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from app.dependencies import (
    get_admin_rls_session,
    get_cms_page_service,
)
from app.main import app
from app.models.cms import CmsPage, CmsPageStatus
from app.services.cms_page_service import (
    CmsPageService,
    CmsPageWriteResult,
    SlugConflictError,
    derive_slug,
)

SERVICE_TOKEN = "service-secret"


# ----- derive_slug ----------------------------------------------------------
def test_derive_slug_lowercases_and_hyphenates() -> None:
    assert derive_slug("Refund Policy") == "refund-policy"
    assert derive_slug("Pricing & Plans!") == "pricing-plans"
    assert derive_slug("   spaces   ") == "spaces"
    assert derive_slug("multi   spaces") == "multi-spaces"
    assert derive_slug("Über-pricing 2024") == "ber-pricing-2024"


def test_derive_slug_falls_back_when_all_punctuation() -> None:
    assert derive_slug("!!!") == "page"
    assert derive_slug("") == "page"


# ----- Fake session + Fake RAG ----------------------------------------------
class _FakeResult:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def scalar_one_or_none(self) -> Any | None:
        return self._rows[0] if self._rows else None

    def scalar_one(self) -> Any:
        return self._rows[0]

    def scalars(self) -> _FakeResult:
        return self

    def all(self) -> list[Any]:
        return list(self._rows)


class _FakeSession:
    """Minimal AsyncSession double for service-level tests."""

    def __init__(self) -> None:
        self.added: list[Any] = []
        self.flushed = 0
        self.execute_results: list[list[Any]] = []
        self.execute_calls: list[Any] = []

    def enqueue(self, rows: list[Any]) -> None:
        self.execute_results.append(rows)

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    async def execute(self, stmt: Any) -> _FakeResult:
        self.execute_calls.append(stmt)
        rows = self.execute_results.pop(0) if self.execute_results else []
        return _FakeResult(rows)

    async def flush(self) -> None:
        self.flushed += 1


class _FakeRagService:
    """Records ``index_page`` / ``delete_page`` calls.

    The chunk count is configurable so individual tests can assert the
    end-to-end "chunks_written" value bubbles up unchanged through the
    service and route layers.
    """

    def __init__(self, chunks_to_return: int = 2, chunks_to_delete: int = 0) -> None:
        self.chunks_to_return = chunks_to_return
        self.chunks_to_delete = chunks_to_delete
        self.calls: list[dict[str, Any]] = []
        self.delete_calls: list[dict[str, Any]] = []

    async def index_page(self, *, tenant_id: UUID, page_id: UUID, content: str) -> int:
        self.calls.append({"tenant_id": tenant_id, "page_id": page_id, "content": content})
        return self.chunks_to_return

    async def delete_page(self, *, tenant_id: UUID, page_id: UUID) -> int:
        self.delete_calls.append({"tenant_id": tenant_id, "page_id": page_id})
        return self.chunks_to_delete


TENANT_A = UUID("00000000-0000-0000-0000-00000000000a")
TENANT_B = UUID("00000000-0000-0000-0000-00000000000b")


# ----- CmsPageService.create_page -------------------------------------------
async def test_create_page_inserts_new_row_and_indexes_chunks() -> None:
    session = _FakeSession()
    session.enqueue([])  # _lookup_by_slug → not found
    rag = _FakeRagService(chunks_to_return=3)
    svc = CmsPageService(session=session, rag_service=rag)

    result = await svc.create_page(
        tenant_id=TENANT_A,
        title="Pricing",
        body="The Starter plan is $19 per month.",
    )

    assert isinstance(result, CmsPageWriteResult)
    assert result.chunks_written == 3
    assert len(session.added) == 1
    page: CmsPage = session.added[0]
    assert page.tenant_id == TENANT_A
    assert page.title == "Pricing"
    assert page.slug == "pricing"
    assert page.status == CmsPageStatus.published

    # The body MUST have flowed through RagService.index_page — not bypassed.
    assert len(rag.calls) == 1
    assert rag.calls[0]["tenant_id"] == TENANT_A
    assert rag.calls[0]["page_id"] == page.id
    assert rag.calls[0]["content"] == "The Starter plan is $19 per month."


async def test_create_page_with_explicit_slug_overrides_auto_derivation() -> None:
    session = _FakeSession()
    session.enqueue([])
    rag = _FakeRagService()
    svc = CmsPageService(session=session, rag_service=rag)

    result = await svc.create_page(
        tenant_id=TENANT_A,
        title="Quick FAQ #1",
        slug="faq-q1",
        body="Yes.",
    )

    assert result.page.slug == "faq-q1"


async def test_create_page_upserts_when_slug_exists() -> None:
    """Re-posting the same slug updates in place + re-indexes via RAG."""
    existing = CmsPage(
        id=uuid4(),
        tenant_id=TENANT_A,
        title="Old title",
        slug="pricing",
        body="old body",
        status=CmsPageStatus.published,
    )
    session = _FakeSession()
    session.enqueue([existing])  # _lookup_by_slug → hit
    rag = _FakeRagService(chunks_to_return=5)
    svc = CmsPageService(session=session, rag_service=rag)

    result = await svc.create_page(
        tenant_id=TENANT_A,
        title="Pricing",
        body="The Starter plan is $19 per month.",
    )

    assert result.chunks_written == 5
    # No new row inserted — the existing row was mutated in place.
    assert session.added == []
    assert existing.title == "Pricing"
    assert existing.body == "The Starter plan is $19 per month."
    # The existing page id flowed into the RAG index call.
    assert rag.calls[0]["page_id"] == existing.id


async def test_create_page_rejects_empty_title() -> None:
    session = _FakeSession()
    svc = CmsPageService(session=session, rag_service=_FakeRagService())
    with pytest.raises(ValueError):
        await svc.create_page(tenant_id=TENANT_A, title="   ", body="something")


async def test_create_page_rejects_empty_body() -> None:
    session = _FakeSession()
    svc = CmsPageService(session=session, rag_service=_FakeRagService())
    with pytest.raises(ValueError):
        await svc.create_page(tenant_id=TENANT_A, title="x", body="   ")


# ----- list_pages / get_page ------------------------------------------------
async def test_list_pages_returns_items_and_total() -> None:
    session = _FakeSession()
    session.enqueue([7])  # count
    rows = [
        CmsPage(
            id=uuid4(),
            tenant_id=TENANT_A,
            title=f"Page {i}",
            slug=f"page-{i}",
            body=".",
            status=CmsPageStatus.published,
        )
        for i in range(3)
    ]
    session.enqueue(rows)
    svc = CmsPageService(session=session, rag_service=_FakeRagService())

    items, total = await svc.list_pages(tenant_id=TENANT_A)
    assert total == 7
    assert items == rows


async def test_list_pages_rejects_bad_pagination() -> None:
    svc = CmsPageService(session=_FakeSession(), rag_service=_FakeRagService())
    with pytest.raises(ValueError):
        await svc.list_pages(tenant_id=TENANT_A, limit=0)
    with pytest.raises(ValueError):
        await svc.list_pages(tenant_id=TENANT_A, offset=-1)


async def test_get_page_returns_none_when_absent_or_cross_tenant() -> None:
    session = _FakeSession()
    session.enqueue([])
    svc = CmsPageService(session=session, rag_service=_FakeRagService())
    assert await svc.get_page(tenant_id=TENANT_A, page_id=uuid4()) is None


# ----- CmsPageService.update_page -------------------------------------------
def _existing_page(
    *,
    tenant_id: UUID = TENANT_A,
    title: str = "Pricing",
    slug: str = "pricing",
    body: str = "Starter is $19/mo.",
    status: CmsPageStatus = CmsPageStatus.published,
) -> CmsPage:
    page = CmsPage(
        id=uuid4(),
        tenant_id=tenant_id,
        title=title,
        slug=slug,
        body=body,
        status=status,
    )
    page.created_at = datetime.now(timezone.utc)  # type: ignore[assignment]
    page.updated_at = datetime.now(timezone.utc)  # type: ignore[assignment]
    return page


async def test_update_page_returns_none_when_absent() -> None:
    session = _FakeSession()
    session.enqueue([])  # get_page → not found
    svc = CmsPageService(session=session, rag_service=_FakeRagService())

    result = await svc.update_page(
        tenant_id=TENANT_A,
        page_id=uuid4(),
        title="anything",
    )
    assert result is None


async def test_update_page_title_only_does_not_reindex() -> None:
    page = _existing_page()
    session = _FakeSession()
    session.enqueue([page])  # get_page
    rag = _FakeRagService()
    svc = CmsPageService(session=session, rag_service=rag)

    result = await svc.update_page(tenant_id=TENANT_A, page_id=page.id, title="Pricing v2")

    assert result is not None
    assert result.page.title == "Pricing v2"
    assert result.chunks_written == 0
    assert rag.calls == []
    assert rag.delete_calls == []


async def test_update_page_body_change_reindexes_when_published() -> None:
    page = _existing_page()
    session = _FakeSession()
    session.enqueue([page])  # get_page
    rag = _FakeRagService(chunks_to_return=4)
    svc = CmsPageService(session=session, rag_service=rag)

    result = await svc.update_page(
        tenant_id=TENANT_A,
        page_id=page.id,
        body="Growth tier is $99/mo and includes everything in Starter.",
    )

    assert result is not None
    assert result.chunks_written == 4
    assert page.body.startswith("Growth tier")
    assert len(rag.calls) == 1
    assert rag.calls[0]["page_id"] == page.id
    assert rag.calls[0]["content"] == page.body
    assert rag.delete_calls == []


async def test_update_page_body_change_on_draft_does_not_reindex() -> None:
    page = _existing_page(status=CmsPageStatus.draft)
    session = _FakeSession()
    session.enqueue([page])  # get_page
    rag = _FakeRagService()
    svc = CmsPageService(session=session, rag_service=rag)

    result = await svc.update_page(tenant_id=TENANT_A, page_id=page.id, body="new draft body")

    assert result is not None
    assert result.chunks_written == 0
    assert rag.calls == []
    assert rag.delete_calls == []


async def test_update_page_unpublish_drops_chunks() -> None:
    page = _existing_page(status=CmsPageStatus.published)
    session = _FakeSession()
    session.enqueue([page])  # get_page
    rag = _FakeRagService(chunks_to_delete=3)
    svc = CmsPageService(session=session, rag_service=rag)

    result = await svc.update_page(tenant_id=TENANT_A, page_id=page.id, status=CmsPageStatus.draft)

    assert result is not None
    assert result.page.status == CmsPageStatus.draft
    assert result.chunks_written == 0
    assert rag.calls == []
    assert len(rag.delete_calls) == 1
    assert rag.delete_calls[0] == {"tenant_id": TENANT_A, "page_id": page.id}


async def test_update_page_slug_conflict_raises() -> None:
    page = _existing_page(slug="pricing")
    clash = _existing_page(slug="faq")  # different id, same tenant
    session = _FakeSession()
    session.enqueue([page])  # get_page
    session.enqueue([clash])  # _lookup_by_slug for new slug "faq"
    svc = CmsPageService(session=session, rag_service=_FakeRagService())

    with pytest.raises(SlugConflictError):
        await svc.update_page(tenant_id=TENANT_A, page_id=page.id, slug="faq")


async def test_update_page_same_slug_is_noop() -> None:
    page = _existing_page(slug="pricing")
    session = _FakeSession()
    session.enqueue([page])  # get_page; no lookup needed because slug unchanged
    svc = CmsPageService(session=session, rag_service=_FakeRagService())

    result = await svc.update_page(tenant_id=TENANT_A, page_id=page.id, slug="pricing")
    assert result is not None
    assert page.slug == "pricing"


async def test_update_page_rejects_empty_body() -> None:
    page = _existing_page()
    session = _FakeSession()
    session.enqueue([page])
    svc = CmsPageService(session=session, rag_service=_FakeRagService())

    with pytest.raises(ValueError):
        await svc.update_page(tenant_id=TENANT_A, page_id=page.id, body="   ")


# ----- CmsPageService.delete_page -------------------------------------------
async def test_delete_page_returns_false_when_absent() -> None:
    session = _FakeSession()
    session.enqueue([])  # get_page → not found
    svc = CmsPageService(session=session, rag_service=_FakeRagService())

    assert await svc.delete_page(tenant_id=TENANT_A, page_id=uuid4()) is False


class _DeletingFakeSession(_FakeSession):
    def __init__(self) -> None:
        super().__init__()
        self.deleted: list[Any] = []

    async def delete(self, obj: Any) -> None:
        self.deleted.append(obj)


async def test_delete_page_purges_chunks_then_row() -> None:
    page = _existing_page()
    session = _DeletingFakeSession()
    session.enqueue([page])  # get_page
    rag = _FakeRagService(chunks_to_delete=5)
    svc = CmsPageService(session=session, rag_service=rag)

    ok = await svc.delete_page(tenant_id=TENANT_A, page_id=page.id)

    assert ok is True
    # Chunks deleted via the RAG service…
    assert rag.delete_calls == [{"tenant_id": TENANT_A, "page_id": page.id}]
    # …then the page row dropped on the session.
    assert session.deleted == [page]


# ----- CmsPageService.publish_page ------------------------------------------
async def test_publish_page_returns_none_when_absent() -> None:
    session = _FakeSession()
    session.enqueue([])  # get_page → not found
    svc = CmsPageService(session=session, rag_service=_FakeRagService())

    assert await svc.publish_page(tenant_id=TENANT_A, page_id=uuid4()) is None


async def test_publish_page_flips_status_and_reindexes() -> None:
    page = _existing_page(status=CmsPageStatus.draft, body="draft body")
    session = _FakeSession()
    session.enqueue([page])  # get_page
    rag = _FakeRagService(chunks_to_return=7)
    svc = CmsPageService(session=session, rag_service=rag)

    result = await svc.publish_page(tenant_id=TENANT_A, page_id=page.id)

    assert result is not None
    assert result.page.status == CmsPageStatus.published
    assert result.chunks_written == 7
    assert len(rag.calls) == 1
    assert rag.calls[0]["content"] == "draft body"


async def test_publish_page_is_idempotent_for_already_published() -> None:
    page = _existing_page(status=CmsPageStatus.published)
    session = _FakeSession()
    session.enqueue([page])
    rag = _FakeRagService(chunks_to_return=2)
    svc = CmsPageService(session=session, rag_service=rag)

    result = await svc.publish_page(tenant_id=TENANT_A, page_id=page.id)

    assert result is not None
    assert result.page.status == CmsPageStatus.published
    # Still reindexes — index_page is itself a delete-then-insert, so this
    # is the right idempotency contract.
    assert len(rag.calls) == 1


# ----- CmsPageService.reindex_page ------------------------------------------
async def test_reindex_page_returns_none_when_absent() -> None:
    session = _FakeSession()
    session.enqueue([])  # get_page → not found
    svc = CmsPageService(session=session, rag_service=_FakeRagService())

    assert await svc.reindex_page(tenant_id=TENANT_A, page_id=uuid4()) is None


async def test_reindex_page_published_calls_index_page_once() -> None:
    page = _existing_page(status=CmsPageStatus.published)
    session = _FakeSession()
    session.enqueue([page])
    rag = _FakeRagService(chunks_to_return=3)
    svc = CmsPageService(session=session, rag_service=rag)

    result = await svc.reindex_page(tenant_id=TENANT_A, page_id=page.id)

    assert result is not None
    assert result.chunks_written == 3
    # Single call — RagService.index_page is itself delete-then-insert, so we
    # do not invoke delete + index separately. This guards against accidental
    # double-write that would briefly leave the page un-retrievable.
    assert len(rag.calls) == 1
    assert rag.calls[0]["page_id"] == page.id


async def test_reindex_page_draft_is_noop() -> None:
    page = _existing_page(status=CmsPageStatus.draft)
    session = _FakeSession()
    session.enqueue([page])
    rag = _FakeRagService()
    svc = CmsPageService(session=session, rag_service=rag)

    result = await svc.reindex_page(tenant_id=TENANT_A, page_id=page.id)

    assert result is not None
    assert result.chunks_written == 0
    assert rag.calls == []
    assert rag.delete_calls == []


# ----- CmsPageService.reindex_all -------------------------------------------
async def test_reindex_all_returns_zero_when_no_pages() -> None:
    session = _FakeSession()
    session.enqueue([])  # select published pages → empty
    rag = _FakeRagService()
    svc = CmsPageService(session=session, rag_service=rag)

    pages_count, chunks = await svc.reindex_all(tenant_id=TENANT_A)
    assert pages_count == 0
    assert chunks == 0
    assert rag.calls == []


async def test_reindex_all_indexes_every_published_page() -> None:
    rows = [_existing_page(slug=f"p{i}", body=f"body {i}") for i in range(3)]
    session = _FakeSession()
    session.enqueue(rows)
    rag = _FakeRagService(chunks_to_return=2)
    svc = CmsPageService(session=session, rag_service=rag)

    pages_count, chunks = await svc.reindex_all(tenant_id=TENANT_A)

    assert pages_count == 3
    assert chunks == 6  # 3 pages * 2 chunks each
    assert [c["page_id"] for c in rag.calls] == [p.id for p in rows]


# ----- HTTP surface ---------------------------------------------------------
class _RouteFakeCmsPageService:
    """CmsPageService double used by the route-level tests.

    Stores writes in an in-memory tenant-keyed map so list/get can be
    exercised against the same state, and tracks the tenant id passed
    on every call so the cross-tenant test can assert isolation.
    """

    def __init__(self) -> None:
        self._by_tenant: dict[UUID, list[CmsPage]] = {}
        self.create_calls: list[dict[str, Any]] = []
        self.list_calls: list[UUID] = []
        self.get_calls: list[tuple[UUID, UUID]] = []
        self.delete_calls: list[UUID] = []
        self.publish_calls: list[UUID] = []
        self.reindex_calls: list[UUID] = []
        self.reindex_all_calls: list[UUID] = []

    async def create_page(
        self,
        *,
        tenant_id: UUID,
        title: str,
        body: str,
        slug: str | None = None,
        status: CmsPageStatus = CmsPageStatus.published,
    ) -> CmsPageWriteResult:
        self.create_calls.append(
            {
                "tenant_id": tenant_id,
                "title": title,
                "body": body,
                "slug": slug,
                "status": status,
            }
        )
        resolved = (slug or derive_slug(title)).strip().lower()
        # Upsert on (tenant_id, slug).
        bucket = self._by_tenant.setdefault(tenant_id, [])
        for existing in bucket:
            if existing.slug == resolved:
                existing.title = title.strip()
                existing.body = body
                existing.status = status
                return CmsPageWriteResult(page=existing, chunks_written=2)
        page = CmsPage(
            id=uuid4(),
            tenant_id=tenant_id,
            title=title.strip(),
            slug=resolved,
            body=body,
            status=status,
        )
        # Pydantic CmsPageRead reads ``created_at`` / ``updated_at`` via
        # ``from_attributes``; fakes need to populate them.
        page.created_at = datetime.now(timezone.utc)  # type: ignore[assignment]
        page.updated_at = datetime.now(timezone.utc)  # type: ignore[assignment]
        bucket.append(page)
        return CmsPageWriteResult(page=page, chunks_written=2)

    async def list_pages(
        self, *, tenant_id: UUID, limit: int = 100, offset: int = 0
    ) -> tuple[list[CmsPage], int]:
        self.list_calls.append(tenant_id)
        bucket = list(self._by_tenant.get(tenant_id, []))
        return bucket[offset : offset + limit], len(bucket)

    async def get_page(self, *, tenant_id: UUID, page_id: UUID) -> CmsPage | None:
        self.get_calls.append((tenant_id, page_id))
        for p in self._by_tenant.get(tenant_id, []):
            if p.id == page_id:
                return p
        return None

    async def update_page(
        self,
        *,
        tenant_id: UUID,
        page_id: UUID,
        title: str | None = None,
        slug: str | None = None,
        body: str | None = None,
        status: CmsPageStatus | None = None,
    ) -> CmsPageWriteResult | None:
        page = await self.get_page(tenant_id=tenant_id, page_id=page_id)
        if page is None:
            return None
        original_status = page.status
        body_changed = body is not None and body != page.body
        if title is not None:
            page.title = title.strip()
        if slug is not None:
            new_slug = slug.strip().lower()
            if new_slug != page.slug:
                for other in self._by_tenant.get(tenant_id, []):
                    if other.id != page.id and other.slug == new_slug:
                        raise SlugConflictError(new_slug)
                page.slug = new_slug
        if body is not None:
            page.body = body
        if status is not None:
            page.status = status
        chunks_written = 0
        if page.status == CmsPageStatus.draft and original_status == CmsPageStatus.published:
            self.delete_calls.append(page.id)
        elif page.status == CmsPageStatus.published and body_changed:
            chunks_written = 2
            self.reindex_calls.append(page.id)
        return CmsPageWriteResult(page=page, chunks_written=chunks_written)

    async def delete_page(self, *, tenant_id: UUID, page_id: UUID) -> bool:
        bucket = self._by_tenant.get(tenant_id, [])
        for i, p in enumerate(bucket):
            if p.id == page_id:
                bucket.pop(i)
                self.delete_calls.append(page_id)
                return True
        return False

    async def publish_page(self, *, tenant_id: UUID, page_id: UUID) -> CmsPageWriteResult | None:
        page = await self.get_page(tenant_id=tenant_id, page_id=page_id)
        if page is None:
            return None
        page.status = CmsPageStatus.published
        self.publish_calls.append(page.id)
        return CmsPageWriteResult(page=page, chunks_written=2)

    async def reindex_page(self, *, tenant_id: UUID, page_id: UUID) -> CmsPageWriteResult | None:
        page = await self.get_page(tenant_id=tenant_id, page_id=page_id)
        if page is None:
            return None
        if page.status != CmsPageStatus.published:
            return CmsPageWriteResult(page=page, chunks_written=0)
        self.reindex_calls.append(page.id)
        return CmsPageWriteResult(page=page, chunks_written=2)

    async def reindex_all(self, *, tenant_id: UUID) -> tuple[int, int]:
        self.reindex_all_calls.append(tenant_id)
        bucket = self._by_tenant.get(tenant_id, [])
        published = [p for p in bucket if p.status == CmsPageStatus.published]
        for p in published:
            self.reindex_calls.append(p.id)
        return len(published), len(published) * 2


@pytest.fixture
def fake_service() -> _RouteFakeCmsPageService:
    return _RouteFakeCmsPageService()


@pytest.fixture
def client(fake_service: _RouteFakeCmsPageService):
    # Make the route use the fake service. The RLS session dependency would
    # otherwise try to open a real Postgres connection; we override it with
    # a no-op too so the dependency tree resolves without I/O.
    app.dependency_overrides[get_cms_page_service] = lambda: fake_service

    async def _noop_session():
        yield None

    app.dependency_overrides[get_admin_rls_session] = _noop_session
    try:
        with TestClient(app) as c:
            yield c
    finally:
        app.dependency_overrides.clear()


def _admin_headers(tenant_id: UUID) -> dict[str, str]:
    return {
        "X-Service-Token": SERVICE_TOKEN,
        "X-Tenant-Id": str(tenant_id),
        "Content-Type": "application/json",
    }


def test_post_cms_page_happy_path(
    client: TestClient, fake_service: _RouteFakeCmsPageService
) -> None:
    body = {"title": "Pricing", "body": "Starter is $19/mo."}
    response = client.post("/cms/pages", json=body, headers=_admin_headers(TENANT_A))

    assert response.status_code == 201, response.text
    payload = response.json()
    assert payload["title"] == "Pricing"
    assert payload["slug"] == "pricing"
    assert payload["status"] == "published"
    assert payload["chunks_written"] == 2
    assert payload["tenant_id"] == str(TENANT_A)

    # The route forwarded the right kwargs to the service.
    assert len(fake_service.create_calls) == 1
    assert fake_service.create_calls[0]["tenant_id"] == TENANT_A
    assert fake_service.create_calls[0]["body"] == "Starter is $19/mo."


def test_post_cms_page_rejects_without_service_token(
    client: TestClient,
) -> None:
    response = client.post(
        "/cms/pages",
        json={"title": "x", "body": "y"},
        headers={"X-Tenant-Id": str(TENANT_A), "Content-Type": "application/json"},
    )
    # FastAPI 422 for missing required header is acceptable; some FastAPI
    # versions surface this as 400 via the custom error handler. Either
    # is "not authorized" — we just need it not to succeed.
    assert response.status_code in (400, 403, 422)


def test_post_cms_page_rejects_bad_tenant_header(client: TestClient) -> None:
    response = client.post(
        "/cms/pages",
        json={"title": "x", "body": "y"},
        headers={
            "X-Service-Token": SERVICE_TOKEN,
            "X-Tenant-Id": "not-a-uuid",
            "Content-Type": "application/json",
        },
    )
    assert response.status_code == 400


def test_post_cms_page_rejects_wrong_service_token(client: TestClient) -> None:
    response = client.post(
        "/cms/pages",
        json={"title": "x", "body": "y"},
        headers={
            "X-Service-Token": "wrong-secret",
            "X-Tenant-Id": str(TENANT_A),
            "Content-Type": "application/json",
        },
    )
    assert response.status_code == 403


def test_get_cms_pages_lists_only_caller_tenant(
    client: TestClient, fake_service: _RouteFakeCmsPageService
) -> None:
    """Tenant isolation — posting under A and reading under B yields zero."""
    client.post(
        "/cms/pages",
        json={"title": "A page", "body": "A body"},
        headers=_admin_headers(TENANT_A),
    )
    client.post(
        "/cms/pages",
        json={"title": "B page", "body": "B body"},
        headers=_admin_headers(TENANT_B),
    )

    a_list = client.get("/cms/pages", headers=_admin_headers(TENANT_A)).json()
    b_list = client.get("/cms/pages", headers=_admin_headers(TENANT_B)).json()

    assert a_list["total"] == 1
    assert a_list["items"][0]["title"] == "A page"
    assert a_list["items"][0]["tenant_id"] == str(TENANT_A)

    assert b_list["total"] == 1
    assert b_list["items"][0]["title"] == "B page"
    assert b_list["items"][0]["tenant_id"] == str(TENANT_B)


def test_get_cms_page_by_id_404_when_cross_tenant(
    client: TestClient,
) -> None:
    created = client.post(
        "/cms/pages",
        json={"title": "Pricing", "body": "Starter $19"},
        headers=_admin_headers(TENANT_A),
    )
    page_id = created.json()["id"]

    # Same tenant → found.
    same = client.get(f"/cms/pages/{page_id}", headers=_admin_headers(TENANT_A))
    assert same.status_code == 200

    # Different tenant → 404 (not 200 with another tenant's body, not 403).
    other = client.get(f"/cms/pages/{page_id}", headers=_admin_headers(TENANT_B))
    assert other.status_code == 404


# ----- PATCH /cms/pages/{id} -----------------------------------------------
def _seed_page(
    client: TestClient,
    *,
    tenant_id: UUID = TENANT_A,
    title: str = "Pricing",
    body: str = "Starter $19",
) -> str:
    r = client.post(
        "/cms/pages",
        json={"title": title, "body": body},
        headers=_admin_headers(tenant_id),
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_patch_cms_page_updates_title(
    client: TestClient, fake_service: _RouteFakeCmsPageService
) -> None:
    page_id = _seed_page(client)
    resp = client.patch(
        f"/cms/pages/{page_id}",
        json={"title": "Pricing v2"},
        headers=_admin_headers(TENANT_A),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["title"] == "Pricing v2"
    # Title-only change → no reindex.
    assert fake_service.reindex_calls == []
    assert body["chunks_written"] == 0


def test_patch_cms_page_body_change_triggers_reindex(
    client: TestClient, fake_service: _RouteFakeCmsPageService
) -> None:
    page_id = _seed_page(client)
    resp = client.patch(
        f"/cms/pages/{page_id}",
        json={"body": "Starter is now $29/mo."},
        headers=_admin_headers(TENANT_A),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["body"] == "Starter is now $29/mo."
    assert body["chunks_written"] == 2
    assert fake_service.reindex_calls == [UUID(page_id)]


def test_patch_cms_page_unpublish_drops_chunks(
    client: TestClient, fake_service: _RouteFakeCmsPageService
) -> None:
    page_id = _seed_page(client)
    resp = client.patch(
        f"/cms/pages/{page_id}",
        json={"status": "draft"},
        headers=_admin_headers(TENANT_A),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "draft"
    assert fake_service.delete_calls == [UUID(page_id)]
    assert fake_service.reindex_calls == []


def test_patch_cms_page_404_when_absent(client: TestClient) -> None:
    resp = client.patch(
        f"/cms/pages/{uuid4()}",
        json={"title": "x"},
        headers=_admin_headers(TENANT_A),
    )
    assert resp.status_code == 404
    assert resp.json()["code"] == "not_found"


def test_patch_cms_page_tenant_isolation(client: TestClient) -> None:
    page_id = _seed_page(client, tenant_id=TENANT_A)
    # Tenant B tries to PATCH tenant A's page → 404, never modifies.
    resp = client.patch(
        f"/cms/pages/{page_id}",
        json={"title": "hijack"},
        headers=_admin_headers(TENANT_B),
    )
    assert resp.status_code == 404


# ----- DELETE /cms/pages/{id} ----------------------------------------------
def test_delete_cms_page_204_and_removes_chunks(
    client: TestClient, fake_service: _RouteFakeCmsPageService
) -> None:
    page_id = _seed_page(client)
    resp = client.delete(f"/cms/pages/{page_id}", headers=_admin_headers(TENANT_A))
    assert resp.status_code == 204
    # Real service delegates chunk deletion to RagService; the route fake
    # records the page id in delete_calls, which is the contract we depend
    # on at the boundary.
    assert UUID(page_id) in fake_service.delete_calls
    # Subsequent GET is 404.
    follow = client.get(f"/cms/pages/{page_id}", headers=_admin_headers(TENANT_A))
    assert follow.status_code == 404


def test_delete_cms_page_404_when_absent(client: TestClient) -> None:
    resp = client.delete(f"/cms/pages/{uuid4()}", headers=_admin_headers(TENANT_A))
    assert resp.status_code == 404


def test_delete_cms_page_tenant_isolation(client: TestClient) -> None:
    page_id = _seed_page(client, tenant_id=TENANT_A)
    # Tenant B → 404, page survives.
    cross = client.delete(f"/cms/pages/{page_id}", headers=_admin_headers(TENANT_B))
    assert cross.status_code == 404
    # Tenant A still sees it.
    still = client.get(f"/cms/pages/{page_id}", headers=_admin_headers(TENANT_A))
    assert still.status_code == 200


# ----- POST /cms/pages/{id}/publish ----------------------------------------
def test_publish_cms_page_reindexes_and_sets_status(
    client: TestClient, fake_service: _RouteFakeCmsPageService
) -> None:
    page_id = _seed_page(client)
    # Demote first via PATCH, then re-publish via the publish route.
    client.patch(
        f"/cms/pages/{page_id}",
        json={"status": "draft"},
        headers=_admin_headers(TENANT_A),
    )

    resp = client.post(f"/cms/pages/{page_id}/publish", headers=_admin_headers(TENANT_A))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "published"
    assert body["chunks_written"] == 2
    assert UUID(page_id) in fake_service.publish_calls


def test_publish_cms_page_404_when_absent(client: TestClient) -> None:
    resp = client.post(f"/cms/pages/{uuid4()}/publish", headers=_admin_headers(TENANT_A))
    assert resp.status_code == 404


# ----- POST /cms/pages/{id}/reindex ----------------------------------------
def test_reindex_cms_page_calls_index_once(
    client: TestClient, fake_service: _RouteFakeCmsPageService
) -> None:
    page_id = _seed_page(client)
    fake_service.reindex_calls.clear()

    resp = client.post(f"/cms/pages/{page_id}/reindex", headers=_admin_headers(TENANT_A))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["chunks_written"] == 2
    # Exactly one reindex call — no duplicate chunks.
    assert fake_service.reindex_calls == [UUID(page_id)]


def test_reindex_cms_page_404_when_absent(client: TestClient) -> None:
    resp = client.post(f"/cms/pages/{uuid4()}/reindex", headers=_admin_headers(TENANT_A))
    assert resp.status_code == 404


def test_reindex_cms_page_draft_is_zero_chunks(
    client: TestClient, fake_service: _RouteFakeCmsPageService
) -> None:
    page_id = _seed_page(client)
    # Flip to draft.
    client.patch(
        f"/cms/pages/{page_id}",
        json={"status": "draft"},
        headers=_admin_headers(TENANT_A),
    )
    fake_service.reindex_calls.clear()

    resp = client.post(f"/cms/pages/{page_id}/reindex", headers=_admin_headers(TENANT_A))
    assert resp.status_code == 200
    assert resp.json()["chunks_written"] == 0
    assert fake_service.reindex_calls == []


# ----- POST /cms/reindex-all -----------------------------------------------
def test_reindex_all_counts_published_pages_for_tenant(
    client: TestClient, fake_service: _RouteFakeCmsPageService
) -> None:
    _seed_page(client, tenant_id=TENANT_A, title="P1", body="b1")
    _seed_page(client, tenant_id=TENANT_A, title="P2", body="b2")
    _seed_page(client, tenant_id=TENANT_B, title="P-other", body="b")

    resp = client.post("/cms/reindex-all", headers=_admin_headers(TENANT_A))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["pages_reindexed"] == 2
    assert body["chunks_written"] == 4  # 2 pages * 2 chunks each
    assert fake_service.reindex_all_calls == [TENANT_A]
    # Tenant B's page is never reindexed by tenant A's request.
    assert all(
        pid in {p.id for p in fake_service._by_tenant.get(TENANT_A, [])}
        for pid in fake_service.reindex_calls
    )


def test_reindex_all_empty_tenant_returns_zero(
    client: TestClient,
) -> None:
    resp = client.post("/cms/reindex-all", headers=_admin_headers(TENANT_A))
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"pages_reindexed": 0, "chunks_written": 0}


# ----- Auth still enforced on the new routes ------------------------------
def test_patch_without_service_token_rejected(client: TestClient) -> None:
    resp = client.patch(
        f"/cms/pages/{uuid4()}",
        json={"title": "x"},
        headers={"X-Tenant-Id": str(TENANT_A), "Content-Type": "application/json"},
    )
    assert resp.status_code in (400, 403, 422)


def test_delete_with_wrong_service_token_rejected(client: TestClient) -> None:
    resp = client.delete(
        f"/cms/pages/{uuid4()}",
        headers={
            "X-Service-Token": "wrong-secret",
            "X-Tenant-Id": str(TENANT_A),
        },
    )
    assert resp.status_code == 403


def test_reindex_all_with_bad_tenant_header_rejected(client: TestClient) -> None:
    resp = client.post(
        "/cms/reindex-all",
        headers={
            "X-Service-Token": SERVICE_TOKEN,
            "X-Tenant-Id": "not-a-uuid",
        },
    )
    assert resp.status_code == 400


@pytest.fixture(autouse=True)
def _settings_service_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force the service-token guard to compare against ``SERVICE_TOKEN``.

    The guard reads ``Settings.SERVICE_AUTH_SECRET`` at request time; pin
    it via the env so the test value is deterministic.
    """
    monkeypatch.setenv("SERVICE_AUTH_SECRET", SERVICE_TOKEN)
    # The Settings instance is cached — clear so the next read picks up env.
    from app.core.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
