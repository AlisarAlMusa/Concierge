"""tenant_configs — add agent/guardrail config columns

Revision ID: 0008_agent_config
Revises: 0007_merge_heads
Create Date: 2026-05-29

Adds persona, refusal_tone, enabled_tools, allowed_topics, blocked_topics
columns to tenant_configs so tenant admins can configure their AI agent
via PATCH /tenant/config without needing a separate guardrail_configs table.
"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008_agent_config"
down_revision: Union[str, None] = "0007_merge"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("tenant_configs", sa.Column("persona", sa.Text(), nullable=True))
    op.add_column("tenant_configs", sa.Column("refusal_tone", sa.String(20), nullable=True))
    op.add_column(
        "tenant_configs",
        sa.Column("enabled_tools", postgresql.JSONB(), nullable=True),
    )
    op.add_column(
        "tenant_configs",
        sa.Column("allowed_topics", postgresql.JSONB(), nullable=True),
    )
    op.add_column(
        "tenant_configs",
        sa.Column("blocked_topics", postgresql.JSONB(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("tenant_configs", "blocked_topics")
    op.drop_column("tenant_configs", "allowed_topics")
    op.drop_column("tenant_configs", "enabled_tools")
    op.drop_column("tenant_configs", "refusal_tone")
    op.drop_column("tenant_configs", "persona")
