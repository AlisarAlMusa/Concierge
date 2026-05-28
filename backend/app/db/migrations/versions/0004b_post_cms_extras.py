"""guardrail_configs + retroactive RLS + deferred cms_chunks FK

Revision ID: 0004b_post_cms_extras
Revises: 0004
Create Date: 2026-05-28

Migration-graph repair note (read this first)
=============================================

This file used to be ``0004_remaining_tables.py`` (revision ``"0004"``,
down_revision ``"0003"``). It collided with ``0004_cms_pages.py`` on the
same revision id and **also re-created** ``cms_pages``, ``widgets``,
``conversations``, ``messages``, ``leads``, and ``escalations`` —
tables whose canonical schema lives in ``0003b_chat_persistence`` and
``0004_cms_pages`` and is what the live ORM models (``app/models/*.py``)
read. Running both branches against any database is impossible:

* ``CREATE TABLE widgets`` would fail (already created in 0003b),
* ``CREATE TYPE conversation_status`` would fail,
* the schemas don't even agree (``widgets.theme`` vs ``widgets.theme_json``,
  ``message_role`` with 2 vs 4 values, etc.).

Repair strategy
---------------

The file is renamed to ``0004b_post_cms_extras`` and chained linearly
after ``0004_cms_pages``. Its ``upgrade()`` is trimmed to **only the
operations not already performed by 0003b / 0004**, namely:

1. ``guardrail_configs`` — table + index + unique constraint + RLS. No
   other migration creates this table; integration tests in
   ``tests/integration/test_rls_isolation.py`` already require it.
2. Deferred foreign key ``cms_chunks.page_id → cms_pages.id`` with
   ``ON DELETE CASCADE``. ``0004_cms_pages`` documents that the FK is
   added "by whichever migration creates [cms_pages]'s referencing
   table"; this is that migration.
3. Retroactive Row-Level Security on ``audit_logs`` (nullable
   ``tenant_id``, so the policy adds an ``IS NOT NULL`` guard) and on
   ``cost_events`` (NOT NULL ``tenant_id``). 0001 created both tables
   but did not enable RLS — these are the only places that wire it.

Everything else from the original file (the duplicate CREATE TABLE
blocks for cms_pages / widgets / conversations / messages / leads /
escalations, and the parallel ``page_status`` / ``message_role`` /
``conversation_status`` / ``escalation_status`` enums) has been removed.
The git history retains the original file under its previous name; this
docstring is the audit trail for reviewers asking "where did the rest
of 0004_remaining_tables go?".

RLS pattern (same as 0003b / 0004): text comparison against
``current_setting('app.tenant_id', true)`` so an unscoped session sees
zero rows (returns ``''``) rather than raising a cast error.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004b_post_cms_extras"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_RLS_POLICY = """
    CREATE POLICY {table}_tenant_isolation ON {table}
      USING (tenant_id::text = current_setting('app.tenant_id', true))
      WITH CHECK (tenant_id::text = current_setting('app.tenant_id', true))
"""


def _enable_rls(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(_RLS_POLICY.format(table=table))


def _disable_rls(table: str) -> None:
    op.execute(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {table}")
    op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")


def upgrade() -> None:
    # 1. guardrail_configs ------------------------------------------------------
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
    _enable_rls("guardrail_configs")

    # 2. Deferred FK: cms_chunks.page_id → cms_pages.id -----------------------
    # 0004_cms_pages defers this so a database with orphan chunks (from any
    # pre-CMS seeding) can migrate cleanly. Fresh DBs have empty cms_chunks
    # at this point so the FK validates trivially.
    op.create_foreign_key(
        "fk_cms_chunks_page_id",
        "cms_chunks",
        "cms_pages",
        ["page_id"],
        ["id"],
        ondelete="CASCADE",
    )

    # 3. Retroactive RLS on audit_logs (nullable tenant_id) -------------------
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

    # 4. Retroactive RLS on cost_events (NOT NULL tenant_id) ------------------
    _enable_rls("cost_events")


def downgrade() -> None:
    # Reverse retroactive RLS first.
    op.execute("DROP POLICY IF EXISTS cost_events_tenant_isolation ON cost_events")
    op.execute("ALTER TABLE cost_events DISABLE ROW LEVEL SECURITY")

    op.execute("DROP POLICY IF EXISTS audit_logs_tenant_isolation ON audit_logs")
    op.execute("ALTER TABLE audit_logs DISABLE ROW LEVEL SECURITY")

    # Drop the deferred FK before dropping the table it depends on.
    op.drop_constraint("fk_cms_chunks_page_id", "cms_chunks", type_="foreignkey")

    _disable_rls("guardrail_configs")
    op.drop_constraint("uq_guardrail_configs_tenant", "guardrail_configs", type_="unique")
    op.drop_index("ix_guardrail_configs_tenant_id", table_name="guardrail_configs")
    op.drop_table("guardrail_configs")
