"""Integration tests for spec 018 — User Story 1 (sidecar refuses unauthenticated traffic).

Covers FR-005, FR-007, FR-009 against the real `guardrails_sidecar` and
`model_server` FastAPI apps loaded via the conftest helper. The model isn't
loaded by these tests — every business route on the sidecar is a stub that
returns hardcoded values, and the `require_service_token` dependency runs
before the route body, so 403 paths never reach any model code.
"""

from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI

from tests.integration.conftest import TEST_SERVICE_TOKEN


def _async_client(app: FastAPI, *, headers: dict[str, str] | None = None) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
        headers=headers or {},
    )


@pytest.mark.asyncio
async def test_guardrails_redact_without_token_returns_403(
    guardrails_sidecar_app: FastAPI,
) -> None:
    async with _async_client(guardrails_sidecar_app) as client:
        response = await client.post("/guardrails/redact", json={"text": "hi"})
    assert response.status_code == 403
    assert response.json()["detail"] == "Invalid service token"


@pytest.mark.asyncio
async def test_guardrails_redact_wrong_token_returns_403(
    guardrails_sidecar_app: FastAPI,
) -> None:
    async with _async_client(
        guardrails_sidecar_app, headers={"X-Service-Token": "definitely-wrong"}
    ) as client:
        response = await client.post("/guardrails/redact", json={"text": "hi"})
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_guardrails_redact_with_correct_token_returns_200(
    guardrails_sidecar_app: FastAPI,
) -> None:
    async with _async_client(
        guardrails_sidecar_app, headers={"X-Service-Token": TEST_SERVICE_TOKEN}
    ) as client:
        response = await client.post("/guardrails/redact", json={"text": "hi"})
    assert response.status_code == 200
    assert "redacted_text" in response.json()


@pytest.mark.asyncio
async def test_model_server_predict_without_token_returns_403(
    model_server_app: FastAPI,
) -> None:
    async with _async_client(model_server_app) as client:
        response = await client.post(
            "/predict-intent",
            json={"message": "hi", "tenant_id": "00000000-0000-0000-0000-000000000000"},
        )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_model_server_predict_with_token_returns_200(
    model_server_app: FastAPI,
) -> None:
    async with _async_client(
        model_server_app, headers={"X-Service-Token": TEST_SERVICE_TOKEN}
    ) as client:
        response = await client.post(
            "/predict-intent",
            json={"message": "hi", "tenant_id": "00000000-0000-0000-0000-000000000000"},
        )
    assert response.status_code == 200
    payload = response.json()
    assert payload["label"] == "ambiguous"  # stub response — model not loaded


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "sidecar_fixture,path",
    [
        ("guardrails_sidecar_app", "/health"),
        ("model_server_app", "/health"),
    ],
)
async def test_health_remains_open(
    request: pytest.FixtureRequest, sidecar_fixture: str, path: str
) -> None:
    app = request.getfixturevalue(sidecar_fixture)
    async with _async_client(app) as client:
        response = await client.get(path)
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


_FASTAPI_BUILTIN_PATHS = {"/health", "/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect"}


@pytest.mark.asyncio
async def test_every_business_route_requires_token(
    guardrails_sidecar_app: FastAPI, model_server_app: FastAPI
) -> None:
    """SC-001: every non-/health business route returns 403 without a token.

    Iterates the OpenAPI route list so a future route added without the
    `require_service_token` dependency is caught by CI rather than discovered
    in production. Skips FastAPI utility paths (docs, openapi) and health.
    """
    failures: list[str] = []
    for app in (guardrails_sidecar_app, model_server_app):
        async with _async_client(app) as client:
            for route in app.routes:
                path = getattr(route, "path", None)
                methods = getattr(route, "methods", set()) or set()
                if not path or path in _FASTAPI_BUILTIN_PATHS:
                    continue
                for method in methods - {"HEAD", "OPTIONS"}:
                    response = await client.request(method, path, json={})
                    if response.status_code != 403:
                        failures.append(
                            f"{app.title} {method} {path} returned {response.status_code}"
                        )
    assert not failures, f"Routes missing service-auth dependency: {failures}"
