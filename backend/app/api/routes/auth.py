"""Auth routes: register, login, logout, /me.

These are custom routes (not the standard fastapi-users routers) so we can:
  • Force role=member on self-registration (T007)
  • Apply per-IP login rate limiting (T026)
  • Emit audit events on login, logout, and failed login (T011)
  • Return platform-standard error shapes (T029)

JTI revocation (T008) is implemented in ConciergeJWTStrategy.destroy_token()
and verified in ConciergeJWTStrategy.read_token().

get_current_user (T009) is in app.dependencies and delegates to the fastapi-users
authenticator which calls ConciergeJWTStrategy.read_token().
"""

from __future__ import annotations

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.security import OAuth2PasswordRequestForm

from app.core.security import auth_backend, fastapi_users_instance, get_user_manager
from app.dependencies import get_current_user, get_redis
from app.models.user import User
from app.schemas.auth import UserCreate, UserRead
from app.services.auth_service import (
    check_login_rate_limit,
    reset_login_rate_limit,
    write_audit_event,
)

log = structlog.get_logger(__name__)

router = APIRouter(tags=["auth"])


# ──────────────────────────────────────────────────────────────────────────────
# POST /auth/register
# ──────────────────────────────────────────────────────────────────────────────


@router.post(
    "/register",
    response_model=UserRead,
    status_code=status.HTTP_201_CREATED,
    summary="Register a new user (role always 'member')",
)
async def register(
    user_create: UserCreate,
    user_manager=Depends(get_user_manager),
    request: Request = None,
):
    """Register a new user.

    • role is ALWAYS forced to `member` regardless of any role field in the body.
    • tenant_id is ALWAYS null for self-registered users.
    • Duplicate email returns 409.
    """
    from fastapi_users import exceptions as fuu_exc

    try:
        user = await user_manager.create(user_create, safe=True, request=request)
    except fuu_exc.UserAlreadyExists:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A user with this email already exists",
            headers={"X-Error-Code": "conflict"},
        )

    return UserRead.model_validate(user)


# ──────────────────────────────────────────────────────────────────────────────
# POST /auth/login
# ──────────────────────────────────────────────────────────────────────────────


@router.post(
    "/login",
    summary="Authenticate with email and password; returns a JWT bearer token",
)
async def login(
    request: Request,
    credentials: OAuth2PasswordRequestForm = Depends(),
    user_manager=Depends(get_user_manager),
    strategy=Depends(auth_backend.get_strategy),
    redis=Depends(get_redis),
):
    """Login endpoint with:
    • Per-IP rate limiting (max 10 attempts per 15-minute window).
    • Audit log on success and failure.
    • Counter reset on successful login.
    """
    ip = request.client.host if request.client else "unknown"

    # Rate-limit check (increments counter regardless of outcome).
    await check_login_rate_limit(ip, redis)

    # Authenticate credentials.
    user = await user_manager.authenticate(credentials)

    if user is None or not user.is_active:
        # Fire-and-forget failed_login audit.
        write_audit_event(
            action="failed_login",
            actor_role="unknown",
            metadata_={"email_attempted": credentials.username, "ip": ip},
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid credentials",
            headers={"X-Error-Code": "invalid_credentials"},
        )

    # Successful login — reset rate-limit counter and issue token.
    await reset_login_rate_limit(ip, redis)

    response = await auth_backend.login(strategy, user)
    await user_manager.on_after_login(user, request, response)
    return response


# ──────────────────────────────────────────────────────────────────────────────
# POST /auth/logout
# ──────────────────────────────────────────────────────────────────────────────


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Invalidate the current bearer token (JTI blacklist)",
)
async def logout(
    request: Request,
    user_token: tuple[User, str] = Depends(
        fastapi_users_instance.authenticator.current_user_token(active=True)
    ),
    strategy=Depends(auth_backend.get_strategy),
):
    """Logout the current user.

    Revokes the JWT by writing its JTI to the Redis blacklist with a TTL equal
    to the token's remaining lifetime.  Subsequent requests with this token
    receive 401 (token_revoked).
    """
    user, token = user_token

    await auth_backend.logout(strategy, user, token)

    write_audit_event(
        action="logout",
        actor_user_id=user.id,
        actor_role=user.role.value,
        tenant_id=user.tenant_id,
    )
    log.info("user.logout", user_id=str(user.id))
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ──────────────────────────────────────────────────────────────────────────────
# GET /auth/me
# ──────────────────────────────────────────────────────────────────────────────


@router.get(
    "/me",
    response_model=UserRead,
    summary="Return the authenticated user's profile",
)
async def me(
    current_user: User = Depends(get_current_user),
):
    """Return the current user's id, email, role, tenant_id, and is_active."""
    return UserRead.model_validate(current_user)
