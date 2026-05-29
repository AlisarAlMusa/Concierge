"""tenant_configs — optional per-tenant branding and contact config

Revision ID: 0006_tenant_config
Revises: 0005_leads_admin
Create Date: 2026-05-29

Adds tenant_configs table (one-to-one with tenants, row is optional).
Enables RLS so authenticated routes can scope reads to current tenant.
Public site reads bypass RLS via explicit WHERE tenant_id filter.
"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006_tenant_config"
down_revision: Union[str, None] = "0005_leads_admin"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "tenant_configs",
        sa.Column(
            "tenant_id",
            sa.UUID(),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            primary_key=True,
            nullable=False,
        ),
        sa.Column("brand_name", sa.String(255), nullable=True),
        sa.Column("theme_color", sa.String(7), nullable=True),
        sa.Column("greeting", sa.Text(), nullable=True),
        sa.Column("public_description", sa.Text(), nullable=True),
        sa.Column("contact_email", sa.String(255), nullable=True),
        sa.Column(
            "allowed_origins",
            postgresql.ARRAY(sa.String()),
            nullable=True,
            server_default="{}",
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

    op.execute("ALTER TABLE tenant_configs ENABLE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY tenant_isolation ON tenant_configs
        USING (tenant_id = current_setting('app.tenant_id', true)::uuid)
        """)


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON tenant_configs")
    op.drop_table("tenant_configs")
