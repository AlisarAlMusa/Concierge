"""Unit tests for CohereEmbeddingClient.

Mocked Cohere ``AsyncClient.embed`` only. No network. Validates the provider
boundary, the retry policy, the batching split, and the dimension contract
from ``specs/embedding-service/spec.md`` without touching live infrastructure.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from cohere.errors import (
    BadRequestError,
    InternalServerError,
    ServiceUnavailableError,
    TooManyRequestsError,
    UnauthorizedError,
)

from app.core.errors import ExternalServiceError
from app.services.embedding_client import (
    COHERE_BATCH_LIMIT,
    DEFAULT_MODEL,
    EMBEDDING_DIM,
    CohereEmbeddingClient,
)


# ----- Minimal Cohere response surface --------------------------------------
class _FakeFloats:
    """Mimics cohere.types.embed_by_type_response_embeddings.EmbedByTypeResponseEmbeddings."""

    def __init__(self, vectors: list[list[float]]) -> None:
        # Real SDK uses the attribute name `float_` (trailing underscore).
        self.float_ = vectors


class _FakeEmbedResponse:
    """Mimics cohere.types.embed_response.EmbeddingsByTypeEmbedResponse."""

    def __init__(self, vectors: list[list[float]]) -> None:
        self.embeddings = _FakeFloats(vectors)


# ----- Fake AsyncClient ------------------------------------------------------
class _FakeAsyncCohere:
    """Replaces cohere.AsyncClient. Tracks every embed(...) call."""

    def __init__(self, responses: list[Any]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    async def embed(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if not self._responses:
            raise AssertionError("FakeAsyncCohere ran out of queued responses")
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


# ----- Helpers --------------------------------------------------------------
def _vec(value: float = 0.1) -> list[float]:
    """Build a 1024-dim vector full of ``value``."""
    return [value] * EMBEDDING_DIM


def _response(*vectors: list[float]) -> _FakeEmbedResponse:
    return _FakeEmbedResponse(list(vectors))


def _client(
    responses: list[Any],
    *,
    batch_size: int = COHERE_BATCH_LIMIT,
    max_attempts: int = 3,
) -> tuple[CohereEmbeddingClient, _FakeAsyncCohere]:
    fake = _FakeAsyncCohere(responses)
    client = CohereEmbeddingClient(
        client=fake,  # type: ignore[arg-type]
        model=DEFAULT_MODEL,
        batch_size=batch_size,
        max_attempts=max_attempts,
        backoff_base_seconds=0.0,  # no sleeping in tests
        backoff_max_seconds=0.0,
    )
    return client, fake


# ----- Documents path -------------------------------------------------------
async def test_embed_documents_returns_one_vector_per_input():
    client, _ = _client([_response(_vec(0.1), _vec(0.2), _vec(0.3))])

    result = await client.embed_documents(["a", "b", "c"])

    assert len(result) == 3
    assert all(len(v) == EMBEDDING_DIM for v in result)


async def test_embed_documents_empty_input_makes_zero_api_calls():
    client, fake = _client([])

    result = await client.embed_documents([])

    assert result == []
    assert fake.calls == []


async def test_documents_use_search_document_input_type():
    client, fake = _client([_response(_vec())])

    await client.embed_documents(["hello"])

    sent = fake.calls[0]
    assert sent["input_type"] == "search_document"
    assert sent["model"] == DEFAULT_MODEL
    assert sent["embedding_types"] == ["float"]


# ----- Query path -----------------------------------------------------------
async def test_embed_query_returns_single_vector():
    client, _ = _client([_response(_vec(0.5))])

    result = await client.embed_query("what are your hours")

    assert isinstance(result, list)
    assert len(result) == EMBEDDING_DIM


async def test_embed_query_empty_string_raises_value_error():
    client, fake = _client([])

    with pytest.raises(ValueError):
        await client.embed_query("")

    assert fake.calls == []


async def test_query_uses_search_query_input_type():
    client, fake = _client([_response(_vec())])

    await client.embed_query("hello")

    sent = fake.calls[0]
    assert sent["input_type"] == "search_query"
    assert sent["model"] == DEFAULT_MODEL


# ----- SDK lever assertions -------------------------------------------------
def _sent_request_options(call: dict[str, Any]) -> dict[str, Any]:
    """request_options is a TypedDict — Cohere accepts it as a plain dict."""
    return call.get("request_options") or {}


async def test_sdk_retries_disabled_via_request_options():
    """We pass max_retries=0 on every embed call — our loop is the single source of truth."""
    client, fake = _client([_response(_vec())])

    await client.embed_query("hi")

    opts = _sent_request_options(fake.calls[0])
    assert opts.get("max_retries") == 0


async def test_sdk_auto_batching_disabled():
    """We pass batching=False on every embed call — we own batching ourselves."""
    client, fake = _client([_response(_vec())])

    await client.embed_query("hi")

    assert fake.calls[0]["batching"] is False


# ----- Batching -------------------------------------------------------------
async def test_batching_splits_at_batch_limit():
    """200 inputs with batch_size=96 → 3 calls of 96, 96, 8."""
    responses = [
        _FakeEmbedResponse([_vec(i / 1000) for i in range(96)]),
        _FakeEmbedResponse([_vec(i / 1000) for i in range(96, 192)]),
        _FakeEmbedResponse([_vec(i / 1000) for i in range(192, 200)]),
    ]
    client, fake = _client(responses, batch_size=96)

    result = await client.embed_documents([f"t{i}" for i in range(200)])

    assert len(result) == 200
    assert len(fake.calls) == 3
    assert len(fake.calls[0]["texts"]) == 96
    assert len(fake.calls[1]["texts"]) == 96
    assert len(fake.calls[2]["texts"]) == 8


async def test_batching_preserves_order_across_calls():
    """The output order matches the input order even when split across batches."""
    # 2 batches of size 2 → 4 total inputs.
    responses = [
        _FakeEmbedResponse([_vec(0.1), _vec(0.2)]),
        _FakeEmbedResponse([_vec(0.3), _vec(0.4)]),
    ]
    client, _ = _client(responses, batch_size=2)

    result = await client.embed_documents(["a", "b", "c", "d"])

    assert result[0][0] == 0.1
    assert result[1][0] == 0.2
    assert result[2][0] == 0.3
    assert result[3][0] == 0.4


async def test_smaller_input_uses_single_batch():
    """With batch_size=96, a list of 5 produces exactly one API call."""
    client, fake = _client([_FakeEmbedResponse([_vec(0.1)] * 5)], batch_size=96)

    await client.embed_documents(["a", "b", "c", "d", "e"])

    assert len(fake.calls) == 1
    assert len(fake.calls[0]["texts"]) == 5


# ----- Retry policy ---------------------------------------------------------
def _too_many_requests() -> TooManyRequestsError:
    return TooManyRequestsError(body="rate limited")


def _internal_server() -> InternalServerError:
    return InternalServerError(body="boom")


def _service_unavailable() -> ServiceUnavailableError:
    return ServiceUnavailableError(body="overloaded")


def _bad_request() -> BadRequestError:
    return BadRequestError(body="malformed")


def _unauthorized() -> UnauthorizedError:
    return UnauthorizedError(body="bad key")


def _connect_error() -> httpx.ConnectError:
    return httpx.ConnectError("dns failed")


async def test_retry_on_rate_limit_then_success():
    client, fake = _client(
        [_too_many_requests(), _response(_vec())],
        max_attempts=3,
    )

    result = await client.embed_query("hi")

    assert len(result) == EMBEDDING_DIM
    assert len(fake.calls) == 2


async def test_retry_on_5xx_then_success():
    client, fake = _client(
        [_internal_server(), _response(_vec())],
        max_attempts=3,
    )

    result = await client.embed_query("hi")

    assert len(result) == EMBEDDING_DIM
    assert len(fake.calls) == 2


async def test_retry_on_service_unavailable_then_success():
    client, fake = _client(
        [_service_unavailable(), _response(_vec())],
        max_attempts=3,
    )

    result = await client.embed_query("hi")

    assert len(result) == EMBEDDING_DIM
    assert len(fake.calls) == 2


async def test_retry_on_network_error_then_success():
    client, fake = _client(
        [_connect_error(), _response(_vec())],
        max_attempts=3,
    )

    result = await client.embed_query("hi")

    assert len(result) == EMBEDDING_DIM
    assert len(fake.calls) == 2


async def test_max_attempts_exhausted_raises_external_service_error():
    client, fake = _client(
        [_connect_error(), _too_many_requests(), _internal_server()],
        max_attempts=3,
    )

    with pytest.raises(ExternalServiceError) as excinfo:
        await client.embed_query("hi")

    assert "max retries" in str(excinfo.value)
    assert len(fake.calls) == 3


# ----- Non-retryable errors -------------------------------------------------
async def test_400_not_retried():
    client, fake = _client([_bad_request(), _response(_vec())], max_attempts=3)

    with pytest.raises(ExternalServiceError) as excinfo:
        await client.embed_query("hi")

    assert "BadRequestError" in str(excinfo.value)
    assert len(fake.calls) == 1


async def test_401_not_retried():
    """Auth failures must not burn through the retry budget."""
    client, fake = _client([_unauthorized()], max_attempts=3)

    with pytest.raises(ExternalServiceError):
        await client.embed_query("hi")

    assert len(fake.calls) == 1


async def test_unexpected_exception_translated():
    """Any unknown failure mode is wrapped so RagService's contract holds."""
    client, fake = _client([RuntimeError("boom")], max_attempts=3)

    with pytest.raises(ExternalServiceError) as excinfo:
        await client.embed_query("hi")

    assert "boom" in str(excinfo.value)
    assert len(fake.calls) == 1


# ----- Dimension validation -------------------------------------------------
async def test_dimension_mismatch_raises_value_error():
    """Defensive guard against silent model swaps — a non-1024 vector is fatal."""
    wrong_dim_response = _FakeEmbedResponse([[0.1] * 512])  # 512 != 1024
    client, _ = _client([wrong_dim_response])

    with pytest.raises(ValueError) as excinfo:
        await client.embed_query("hi")

    assert "1024" in str(excinfo.value)


async def test_dimension_mismatch_in_middle_of_batch_raises():
    """One bad vector in a batch fails the entire batch."""
    mixed = _FakeEmbedResponse([_vec(0.1), [0.0] * 768, _vec(0.3)])
    client, _ = _client([mixed])

    with pytest.raises(ValueError) as excinfo:
        await client.embed_documents(["a", "b", "c"])

    assert "index 1" in str(excinfo.value)


# ----- Constructor validation ----------------------------------------------
def test_invalid_max_attempts_raises():
    fake = _FakeAsyncCohere([])
    with pytest.raises(ValueError):
        CohereEmbeddingClient(client=fake, max_attempts=0)  # type: ignore[arg-type]


def test_invalid_backoff_raises():
    fake = _FakeAsyncCohere([])
    with pytest.raises(ValueError):
        CohereEmbeddingClient(client=fake, backoff_base_seconds=-0.1)  # type: ignore[arg-type]


def test_invalid_batch_size_too_small_raises():
    fake = _FakeAsyncCohere([])
    with pytest.raises(ValueError):
        CohereEmbeddingClient(client=fake, batch_size=0)  # type: ignore[arg-type]


def test_invalid_batch_size_too_large_raises():
    fake = _FakeAsyncCohere([])
    with pytest.raises(ValueError):
        CohereEmbeddingClient(client=fake, batch_size=COHERE_BATCH_LIMIT + 1)  # type: ignore[arg-type]


# ----- Sanity ---------------------------------------------------------------
async def test_fake_client_is_deterministic():
    """The fake returns queued responses in order on repeated runs — anchors test reliability."""
    response = _response(_vec(0.42))
    client_a, _ = _client([response])
    client_b, _ = _client([_response(_vec(0.42))])

    out_a = await client_a.embed_query("x")
    out_b = await client_b.embed_query("x")

    assert out_a == out_b


def test_module_constants_match_spec():
    """Spec §1 / §6.3: model = embed-english-v3.0; dimension = 1024; batch limit = 96."""
    assert DEFAULT_MODEL == "embed-english-v3.0"
    assert EMBEDDING_DIM == 1024
    assert COHERE_BATCH_LIMIT == 96
