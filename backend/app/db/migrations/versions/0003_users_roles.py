"""Add created_at to users, CHECK constraint, and role index.

Also updates the users table to have a `created_at` timestamp column,
a CHECK constraint enforcing the tenant_manager ↔ tenant_id nullability rule,
and an index on the `role` column.

Revision ID: 0003
Revises: 0002
Create Date: 2026-05-27

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003_users"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add created_at column to users
    op.add_column(
        "users",
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )

    # Add role index (tenant_id index already exists from 0001)
    op.create_index("ix_users_role", "users", ["role"])

    # Add CHECK constraint enforcing:
    #   tenant_manager → tenant_id IS NULL
    #   tenant_admin / member → tenant_id IS NOT NULL
    op.create_check_constraint(
        "ck_users_role_tenant_id",
        "users",
        "(role = 'tenant_manager' AND tenant_id IS NULL) OR "
        "(role != 'tenant_manager' AND tenant_id IS NOT NULL)",
    )


def downgrade() -> None:
    op.drop_constraint("ck_users_role_tenant_id", "users", type_="check")
    op.drop_index("ix_users_role", table_name="users")
    op.drop_column("users", "created_at")
