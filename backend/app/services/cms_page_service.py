"""CmsPageService — admin CMS ingestion routed through RagService.index_page.

Single entry point for ``POST /cms/pages``. The service:

1. Upserts the ``CmsPage`` row (tenant-scoped, slug-unique).
2. Calls ``RagService.index_page`` so the embedding pipeline produces
   the corresponding ``cms_chunks`` rows in the same transaction.

Architectural rules honored here:

* All writes are tenant-scoped at the SQL layer; RLS is the second wall.
* No direct embedding math, no raw vector generation, no fake RAG —
  every embedding goes through ``CohereEmbeddingClient`` via
  ``RagService.index_page`` (Spec 006 §6.7).
* The CMS row and its chunks are written under the same transaction the
  HTTP dependency commits, so partial-publish states (page row without
  chunks) cannot leak out of a request.
* Idempotent: re-posting the same ``(title, slug, body)`` updates the
  row in place and re-indexes the chunks via the existing
  delete-then-insert path. Posting an empty body still clears prior
  chunks for that page.
* All SQL statement construction, ``session.add`` / ``session.delete`` /
  ``session.flush`` calls live in ``app.repositories.cms_repository`` —
  this service contains only business logic (slug derivation, conflict
  detection, status-transition reindex/delete decisions, DTO
  construction, logging).

Owner: Person B.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from uuid import UUID, uuid4

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.cms import CmsPage, CmsPageStatus
from app.repositories import cms_repository
from app.services.rag_service import RagService

logger = structlog.get_logger(__name__)

_SLUG_RE = re.compile(r"[^a-z0-9]+")


class SlugConflictError(Exception):
    """Raised when a PATCH would collide with an existing ``(tenant_id, slug)``.

    Distinct from the upsert path used by ``create_page``: an explicit PATCH
    asks for a specific page by id, so a slug collision with a *different*
    page is unambiguously a conflict (HTTP 409) rather than an upsert.
    """

    def __init__(self, slug: str) -> None:
        self.slug = slug
        super().__init__(f"slug {slug!r} is already taken for this tenant")


def derive_slug(title: str) -> str:
    """Lower-case, hyphen-separated slug from a free-form title.

    Pure + deterministic: ``"Refund Policy!"`` → ``"refund-policy"``. The
    service-level uniqueness check on ``(tenant_id, slug)`` catches
    duplicate slugs across different titles.
    """
    cleaned = _SLUG_RE.sub("-", title.strip().lower()).strip("-")
    return cleaned or "page"


@dataclass(frozen=True)
class CmsPageWriteResult:
    """What ``create_page`` returns — the row plus how many chunks landed."""

    page: CmsPage
    chunks_written: int


class CmsPageService:
    def __init__(self, *, session: AsyncSession, rag_service: RagService) -> None:
        self._session = session
        self._rag = rag_service

    async def create_page(
        self,
        *,
        tenant_id: UUID,
        title: str,
        body: str,
        slug: str | None = None,
        status: CmsPageStatus = CmsPageStatus.published,
    ) -> CmsPageWriteResult:
        """Upsert one CMS page and (re)index its chunks.

        Idempotency contract: looking up by ``(tenant_id, slug)`` means a
        repeated POST with the same slug updates the page in place. The
        downstream ``RagService.index_page`` is itself a
        delete-then-insert keyed on ``(tenant_id, page_id)``, so the
        chunks end up in the same final state regardless of how many
        times the client retries.
        """
        if not title or not title.strip():
            raise ValueError("CmsPageService.create_page: title must be non-empty")
        if not body or not body.strip():
            raise ValueError("CmsPageService.create_page: body must be non-empty")

        resolved_slug = (slug or derive_slug(title)).strip().lower()

        existing = await self._lookup_by_slug(tenant_id=tenant_id, slug=resolved_slug)
        if existing is None:
            page = CmsPage(
                id=uuid4(),
                tenant_id=tenant_id,
                title=title.strip(),
                slug=resolved_slug,
                body=body,
                status=status,
            )
            await cms_repository.add(self._session, page)
            logger.info(
                "cms_page.created",
                tenant_id=str(tenant_id),
                page_id=str(page.id),
                slug=page.slug,
                body_chars=len(body),
            )
        else:
            existing.title = title.strip()
            existing.body = body
            existing.status = status
            await cms_repository.flush_pending(self._session)
            # Same MissingGreenlet guard as ``update_page``: the UPDATE
            # expires ``updated_at`` (``onupdate=func.now()``) and the route
            # serializes the ORM object synchronously, so we must re-fetch
            # before returning.
            await self._session.refresh(existing)
            page = existing
            logger.info(
                "cms_page.updated",
                tenant_id=str(tenant_id),
                page_id=str(page.id),
                slug=page.slug,
                body_chars=len(body),
            )

        chunks_written = await self._rag.index_page(
            tenant_id=tenant_id,
            page_id=page.id,
            content=page.body,
        )

        return CmsPageWriteResult(page=page, chunks_written=chunks_written)

    async def list_pages(
        self, *, tenant_id: UUID, limit: int = 100, offset: int = 0
    ) -> tuple[list[CmsPage], int]:
        """Return ``(items, total)`` for the caller's tenant, newest first."""
        if limit < 1 or limit > 500:
            raise ValueError("list_pages: limit must be in [1, 500]")
        if offset < 0:
            raise ValueError("list_pages: offset must be >= 0")

        total = await cms_repository.count_for_tenant(self._session, tenant_id=tenant_id)
        items = await cms_repository.list_for_tenant(
            self._session, tenant_id=tenant_id, limit=limit, offset=offset
        )
        return items, total

    async def get_page(self, *, tenant_id: UUID, page_id: UUID) -> CmsPage | None:
        """Return one page or ``None`` if absent / cross-tenant."""
        return await cms_repository.get_by_id(
            self._session, tenant_id=tenant_id, page_id=page_id
        )

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
        """Partial update of a single page. Returns ``None`` if not found.

        Reindex policy (Spec 005 FR-004 + FR-009):

        * ``body`` change on a page whose final status is ``published`` →
          re-route the new body through ``RagService.index_page`` (which is
          itself a delete-then-insert per ``(tenant_id, page_id)``).
        * Final status flipped to ``draft`` → drop any chunks via
          ``RagService.delete_page`` so the "draft pages MUST NOT be
          indexed" invariant is held even when a page is unpublished.
        * Slug change → enforce ``(tenant_id, slug)`` uniqueness at the
          service layer with a clear ``SlugConflictError`` instead of
          letting a Postgres ``IntegrityError`` surface as a 500.
        """
        page = await self.get_page(tenant_id=tenant_id, page_id=page_id)
        if page is None:
            return None

        original_status = page.status
        body_changed = body is not None and body != page.body

        if title is not None:
            if not title.strip():
                raise ValueError("update_page: title must be non-empty")
            page.title = title.strip()

        if slug is not None:
            new_slug = slug.strip().lower()
            if not new_slug:
                raise ValueError("update_page: slug must be non-empty")
            if new_slug != page.slug:
                clash = await self._lookup_by_slug(tenant_id=tenant_id, slug=new_slug)
                if clash is not None and clash.id != page.id:
                    raise SlugConflictError(new_slug)
                page.slug = new_slug

        if body is not None:
            if not body.strip():
                raise ValueError("update_page: body must be non-empty")
            page.body = body

        if status is not None:
            page.status = status

        await cms_repository.flush_pending(self._session)
        # Targeted probe for the MissingGreenlet root-cause diagnosis: force
        # SQLAlchemy to re-fetch any column expired by the UPDATE flush
        # (specifically ``updated_at``, which has ``onupdate=func.now()``) so
        # the route can serialize the ORM object without triggering a lazy
        # load outside an active greenlet. See investigation report dated
        # 2026-05-30; if confirmed, the structural fix is
        # ``__mapper_args__ = {"eager_defaults": True}`` on the model.
        await self._session.refresh(page)

        chunks_written = 0
        # Final-state checks (Spec 005 FR-004 + FR-009).
        if page.status == CmsPageStatus.draft and original_status == CmsPageStatus.published:
            # Page was unpublished — purge stale chunks so retrieval can never
            # return a draft page's content.
            await self._rag.delete_page(tenant_id=tenant_id, page_id=page.id)
        elif page.status == CmsPageStatus.published and body_changed:
            chunks_written = await self._rag.index_page(
                tenant_id=tenant_id,
                page_id=page.id,
                content=page.body,
            )

        logger.info(
            "cms_page.patched",
            tenant_id=str(tenant_id),
            page_id=str(page.id),
            slug=page.slug,
            body_changed=body_changed,
            status=page.status.value,
            chunks_written=chunks_written,
        )
        return CmsPageWriteResult(page=page, chunks_written=chunks_written)

    async def delete_page(self, *, tenant_id: UUID, page_id: UUID) -> bool:
        """Delete a page and its chunks. Returns ``True`` if removed, ``False`` if absent.

        Chunks are deleted first via ``RagService.delete_page`` because
        ``cms_chunks.page_id`` does not yet have an FK to ``cms_pages.id``
        (intentional — see migration 0004 comment). Both deletes run in
        the caller's transaction so the request is atomic.
        """
        page = await self.get_page(tenant_id=tenant_id, page_id=page_id)
        if page is None:
            return False

        chunks_deleted = await self._rag.delete_page(tenant_id=tenant_id, page_id=page_id)
        await cms_repository.remove(self._session, page)
        logger.info(
            "cms_page.deleted",
            tenant_id=str(tenant_id),
            page_id=str(page_id),
            slug=page.slug,
            chunks_deleted=chunks_deleted,
        )
        return True

    async def publish_page(self, *, tenant_id: UUID, page_id: UUID) -> CmsPageWriteResult | None:
        """Flip status to ``published`` and re-index the body. ``None`` if absent.

        Idempotent: re-publishing an already-published page still re-indexes
        (Spec 005 FR-006). The reindex goes through ``RagService.index_page``,
        which is itself a delete-then-insert per ``(tenant_id, page_id)``.
        """
        page = await self.get_page(tenant_id=tenant_id, page_id=page_id)
        if page is None:
            return None

        page.status = CmsPageStatus.published
        await cms_repository.flush_pending(self._session)

        chunks_written = await self._rag.index_page(
            tenant_id=tenant_id,
            page_id=page.id,
            content=page.body,
        )
        logger.info(
            "cms_page.published",
            tenant_id=str(tenant_id),
            page_id=str(page.id),
            slug=page.slug,
            chunks_written=chunks_written,
        )
        return CmsPageWriteResult(page=page, chunks_written=chunks_written)

    async def reindex_page(self, *, tenant_id: UUID, page_id: UUID) -> CmsPageWriteResult | None:
        """Reindex one page in place. ``None`` if absent.

        Spec 005 FR-007 / edge case: draft pages are a no-op (FR-009 says
        draft pages MUST NOT be indexed). The single call to
        ``RagService.index_page`` covers FR-007's "delete existing chunks
        for the page and re-trigger the embedding pipeline" because
        ``index_page`` is itself idempotent and delete-then-inserts.
        """
        page = await self.get_page(tenant_id=tenant_id, page_id=page_id)
        if page is None:
            return None

        if page.status != CmsPageStatus.published:
            logger.info(
                "cms_page.reindex_skipped_draft",
                tenant_id=str(tenant_id),
                page_id=str(page.id),
            )
            return CmsPageWriteResult(page=page, chunks_written=0)

        chunks_written = await self._rag.index_page(
            tenant_id=tenant_id,
            page_id=page.id,
            content=page.body,
        )
        logger.info(
            "cms_page.reindexed",
            tenant_id=str(tenant_id),
            page_id=str(page.id),
            slug=page.slug,
            chunks_written=chunks_written,
        )
        return CmsPageWriteResult(page=page, chunks_written=chunks_written)

    async def reindex_all(self, *, tenant_id: UUID) -> tuple[int, int]:
        """Reindex every published page for ``tenant_id``.

        Returns ``(pages_reindexed, chunks_written)``. Runs synchronously
        and in order — fine for the seed corpus (Spec 005 SC-005 caps a
        tenant at 500 pages). Spec edge case mentions a 202-accepted
        background-job path for 100+ pages; implementing the queue lives
        with the worker service and is intentionally out of scope here.
        """
        pages = await cms_repository.list_published_for_tenant(
            self._session, tenant_id=tenant_id
        )

        total_chunks = 0
        for page in pages:
            total_chunks += await self._rag.index_page(
                tenant_id=tenant_id,
                page_id=page.id,
                content=page.body,
            )

        logger.info(
            "cms.reindex_all.completed",
            tenant_id=str(tenant_id),
            pages_reindexed=len(pages),
            chunks_written=total_chunks,
        )
        return len(pages), total_chunks

    async def _lookup_by_slug(self, *, tenant_id: UUID, slug: str) -> CmsPage | None:
        return await cms_repository.get_by_slug(
            self._session, tenant_id=tenant_id, slug=slug
        )
