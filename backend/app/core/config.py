"""Application settings.

Owner B PR 3 adds the keys the chat/agent/widget paths consume at runtime —
``GROQ_API_KEY`` / ``COHERE_API_KEY`` (provider auth), the memory + agent + router
caps the DI layer wires into services, and ``WIDGET_TOKEN_TTL_SECONDS`` for the
short-lived widget session JWT (Spec 011 FR-012). Defaults match the frozen
specs so a fresh ``.env`` boots a usable local stack.
"""

import logging
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.vault import fetch_service_token

logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="forbid")

    APP_ENV: str = "local"

    DATABASE_URL: str

    REDIS_URL: str

    VAULT_ADDR: str
    VAULT_TOKEN: str
    VAULT_SERVICE_AUTH_PATH: str = "concierge/service-auth"

    MINIO_ENDPOINT: str
    MINIO_ACCESS_KEY: str
    MINIO_SECRET_KEY: str

    # LLM (hosted API — no local models).
    LLM_PROVIDER: str
    LLM_MODEL: str
    EMBEDDING_MODEL: str
    # Hosted-provider keys. Empty default = production reads them from Vault;
    # local dev fills them in via ``.env``.
    OPENAI_API_KEY: str = ""
    ANTHROPIC_API_KEY: str = ""
    AZURE_OPENAI_API_KEY: str = ""
    AZURE_OPENAI_ENDPOINT: str = ""
    AZURE_OPENAI_API_VERSION: str = "2024-02-01"
    AZURE_OPENAI_DEPLOYMENT: str = ""
    GROQ_API_KEY: str = ""
    COHERE_API_KEY: str = ""

    # Internal service URLs
    MODEL_SERVER_URL: str
    GUARDRAILS_URL: str

    # Service credentials. Seeded into Vault at startup; the app reads
    # live values from ``app.state.secrets`` at runtime. ``.env`` values are
    # the local-dev fallback for every secret.
    SERVICE_AUTH_SECRET: str
    WIDGET_TOKEN_SECRET: str
    JWT_SECRET: str = "change-me-local-dev-only"

    # Router (Spec 008 FR-011). Default 0.6 mirrors docs/SPEC.md §4.
    ROUTER_CONFIDENCE_THRESHOLD: float = 0.6
    # When false (default), DI returns ``UnavailableClassifierClient`` and the
    # router fails open to the agent. Flipped to true once model_server is
    # reachable in the stack.
    CLASSIFIER_ENABLED: bool = False

    # Agent loop caps (Spec 009 FR-002 / FR-003).
    AGENT_MAX_TOOL_ITERATIONS: int = 3
    AGENT_MAX_OUTPUT_TOKENS: int = 1024

    # Short-term memory (Spec 009 FR-007 / FR-008). 1800s == 30 min sliding
    # window; ``MEMORY_MAX_ENTRIES`` caps each conversation list via LTRIM.
    MEMORY_TTL_SECONDS: int = 1800
    MEMORY_MAX_ENTRIES: int = 40

    # Widget session token (Spec 011 FR-012). 15 minutes — short enough that
    # a leaked token expires fast, long enough that the widget rarely has to
    # silently refresh during a chat. The widget loader is responsible for
    # refresh.
    WIDGET_TOKEN_TTL_SECONDS: int = 900

    # capture_lead per-session rate limit (Spec 012 FR-003).
    LEAD_CAPTURE_LIMIT_PER_SESSION: int = 5
    LEAD_CAPTURE_WINDOW_HOURS: int = 1


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
