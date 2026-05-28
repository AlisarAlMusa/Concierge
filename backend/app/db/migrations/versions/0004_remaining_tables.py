"""Remaining tables + retroactive RLS on audit_logs and cost_events

Revision ID: 0004
Revises: 0003
Create Date: 2026-05-28

Creates the 7 tables owned by Person B / Person C (cms_pages, widgets,
conversations, messages, leads, escalations, guardrail_configs), wires the
deferred FK from cms_chunks.page_id → cms_pages.id, and adds Row-Level
Security retroactively to audit_logs and cost_events (omitted in 0001).

RLS pattern (all tables with NOT NULL tenant_id):
  USING (tenant_id::text = current_setting('app.tenant_id', true))
  WITH CHECK (tenant_id::text = current_setting('app.tenant_id', true))

Text comparison (not ::uuid cast) is intentional: when app.tenant_id is
unset current_setting returns '' rather than raising a cast error, so
zero rows are returned instead of a Postgres exception (spec SC-005).

audit_logs uses an additional IS NOT NULL guard because its tenant_id is
nullable — platform-level events (NULL tenant_id) must be invisible to any
tenant context and must not leak cross-tenant.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# upgrade
# ---------------------------------------------------------------------------


def upgrade() -> None:
    # 1. ENUMs ------------------------------------------------------------------
    page_status = postgresql.ENUM(
        "draft",
        "published",
        "archived",
        name="page_status",
        create_type=True,
    )
    conversation_status = postgresql.ENUM(
        "active",
        "closed",
        "escalated",
        name="conversation_status",
        create_type=True,
    )
    message_role = postgresql.ENUM(
        "visitor",
        "assistant",
        "tool",
        "system",
        name="message_role",
        create_type=True,
    )
    escalation_status = postgresql.ENUM(
        "pending",
        "handled",
        name="escalation_status",
        create_type=True,
    )
    for enum in (page_status, conversation_status, message_role, escalation_status):
        enum.create(op.get_bind(), checkfirst=True)

    # 2. cms_pages --------------------------------------------------------------
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
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("slug", sa.String(200), nullable=False),
        sa.Column("body", sa.Text, nullable=False),
        sa.Column(
            "status",
            sa.Enum("draft", "published", "archived", name="page_status", create_type=False),
            nullable=False,
            server_default="draft",
        ),
        sa.Column(
            "created_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_cms_pages_tenant_id", "cms_pages", ["tenant_id"])
    _enable_rls("cms_pages")

    # 3. Deferred FK: cms_chunks.page_id → cms_pages.id ----------------------
    op.create_foreign_key(
        "fk_cms_chunks_page_id",
        "cms_chunks",
        "cms_pages",
        ["page_id"],
        ["id"],
        ondelete="CASCADE",
    )

    # 4. widgets ----------------------------------------------------------------
    op.create_table(
        "widgets",
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
        sa.Column("public_widget_id", sa.String(100), nullable=False, unique=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("theme_json", postgresql.JSONB, nullable=True),
        sa.Column("greeting", sa.Text, nullable=True),
        sa.Column(
            "allowed_origins",
            postgresql.ARRAY(sa.Text),
            nullable=False,
            server_default="{}",
        ),
        sa.Column("enabled_tools", postgresql.JSONB, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_widgets_tenant_id", "widgets", ["tenant_id"])
    _enable_rls("widgets")

    # 5. conversations ----------------------------------------------------------
    op.create_table(
        "conversations",
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
        sa.Column(
            "widget_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("widgets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "visitor_session_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum(
                "active",
                "closed",
                "escalated",
                name="conversation_status",
                create_type=False,
            ),
            nullable=False,
            server_default="active",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_conversations_tenant_id", "conversations", ["tenant_id"])
    op.create_index(
        "ix_conversations_tenant_widget",
        "conversations",
        ["tenant_id", "widget_id"],
    )
    op.create_index(
        "ix_conversations_tenant_session",
        "conversations",
        ["tenant_id", "visitor_session_id"],
    )
    _enable_rls("conversations")

    # 6. messages ---------------------------------------------------------------
    op.create_table(
        "messages",
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
        sa.Column(
            "conversation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("conversations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "role",
            sa.Enum(
                "visitor",
                "assistant",
                "tool",
                "system",
                name="message_role",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column("content_redacted", sa.Text, nullable=False),
        sa.Column("metadata", postgresql.JSONB, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_messages_tenant_id", "messages", ["tenant_id"])
    op.create_index(
        "ix_messages_tenant_conversation",
        "messages",
        ["tenant_id", "conversation_id"],
    )
    _enable_rls("messages")

    # 7. leads ------------------------------------------------------------------
    op.create_table(
        "leads",
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
        sa.Column(
            "conversation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("conversations.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("name", sa.String(255), nullable=True),
        sa.Column("email", sa.String(320), nullable=True),
        sa.Column("phone", sa.String(50), nullable=True),
        sa.Column("intent", sa.String(255), nullable=False),
        sa.Column("lead_score", sa.Numeric(5, 4), nullable=True),
        sa.Column("source", sa.String(100), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_leads_tenant_id", "leads", ["tenant_id"])
    op.create_index(
        "ix_leads_tenant_conversation",
        "leads",
        ["tenant_id", "conversation_id"],
    )
    _enable_rls("leads")

    # 8. escalations ------------------------------------------------------------
    op.create_table(
        "escalations",
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
        sa.Column(
            "conversation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("conversations.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("reason", sa.Text, nullable=False),
        sa.Column(
            "status",
            sa.Enum("pending", "handled", name="escalation_status", create_type=False),
            nullable=False,
            server_default="pending",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_escalations_tenant_id", "escalations", ["tenant_id"])
    _enable_rls("escalations")

    # 9. guardrail_configs ------------------------------------------------------
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

    # 10. Retroactive RLS on audit_logs (nullable tenant_id) -------------------
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

    # 11. Retroactive RLS on cost_events (NOT NULL tenant_id) ------------------
    _enable_rls("cost_events")


# ---------------------------------------------------------------------------
# downgrade
# ---------------------------------------------------------------------------


def downgrade() -> None:
    # Reverse retroactive RLS
    op.execute("DROP POLICY IF EXISTS cost_events_tenant_isolation ON cost_events")
    op.execute("ALTER TABLE cost_events DISABLE ROW LEVEL SECURITY")

    op.execute("DROP POLICY IF EXISTS audit_logs_tenant_isolation ON audit_logs")
    op.execute("ALTER TABLE audit_logs DISABLE ROW LEVEL SECURITY")

    # Drop new tables in reverse creation order
    _disable_rls("guardrail_configs")
    op.drop_table("guardrail_configs")

    _disable_rls("escalations")
    op.drop_table("escalations")

    _disable_rls("leads")
    op.drop_table("leads")

    _disable_rls("messages")
    op.drop_table("messages")

    _disable_rls("conversations")
    op.drop_table("conversations")

    _disable_rls("widgets")
    op.drop_table("widgets")

    # Remove deferred FK before dropping cms_pages
    op.drop_constraint("fk_cms_chunks_page_id", "cms_chunks", type_="foreignkey")

    _disable_rls("cms_pages")
    op.drop_table("cms_pages")

    # Drop ENUMs
    for name in (
        "escalation_status",
        "message_role",
        "conversation_status",
        "page_status",
    ):
        postgresql.ENUM(name=name).drop(op.get_bind(), checkfirst=True)
