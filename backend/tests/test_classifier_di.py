"""DI smoke tests for the classifier-client singleton.

Verifies the env-conditional behavior of
``_get_classifier_client_singleton`` in ``app.dependencies``:

* ``Settings.CLASSIFIER_ENABLED == False`` → ``UnavailableClassifierClient``
* ``Settings.CLASSIFIER_ENABLED == True``  → ``HttpClassifierClient``

The DI cache is reset between each test so the singleton picks up the
patched Settings.
"""

from __future__ import annotations

import pytest

from app import dependencies
from app.core.config import Settings
from app.services.classifier_client import (
    HttpClassifierClient,
    UnavailableClassifierClient,
)


def _make_settings(*, classifier_enabled: bool) -> Settings:
    """Build a Settings object with the fields the DI factory consults.

    The other required fields are filled with safe dummies so Settings's
    forbid-extra validation passes.
    """
    return Settings(  # type: ignore[call-arg]
        APP_ENV="local",
        DATABASE_URL="postgresql+asyncpg://user:pw@db/db",
        REDIS_URL="redis://r:6379/0",
        VAULT_ADDR="http://v",
        VAULT_TOKEN="t",
        MINIO_ENDPOINT="m",
        MINIO_ACCESS_KEY="k",
        MINIO_SECRET_KEY="s",
        LLM_PROVIDER="groq",
        LLM_MODEL="llama-3.1-70b-versatile",
        EMBEDDING_MODEL="embed-english-v3.0",
        MODEL_SERVER_URL="http://model_server:8001",
        GUARDRAILS_URL="http://guardrails:8002",
        SERVICE_AUTH_SECRET="service-secret",
        WIDGET_TOKEN_SECRET="widget-secret",
        CLASSIFIER_ENABLED=classifier_enabled,
    )


@pytest.fixture
def reset_singletons():
    dependencies.reset_singletons()
    yield
    dependencies.reset_singletons()


def test_classifier_singleton_uses_stub_when_disabled(
    monkeypatch: pytest.MonkeyPatch, reset_singletons: None
) -> None:
    monkeypatch.setattr(
        dependencies, "get_settings", lambda: _make_settings(classifier_enabled=False)
    )

    client = dependencies._get_classifier_client_singleton()

    assert isinstance(client, UnavailableClassifierClient)


def test_classifier_singleton_uses_http_client_when_enabled(
    monkeypatch: pytest.MonkeyPatch, reset_singletons: None
) -> None:
    monkeypatch.setattr(
        dependencies, "get_settings", lambda: _make_settings(classifier_enabled=True)
    )

    client = dependencies._get_classifier_client_singleton()

    assert isinstance(client, HttpClassifierClient)
