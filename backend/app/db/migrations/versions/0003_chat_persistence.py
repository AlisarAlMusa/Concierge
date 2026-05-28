"""chat persistence — widgets, conversations, messages, leads, escalations

Revision ID: 0003
Revises: 0002
Create Date: 2026-05-28

Person B (PR 3 — widget + chat persistence). Creates the durable storage
behind Spec 009 FR-009, Spec 011, and Spec 012.

Enum-creation pattern (important):

* Enums are declared with ``create_type=False`` so column references during
  ``op.create_table`` do **not** implicitly emit ``CREATE TYPE``.
* Each enum is created explicitly via ``.create(op.get_bind(), checkfirst=True)``
  before the table that uses it. ``checkfirst=True`` makes the migration safe
  to re-run against a partially-migrated database.

Every new table:

* Carries ``tenant_id`` with an FK + cascade to ``tenants.id`` (right-to-erasure
  works without any extra plumbing).
* Has RLS enabled with a ``USING + WITH CHECK`` policy against
  ``current_setting('app.tenant_id', true)``. RLS is the *second* wall — the
  service layer always emits explicit ``WHERE tenant_id = …`` filters.
* Sets ``permissive`` exemption for unset session vars off: the policy compares
  the column to ``current_setting(..., true)`` which returns ``''`` when unset,
  so an unscoped session sees zero rows by design.

Token issuance hits ``widgets`` *before* the request has an authenticated
tenant, so that single table's policy is intentionally relaxed (allow when
``app.tenant_id`` is unset) — see ``services/widget_service.py`` for the
caller-side discipline this assumes.
"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Enums declared with create_type=False so column references don't try to
# emit CREATE TYPE during create_table. We do that explicitly below.
conversation_status_enum = postgresql.ENUM(
    "active",
    "escalated",
    "closed",
    name="conversation_status",
    create_type=False,
)
message_role_enum = postgresql.ENUM(
    "visitor",
    "assistant",
    name="message_role",
    create_type=False,
)
escalation_status_enum = postgresql.ENUM(
    "open",
    "in_progress",
    "resolved",
    "dismissed",
    name="escalation_status",
    create_type=False,
)


def upgrade() -> None:
    bind = op.get_bind()
    # Idempotent enum creation — safe on fresh and partially-migrated DBs.
    conversation_status_enum.create(bind, checkfirst=True)
    message_role_enum.create(bind, checkfirst=True)
    escalation_status_enum.create(bind, checkfirst=True)

    # ----- widgets ----------------------------------------------------------
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
        sa.Column("public_widget_id", sa.String(64), nullable=False),
        sa.Column("name", sa.String(255), nullable=False, server_default="default"),
        sa.Column(
            "allowed_origins",
            postgresql.ARRAY(sa.String()),
            nullable=False,
            server_default=sa.text("'{}'::varchar[]"),
        ),
        sa.Column("theme", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("greeting", sa.Text(), nullable=False, server_default=""),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
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
        sa.UniqueConstraint("public_widget_id", name="uq_widgets_public_widget_id"),
    )
    op.create_index("ix_widgets_tenant_id", "widgets", ["tenant_id"])
    op.create_index("ix_widgets_public_widget_id", "widgets", ["public_widget_id"])

    op.execute("ALTER TABLE widgets ENABLE ROW LEVEL SECURITY")
    # Token-mint reads happen BEFORE the request has an authenticated tenant
    # context, so this policy intentionally permits reads when app.tenant_id
    # is unset. WidgetService is the only caller and constrains its lookups
    # to public_widget_id, which is a non-secret identifier.
    op.execute("""
        CREATE POLICY widgets_tenant_isolation ON widgets
          USING (
            tenant_id::text = current_setting('app.tenant_id', true)
            OR current_setting('app.tenant_id', true) = ''
          )
          WITH CHECK (tenant_id::text = current_setting('app.tenant_id', true))
        """)

    # ----- conversations ----------------------------------------------------
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
            sa.ForeignKey("widgets.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("visitor_session_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "status",
            conversation_status_enum,
            nullable=False,
            server_default="active",
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
    )
    op.create_index("ix_conversations_tenant_id", "conversations", ["tenant_id"])
    op.create_index("ix_conversations_visitor_session_id", "conversations", ["visitor_session_id"])
    op.execute("ALTER TABLE conversations ENABLE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY conversations_tenant_isolation ON conversations
          USING (tenant_id::text = current_setting('app.tenant_id', true))
          WITH CHECK (tenant_id::text = current_setting('app.tenant_id', true))
        """)

    # ----- messages ---------------------------------------------------------
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
        sa.Column("role", message_role_enum, nullable=False),
        sa.Column("content_redacted", sa.Text(), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_messages_tenant_id", "messages", ["tenant_id"])
    op.create_index("ix_messages_conversation_id", "messages", ["conversation_id"])
    op.create_index(
        "ix_messages_conversation_created", "messages", ["conversation_id", "created_at"]
    )
    op.execute("ALTER TABLE messages ENABLE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY messages_tenant_isolation ON messages
          USING (tenant_id::text = current_setting('app.tenant_id', true))
          WITH CHECK (tenant_id::text = current_setting('app.tenant_id', true))
        """)

    # ----- leads ------------------------------------------------------------
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
            sa.ForeignKey("conversations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("visitor_session_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("name", sa.String(255), nullable=True),
        sa.Column("email", sa.String(255), nullable=True),
        sa.Column("phone", sa.String(64), nullable=True),
        sa.Column("intent", sa.Text(), nullable=False),
        sa.Column("context", sa.Text(), nullable=True),
        sa.Column("lead_score", sa.Float(), nullable=True),
        sa.Column("source", sa.String(32), nullable=False, server_default="agent"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_leads_tenant_id", "leads", ["tenant_id"])
    op.create_index("ix_leads_conversation_id", "leads", ["conversation_id"])
    op.create_index("ix_leads_visitor_session_id", "leads", ["visitor_session_id"])
    op.create_index("ix_leads_created_at", "leads", ["created_at"])
    op.execute("ALTER TABLE leads ENABLE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY leads_tenant_isolation ON leads
          USING (tenant_id::text = current_setting('app.tenant_id', true))
          WITH CHECK (tenant_id::text = current_setting('app.tenant_id', true))
        """)

    # ----- escalations ------------------------------------------------------
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
            sa.ForeignKey("conversations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("context", sa.Text(), nullable=True),
        sa.Column(
            "status",
            escalation_status_enum,
            nullable=False,
            server_default="open",
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
        sa.UniqueConstraint("conversation_id", name="uq_escalations_conversation"),
    )
    op.create_index("ix_escalations_tenant_id", "escalations", ["tenant_id"])
    op.execute("ALTER TABLE escalations ENABLE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY escalations_tenant_isolation ON escalations
          USING (tenant_id::text = current_setting('app.tenant_id', true))
          WITH CHECK (tenant_id::text = current_setting('app.tenant_id', true))
        """)


def downgrade() -> None:
    # Drop in reverse FK order.
    for table in ("escalations", "leads", "messages", "conversations", "widgets"):
        op.execute(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {table}")
        op.execute(f"ALTER TABLE IF EXISTS {table} DISABLE ROW LEVEL SECURITY")
        op.drop_table(table)

    bind = op.get_bind()
    escalation_status_enum.drop(bind, checkfirst=True)
    message_role_enum.drop(bind, checkfirst=True)
    conversation_status_enum.drop(bind, checkfirst=True)
