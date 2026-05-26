from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="forbid")

    APP_ENV: str = "local"

    # Database
    DATABASE_URL: str

    # Redis
    REDIS_URL: str

    # Vault
    VAULT_ADDR: str
    VAULT_TOKEN: str

    # MinIO
    MINIO_ENDPOINT: str
    MINIO_ACCESS_KEY: str
    MINIO_SECRET_KEY: str

    # LLM (hosted API — no local models)
    LLM_PROVIDER: str
    LLM_MODEL: str
    EMBEDDING_MODEL: str
    # API keys — empty default because production reads them from Vault
    OPENAI_API_KEY: str = ""
    ANTHROPIC_API_KEY: str = ""
    AZURE_OPENAI_API_KEY: str = ""
    AZURE_OPENAI_ENDPOINT: str = ""
    AZURE_OPENAI_API_VERSION: str = "2024-02-01"
    AZURE_OPENAI_DEPLOYMENT: str = ""

    # Internal service URLs
    MODEL_SERVER_URL: str
    GUARDRAILS_URL: str

    # Secrets — seeded into Vault at startup; app reads live values from
    # app.state.secrets at runtime. .env values are the local-dev fallback.
    SERVICE_AUTH_SECRET: str
    WIDGET_TOKEN_SECRET: str
    JWT_SECRET: str = "change-me-local-dev-only"


@lru_cache
def get_settings() -> Settings:
    return Settings()
