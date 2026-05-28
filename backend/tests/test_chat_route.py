"""End-to-end-ish tests for the /chat HTTP route + widget auth chain.

Validates the integration between:

* the FastAPI route handler in ``app.api.routes.chat``,
* the widget-token auth chain in ``app.dependencies.get_widget_claims``,
* the ``ChatOrchestrator`` boundary via dependency override.

The orchestrator itself is overridden with a fake so this test exercises the
HTTP/auth wiring without spinning Redis or Postgres. Orchestrator behavior is
covered separately in ``tests/test_chat_orchestrator.py``.

No real Redis, no real DB, no real LLM, no real classifier.
"""

from __future__ import annotations

import os
from typing import Any
from uuid import UUID, uuid4

# Settings is constructed at app-import time (main.py reads APP_ENV for the
# docs_url toggle); satisfy the required-fields validator with safe dummies
# BEFORE importing anything that touches the settings cache.
os.environ.setdefault("APP_ENV", "local")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://u:p@db/db")
os.environ.setdefault("REDIS_URL", "redis://r:6379/0")
os.environ.setdefault("VAULT_ADDR", "http://v")
os.environ.setdefault("VAULT_TOKEN", "t")
os.environ.setdefault("MINIO_ENDPOINT", "m")
os.environ.setdefault("MINIO_ACCESS_KEY", "k")
os.environ.setdefault("MINIO_SECRET_KEY", "s")
os.environ.setdefault("LLM_PROVIDER", "groq")
os.environ.setdefault("LLM_MODEL", "llama-3.1-70b-versatile")
os.environ.setdefault("EMBEDDING_MODEL", "embed-english-v3.0")
os.environ.setdefault("MODEL_SERVER_URL", "http://model_server:8001")
os.environ.setdefault("GUARDRAILS_URL", "http://guardrails:8002")
os.environ.setdefault("SERVICE_AUTH_SECRET", "service-secret")
os.environ.setdefault("WIDGET_TOKEN_SECRET", "integration-test-secret")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.dependencies import (  # noqa: E402
    get_chat_orchestrator,
    get_widget_token_service,
)
from app.main import app  # noqa: E402
from app.services.chat_orchestrator import ChatTurn  # noqa: E402
from app.services.router_service import RouteDecision  # noqa: E402
from app.services.widget_token_service import WidgetTokenService  # noqa: E402

SECRET = "integration-test-secret"


class _FakeOrchestrator:
    """Captures the kwargs the route forwards and returns a canned ChatTurn."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.reply = "deterministic reply"
        self.sources: list[UUID] = []
        self.route = RouteDecision(
            path="agent",
            reason="ambiguous",
            confidence=0.4,
            classifier_label="ambiguous",
        )

    async def handle_turn(self, **kwargs: Any) -> ChatTurn:
        self.calls.append(kwargs)
        return ChatTurn(
            reply=self.reply,
            conversation_id=kwargs.get("conversation_id") or uuid4(),
            route=self.route,
            sources=self.sources,
            agent_iterations=1,
            used_refusal_fallback=False,
        )


@pytest.fixture
def token_service() -> WidgetTokenService:
    return WidgetTokenService(secret=SECRET, ttl_seconds=600)


@pytest.fixture
def fake_orchestrator() -> _FakeOrchestrator:
    return _FakeOrchestrator()


@pytest.fixture
def client(fake_orchestrator: _FakeOrchestrator, token_service: WidgetTokenService):
    """Wire the orchestrator + token service overrides for every test."""
    app.dependency_overrides[get_chat_orchestrator] = lambda: fake_orchestrator
    app.dependency_overrides[get_widget_token_service] = lambda: token_service
    try:
        with TestClient(app) as c:
            yield c
    finally:
        app.dependency_overrides.clear()


def _bearer(token_service: WidgetTokenService, **overrides: Any) -> dict[str, str]:
    """Build an Authorization header with a valid signed token."""
    claims = {
        "tenant_id": uuid4(),
        "widget_id": uuid4(),
        "visitor_session_id": uuid4(),
        "origin": "https://example.com",
        **overrides,
    }
    token = token_service.issue(**claims)
    return {"Authorization": f"Bearer {token}"}


# ----- happy path -----------------------------------------------------------
def test_chat_happy_path_returns_200_and_orchestrator_kwargs(
    client: TestClient,
    fake_orchestrator: _FakeOrchestrator,
    token_service: WidgetTokenService,
) -> None:
    tenant_id = uuid4()
    widget_id = uuid4()
    visitor_session = uuid4()
    headers = _bearer(
        token_service,
        tenant_id=tenant_id,
        widget_id=widget_id,
        visitor_session_id=visitor_session,
    )

    response = client.post("/chat", json={"message": "hi"}, headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert body["message"] == "deterministic reply"
    assert body["intent_label"] == "ambiguous"

    # Tenant + widget + visitor are sourced from the token, not the body.
    kwargs = fake_orchestrator.calls[0]
    assert kwargs["tenant_id"] == tenant_id
    assert kwargs["widget_id"] == widget_id
    assert kwargs["visitor_session_id"] == visitor_session
    assert kwargs["user_message"] == "hi"
    assert kwargs["conversation_id"] is None  # not supplied → orchestrator mints


# ----- auth -----------------------------------------------------------------
def test_chat_without_authorization_returns_401(client: TestClient) -> None:
    response = client.post("/chat", json={"message": "hi"})
    assert response.status_code == 401


def test_chat_with_malformed_bearer_returns_401(client: TestClient) -> None:
    response = client.post("/chat", json={"message": "hi"}, headers={"Authorization": "Token abc"})
    assert response.status_code == 401


def test_chat_with_invalid_token_returns_401(client: TestClient) -> None:
    response = client.post(
        "/chat", json={"message": "hi"}, headers={"Authorization": "Bearer not-a-jwt"}
    )
    assert response.status_code == 401
    assert response.json()["detail"]["code"] in ("invalid_token", "malformed_token")


def test_chat_with_wrong_secret_token_returns_401(client: TestClient) -> None:
    other = WidgetTokenService(secret="different-secret")
    bad = other.issue(
        tenant_id=uuid4(),
        widget_id=uuid4(),
        visitor_session_id=uuid4(),
        origin="https://example.com",
    )
    response = client.post(
        "/chat", json={"message": "hi"}, headers={"Authorization": f"Bearer {bad}"}
    )
    assert response.status_code == 401


# ----- tenant isolation ----------------------------------------------------
def test_chat_uses_tenant_from_token_not_body(
    client: TestClient,
    fake_orchestrator: _FakeOrchestrator,
    token_service: WidgetTokenService,
) -> None:
    token_tenant = uuid4()
    body_tenant_attempt = uuid4()
    headers = _bearer(token_service, tenant_id=token_tenant)

    # ``tenant_id`` in the body is intentionally ignored by the route.
    response = client.post(
        "/chat",
        json={"message": "hi", "tenant_id": str(body_tenant_attempt)},
        headers=headers,
    )

    assert response.status_code == 200
    assert fake_orchestrator.calls[0]["tenant_id"] == token_tenant
    assert fake_orchestrator.calls[0]["tenant_id"] != body_tenant_attempt


# ----- input validation -----------------------------------------------------
def test_chat_empty_message_returns_400(
    client: TestClient, token_service: WidgetTokenService
) -> None:
    response = client.post("/chat", json={"message": " "}, headers=_bearer(token_service))
    assert response.status_code == 400


def test_chat_invalid_conversation_id_returns_400(
    client: TestClient, token_service: WidgetTokenService
) -> None:
    response = client.post(
        "/chat",
        json={"message": "hi", "conversation_id": "not-a-uuid"},
        headers=_bearer(token_service),
    )
    assert response.status_code == 400


def test_chat_valid_conversation_id_is_forwarded(
    client: TestClient,
    fake_orchestrator: _FakeOrchestrator,
    token_service: WidgetTokenService,
) -> None:
    conv = uuid4()
    response = client.post(
        "/chat",
        json={"message": "hi", "conversation_id": str(conv)},
        headers=_bearer(token_service),
    )
    assert response.status_code == 200
    assert fake_orchestrator.calls[0]["conversation_id"] == conv
