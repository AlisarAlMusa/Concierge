"""CohereEmbeddingClient — Cohere embed-english-v3.0 implementation.

The single, provider-isolated implementation of the embedding boundary used by
``RagService``. This module is the ONLY place in the backend that imports the
``cohere`` SDK or speaks Cohere's wire format. Swapping providers means
replacing this file. ``RagService`` does not change.

Architecture invariants (frozen, see ``specs/embedding-service/spec.md``):

* Two methods only. ``input_type`` is fixed by the method name so a query
  cannot accidentally be embedded as a document or vice-versa.
* SDK-level retries disabled by passing ``request_options={"max_retries": 0}``
  on every call. Our retry loop is the single source of truth.
* SDK auto-batching disabled by passing ``batching=False``. We own the batch
  split so behavior is observable in tests.
* Output dimension fixed at 1024 by the model. ``_extract_embeddings``
  asserts it and raises ``ValueError`` if the provider returns anything else
  (silent model swap protection).

Owner: Person B.
"""

from __future__ import annotations

import asyncio
from typing import Any, Literal

import httpx
import structlog
from cohere import AsyncClient
from cohere.core import ApiError
from cohere.errors import (
    BadRequestError,
    ClientClosedRequestError,
    ForbiddenError,
    GatewayTimeoutError,
    InternalServerError,
    NotFoundError,
    ServiceUnavailableError,
    TooManyRequestsError,
    UnauthorizedError,
    UnprocessableEntityError,
)

from app.core.errors import ExternalServiceError

logger = structlog.get_logger(__name__)

# ----- module constants (per spec §8) ----------------------------------------
DEFAULT_MODEL = "embed-english-v3.0"
EMBEDDING_DIM = 1024
COHERE_BATCH_LIMIT = 96
DEFAULT_TIMEOUT_SECONDS = 30.0

# Retryable status errors (429 + 5xx + 499).
_RETRYABLE_STATUS_ERRORS: tuple[type[Exception], ...] = (
    TooManyRequestsError,
    InternalServerError,
    ServiceUnavailableError,
    GatewayTimeoutError,
    ClientClosedRequestError,
)

# Retryable network errors raised by the underlying httpx client.
_RETRYABLE_NETWORK_ERRORS: tuple[type[Exception], ...] = (
    httpx.ConnectError,
    httpx.ReadError,
    httpx.WriteError,
    httpx.TimeoutException,
)

# Non-retryable client errors (deterministic outcomes).
_NON_RETRYABLE_CLIENT_ERRORS: tuple[type[Exception], ...] = (
    BadRequestError,
    UnauthorizedError,
    ForbiddenError,
    NotFoundError,
    UnprocessableEntityError,
)


class CohereEmbeddingClient:
    """Bounded, retrying Cohere embed-english-v3.0 client.

    Why a class (not free functions): the retry budget, model id, and the
    underlying ``AsyncClient`` are configuration that the caller (DI later,
    tests now) owns once per app instance.
    """

    def __init__(
        self,
        *,
        client: AsyncClient,
        model: str = DEFAULT_MODEL,
        batch_size: int = COHERE_BATCH_LIMIT,
        max_attempts: int = 3,
        backoff_base_seconds: float = 0.5,
        backoff_max_seconds: float = 5.0,
    ) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")
        if backoff_base_seconds < 0 or backoff_max_seconds < 0:
            raise ValueError("backoff seconds must be >= 0")
        if not 1 <= batch_size <= COHERE_BATCH_LIMIT:
            raise ValueError(f"batch_size must be in [1, {COHERE_BATCH_LIMIT}]")
        self._client = client
        self._model = model
        self._batch_size = batch_size
        self._max_attempts = max_attempts
        self._backoff_base = backoff_base_seconds
        self._backoff_max = backoff_max_seconds

    @classmethod
    def from_api_key(
        cls,
        *,
        api_key: str,
        model: str = DEFAULT_MODEL,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        batch_size: int = COHERE_BATCH_LIMIT,
        max_attempts: int = 3,
        backoff_base_seconds: float = 0.5,
        backoff_max_seconds: float = 5.0,
    ) -> CohereEmbeddingClient:
        """Convenience factory used by DI when wiring lands.

        The cohere SDK does not expose ``max_retries`` at the client level; we
        disable retries per-request through ``RequestOptions`` instead. See
        ``_embed_one_batch``.
        """
        client = AsyncClient(api_key=api_key, timeout=timeout_seconds)
        return cls(
            client=client,
            model=model,
            batch_size=batch_size,
            max_attempts=max_attempts,
            backoff_base_seconds=backoff_base_seconds,
            backoff_max_seconds=backoff_max_seconds,
        )

    # ----- public API --------------------------------------------------------
    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Embed CMS chunk texts. Always uses ``input_type='search_document'``.

        Empty input is a no-op: returns ``[]`` and makes zero API calls.
        Output order matches input order across batches.
        """
        if not texts:
            return []
        return await self._embed_batched(texts, input_type="search_document")

    async def embed_query(self, text: str) -> list[float]:
        """Embed a single search query. Always uses ``input_type='search_query'``.

        Empty input is a programming error (``ValueError``) — callers must
        guard at the use site (``RagService.search`` short-circuits before
        calling).
        """
        if not text:
            raise ValueError("embed_query requires non-empty text")
        result = await self._embed_one_batch([text], input_type="search_query")
        return result[0]

    # ----- internals ---------------------------------------------------------
    async def _embed_batched(
        self,
        texts: list[str],
        *,
        input_type: Literal["search_document", "search_query"],
    ) -> list[list[float]]:
        """Sequentially issue ``ceil(len(texts) / batch_size)`` Cohere calls."""
        out: list[list[float]] = []
        for i in range(0, len(texts), self._batch_size):
            batch = texts[i : i + self._batch_size]
            out.extend(await self._embed_one_batch(batch, input_type=input_type))
        return out

    async def _embed_one_batch(
        self,
        batch: list[str],
        *,
        input_type: Literal["search_document", "search_query"],
    ) -> list[list[float]]:
        """One Cohere call with bounded retries.

        Retry policy mirrors ``GroqLLMClient``: retry on network failures, 429,
        and 5xx; surface non-retryable client errors immediately.
        """
        last_exc: BaseException | None = None
        for attempt in range(self._max_attempts):
            try:
                response = await self._client.embed(
                    texts=batch,
                    model=self._model,
                    input_type=input_type,
                    embedding_types=["float"],
                    batching=False,
                    request_options={"max_retries": 0},
                )
            except _NON_RETRYABLE_CLIENT_ERRORS as exc:
                logger.error(
                    "embedding.cohere.client_error",
                    error=str(exc),
                    error_type=type(exc).__name__,
                )
                raise ExternalServiceError(
                    service="cohere",
                    reason=f"{type(exc).__name__}: {exc}",
                ) from exc
            except _RETRYABLE_STATUS_ERRORS as exc:
                last_exc = exc
            except _RETRYABLE_NETWORK_ERRORS as exc:
                last_exc = exc
            except ApiError as exc:
                # Other unclassified Cohere SDK errors. Don't retry; surface.
                logger.error(
                    "embedding.cohere.api_error",
                    error=str(exc),
                    error_type=type(exc).__name__,
                )
                raise ExternalServiceError(service="cohere", reason=str(exc)) from exc
            except Exception as exc:
                logger.exception("embedding.cohere.unexpected_error")
                raise ExternalServiceError(service="cohere", reason=str(exc)) from exc
            else:
                # Success path. Parsing errors (wrong dimension, unknown shape)
                # are programming/contract violations — they propagate as
                # ValueError, not as a retryable transport failure.
                return self._extract_embeddings(response)

            if attempt + 1 < self._max_attempts:
                delay = min(self._backoff_base * (2**attempt), self._backoff_max)
                logger.warning(
                    "embedding.cohere.retry",
                    attempt=attempt + 1,
                    max_attempts=self._max_attempts,
                    delay_seconds=delay,
                    error=str(last_exc),
                )
                if delay > 0:
                    await asyncio.sleep(delay)

        logger.error(
            "embedding.cohere.retries_exhausted",
            attempts=self._max_attempts,
            error=str(last_exc),
        )
        raise ExternalServiceError(
            service="cohere",
            reason=f"max retries ({self._max_attempts}) exhausted: {last_exc}",
        ) from last_exc

    def _extract_embeddings(self, response: Any) -> list[list[float]]:
        """Normalize the Cohere response into ``list[list[float]]``.

        With ``embedding_types=['float']`` Cohere returns an
        ``EmbeddingsByTypeEmbedResponse`` whose ``embeddings`` field has a
        ``float_`` attribute (trailing underscore — ``float`` is reserved).
        ``EmbeddingsFloatsEmbedResponse`` (legacy shape) carries a plain list.
        We support both for forward compatibility.
        """
        raw = response.embeddings
        if hasattr(raw, "float_"):
            embeddings: list[list[float]] = raw.float_
        elif hasattr(raw, "float"):  # pragma: no cover — defensive
            embeddings = raw.float
        elif isinstance(raw, list):
            embeddings = raw
        else:
            raise ValueError(f"unexpected Cohere embeddings shape: {type(raw).__name__}")

        for i, vec in enumerate(embeddings):
            if len(vec) != EMBEDDING_DIM:
                raise ValueError(
                    f"unexpected embedding dimension {len(vec)} at index {i}; "
                    f"expected {EMBEDDING_DIM} (model={self._model})"
                )
        return embeddings
