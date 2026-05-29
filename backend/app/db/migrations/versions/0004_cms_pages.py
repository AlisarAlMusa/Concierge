"""cms_pages — admin-authored ground truth for RAG retrieval

Revision ID: 0004
Revises: 0003b_chat_persistence
Create Date: 2026-05-28

Person B. Creates the ``cms_pages`` table behind ``POST /cms/pages``. The
table stores the human-authored source content; ``cms_chunks`` continues
to store the derived embedding rows (delete-then-insert through
``RagService.index_page``).

Parent reset from ``"0003"`` → ``"0003b_chat_persistence"`` as part of
the migration-graph repair (see ``0003b_chat_persistence.py`` docstring).
The CMS pages table itself does not depend on widgets/conversations/etc.,
but linearising after the chat-persistence revision keeps the DAG
strictly single-headed and reflects the order CMS work landed.

Enum-creation pattern matches 0003: declared with ``create_type=False``
and created explicitly via ``.create(bind, checkfirst=True)`` so the
migration is idempotent against partially-migrated databases.

RLS:

* Strict per-tenant ``USING + WITH CHECK`` policy (defense in depth).
  ``CmsPageService`` runs under ``get_tenant_rls_session`` which always
  sets ``app.tenant_id`` from the verified caller header.

FK to ``cms_chunks.page_id`` is intentionally NOT added here — seeded
deployments may already have chunks whose ``page_id`` references nothing,
and adding the FK would fail to validate. A follow-up migration adds the
FK once all live deployments have republished through the CMS flow.
"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: Union[str, None] = "0003b_chat_persistence"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


cms_page_status_enum = postgresql.ENUM(
    "draft",
    "published",
    name="cms_page_status",
    create_type=False,
)


def upgrade() -> None:
    bind = op.get_bind()
    cms_page_status_enum.create(bind, checkfirst=True)

    op.create_table(
        "cms_pages",
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
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("slug", sa.String(255), nullable=False),
        sa.Column("body", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "status",
            cms_page_status_enum,
            nullable=False,
            server_default="published",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("tenant_id", "slug", name="uq_cms_pages_tenant_slug"),
    )
    op.create_index("ix_cms_pages_tenant_id", "cms_pages", ["tenant_id"])
    op.create_index("ix_cms_pages_tenant_status", "cms_pages", ["tenant_id", "status"])

    op.execute("ALTER TABLE cms_pages ENABLE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY cms_pages_tenant_isolation ON cms_pages
          USING (tenant_id::text = current_setting('app.tenant_id', true))
          WITH CHECK (tenant_id::text = current_setting('app.tenant_id', true))
        """)


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS cms_pages_tenant_isolation ON cms_pages")
    op.execute("ALTER TABLE IF EXISTS cms_pages DISABLE ROW LEVEL SECURITY")
    op.drop_index("ix_cms_pages_tenant_status", table_name="cms_pages")
    op.drop_index("ix_cms_pages_tenant_id", table_name="cms_pages")
    op.drop_table("cms_pages")

    bind = op.get_bind()
    cms_page_status_enum.drop(bind, checkfirst=True)
