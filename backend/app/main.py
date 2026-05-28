from contextlib import asynccontextmanager

import httpx
import redis.asyncio as aioredis
from fastapi import FastAPI

from app.api.router import api_router
from app.core.config import get_settings
from app.core.errors import register_error_handlers
from app.core.logging import RequestIDMiddleware, configure_logging
from app.core.tracing import setup_tracing
from app.db.session import close_engine, get_engine


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    configure_logging(settings.APP_ENV)
    setup_tracing(app)
    get_engine()
    app.state.redis = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
    # Single authenticated client for every outbound sidecar call. The header is
    # attached here once so service-layer code can never forget it (spec 018).
    app.state.service_client = httpx.AsyncClient(
        headers={"X-Service-Token": settings.SERVICE_AUTH_SECRET},
        timeout=10.0,
    )
    # Secrets dictionary — values sourced from settings, which reads from Vault
    # in non-local environments via fetch_service_token in config.py.
    app.state.secrets = {
        "jwt_secret": settings.JWT_SECRET,
        "service_auth_secret": settings.SERVICE_AUTH_SECRET,
        "widget_token_secret": settings.WIDGET_TOKEN_SECRET,
        "minio_secret_key": settings.MINIO_SECRET_KEY,
        "openai_api_key": settings.OPENAI_API_KEY,
        "anthropic_api_key": settings.ANTHROPIC_API_KEY,
        "azure_openai_api_key": settings.AZURE_OPENAI_API_KEY,
        "azure_openai_endpoint": settings.AZURE_OPENAI_ENDPOINT,
        "azure_openai_api_version": settings.AZURE_OPENAI_API_VERSION,
        "azure_openai_deployment": settings.AZURE_OPENAI_DEPLOYMENT,
    }

    # Vault sentinel: refuse to start in non-local environments if JWT secret
    # is still the placeholder value (catches deploy-without-vault failures).
    _PLACEHOLDER = "change-me-local-dev-only"
    if settings.APP_ENV != "local" and app.state.secrets["jwt_secret"] == _PLACEHOLDER:
        raise RuntimeError(
            "JWT secret is the placeholder value — refusing to start in non-local environment. "
            "Ensure Vault is reachable and the jwt_secret path is populated."
        )

    yield
    await app.state.service_client.aclose()
    await app.state.redis.aclose()
    await close_engine()


app = FastAPI(
    title="Concierge API",
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/docs" if get_settings().APP_ENV == "local" else None,
)

app.add_middleware(RequestIDMiddleware)
register_error_handlers(app)
app.include_router(api_router)
