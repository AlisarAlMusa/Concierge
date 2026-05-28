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
