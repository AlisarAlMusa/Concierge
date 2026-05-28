"""leads admin fields — status enum, notes, updated_at

Revision ID: 0005_leads_admin
Revises: 0003
Create Date: 2026-05-28

Person B — Spec 012 FR-006: the admin ``PATCH /leads/{lead_id}`` route needs
to update ``status`` and ``notes``. The original ``0003_chat_persistence``
migration that creates the ``leads`` table predates that contract; this
migration brings the row schema in line with the spec without rewriting
any earlier file.

Why ``down_revision = "0003"`` (and not ``"0004"``):

* The ``leads`` table is created by ``0003_chat_persistence`` and this
  migration only touches that one table. Following the ``0003 → 0005``
  chain keeps the change reviewable in isolation.
* The repo currently has two parallel ``0004_*`` heads (cms_pages and
  remaining_tables) authored by different owners. Reconciling those heads
  is **not** in this PR's scope — they will be merged with a follow-up
  alembic merge revision. Pointing this migration at ``"0003"`` makes the
  merge straightforward (both new ``0005`` and the two ``0004`` heads
  share ancestor ``0003``).

Idempotent: the ``lead_status`` enum is created with ``checkfirst=True`` so
re-running against a partially-migrated database does not crash.
"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005_leads_admin"
down_revision: Union[str, None] = "0003"
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
