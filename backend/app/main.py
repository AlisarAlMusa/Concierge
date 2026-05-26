from contextlib import asynccontextmanager

import redis.asyncio as aioredis
from fastapi import FastAPI

from app.api.router import api_router
from app.core.config import get_settings
from app.core.errors import register_error_handlers
from app.core.logging import RequestIDMiddleware, configure_logging
from app.core.vault import fetch_vault_secrets
from app.db.session import close_engine, get_engine


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    configure_logging(settings.APP_ENV)
    get_engine()
    app.state.redis = aioredis.from_url(settings.REDIS_URL, decode_responses=True)

    vault_secrets = await fetch_vault_secrets(settings.VAULT_ADDR, settings.VAULT_TOKEN)
    # Vault wins; .env settings are the local-dev fallback for every secret.
    app.state.secrets = {
        "jwt_secret":           vault_secrets.get("jwt_secret",           settings.JWT_SECRET),
        "service_auth_secret":  vault_secrets.get("service_auth_secret",  settings.SERVICE_AUTH_SECRET),
        "widget_token_secret":  vault_secrets.get("widget_token_secret",  settings.WIDGET_TOKEN_SECRET),
        "minio_secret_key":     vault_secrets.get("minio_secret_key",     settings.MINIO_SECRET_KEY),
        "openai_api_key":           vault_secrets.get("openai_api_key",           settings.OPENAI_API_KEY),
        "anthropic_api_key":        vault_secrets.get("anthropic_api_key",        settings.ANTHROPIC_API_KEY),
        "azure_openai_api_key":     vault_secrets.get("azure_openai_api_key",     settings.AZURE_OPENAI_API_KEY),
        "azure_openai_endpoint":    vault_secrets.get("azure_openai_endpoint",    settings.AZURE_OPENAI_ENDPOINT),
        "azure_openai_api_version": vault_secrets.get("azure_openai_api_version", settings.AZURE_OPENAI_API_VERSION),
        "azure_openai_deployment":  vault_secrets.get("azure_openai_deployment",  settings.AZURE_OPENAI_DEPLOYMENT),
    }

    yield
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
