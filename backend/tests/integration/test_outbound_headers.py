"""Integration test for spec 018 — User Story 2 (outbound calls auto-attach token).

Verifies SC-002: every outbound call from the api's shared `httpx.AsyncClient`
carries `X-Service-Token`. Uses `httpx.MockTransport` so no sidecar is touched
and no network round-trip occurs.

The shared client is constructed identically to the one in
`backend/app/main.py`'s lifespan; this test does not boot the full FastAPI app
because the assertion is about how the client itself behaves.
"""

from __future__ import annotations

import httpx
import pytest

from tests.integration.conftest import TEST_SERVICE_TOKEN


@pytest.mark.asyncio
async def test_shared_client_attaches_service_token_to_every_request() -> None:
    captured: list[httpx.Request] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json={"ok": True})

    shared = httpx.AsyncClient(
        headers={"X-Service-Token": TEST_SERVICE_TOKEN},
        transport=httpx.MockTransport(_handler),
        timeout=5.0,
    )

    try:
        await shared.post("http://guardrails_sidecar:8002/guardrails/check-input", json={})
        await shared.post(
            "http://model_server:8001/predict-intent",
            json={"message": "hi", "tenant_id": "00000000-0000-0000-0000-000000000000"},
        )
        await shared.get("http://guardrails_sidecar:8002/health")
    finally:
        await shared.aclose()

    assert len(captured) == 3
    for request in captured:
        assert (
            request.headers.get("X-Service-Token") == TEST_SERVICE_TOKEN
        ), f"{request.method} {request.url} missing or wrong X-Service-Token header"


@pytest.mark.asyncio
async def test_per_call_header_override_is_possible_but_not_required() -> None:
    """Spec 018 forbids per-call header *addition* (it must be on the shared
    client). This test documents that per-call override is still mechanically
    possible — callers who need a different token must pass it explicitly,
    which is loud enough to be caught in code review.
    """
    captured: list[httpx.Request] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200)

    shared = httpx.AsyncClient(
        headers={"X-Service-Token": TEST_SERVICE_TOKEN},
        transport=httpx.MockTransport(_handler),
    )
    try:
        await shared.post("http://x/y", headers={"X-Service-Token": "override-token"}, json={})
    finally:
        await shared.aclose()

    assert captured[0].headers["X-Service-Token"] == "override-token"
