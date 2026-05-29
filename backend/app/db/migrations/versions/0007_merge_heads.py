"""Merge 0004_remaining and 0006_tenant_config into a single head.

Revision ID: 0007_merge
Revises: 0004_remaining, 0006_tenant_config
Create Date: 2026-05-29
"""

from typing import Sequence, Union

revision: str = "0007_merge"
down_revision: Union[str, Sequence[str], None] = ("0004_remaining", "0006_tenant_config")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
