"""Merge migration heads: 0008_agent_config + 0006_tenant_guardrails

Revision ID: 0009_merge_heads
Revises: 0008_agent_config, 0006_tenant_guardrails
Create Date: 2026-05-29

Merges the agent_config columns branch (022-public-tenant-site) with the
guardrails_config branch (feature/guardrails-sidecar) into a single head.
"""

from collections.abc import Sequence
from typing import Union

revision: str = "0009_merge_heads"
down_revision: Union[str, Sequence[str], None] = ("0008_agent_config", "0006_tenant_guardrails")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
