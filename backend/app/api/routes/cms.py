
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
from app.schemas.cms import CmsPageCreate, CmsPageList, CmsPageRead
from app.services.cms_page_service import CmsPageService

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
