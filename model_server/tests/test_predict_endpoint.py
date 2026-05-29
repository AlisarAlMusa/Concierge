"""End-to-end tests for `POST /predict-intent` (spec 007 US1).

Boots the real `model_server` FastAPI app via `ASGITransport`. Cohere is
swapped out at `app.state.embedder` with a deterministic fake — no API key
is exercised — so these tests run anywhere with the artifacts present.
"""

from __future__ import annotations

from typing import AsyncIterator

import httpx
import numpy as np
import pytest
from fastapi import FastAPI

from app.main import app as real_app
import os  # noqa: E402

# Read at request time — see conftest. The autouse fixture sets this in os.environ.
def _token() -> str:
    return os.environ["SERVICE_AUTH_SECRET"]


class _FakeEmbedder:
    """Returns a deterministic vector. The trained classifier's prediction
    on an all-zero or all-rng vector is irrelevant for the auth/contract
    tests; only the *shape* of the response matters here."""

    def __init__(self, vector: list[float] | None = None) -> None:
        rng = np.random.default_rng(0)
        self._vector = vector or rng.normal(size=1024).astype(np.float64).tolist()

    async def embed_query(self, text: str) -> list[float]:
        return list(self._vector)

    async def aclose(self) -> None:
        return None


@pytest.fixture()
async def client() -> AsyncIterator[httpx.AsyncClient]:
    """Spin up the real app with its lifespan, then swap the embedder.

    httpx 0.28's ASGITransport does NOT trigger lifespan automatically — we
    drive it manually via the Starlette router's lifespan context manager so
    `app.state.loader` and `app.state.embedder` are populated as in production.
    """
    async with real_app.router.lifespan_context(real_app):
        # Lifespan has finished its setup phase; swap the embedder for tests.
        real_app.state.embedder = _FakeEmbedder()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=real_app),
            base_url="http://testserver",
        ) as _client:
            yield _client


def _ms_app() -> FastAPI:
    return real_app


@pytest.mark.asyncio
async def test_health_is_open(client: httpx.AsyncClient) -> None:
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_predict_without_token_returns_403(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/predict-intent",
        json={"message": "hi", "tenant_id": "00000000-0000-0000-0000-000000000000"},
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_predict_with_token_returns_routing_intent(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/predict-intent",
        json={"message": "what is your refund policy", "tenant_id": "00000000-0000-0000-0000-000000000000"},
        headers={"X-Service-Token": _token()},
    )
    assert response.status_code == 200, response.text
    body = response.json()

    loader = real_app.state.loader
    assert body["label"] in loader.label_map.routing_intents
    assert 0.0 <= body["confidence"] <= 1.0
    # FR-016: model_version must always be non-empty.
    assert body["model_version"].startswith(("classical:", "onnx:"))
    assert ":" in body["model_version"]
    assert body["model_version"].split(":", 1)[1]  # the suffix is non-empty


@pytest.mark.asyncio
async def test_predict_with_empty_message_returns_503(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/predict-intent",
        json={"message": "   ", "tenant_id": "00000000-0000-0000-0000-000000000000"},
        headers={"X-Service-Token": _token()},
    )
    assert response.status_code == 503


@pytest.mark.asyncio
async def test_predict_with_missing_message_returns_422(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/predict-intent",
        json={"tenant_id": "00000000-0000-0000-0000-000000000000"},
        headers={"X-Service-Token": _token()},
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_predict_with_embedding_failure_returns_503(client: httpx.AsyncClient) -> None:
    class _BrokenEmbedder:
        async def embed_query(self, text: str) -> list[float]:
            raise RuntimeError("cohere down")

        async def aclose(self) -> None:
            return None

    real_app.state.embedder = _BrokenEmbedder()
    try:
        response = await client.post(
            "/predict-intent",
            json={"message": "hi", "tenant_id": "00000000-0000-0000-0000-000000000000"},
            headers={"X-Service-Token": _token()},
        )
    finally:
        real_app.state.embedder = _FakeEmbedder()

    assert response.status_code == 503
