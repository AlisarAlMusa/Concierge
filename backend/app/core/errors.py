from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse


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


def register_error_handlers(app: FastAPI) -> None:
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
