"""HTTP contracts for the CMS ingestion surface (POST/GET /cms/pages).

Only the fields the admin client actually sends/receives. The internal
``CmsPage`` ORM row carries timestamps + tenant id; the wire format
intentionally drops ``tenant_id`` because the server reads it from the
authenticated header, never from the body.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.cms import CmsPageStatus


class CmsPageCreate(BaseModel):
    """``POST /cms/pages`` request body.

    ``slug`` is optional — if omitted the service derives a URL-safe slug
    from ``title``. Re-using an existing slug within the tenant updates
    that page (idempotent write).
    """

    title: str = Field(..., min_length=1, max_length=255)
    slug: str | None = Field(default=None, min_length=1, max_length=255)
    body: str = Field(..., min_length=1)
    status: CmsPageStatus = CmsPageStatus.published


class CmsPageRead(BaseModel):
    """``POST /cms/pages`` and ``GET /cms/pages[/{id}]`` response shape."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    title: str
    slug: str
    body: str
    status: CmsPageStatus
    chunks_written: int | None = Field(
        default=None,
        description=(
            "Number of pgvector chunks written to cms_chunks for this page on "
            "the most recent index. Only populated by POST responses; list "
            "responses leave it null."
        ),
    )
    created_at: datetime
    updated_at: datetime


class CmsPageList(BaseModel):
    """``GET /cms/pages`` response — flat list scoped to the caller's tenant."""

    items: list[CmsPageRead]
    total: int


class CmsPageUpdate(BaseModel):
    """``PATCH /cms/pages/{page_id}`` request body — every field optional.

    All four fields are optional so the client can submit any combination
    (e.g. just ``status`` to unpublish, just ``body`` to trigger reindex).
    The service applies the reindex policy: body change on a published
    page reindexes, flipping to ``draft`` drops chunks. See
    ``CmsPageService.update_page`` for the full state machine.
    """

    title: str | None = Field(default=None, min_length=1, max_length=255)
    slug: str | None = Field(default=None, min_length=1, max_length=255)
    body: str | None = Field(default=None, min_length=1)
    status: CmsPageStatus | None = None


class CmsReindexAllResult(BaseModel):
    """``POST /cms/reindex-all`` response.

    Synchronous bulk reindex result for the seed/demo corpus. Larger
    corpora (spec edge case: 100+ pages) would queue a background job
    and return 202 — implementing that queue lives with the worker
    service and is intentionally out of scope here.
    """

    pages_reindexed: int = Field(..., ge=0)
    chunks_written: int = Field(..., ge=0)
