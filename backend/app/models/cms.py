"""CmsPage — durable, admin-authored ground truth for RAG retrieval.

One row per page the tenant publishes via ``POST /cms/pages``. The page
``body`` is the source of truth; chunks under ``cms_chunks`` are derived
from it via ``RagService.index_page`` and can be regenerated at any time.

Ownership boundary:

* This model owns the human-authored content (``title``, ``slug``,
  ``body``, ``status``).
* ``cms_chunks`` owns the derived vector representation. The two are
  kept aligned by always routing publishes through
  ``CmsPageService.create_page`` (or ``update_page``) — never by writing
  to either table directly.

The ``page_id`` column on ``cms_chunks`` is intentionally not yet a
formal foreign key here — see the comment in ``models/chunk.py``. The
FK will be added once existing deployments have re-published their
seeded content through the CMS flow.

Owner: Person B.
"""

from __future__ import annotations

import enum
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    DateTime,
    Enum,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class CmsPageStatus(str, enum.Enum):
    draft = "draft"
    published = "published"


class CmsPage(Base):
    __tablename__ = "cms_pages"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    # Per-tenant URL-safe identifier. Unique within a tenant — different
    # tenants may legitimately have a "/pricing" page each.
    slug: Mapped[str] = mapped_column(String(255), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    status: Mapped[CmsPageStatus] = mapped_column(
        Enum(CmsPageStatus, name="cms_page_status", native_enum=True),
        nullable=False,
        default=CmsPageStatus.published,
        server_default=CmsPageStatus.published.value,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    __table_args__ = (
        UniqueConstraint("tenant_id", "slug", name="uq_cms_pages_tenant_slug"),
        Index("ix_cms_pages_tenant_status", "tenant_id", "status"),
    )
