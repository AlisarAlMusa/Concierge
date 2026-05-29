"""Integration tests for the public widget runtime surface (Spec 011).

Covers the three routes mounted under ``/public/*`` and proves both that
the new aliases work end-to-end **and** that the legacy paths still
respond so the migration is backward-compatible:

* ``POST /public/widgets/session`` (alias for ``POST /widgets/session``)
* ``POST /public/chat`` (alias for ``POST /chat``)
* ``GET  /public/widgets/config`` (new — greeting + theme for the
  verified widget session)

Security invariants asserted here, not just route plumbing:

* ``tenant_id`` on the request body is **ignored** on chat — the value
  in the verified JWT is always authoritative.
* Bad origin → 403 on session mint.
* Invalid / stale token → 401 on chat + config.
* Cross-tenant config lookup (token says tenant A but widget belongs to
  tenant B) returns 404 — never leaks the row.
* Disabled widgets surface as 404 on config (matches the session-mint
  contract).

No Redis, no Postgres, no LLM, no embedding API: every collaborator is
swapped through ``app.dependency_overrides``.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from app.dependencies import (
    get_chat_orchestrator,
    get_runtime_widget_service,
    get_widget_service,
    get_widget_token_service,
)
from app.main import app
from app.services.chat_orchestrator import ChatTurn
from app.services.router_service import RouteDecision
from app.services.widget_token_service import WidgetTokenService

SECRET = "public-runtime-test-secret"


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeWidget:
    """Minimal duck of the Widget ORM row — only fields the routes read."""

    def __init__(
        self,
        *,
        widget_id: UUID | None = None,
        tenant_id: UUID | None = None,
        public_widget_id: str = "pub_wid_test",
        allowed_origins: list[str] | None = None,
        greeting: str = "Hello from Acme!",
        theme: dict[str, Any] | None = None,
        enabled: bool = True,
    ) -> None:
        self.id = widget_id or uuid4()
        self.tenant_id = tenant_id or uuid4()
        self.public_widget_id = public_widget_id
        self.allowed_origins = allowed_origins or []
        self.greeting = greeting
        self.theme = theme or {"primary": "#ff6600", "font": "Inter"}
        self.enabled = enabled


class _FakePreAuthWidgetService:
    """``WidgetService`` double for ``POST /(public/)widgets/session``.

    The session handler resolves widgets by ``public_widget_id`` BEFORE
    the request has a tenant context, so this fake only needs the
    ``get_by_public_id`` method.
    """

    def __init__(self, widgets: dict[str, _FakeWidget] | None = None) -> None:
        self.widgets = widgets or {}

    async def get_by_public_id(self, public_widget_id: str) -> _FakeWidget | None:
        widget = self.widgets.get(public_widget_id)
        if widget is None or not widget.enabled:
            return None
        return widget


class _FakeRuntimeWidgetService:
    """``WidgetService`` double for ``GET /public/widgets/config``.

    The config handler is invoked POST-auth with a tenant context, so
    this fake mirrors the production ``get_by_id`` signature including
    the explicit ``tenant_id`` filter the route asserts for defense in
    depth.
    """

    def __init__(self, widgets: dict[UUID, _FakeWidget] | None = None) -> None:
        self.widgets = widgets or {}
        self.calls: list[dict[str, Any]] = []

    async def get_by_id(self, widget_id: UUID, *, tenant_id: UUID) -> _FakeWidget | None:
        self.calls.append({"widget_id": widget_id, "tenant_id": tenant_id})
        widget = self.widgets.get(widget_id)
        # Hard tenant filter — mirrors the production WHERE clause.
        if widget is None or widget.tenant_id != tenant_id or not widget.enabled:
            return None
        return widget


class _FakeOrchestrator:
    """Captures the kwargs the chat route forwards and returns a canned reply."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.route = RouteDecision(
            path="agent",
            reason="ambiguous",
            confidence=0.4,
            classifier_label="ambiguous",
        )

    async def handle_turn(self, **kwargs: Any) -> ChatTurn:
        self.calls.append(kwargs)
        return ChatTurn(
            reply="deterministic public reply",
            conversation_id=kwargs.get("conversation_id") or uuid4(),
            route=self.route,
            sources=[],
            agent_iterations=1,
            used_refusal_fallback=False,
        )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def token_service() -> WidgetTokenService:
    return WidgetTokenService(secret=SECRET, ttl_seconds=900)


@pytest.fixture
def fake_widget() -> _FakeWidget:
    return _FakeWidget(
        public_widget_id="pub_wid_test",
        allowed_origins=["https://allowed.example.com"],
        greeting="Hi there — how can I help?",
        theme={"primary": "#0066cc"},
    )


@pytest.fixture
def pre_auth_service(fake_widget: _FakeWidget) -> _FakePreAuthWidgetService:
    return _FakePreAuthWidgetService({fake_widget.public_widget_id: fake_widget})


@pytest.fixture
def runtime_service(fake_widget: _FakeWidget) -> _FakeRuntimeWidgetService:
    return _FakeRuntimeWidgetService({fake_widget.id: fake_widget})


@pytest.fixture
def fake_orchestrator() -> _FakeOrchestrator:
    return _FakeOrchestrator()


@pytest.fixture
def client(
    pre_auth_service: _FakePreAuthWidgetService,
    runtime_service: _FakeRuntimeWidgetService,
    fake_orchestrator: _FakeOrchestrator,
    token_service: WidgetTokenService,
):
    app.dependency_overrides[get_widget_service] = lambda: pre_auth_service
    app.dependency_overrides[get_runtime_widget_service] = lambda: runtime_service
    app.dependency_overrides[get_chat_orchestrator] = lambda: fake_orchestrator
    app.dependency_overrides[get_widget_token_service] = lambda: token_service
    try:
        with TestClient(app) as c:
            yield c
    finally:
        app.dependency_overrides.clear()


def _bearer(token_service: WidgetTokenService, **overrides: Any) -> dict[str, str]:
    claims = {
        "tenant_id": uuid4(),
        "widget_id": uuid4(),
        "visitor_session_id": uuid4(),
        "origin": "https://allowed.example.com",
        **overrides,
    }
    token = token_service.issue(**claims)
    return {"Authorization": f"Bearer {token}"}


# ===========================================================================
# POST /public/widgets/session   (alias for POST /widgets/session)
# ===========================================================================


def test_public_session_happy_path_returns_token(
    client: TestClient,
    fake_widget: _FakeWidget,
    token_service: WidgetTokenService,
) -> None:
    response = client.post(
        "/public/widgets/session",
        json={
            "public_widget_id": fake_widget.public_widget_id,
            "origin": "https://allowed.example.com",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "Bearer"
    assert body["expires_in"] == 900
    # The token round-trips through the same secret so the claims carry
    # the verified widget identity.
    claims = token_service.verify(body["token"])
    assert claims.widget_id == fake_widget.id
    assert claims.tenant_id == fake_widget.tenant_id
    assert claims.origin == "https://allowed.example.com"


def test_legacy_session_still_works(
    client: TestClient,
    fake_widget: _FakeWidget,
) -> None:
    """Backward-compat: the old /widgets/session URL must stay live."""
    response = client.post(
        "/widgets/session",
        json={
            "public_widget_id": fake_widget.public_widget_id,
            "origin": "https://allowed.example.com",
        },
    )
    assert response.status_code == 200
    assert response.json()["token_type"] == "Bearer"


def test_public_session_bad_origin_returns_403(client: TestClient) -> None:
    response = client.post(
        "/public/widgets/session",
        json={"public_widget_id": "pub_wid_test", "origin": "https://evil.example.com"},
    )
    assert response.status_code == 403


def test_public_session_unknown_widget_returns_404(client: TestClient) -> None:
    response = client.post(
        "/public/widgets/session",
        json={
            "public_widget_id": "does-not-exist",
            "origin": "https://allowed.example.com",
        },
    )
    assert response.status_code == 404


def test_public_session_missing_origin_returns_422(client: TestClient) -> None:
    response = client.post(
        "/public/widgets/session",
        json={"public_widget_id": "pub_wid_test"},
    )
    assert response.status_code == 422


# ===========================================================================
# POST /public/chat   (alias for POST /chat)
# ===========================================================================


def test_public_chat_happy_path_returns_200(
    client: TestClient,
    fake_orchestrator: _FakeOrchestrator,
    token_service: WidgetTokenService,
) -> None:
    tenant_id = uuid4()
    widget_id = uuid4()
    headers = _bearer(token_service, tenant_id=tenant_id, widget_id=widget_id)

    response = client.post("/public/chat", json={"message": "hi"}, headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert body["message"] == "deterministic public reply"
    assert body["intent_label"] == "ambiguous"

    # The orchestrator received the kwargs sourced from the token, not
    # from any body field the visitor could control.
    kwargs = fake_orchestrator.calls[0]
    assert kwargs["tenant_id"] == tenant_id
    assert kwargs["widget_id"] == widget_id
    assert kwargs["user_message"] == "hi"


def test_legacy_chat_still_works(
    client: TestClient,
    token_service: WidgetTokenService,
) -> None:
    """Backward-compat: the old /chat URL must stay live."""
    response = client.post("/chat", json={"message": "hi"}, headers=_bearer(token_service))
    assert response.status_code == 200
    assert response.json()["message"] == "deterministic public reply"


def test_public_chat_without_token_returns_401(client: TestClient) -> None:
    response = client.post("/public/chat", json={"message": "hi"})
    assert response.status_code == 401


def test_public_chat_invalid_token_returns_401(client: TestClient) -> None:
    response = client.post(
        "/public/chat",
        json={"message": "hi"},
        headers={"Authorization": "Bearer not-a-jwt-at-all"},
    )
    assert response.status_code == 401


def test_public_chat_token_from_other_secret_returns_401(
    client: TestClient,
) -> None:
    """Token signed by a different service instance must be rejected."""
    foreign = WidgetTokenService(secret="some-other-secret")
    bad_token = foreign.issue(
        tenant_id=uuid4(),
        widget_id=uuid4(),
        visitor_session_id=uuid4(),
        origin="https://allowed.example.com",
    )
    response = client.post(
        "/public/chat",
        json={"message": "hi"},
        headers={"Authorization": f"Bearer {bad_token}"},
    )
    assert response.status_code == 401


def test_public_chat_expired_token_returns_401(client: TestClient) -> None:
    """A token whose ``exp`` has passed must be rejected with a clear code."""
    # Use a fresh service with a known clock so we can mint a stale token.
    short = WidgetTokenService(secret=SECRET, ttl_seconds=1)
    stale = short.issue(
        tenant_id=uuid4(),
        widget_id=uuid4(),
        visitor_session_id=uuid4(),
        origin="https://allowed.example.com",
        now=0,  # epoch 0 → exp=1 → long since past
    )
    response = client.post(
        "/public/chat",
        json={"message": "hi"},
        headers={"Authorization": f"Bearer {stale}"},
    )
    assert response.status_code == 401
    # The widget runtime distinguishes expired (refresh + retry) from
    # invalid (re-issue session) — the auth dep stamps the discriminator
    # into ``X-Error-Code`` on the HTTPException, which the global 401
    # handler folds into the response body as ``code``.
    assert response.json()["code"] == "token_expired"


def test_public_chat_ignores_tenant_id_in_body(
    client: TestClient,
    fake_orchestrator: _FakeOrchestrator,
    token_service: WidgetTokenService,
) -> None:
    """A hostile body field MUST NOT override the token's tenant_id."""
    token_tenant = uuid4()
    body_tenant = uuid4()
    assert token_tenant != body_tenant
    headers = _bearer(token_service, tenant_id=token_tenant)

    response = client.post(
        "/public/chat",
        json={"message": "hi", "tenant_id": str(body_tenant)},
        headers=headers,
    )
    assert response.status_code == 200
    forwarded = fake_orchestrator.calls[0]["tenant_id"]
    assert forwarded == token_tenant
    assert forwarded != body_tenant


# ===========================================================================
# GET /public/widgets/config   (new endpoint)
# ===========================================================================


def test_public_config_happy_path_returns_greeting_and_theme(
    client: TestClient,
    fake_widget: _FakeWidget,
    runtime_service: _FakeRuntimeWidgetService,
    token_service: WidgetTokenService,
) -> None:
    headers = _bearer(
        token_service,
        tenant_id=fake_widget.tenant_id,
        widget_id=fake_widget.id,
    )

    response = client.get("/public/widgets/config", headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert body == {
        "public_widget_id": fake_widget.public_widget_id,
        "greeting": fake_widget.greeting,
        "theme": fake_widget.theme,
        "enabled": True,
    }
    # The service was called with the verified ids — not anything the
    # caller could have supplied via URL or body.
    assert runtime_service.calls == [
        {"widget_id": fake_widget.id, "tenant_id": fake_widget.tenant_id}
    ]


def test_public_config_does_not_leak_allowed_origins(
    client: TestClient,
    fake_widget: _FakeWidget,
    token_service: WidgetTokenService,
) -> None:
    """``allowed_origins`` is server-side enforcement state — never returned."""
    headers = _bearer(token_service, tenant_id=fake_widget.tenant_id, widget_id=fake_widget.id)
    response = client.get("/public/widgets/config", headers=headers)
    assert response.status_code == 200
    assert "allowed_origins" not in response.json()


def test_public_config_without_token_returns_401(client: TestClient) -> None:
    response = client.get("/public/widgets/config")
    assert response.status_code == 401


def test_public_config_invalid_token_returns_401(client: TestClient) -> None:
    response = client.get(
        "/public/widgets/config",
        headers={"Authorization": "Bearer garbage"},
    )
    assert response.status_code == 401


def test_public_config_disabled_widget_returns_404(
    client: TestClient,
    fake_widget: _FakeWidget,
    token_service: WidgetTokenService,
) -> None:
    fake_widget.enabled = False
    headers = _bearer(token_service, tenant_id=fake_widget.tenant_id, widget_id=fake_widget.id)
    response = client.get("/public/widgets/config", headers=headers)
    assert response.status_code == 404


def test_public_config_cross_tenant_lookup_returns_404(
    client: TestClient,
    fake_widget: _FakeWidget,
    token_service: WidgetTokenService,
) -> None:
    """Tenant in token != widget's real tenant → 404, no leakage."""
    attacker_tenant = uuid4()
    assert attacker_tenant != fake_widget.tenant_id
    headers = _bearer(
        token_service,
        tenant_id=attacker_tenant,
        widget_id=fake_widget.id,  # real widget id, wrong tenant
    )
    response = client.get("/public/widgets/config", headers=headers)
    assert response.status_code == 404


def test_public_config_unknown_widget_id_returns_404(
    client: TestClient,
    token_service: WidgetTokenService,
) -> None:
    """A perfectly valid token whose widget_id has no row → clean 404."""
    headers = _bearer(token_service)  # random ids — none registered
    response = client.get("/public/widgets/config", headers=headers)
    assert response.status_code == 404
