"""Core security module: fastapi-users wiring, JTI revocation, role-aware JWT.

Key design points
─────────────────
• BearerTransport — no cookies; works cleanly with Streamlit + service calls.
• ConciergeJWTStrategy — extends JWTStrategy to:
    1. Add `jti` (UUID) and `role` to every issued token.
    2. Check the Redis JTI blacklist in read_token() (revoked → 401).
    3. Write the JTI to the Redis blacklist in destroy_token() (logout).
• get_jwt_strategy — per-request FastAPI dependency; reads the signing secret
  lazily from app.state.secrets so it is never imported at module load time
  (Vault must be fetched first).
• UserManager — overrides create() to force role=member on self-registration,
  and exposes hooks for audit logging.
• The FastAPIUsers instance is created once at module level and re-used
  in router.py and dependencies.py.

MUST NOT CONTAIN: os.getenv() calls, static secret literals, blocking I/O.
"""

from __future__ import annotations

import hmac
import uuid
from datetime import datetime, timezone
from typing import Any

import redis.asyncio as aioredis
import structlog
from fastapi import Depends, Header, HTTPException, Request
from fastapi_users import BaseUserManager, FastAPIUsers, UUIDIDMixin
from fastapi_users.authentication import (
    AuthenticationBackend,
    BearerTransport,
    JWTStrategy,
)
from fastapi_users.db import SQLAlchemyUserDatabase
from fastapi_users.jwt import decode_jwt, generate_jwt
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.session import get_db_session
from app.models.user import User, UserRole

log = structlog.get_logger(__name__)

# ──────────────────────────────────────────────────────────────────────────────
# Constants
# ──────────────────────────────────────────────────────────────────────────────

JWT_LIFETIME_SECONDS: int = 3600  # 1 hour
JWT_AUDIENCE: list[str] = ["fastapi-users:auth"]
JWT_ALGORITHM: str = "HS256"

# ──────────────────────────────────────────────────────────────────────────────
# UserDatabase dependency
# ──────────────────────────────────────────────────────────────────────────────


async def get_user_db(session: AsyncSession = Depends(get_db_session)):
    """Yield an SQLAlchemyUserDatabase adapter scoped to this request's session."""
    yield SQLAlchemyUserDatabase(session, User)


# ──────────────────────────────────────────────────────────────────────────────
# UserManager
# ──────────────────────────────────────────────────────────────────────────────


class UserManager(UUIDIDMixin, BaseUserManager[User, uuid.UUID]):
    """Custom UserManager for Concierge.

    • Overrides create() to always force role=member and tenant_id=None
      for self-registration (prevents role escalation via request body).
    • Provides on_after_register and on_after_login hooks for audit logging.
    • reset_password_token_secret and verification_token_secret are set from
      the JWT secret at instantiation time (not used in Week 8, but required
      by BaseUserManager).
    """

    def __init__(self, user_db: SQLAlchemyUserDatabase, jwt_secret: str) -> None:
        super().__init__(user_db)
        # Required class attributes — use JWT secret as a pragmatic placeholder;
        # password-reset and email-verification are not implemented in Week 8.
        self.reset_password_token_secret = jwt_secret
        self.verification_token_secret = jwt_secret

    async def create(self, user_create: Any, safe: bool = False, request: Any = None) -> User:
        """Create a user, always forcing role=member for self-registration.

        The invite-admin flow bypasses this method and calls
        auth_service.invite_admin() directly, which sets role=tenant_admin.
        """
        # Delegate password hashing + duplicate-email checks to the base class.
        user = await super().create(user_create, safe=safe, request=request)

        # Belt-and-suspenders: overwrite role/tenant_id even if something slipped
        # through the schema layer.  Do a direct DB update so the returned object
        # reflects the enforced values.
        if user.role != UserRole.member or user.tenant_id is not None:
            await self.user_db.update(user, {"role": UserRole.member, "tenant_id": None})
            # Refresh the object so callers see the enforced values.
            await self.user_db.session.refresh(user)

        return user

    async def on_after_register(self, user: User, request: Any = None) -> None:
        """Audit log for new user registration (fire-and-forget)."""
        # Import here to avoid circular imports at module level.
        # write_audit_event is a sync function that schedules an asyncio task.
        from app.services.auth_service import write_audit_event

        write_audit_event(
            action="register",
            actor_user_id=user.id,
            actor_role=user.role.value,
            tenant_id=user.tenant_id,
            metadata_={"email": user.email},
        )
        log.info("user.registered", user_id=str(user.id))

    async def on_after_login(self, user: User, request: Any = None, response: Any = None) -> None:
        """Audit log for successful login (fire-and-forget)."""
        from app.services.auth_service import write_audit_event

        ip = request.client.host if request and request.client else "unknown"
        write_audit_event(
            action="login",
            actor_user_id=user.id,
            actor_role=user.role.value,
            tenant_id=user.tenant_id,
            metadata_={"ip": ip},
        )
        log.info("user.login", user_id=str(user.id))


async def get_user_manager(
    user_db: SQLAlchemyUserDatabase = Depends(get_user_db),
    request: Request = None,
) -> UserManager:
    """Dependency: yield a UserManager with the Vault-sourced JWT secret."""
    jwt_secret = request.app.state.secrets["jwt_secret"]
    yield UserManager(user_db, jwt_secret)


# ──────────────────────────────────────────────────────────────────────────────
# Custom JWTStrategy with JTI revocation
# ──────────────────────────────────────────────────────────────────────────────


class ConciergeJWTStrategy(JWTStrategy[User, uuid.UUID]):
    """JWTStrategy extended with:

    • JTI (JWT ID) added to every token for revocation tracking.
    • `role` added to every token to avoid a DB round-trip in role-check deps.
    • read_token() checks the Redis JTI blacklist before returning a user.
    • destroy_token() writes the JTI to the Redis blacklist with a TTL equal
      to the token's remaining lifetime.
    """

    def __init__(
        self,
        secret: str,
        redis: aioredis.Redis,
        lifetime_seconds: int = JWT_LIFETIME_SECONDS,
    ) -> None:
        super().__init__(
            secret=secret,
            lifetime_seconds=lifetime_seconds,
            token_audience=JWT_AUDIENCE,
            algorithm=JWT_ALGORITHM,
        )
        self.redis = redis

    async def write_token(self, user: User) -> str:  # type: ignore[override]
        """Issue a JWT with sub, role, jti, aud, exp.

        MUST NOT include email, tenant_id, or any other PII.
        """
        jti = str(uuid.uuid4())
        data: dict[str, Any] = {
            "sub": str(user.id),
            "aud": JWT_AUDIENCE,
            "role": user.role.value,
            "jti": jti,
        }
        return generate_jwt(data, self.encode_key, self.lifetime_seconds, algorithm=JWT_ALGORITHM)

    async def read_token(self, token: str | None, user_manager: UserManager) -> User | None:  # type: ignore[override]
        """Decode and validate a token, rejecting revoked JTIs.

        Returns None (→ 401) for any invalid, expired, or revoked token.
        Raises HTTPException(401, code=token_revoked) for blacklisted JTIs so
        the error shape matches the platform contract exactly.
        """
        if token is None:
            return None

        try:
            data = decode_jwt(token, self.decode_key, JWT_AUDIENCE, algorithms=[JWT_ALGORITHM])
        except Exception:
            return None

        jti: str | None = data.get("jti")
        if jti:
            revoked = await self.redis.exists(f"revoked_jti:{jti}")
            if revoked:
                raise HTTPException(
                    status_code=401,
                    detail="Token has been revoked",
                    headers={"WWW-Authenticate": "Bearer", "X-Error-Code": "token_revoked"},
                )

        user_id: str | None = data.get("sub")
        if user_id is None:
            return None

        from fastapi_users import exceptions

        try:
            parsed_id = user_manager.parse_id(user_id)
            return await user_manager.get(parsed_id)
        except (exceptions.UserNotExists, exceptions.InvalidID):
            return None

    async def destroy_token(self, token: str, user: User) -> None:
        """Revoke a token by writing its JTI to the Redis blacklist.

        TTL is set to the token's remaining lifetime so the blacklist entry
        expires naturally when the token would have expired anyway.

        If Redis is unavailable, log a warning but return 204 — logout must
        not fail for the user.
        """
        try:
            data = decode_jwt(token, self.decode_key, JWT_AUDIENCE, algorithms=[JWT_ALGORITHM])
        except Exception:
            log.warning("logout.token_decode_failed")
            return

        jti: str | None = data.get("jti")
        if not jti:
            log.warning("logout.no_jti_in_token")
            return

        exp: int | None = data.get("exp")
        if exp is not None:
            remaining_ttl = max(1, int(exp - datetime.now(timezone.utc).timestamp()))
        else:
            remaining_ttl = JWT_LIFETIME_SECONDS

        try:
            await self.redis.set(f"revoked_jti:{jti}", "1", ex=remaining_ttl)
            log.info("token.revoked", jti=jti, ttl=remaining_ttl)
        except Exception as exc:
            # Redis failure — log but do not fail the logout request.
            log.warning("token.revocation_redis_error", error=str(exc))


async def get_jwt_strategy(request: Request) -> ConciergeJWTStrategy:
    """Per-request dependency: return a ConciergeJWTStrategy.

    Reads the signing secret from app.state.secrets (populated from Vault at
    startup) so it is never present at import time.
    """
    return ConciergeJWTStrategy(
        secret=request.app.state.secrets["jwt_secret"],
        redis=request.app.state.redis,
        lifetime_seconds=JWT_LIFETIME_SECONDS,
    )


# ──────────────────────────────────────────────────────────────────────────────
# Authentication backend + FastAPIUsers instance
# ──────────────────────────────────────────────────────────────────────────────

bearer_transport = BearerTransport(tokenUrl="/auth/login")

auth_backend = AuthenticationBackend(
    name="jwt",
    transport=bearer_transport,
    get_strategy=get_jwt_strategy,
)

fastapi_users_instance = FastAPIUsers[User, uuid.UUID](
    get_user_manager,
    [auth_backend],
)


# ──────────────────────────────────────────────────────────────────────────────
# Service-to-service credential check (retained from original security.py)
# ──────────────────────────────────────────────────────────────────────────────


def verify_service_token(token: str | None) -> bool:
    """Constant-time comparison of service-to-service credential.

    Returns False for any falsy input (missing/empty header) so the dependency
    always emits the same 403 — no oracle distinguishing absent vs. wrong
    (spec 018 FR-007).
    """
    if not token:
        return False
    expected = get_settings().SERVICE_AUTH_SECRET
    if not expected:
        return False
    return hmac.compare_digest(token, expected)


async def require_service_token(
    x_service_token: str | None = Header(default=None, alias="X-Service-Token"),
) -> None:
    """FastAPI dependency for internal service routes.

    Header is declared optional so a missing header produces 403 (not 422 from
    Pydantic validation). Per spec 018 FR-007, missing/empty/wrong tokens MUST
    all return the identical response body.
    """
    if not verify_service_token(x_service_token):
        raise HTTPException(status_code=403, detail="Invalid service token")
