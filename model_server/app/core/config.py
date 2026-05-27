"""Settings singleton for `model_server`.

Mirrors `backend/app/core/config.py` for the fields this sidecar needs.
Service-auth secret resolution follows the spec 018 contract: fetch from Vault
when `APP_ENV != "local"`, fall back to `.env` (with a warning) in local mode.
"""

from __future__ import annotations

import logging
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.vault import fetch_service_token

logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    APP_ENV: str = "local"

    # Vault
    VAULT_ADDR: str = "http://vault:8200"
    VAULT_TOKEN: str = "dev-root-token"
    VAULT_SERVICE_AUTH_PATH: str = "concierge/service-auth"

    # Secret — populated from Vault when APP_ENV != "local".
    SERVICE_AUTH_SECRET: str = ""


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    if settings.APP_ENV != "local":
        settings.SERVICE_AUTH_SECRET = fetch_service_token(
            addr=settings.VAULT_ADDR,
            token=settings.VAULT_TOKEN,
            secret_path=settings.VAULT_SERVICE_AUTH_PATH,
        )
    elif not settings.SERVICE_AUTH_SECRET:
        logger.warning(
            "APP_ENV=local and SERVICE_AUTH_SECRET unset; Vault not consulted. "
            "Service-to-service auth will reject every request."
        )
    return settings
