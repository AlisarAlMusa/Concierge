import hmac
from fastapi import Header, HTTPException

from app.core.config import get_settings


def verify_service_token(token: str) -> bool:
    """Constant-time comparison of service-to-service credential."""
    expected = get_settings().SERVICE_AUTH_SECRET
    return hmac.compare_digest(token, expected)


async def require_service_token(
    x_service_token: str = Header(..., alias="X-Service-Token"),
) -> None:
    """FastAPI dependency for internal service routes."""
    if not verify_service_token(x_service_token):
        raise HTTPException(status_code=403, detail="Invalid service token")
