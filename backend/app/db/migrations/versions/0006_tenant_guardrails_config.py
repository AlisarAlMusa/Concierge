"""tenant guardrails config — JSONB column on tenants

Revision ID: 0006_tenant_guardrails
Revises: 0005_leads_admin
Create Date: 2026-05-29

Spec 010 FR-022: adds `tenants.guardrails_config JSONB NOT NULL DEFAULT '{}'`.
Stores `persona`, `refusal_tone`, `blocked_topics` per FR-023. RLS policies
on `tenants` are unchanged — the column is per-tenant and accessed only via
the existing tenant context.

The `server_default='{}'::jsonb` backfills every existing row atomically
during the ALTER TABLE; no data-migration step is required.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0006_tenant_guardrails"
down_revision = "0005_leads_admin"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tenants",
        sa.Column(
            "guardrails_config",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("tenants", "guardrails_config")
