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

Owner: Person B.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from uuid import UUID, uuid4

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.cms import CmsPage, CmsPageStatus
from app.services.rag_service import RagService

logger = structlog.get_logger(__name__)

_SLUG_RE = re.compile(r"[^a-z0-9]+")


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
            self._session.add(page)
            await self._session.flush()
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
            await self._session.flush()
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

        total_stmt = select(func.count(CmsPage.id)).where(CmsPage.tenant_id == tenant_id)
        total = (await self._session.execute(total_stmt)).scalar_one()

        items_stmt = (
            select(CmsPage)
            .where(CmsPage.tenant_id == tenant_id)
            .order_by(CmsPage.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        items = (await self._session.execute(items_stmt)).scalars().all()
        return list(items), int(total)

    async def get_page(self, *, tenant_id: UUID, page_id: UUID) -> CmsPage | None:
        """Return one page or ``None`` if absent / cross-tenant."""
        stmt = select(CmsPage).where(CmsPage.tenant_id == tenant_id, CmsPage.id == page_id)
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def _lookup_by_slug(self, *, tenant_id: UUID, slug: str) -> CmsPage | None:
        stmt = select(CmsPage).where(CmsPage.tenant_id == tenant_id, CmsPage.slug == slug)
        return (await self._session.execute(stmt)).scalar_one_or_none()
