"""CmsChunk — one retrievable unit of CMS content with its Cohere v3 embedding.

The single SQL truth for RAG retrieval. Tenant isolation is enforced at three
layers — application (explicit ``WHERE tenant_id = …`` in ``RagService``),
database (RLS policy ``cms_chunks_tenant_isolation``), and indexing (the
``(tenant_id, page_id)`` composite index ensures cross-tenant rows never share
a hot path).

Owner: Person B. See ``specs/rag-service/spec.md`` for the authoritative contract.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

# Cohere ``embed-english-v3.0`` produces 1024-dim vectors. Mirrors
# ``app.services.embedding_client.EMBEDDING_DIM`` — kept as a local literal so
# importing the model doesn't pull in the embedding client at module load.
CMS_CHUNK_EMBEDDING_DIM = 1024


class CmsChunk(Base):
    """Storage row for one ``CmsChunk`` produced by ``chunk_page``.

    The ``page_id`` is a UUID, not a foreign key yet — the ``cms_pages`` table
    is owned by a separate migration and the FK constraint is added on top
    when that table lands. Tenant FK and cascade are wired today so cleanup
    on tenant deletion remains correct.
    """

    __tablename__ = "cms_chunks"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # FK to cms_pages added in a later migration once that table exists.
    page_id: Mapped[UUID] = mapped_column(nullable=False)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[list[float]] = mapped_column(Vector(CMS_CHUNK_EMBEDDING_DIM), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "page_id",
            "chunk_index",
            name="uq_cms_chunks_tenant_page_idx",
        ),
        Index("ix_cms_chunks_tenant_page", "tenant_id", "page_id"),
    )
