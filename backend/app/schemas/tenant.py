import re
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, field_validator

from app.models.tenant import TenantStatus

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]*[a-z0-9]$")


class TenantCreate(BaseModel):
    name: str
    slug: str

    @field_validator("slug")
    @classmethod
    def slug_format(cls, v: str) -> str:
        v = v.lower()
        if not _SLUG_RE.match(v):
            raise ValueError(
                "slug must be lowercase alphanumeric with hyphens only, "
                "start and end with alphanumeric, minimum 2 characters"
            )
        return v


class TenantRead(BaseModel):
    id: UUID
    name: str
    slug: str
    status: TenantStatus
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class TenantUpdate(BaseModel):
    name: str | None = None
    status: TenantStatus | None = None


class TenantUsageSummary(BaseModel):
    tenant_id: UUID
    total_input_tokens: int
    total_output_tokens: int
    total_cost_usd: Decimal
