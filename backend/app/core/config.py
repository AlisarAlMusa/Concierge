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

    # Internal service URLs
    MODEL_SERVER_URL: str
    GUARDRAILS_URL: str

    # Secrets (from Vault in non-local envs)
    SERVICE_AUTH_SECRET: str
    WIDGET_TOKEN_SECRET: str


@lru_cache
def get_settings() -> Settings:
    return Settings()
