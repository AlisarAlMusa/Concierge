"""Centralised exception handlers and custom exception classes.

Platform error contract
───────────────────────
All 4xx/5xx responses follow:
    {"detail": "<human-readable message>", "code": "<machine-readable code>"}

401 codes: auth_required | token_expired | invalid_token | token_revoked
403 codes: permission_denied | tenant_suspended
404 codes: not_found
409 codes: conflict
429 codes: rate_limited
503 codes: upstream_error
"""

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.responses import JSONResponse
from fastapi_users import exceptions as fuu_exc

# ──────────────────────────────────────────────────────────────────────────────
# Custom application exception classes
# ──────────────────────────────────────────────────────────────────────────────


class NotFoundError(Exception):
    def __init__(self, resource: str, resource_id: str | None = None) -> None:
        self.resource = resource
        self.resource_id = resource_id
        super().__init__(f"{resource} not found")


class PermissionDeniedError(Exception):
    def __init__(self, message: str = "Permission denied") -> None:
        super().__init__(message)


class TenantSuspendedError(Exception):
    def __init__(self) -> None:
        super().__init__("Tenant is suspended")


class RateLimitError(Exception):
    def __init__(self, message: str = "Rate limit exceeded") -> None:
        super().__init__(message)


class ToolFailureError(Exception):
    def __init__(self, tool: str, reason: str) -> None:
        self.tool = tool
        self.reason = reason
        super().__init__(f"Tool {tool} failed: {reason}")


class ExternalServiceError(Exception):
    def __init__(self, service: str, reason: str) -> None:
        self.service = service
        self.reason = reason
        super().__init__(f"External service {service} error: {reason}")


# ──────────────────────────────────────────────────────────────────────────────
# Handler registration
# ──────────────────────────────────────────────────────────────────────────────


def register_error_handlers(app: FastAPI) -> None:  # noqa: C901
    # ── Custom application exceptions ──────────────────────────────────────

    @app.exception_handler(NotFoundError)
    async def not_found_handler(request: Request, exc: NotFoundError) -> JSONResponse:
        return JSONResponse(
            status_code=404,
            content={"detail": str(exc), "code": "not_found"},
        )

    @app.exception_handler(PermissionDeniedError)
    async def permission_denied_handler(
        request: Request, exc: PermissionDeniedError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=403,
            content={"detail": str(exc), "code": "permission_denied"},
        )

    @app.exception_handler(TenantSuspendedError)
    async def tenant_suspended_handler(request: Request, exc: TenantSuspendedError) -> JSONResponse:
        return JSONResponse(
            status_code=403,
            content={"detail": str(exc), "code": "tenant_suspended"},
        )

    @app.exception_handler(RateLimitError)
    async def rate_limit_handler(request: Request, exc: RateLimitError) -> JSONResponse:
        return JSONResponse(
            status_code=429,
            content={"detail": str(exc), "code": "rate_limited"},
        )

    @app.exception_handler(ExternalServiceError)
    async def external_service_handler(request: Request, exc: ExternalServiceError) -> JSONResponse:
        return JSONResponse(
            status_code=503,
            content={"detail": str(exc), "code": "upstream_error"},
        )

    # ── fastapi-users exceptions → platform contract ───────────────────────

    @app.exception_handler(fuu_exc.UserAlreadyExists)
    async def user_already_exists_handler(
        request: Request, exc: fuu_exc.UserAlreadyExists
    ) -> JSONResponse:
        return JSONResponse(
            status_code=409,
            content={"detail": "A user with this email already exists", "code": "conflict"},
        )

    @app.exception_handler(fuu_exc.UserNotExists)
    async def user_not_exists_handler(request: Request, exc: fuu_exc.UserNotExists) -> JSONResponse:
        return JSONResponse(
            status_code=401,
            content={"detail": "Authentication required", "code": "auth_required"},
            headers={"WWW-Authenticate": "Bearer"},
        )

    @app.exception_handler(fuu_exc.UserInactive)
    async def user_inactive_handler(request: Request, exc: fuu_exc.UserInactive) -> JSONResponse:
        return JSONResponse(
            status_code=401,
            content={"detail": "Account is inactive", "code": "auth_required"},
            headers={"WWW-Authenticate": "Bearer"},
        )

    # ── Generic HTTPException enrichment ──────────────────────────────────
    # Add a `code` field to any plain 401/403 that doesn't already carry one.
    # This catches fastapi-users' bare HTTPException(401) raised by the
    # authenticator when no valid token is presented.

    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
        detail = exc.detail
        headers = dict(exc.headers) if exc.headers else {}

        if exc.status_code == status.HTTP_401_UNAUTHORIZED:
            # Determine the code from the X-Error-Code header if set by our
            # custom strategy (e.g. token_revoked).
            code = headers.pop("X-Error-Code", "auth_required")
            headers.setdefault("WWW-Authenticate", "Bearer")
            if isinstance(detail, str):
                body: dict = {"detail": detail, "code": code}
            else:
                body = {"detail": "Authentication required", "code": code}
            return JSONResponse(
                status_code=401,
                content=body,
                headers=headers,
            )

        if exc.status_code == status.HTTP_403_FORBIDDEN:
            code = headers.pop("X-Error-Code", "permission_denied")
            if isinstance(detail, str):
                body = {"detail": detail, "code": code}
            else:
                body = {"detail": "Insufficient permissions", "code": "permission_denied"}
            return JSONResponse(
                status_code=403,
                content=body,
                headers=headers,
            )

        if exc.status_code == status.HTTP_429_TOO_MANY_REQUESTS:
            if isinstance(detail, str):
                body = {"detail": detail, "code": "rate_limit"}
            else:
                body = {"detail": "Too many requests", "code": "rate_limit"}
            return JSONResponse(
                status_code=429,
                content=body,
                headers=headers,
            )

        # Fall through: return as-is for other status codes.
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": detail} if not isinstance(detail, dict) else detail,
            headers=headers or None,
        )
