# Stub endpoints — Person B will implement full CMS CRUD here.
# The require_tenant_admin dependency is wired so that when content is added,
# RLS automatically scopes queries to the authenticated tenant.
"""CMS ingestion routes — ``POST /cms/pages`` + ``GET /cms/pages``.

Authoring surface for tenant admins. Every write through this router
goes through ``CmsPageService`` → ``RagService.index_page`` so the
embedding pipeline writes the corresponding ``cms_chunks`` rows in the
same transaction. There is no direct path to ``cms_chunks`` from here.

Authentication (transitional):

Owner A's ``/auth`` admin surface is not yet shipped, so these endpoints
are gated by two layers:

1. ``X-Service-Token`` — shared secret from ``Settings.SERVICE_AUTH_SECRET``.
   Already used elsewhere as the service-to-service credential.
2. ``X-Tenant-Id`` — the tenant the caller is operating on. The header
   value is the tenant id baked into the RLS session, into the
   ``CmsPage.tenant_id`` column, and into the ``cms_chunks`` write.

Once Owner A's admin JWT lands, this gate is replaced with
``require_tenant_admin`` and the tenant comes from the verified user —
the service layer below does not change.

Owner: Person B.
"""

from __future__ import annotations

from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import JSONResponse

from app.core.security import require_service_token
from app.dependencies import get_admin_tenant_id, get_cms_page_service
from app.schemas.cms import (
    CmsPageCreate,
    CmsPageList,
    CmsPageRead,
    CmsPageUpdate,
    CmsReindexAllResult,
)
from app.services.cms_page_service import CmsPageService, SlugConflictError

logger = structlog.get_logger(__name__)

router = APIRouter(tags=["cms"], dependencies=[Depends(require_service_token)])


def _to_read(
    page,
    *,
    chunks_written: int | None = None,
) -> CmsPageRead:
    """Build the public response from an ORM row."""
    return CmsPageRead(
        id=page.id,
        tenant_id=page.tenant_id,
        title=page.title,
        slug=page.slug,
        body=page.body,
        status=page.status,
        chunks_written=chunks_written,
        created_at=page.created_at,
        updated_at=page.updated_at,
    )


@router.post(
    "/pages",
    response_model=CmsPageRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create or update a CMS page (idempotent on (tenant_id, slug))",
)
async def post_cms_page(
    payload: CmsPageCreate,
    tenant_id: UUID = Depends(get_admin_tenant_id),
    service: CmsPageService = Depends(get_cms_page_service),
) -> JSONResponse:
    """Upsert a CMS page and (re)index its chunks.

    Returns 201 with the persisted page on both create and update — the
    update case is documented but the status code stays 201 so callers
    can treat the endpoint as "ensure exists with these contents".
    """
    try:
        result = await service.create_page(
            tenant_id=tenant_id,
            title=payload.title,
            slug=payload.slug,
            body=payload.body,
            status=payload.status,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={"detail": str(exc), "code": "invalid_payload"},
        ) from exc

    logger.info(
        "cms.page.published",
        tenant_id=str(tenant_id),
        page_id=str(result.page.id),
        slug=result.page.slug,
        chunks_written=result.chunks_written,
    )
    body = _to_read(result.page, chunks_written=result.chunks_written).model_dump(mode="json")
    return JSONResponse(status_code=status.HTTP_201_CREATED, content=body)


@router.get(
    "/pages",
    response_model=CmsPageList,
    summary="List CMS pages for the caller's tenant (newest first)",
)
async def list_cms_pages(
    tenant_id: UUID = Depends(get_admin_tenant_id),
    service: CmsPageService = Depends(get_cms_page_service),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> CmsPageList:
    items, total = await service.list_pages(tenant_id=tenant_id, limit=limit, offset=offset)
    return CmsPageList(items=[_to_read(p) for p in items], total=total)


@router.get(
    "/pages/{page_id}",
    response_model=CmsPageRead,
    summary="Fetch one CMS page by id",
)
async def get_cms_page(
    page_id: UUID,
    tenant_id: UUID = Depends(get_admin_tenant_id),
    service: CmsPageService = Depends(get_cms_page_service),
) -> CmsPageRead:
    page = await service.get_page(tenant_id=tenant_id, page_id=page_id)
    if page is None:
        raise HTTPException(
            status_code=404,
            detail={"detail": "page not found", "code": "not_found"},
        )
    return _to_read(page)


def _not_found() -> HTTPException:
    return HTTPException(
        status_code=404,
        detail={"detail": "page not found", "code": "not_found"},
    )


@router.patch(
    "/pages/{page_id}",
    response_model=CmsPageRead,
    summary="Partially update a CMS page (reindex on body change for published pages)",
)
async def patch_cms_page(
    page_id: UUID,
    payload: CmsPageUpdate,
    tenant_id: UUID = Depends(get_admin_tenant_id),
    service: CmsPageService = Depends(get_cms_page_service),
) -> CmsPageRead:
    """Partial update.

    Spec 005 FR-004 + FR-009: a body change on a published page triggers a
    reindex through ``RagService.index_page``; flipping a page to ``draft``
    purges its chunks. Other field-only updates (title, slug) do not touch
    the embedding pipeline.
    """
    try:
        result = await service.update_page(
            tenant_id=tenant_id,
            page_id=page_id,
            title=payload.title,
            slug=payload.slug,
            body=payload.body,
            status=payload.status,
        )
    except SlugConflictError as exc:
        raise HTTPException(
            status_code=409,
            detail={"detail": str(exc), "code": "conflict"},
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={"detail": str(exc), "code": "invalid_payload"},
        ) from exc

    if result is None:
        raise _not_found()

    return _to_read(result.page, chunks_written=result.chunks_written)


@router.delete(
    "/pages/{page_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a CMS page and its chunks (tenant-scoped, 404 if absent)",
)
async def delete_cms_page(
    page_id: UUID,
    tenant_id: UUID = Depends(get_admin_tenant_id),
    service: CmsPageService = Depends(get_cms_page_service),
) -> JSONResponse:
    """Delete one page + its chunks.

    Returns 204 on success, 404 if the page does not exist for the caller's
    tenant. Cross-tenant deletes are indistinguishable from "not found" by
    design — never leak existence across tenants.
    """
    deleted = await service.delete_page(tenant_id=tenant_id, page_id=page_id)
    if not deleted:
        raise _not_found()
    return JSONResponse(status_code=status.HTTP_204_NO_CONTENT, content=None)


@router.post(
    "/pages/{page_id}/publish",
    response_model=CmsPageRead,
    summary="Set status to published and reindex (Spec 005 FR-006)",
)
async def publish_cms_page(
    page_id: UUID,
    tenant_id: UUID = Depends(get_admin_tenant_id),
    service: CmsPageService = Depends(get_cms_page_service),
) -> CmsPageRead:
    result = await service.publish_page(tenant_id=tenant_id, page_id=page_id)
    if result is None:
        raise _not_found()
    return _to_read(result.page, chunks_written=result.chunks_written)


@router.post(
    "/pages/{page_id}/reindex",
    response_model=CmsPageRead,
    summary="Reindex a single published page in place (no-op on drafts)",
)
async def reindex_cms_page(
    page_id: UUID,
    tenant_id: UUID = Depends(get_admin_tenant_id),
    service: CmsPageService = Depends(get_cms_page_service),
) -> CmsPageRead:
    """Reindex one page.

    Spec 005 FR-007. ``RagService.index_page`` is idempotent
    (delete-then-insert per ``(tenant_id, page_id)``), so this never
    duplicates chunks. Draft pages return ``chunks_written=0`` without
    touching the vector store (Spec 005 edge case + FR-009).
    """
    result = await service.reindex_page(tenant_id=tenant_id, page_id=page_id)
    if result is None:
        raise _not_found()
    return _to_read(result.page, chunks_written=result.chunks_written)


@router.post(
    "/reindex-all",
    response_model=CmsReindexAllResult,
    summary="Reindex every published page for the caller's tenant",
)
async def reindex_all_cms_pages(
    tenant_id: UUID = Depends(get_admin_tenant_id),
    service: CmsPageService = Depends(get_cms_page_service),
) -> CmsReindexAllResult:
    """Bulk reindex.

    Synchronous over the tenant's published corpus. Returns the page
    count and total chunks written so admin tooling can verify the
    operation. See ``CmsPageService.reindex_all`` for the
    out-of-scope async-job note.
    """
    pages, chunks = await service.reindex_all(tenant_id=tenant_id)
    return CmsReindexAllResult(pages_reindexed=pages, chunks_written=chunks)
