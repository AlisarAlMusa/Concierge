"""Pydantic schemas for the Auth & Roles feature.

UserRead  — public user representation returned by register, /me, invite-admin.
UserCreate — self-registration input (email + password only; role always member).
UserUpdate — profile update (no role escalation allowed).
InviteAdminRequest — body for POST /platform/tenants/{id}/invite-admin.
"""

from uuid import UUID

from fastapi_users import schemas
from pydantic import BaseModel, EmailStr

from app.models.user import UserRole


class UserRead(schemas.BaseUser[UUID]):
    """Public user representation.

    Includes role and tenant_id but NO sensitive fields (hashed_password, etc.).
    JWT payload contract: sub, role, jti, exp — no email or tenant_id in the token.
    """

    role: UserRole
    tenant_id: UUID | None = None

    model_config = {"from_attributes": True}


class UserCreate(schemas.BaseUserCreate):
    """Self-registration schema.

    Intentionally has NO role or tenant_id fields — role is always forced to
    `member` by UserManager.create(), and tenant_id is always NULL on
    self-registration.

    Any extra fields sent by the client (e.g. role=tenant_admin) are silently
    ignored by Pydantic, preventing role escalation via the request body.
    """

    # Only email + password (inherited from BaseUserCreate).
    # is_active, is_superuser, is_verified have safe defaults.


class UserUpdate(schemas.BaseUserUpdate):
    """Profile update schema.

    Does NOT allow updating role or tenant_id through the standard users router.
    Elevated roles are granted exclusively via the invite-admin endpoint.
    """


class InviteAdminRequest(BaseModel):
    """Request body for POST /platform/tenants/{tenant_id}/invite-admin."""

    email: EmailStr


class InviteAdminResponse(UserRead):
    """Response body for POST /platform/tenants/{tenant_id}/invite-admin.

    Extends ``UserRead`` (every existing field is still returned at the root,
    so older clients and tests asserting ``body["role"]`` / ``body["tenant_id"]``
    continue to work). Adds the one-time ``temporary_password`` so the platform
    manager can hand it to the new admin out of band — Week-8 has no email
    flow, so this is the only place the plaintext password ever surfaces.
    The value is never logged or audited.
    """

    temporary_password: str | None = None
