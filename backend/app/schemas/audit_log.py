from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class AuditLogRead(BaseModel):
    id: UUID
    actor_user_id: UUID | None
    actor_role: str
    tenant_id: UUID | None
    action: str
    target_type: str | None
    target_id: str | None
    metadata_: dict | None
    created_at: datetime

    model_config = {"from_attributes": True}
