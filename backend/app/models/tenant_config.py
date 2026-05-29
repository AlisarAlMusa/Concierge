"""TenantConfig — optional per-tenant branding and contact configuration.

One-to-one with tenants. Row may be absent; callers must apply fallback
defaults when the row is missing. Public fields (brand_name, theme_color,
greeting, public_description, contact_email) are safe to render on the
public site. allowed_origins is internal and MUST NOT be exposed publicly.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class TenantConfig(Base):
    __tablename__ = "tenant_configs"

    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"),
        primary_key=True,
        nullable=False,
    )
    brand_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    theme_color: Mapped[str | None] = mapped_column(String(7), nullable=True)
    greeting: Mapped[str | None] = mapped_column(Text, nullable=True)
    public_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    contact_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Internal — never rendered on the public site.
    allowed_origins: Mapped[list[str] | None] = mapped_column(
        ARRAY(String), nullable=True, server_default="{}"
    )
    # Agent / guardrail config — editable by tenant_admin via /tenant/config.
    persona: Mapped[str | None] = mapped_column(Text, nullable=True)
    refusal_tone: Mapped[str | None] = mapped_column(String(20), nullable=True)
    enabled_tools: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    allowed_topics: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    blocked_topics: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
