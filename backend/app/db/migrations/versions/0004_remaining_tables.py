"""guardrail_configs + FK wire + retroactive RLS on audit_logs / cost_events

Revision ID: 0004_remaining
Revises: 0005_leads_admin, 0003_users
Create Date: 2026-05-28

Merge node: depends on both Person B's last migration (0005_leads_admin)
and Person A's users/roles migration (0003_users).

Unique additions not covered by any other migration:
  1. guardrail_configs table (Person C scope)
  2. Deferred FK: cms_chunks.page_id → cms_pages.id (cms_pages now exists)
  3. Retroactive RLS on audit_logs (nullable tenant_id guard)
  4. Retroactive RLS on cost_events

All tables created by 0003_chat_persistence and 0004_cms_pages already have
RLS enabled — this migration does NOT re-apply RLS to those tables.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004_remaining"
down_revision: Union[str, Sequence[str], None] = ("0005_leads_admin", "0004", "0003_users")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. guardrail_configs -------------------------------------------------------
    op.create_table(
        "guardrail_configs",
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
            unique=True,
        ),
        sa.Column("persona", sa.Text, nullable=True),
        sa.Column("allowed_topics", postgresql.ARRAY(sa.Text), nullable=True),
        sa.Column("blocked_topics", postgresql.ARRAY(sa.Text), nullable=True),
        sa.Column("refusal_tone", sa.String(100), nullable=True),
        sa.Column("enabled_tools", postgresql.JSONB, nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_guardrail_configs_tenant_id", "guardrail_configs", ["tenant_id"])
    op.create_unique_constraint(
        "uq_guardrail_configs_tenant",
        "guardrail_configs",
        ["tenant_id"],
    )
    op.execute("ALTER TABLE guardrail_configs ENABLE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY guardrail_configs_tenant_isolation ON guardrail_configs
          USING (tenant_id::text = current_setting('app.tenant_id', true))
          WITH CHECK (tenant_id::text = current_setting('app.tenant_id', true))
    """)

    # 2. Deferred FK: cms_chunks.page_id → cms_pages.id -------------------------
    op.create_foreign_key(
        "fk_cms_chunks_page_id",
        "cms_chunks",
        "cms_pages",
        ["page_id"],
        ["id"],
        ondelete="CASCADE",
    )

    # 3. Retroactive RLS on audit_logs (nullable tenant_id) ---------------------
    op.execute("ALTER TABLE audit_logs ENABLE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY audit_logs_tenant_isolation ON audit_logs
          USING (
            tenant_id IS NOT NULL
            AND tenant_id::text = current_setting('app.tenant_id', true)
          )
          WITH CHECK (
            tenant_id IS NOT NULL
            AND tenant_id::text = current_setting('app.tenant_id', true)
          )
    """)

    # 4. Retroactive RLS on cost_events (NOT NULL tenant_id) -------------------
    op.execute("ALTER TABLE cost_events ENABLE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY cost_events_tenant_isolation ON cost_events
          USING (tenant_id::text = current_setting('app.tenant_id', true))
          WITH CHECK (tenant_id::text = current_setting('app.tenant_id', true))
    """)


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS cost_events_tenant_isolation ON cost_events")
    op.execute("ALTER TABLE cost_events DISABLE ROW LEVEL SECURITY")

    op.execute("DROP POLICY IF EXISTS audit_logs_tenant_isolation ON audit_logs")
    op.execute("ALTER TABLE audit_logs DISABLE ROW LEVEL SECURITY")

    op.drop_constraint("fk_cms_chunks_page_id", "cms_chunks", type_="foreignkey")

    op.execute("DROP POLICY IF EXISTS guardrail_configs_tenant_isolation ON guardrail_configs")
    op.execute("ALTER TABLE guardrail_configs DISABLE ROW LEVEL SECURITY")
    op.drop_index("ix_guardrail_configs_tenant_id", table_name="guardrail_configs")
    op.drop_table("guardrail_configs")
