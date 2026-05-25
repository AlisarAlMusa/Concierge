from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, field_validator

from app.models.tenant import TenantStatus


class TenantCreate(BaseModel):
    name: str
    slug: str

    @field_validator("slug")
    @classmethod
    def slug_format(cls, v: str) -> str:
        if not v.replace("-", "").replace("_", "").isalnum():
            raise ValueError("slug must be alphanumeric with hyphens/underscores only")
        return v.lower()


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
