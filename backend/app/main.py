from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.router import api_router
from app.core.config import get_settings
from app.core.errors import register_error_handlers
from app.core.logging import configure_logging
from app.core.tracing import setup_tracing
from app.db.session import close_engine, get_engine


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    configure_logging(settings.APP_ENV)
    setup_tracing(app)
    # Warm up the DB connection pool on startup.
    get_engine()
    yield
    await close_engine()


app = FastAPI(
    title="Concierge API",
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/docs" if get_settings().APP_ENV == "local" else None,
)

register_error_handlers(app)
app.include_router(api_router)
