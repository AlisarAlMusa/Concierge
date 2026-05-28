"""cms_chunks table for RAG retrieval

Revision ID: 0002
Revises: 0001
Create Date: 2026-05-26

Person B (RAG). Creates the storage backing ``RagService`` — one row per
``CmsChunk`` emitted by the chunking pipeline, with the Cohere
``embed-english-v3.0`` vector and the tenant id required for the explicit
``WHERE tenant_id = …`` filter the spec mandates.

Tenant isolation is enforced at three levels:

1. Application: explicit ``WHERE tenant_id = $1`` in every ``RagService`` query.
2. Database: RLS policy ``cms_chunks_tenant_isolation`` (defense in depth).
3. Indexing: composite ``(tenant_id, page_id)`` index for hot paths.

No ANN index (HNSW) at MVP — sequential scan is acceptable for the seed corpus
and avoids configuring index parameters before the eval harness exists. HNSW
lands in a follow-up migration if golden-set latency demands it.

The ``page_id`` column is a plain UUID at this revision — the FK to
``cms_pages`` will be added by whichever migration creates that table.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "cms_chunks",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("page_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("chunk_index", sa.Integer, nullable=False),
        sa.Column("text", sa.Text, nullable=False),
        sa.Column("embedding", Vector(1024), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "page_id",
            "chunk_index",
            name="uq_cms_chunks_tenant_page_idx",
        ),
    )
    op.create_index("ix_cms_chunks_tenant_id", "cms_chunks", ["tenant_id"])
    op.create_index("ix_cms_chunks_tenant_page", "cms_chunks", ["tenant_id", "page_id"])

    # Row-level security: read and write both gated by app.tenant_id.
    # Reading: the policy filters rows where tenant_id doesn't match the session var.
    # Writing: WITH CHECK prevents inserting / updating rows under another tenant.
    op.execute("ALTER TABLE cms_chunks ENABLE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY cms_chunks_tenant_isolation ON cms_chunks
          USING (tenant_id::text = current_setting('app.tenant_id', true))
          WITH CHECK (tenant_id::text = current_setting('app.tenant_id', true))
        """)


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS cms_chunks_tenant_isolation ON cms_chunks")
    op.execute("ALTER TABLE cms_chunks DISABLE ROW LEVEL SECURITY")
    op.drop_index("ix_cms_chunks_tenant_page", table_name="cms_chunks")
    op.drop_index("ix_cms_chunks_tenant_id", table_name="cms_chunks")
    op.drop_table("cms_chunks")
