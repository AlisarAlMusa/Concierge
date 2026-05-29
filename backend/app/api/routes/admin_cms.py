"""Tenant-admin CMS routes — JWT-authenticated, tenant derived from the user.

Mounted at /tenant/cms. Replaces the transitional X-Service-Token +
X-Tenant-Id pattern on /cms/pages for admin-UI callers. The service
layer (CmsPageService) is identical — only the auth layer changes.
"""

from __future__ import annotations

from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ExternalServiceError
from app.dependencies import get_admin_cms_rag_service, get_db_session, require_tenant_admin
from app.models.user import User
from app.schemas.cms import (
    CmsPageCreate,
    CmsPageList,
    CmsPageRead,
    CmsPageUpdate,
    CmsReindexAllResult,
)
from app.services.cms_page_service import CmsPageService, SlugConflictError
from app.services.rag_service import RagService

logger = structlog.get_logger(__name__)

router = APIRouter(tags=["admin_cms"])


def _to_read(page, *, chunks_written: int | None = None) -> CmsPageRead:
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


def _not_found() -> HTTPException:
    return HTTPException(status_code=404, detail="Page not found")


def _get_service(
    session: AsyncSession = Depends(get_db_session),
    rag_service: RagService = Depends(get_admin_cms_rag_service),
) -> CmsPageService:
    return CmsPageService(session=session, rag_service=rag_service)


@router.get("/", response_model=CmsPageList, summary="List CMS pages for the calling tenant")
async def list_cms_pages(
    current_user: User = Depends(require_tenant_admin),
    service: CmsPageService = Depends(_get_service),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> CmsPageList:
    items, total = await service.list_pages(
        tenant_id=current_user.tenant_id, limit=limit, offset=offset
    )
    return CmsPageList(items=[_to_read(p) for p in items], total=total)


@router.post(
    "/",
    response_model=CmsPageRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a CMS page",
)
async def create_cms_page(
    payload: CmsPageCreate,
    current_user: User = Depends(require_tenant_admin),
    service: CmsPageService = Depends(_get_service),
) -> JSONResponse:
    try:
        result = await service.create_page(
            tenant_id=current_user.tenant_id,
            title=payload.title,
            slug=payload.slug,
            body=payload.body,
            status=payload.status,
        )
        body = _to_read(result.page, chunks_written=result.chunks_written).model_dump(mode="json")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ExternalServiceError:
        # Embedding unavailable — page row was flushed and will be committed by
        # require_tenant_admin. Look it up by slug so we can return a 201.
        from sqlalchemy import select as _select
        from app.models.cms import CmsPage as _CmsPage
        from app.services.cms_page_service import derive_slug
        resolved_slug = (payload.slug or derive_slug(payload.title)).strip().lower()
        stmt = _select(_CmsPage).where(
            _CmsPage.tenant_id == current_user.tenant_id,
            _CmsPage.slug == resolved_slug,
        )
        result_row = await service._session.execute(stmt)
        page = result_row.scalar_one_or_none()
        if page is None:
            raise HTTPException(status_code=500, detail="Page saved but could not be retrieved")
        body = _to_read(page).model_dump(mode="json")
    return JSONResponse(status_code=status.HTTP_201_CREATED, content=body)


@router.get("/{page_id}", response_model=CmsPageRead, summary="Get one CMS page")
async def get_cms_page(
    page_id: UUID,
    current_user: User = Depends(require_tenant_admin),
    service: CmsPageService = Depends(_get_service),
) -> CmsPageRead:
    page = await service.get_page(tenant_id=current_user.tenant_id, page_id=page_id)
    if page is None:
        raise _not_found()
    return _to_read(page)


@router.put("/{page_id}", response_model=CmsPageRead, summary="Replace a CMS page")
async def update_cms_page(
    page_id: UUID,
    payload: CmsPageUpdate,
    current_user: User = Depends(require_tenant_admin),
    service: CmsPageService = Depends(_get_service),
) -> CmsPageRead:
    try:
        result = await service.update_page(
            tenant_id=current_user.tenant_id,
            page_id=page_id,
            title=payload.title,
            slug=payload.slug,
            body=payload.body,
            status=payload.status,
        )
        if result is None:
            raise _not_found()
        return _to_read(result.page, chunks_written=result.chunks_written)
    except SlugConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ExternalServiceError:
        # Embedding unavailable — page data was flushed and will be committed.
        # Return the page as-is; chunks are stale until Cohere is reachable.
        page = await service.get_page(tenant_id=current_user.tenant_id, page_id=page_id)
        if page is None:
            raise _not_found()
        return _to_read(page)


@router.delete("/{page_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Delete a CMS page")
async def delete_cms_page(
    page_id: UUID,
    current_user: User = Depends(require_tenant_admin),
    service: CmsPageService = Depends(_get_service),
) -> JSONResponse:
    deleted = await service.delete_page(tenant_id=current_user.tenant_id, page_id=page_id)
    if not deleted:
        raise _not_found()
    return JSONResponse(status_code=status.HTTP_204_NO_CONTENT, content=None)


@router.post("/{page_id}/reindex", response_model=CmsPageRead, summary="Reindex a CMS page")
async def reindex_cms_page(
    page_id: UUID,
    current_user: User = Depends(require_tenant_admin),
    service: CmsPageService = Depends(_get_service),
) -> CmsPageRead:
    result = await service.reindex_page(tenant_id=current_user.tenant_id, page_id=page_id)
    if result is None:
        raise _not_found()
    return _to_read(result.page, chunks_written=result.chunks_written)


@router.post("/reindex-all", response_model=CmsReindexAllResult, summary="Reindex all pages")
async def reindex_all_cms_pages(
    current_user: User = Depends(require_tenant_admin),
    service: CmsPageService = Depends(_get_service),
) -> CmsReindexAllResult:
    pages, chunks = await service.reindex_all(tenant_id=current_user.tenant_id)
    return CmsReindexAllResult(pages_reindexed=pages, chunks_written=chunks)
