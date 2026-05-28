"""Widget — one embeddable chat surface per tenant (Spec 011).

Stores the public ``widget_id`` the loader uses to identify itself, the
``allowed_origins`` list the session endpoint validates against
server-side, and the theme/greeting config the widget reads at load time.

``public_widget_id`` is what gets pasted into the host site's ``<script>``
tag; the row's primary key ``id`` is the internal handle the JWT carries.
Two columns so a tenant can rotate the public id without changing every
embed snippet on the planet.

Owner: Person B.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Widget(Base):
    __tablename__ = "widgets"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Public, copy-paste-safe identifier. Indexed because every session token
    # mint starts with a lookup by this column.
    public_widget_id: Mapped[str] = mapped_column(
        String(64), nullable=False, unique=True, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False, default="default")
    # Per-tenant origin allowlist (Spec 011 FR-003 / FR-004). The widget
    # session endpoint validates ``origin`` against this server-side; CORS +
    # CSP frame-ancestors are layered on top for defense-in-depth.
    allowed_origins: Mapped[list[str]] = mapped_column(
        ARRAY(String), nullable=False, server_default="{}"
    )
    # Theme + greeting are read by the widget bundle on load. ``theme`` is
    # opaque JSON so Owner A's admin UI can evolve the schema without a
    # backend migration.
    theme: Mapped[dict] = mapped_column(JSON, nullable=False, server_default="{}")
    greeting: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
