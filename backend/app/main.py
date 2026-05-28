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
