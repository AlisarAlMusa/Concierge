import enum
from uuid import UUID

from fastapi_users.db import SQLAlchemyBaseUserTableUUID
from sqlalchemy import Enum, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class UserRole(str, enum.Enum):
    tenant_manager = "tenant_manager"
    tenant_admin = "tenant_admin"
    member = "member"


class User(SQLAlchemyBaseUserTableUUID, Base):
    __tablename__ = "users"

    role: Mapped[UserRole] = mapped_column(
        Enum(UserRole, name="user_role"),
        nullable=False,
        default=UserRole.member,
    )
    # Nullable for tenant_manager (platform role with no tenant affiliation).
    tenant_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
