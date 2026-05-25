from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class UUIDModel(BaseModel):
    id: UUID


class TimestampedModel(UUIDModel):
    created_at: datetime


class PaginationParams(BaseModel):
    offset: int = 0
    limit: int = 20


class StandardErrorResponse(BaseModel):
    detail: str
    code: str
