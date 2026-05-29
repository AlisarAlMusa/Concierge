"""leads admin fields — status enum, notes, updated_at

Revision ID: 0005_leads_admin
Revises: 0004b_post_cms_extras
Create Date: 2026-05-28

Person B — Spec 012 FR-006: the admin ``PATCH /leads/{lead_id}`` route needs
to update ``status`` and ``notes``. The ``leads`` table is created by
``0003b_chat_persistence`` and predates this admin contract; this
migration brings the row schema in line with the spec without rewriting
any earlier file.

Parent updated as part of the migration-graph repair:

* Original ``down_revision = "0003"`` chained off the ancestor of the
  two parallel broken ``0004_*`` heads — that workaround is no longer
  needed now that the graph is linear.
* New ``down_revision = "0004b_post_cms_extras"`` puts this migration
  at the tip of the single linear chain
  ``0001 → 0002 → 0003 → 0003b_chat_persistence → 0004 → 0004b_post_cms_extras → 0005_leads_admin``.

Idempotent: the ``lead_status`` enum is created with ``checkfirst=True`` so
re-running against a partially-migrated database does not crash.
"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005_leads_admin"
down_revision: Union[str, None] = "0004b_post_cms_extras"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Enum declared with create_type=False so column references during the
# ALTER TABLE do not implicitly emit CREATE TYPE. The .create() call below
# does it explicitly and idempotently.
lead_status_enum = postgresql.ENUM(
    "new",
    "contacted",
    "converted",
    "rejected",
    name="lead_status",
    create_type=False,
)


def upgrade() -> None:
    bind = op.get_bind()
    lead_status_enum.create(bind, checkfirst=True)

    op.add_column(
        "leads",
        sa.Column(
            "status",
            lead_status_enum,
            nullable=False,
            server_default="new",
        ),
    )
    op.add_column(
        "leads",
        sa.Column("notes", sa.Text(), nullable=True),
    )
    op.add_column(
        "leads",
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("leads", "updated_at")
    op.drop_column("leads", "notes")
    op.drop_column("leads", "status")
    bind = op.get_bind()
    lead_status_enum.drop(bind, checkfirst=True)
