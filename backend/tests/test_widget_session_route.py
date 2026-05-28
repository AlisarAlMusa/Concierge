"""Integration tests for ``POST /widgets/session``.

Validates the HTTP contract of the session-token endpoint (Spec 011
FR-002 / FR-003 / FR-004 / FR-005):

* 200 on a known public_widget_id + allowed origin → returns a token
* 403 on an origin not in ``allowed_origins`` (server-side check)
* 404 on an unknown public_widget_id
* 422 on a malformed body

``WidgetService`` is overridden via FastAPI dependency override so the
test exercises the route + token-service wiring without Postgres.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.dependencies import (
    get_widget_service,
    get_widget_token_service,
)
from app.main import app
from app.services.widget_service import WidgetService
from app.services.widget_token_service import WidgetTokenService

SECRET = "widget-route-test-secret"


class _FakeWidget:
    """Minimal duck of the Widget ORM row — only fields the route reads."""

    def __init__(
        self,
        *,
        widget_id: Any = None,
        tenant_id: Any = None,
        allowed_origins: list[str] | None = None,
        enabled: bool = True,
    ) -> None:
        self.id = widget_id or uuid4()
        self.tenant_id = tenant_id or uuid4()
        self.allowed_origins = allowed_origins or []
        self.enabled = enabled


class _FakeWidgetService:
    """Routes' WidgetService double — keyed by public_widget_id."""

    def __init__(self, widgets: dict[str, _FakeWidget] | None = None) -> None:
        self.widgets = widgets or {}

    async def get_by_public_id(self, public_widget_id: str) -> _FakeWidget | None:
        widget = self.widgets.get(public_widget_id)
        if widget is None or not widget.enabled:
            return None
        return widget


@pytest.fixture
def token_service() -> WidgetTokenService:
    return WidgetTokenService(secret=SECRET, ttl_seconds=900)


@pytest.fixture
def fake_widget() -> _FakeWidget:
    return _FakeWidget(allowed_origins=["https://allowed.example.com"])


@pytest.fixture
def fake_widget_service(fake_widget: _FakeWidget) -> _FakeWidgetService:
    return _FakeWidgetService({"public-1": fake_widget})


@pytest.fixture
def client(fake_widget_service: _FakeWidgetService, token_service: WidgetTokenService):
    app.dependency_overrides[get_widget_service] = lambda: fake_widget_service
    app.dependency_overrides[get_widget_token_service] = lambda: token_service
    try:
        with TestClient(app) as c:
            yield c
    finally:
        app.dependency_overrides.clear()


# ----- happy path -----------------------------------------------------------
def test_session_happy_path_returns_token(
    client: TestClient,
    fake_widget: _FakeWidget,
    token_service: WidgetTokenService,
) -> None:
    response = client.post(
        "/widgets/session",
        json={"public_widget_id": "public-1", "origin": "https://allowed.example.com"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "Bearer"
    assert body["expires_in"] == 900
    # Token round-trips through the same secret/TTL → claims carry the widget.
    claims = token_service.verify(body["token"])
    assert claims.widget_id == fake_widget.id
    assert claims.tenant_id == fake_widget.tenant_id
    assert claims.origin == "https://allowed.example.com"


# ----- 403: origin allowlist (Spec 011 FR-003 / FR-004) ---------------------
def test_session_disallowed_origin_returns_403(client: TestClient) -> None:
    response = client.post(
        "/widgets/session",
        json={"public_widget_id": "public-1", "origin": "https://attacker.example.com"},
    )
    assert response.status_code == 403


def test_session_empty_allowlist_returns_403(client: TestClient, fake_widget: _FakeWidget) -> None:
    fake_widget.allowed_origins = []
    response = client.post(
        "/widgets/session",
        json={"public_widget_id": "public-1", "origin": "https://anywhere.example.com"},
    )
    assert response.status_code == 403


# ----- 404: unknown widget --------------------------------------------------
def test_session_unknown_widget_returns_404(client: TestClient) -> None:
    response = client.post(
        "/widgets/session",
        json={"public_widget_id": "does-not-exist", "origin": "https://allowed.example.com"},
    )
    assert response.status_code == 404


def test_session_disabled_widget_returns_404(
    client: TestClient, fake_widget_service: _FakeWidgetService
) -> None:
    fake_widget_service.widgets["public-1"].enabled = False
    response = client.post(
        "/widgets/session",
        json={"public_widget_id": "public-1", "origin": "https://allowed.example.com"},
    )
    assert response.status_code == 404


# ----- 422: malformed body --------------------------------------------------
def test_session_missing_origin_returns_422(client: TestClient) -> None:
    response = client.post("/widgets/session", json={"public_widget_id": "public-1"})
    assert response.status_code == 422


def test_session_missing_public_widget_id_returns_422(client: TestClient) -> None:
    response = client.post("/widgets/session", json={"origin": "https://allowed.example.com"})
    assert response.status_code == 422


# ----- WidgetService.validate_origin (unit-level) ---------------------------
def test_validate_origin_exact_match_only() -> None:
    widget = _FakeWidget(allowed_origins=["https://a.example.com"])

    assert WidgetService.validate_origin(widget, "https://a.example.com") is True
    assert WidgetService.validate_origin(widget, "https://A.example.com") is False  # case
    assert WidgetService.validate_origin(widget, "https://a.example.com/path") is False  # no prefix
    assert WidgetService.validate_origin(widget, None) is False
    assert WidgetService.validate_origin(widget, "") is False
