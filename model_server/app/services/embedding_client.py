"""Cohere embedding client for `model_server`.

A minimal wrapper around `cohere.AsyncClient.embed` for one-shot
`POST /predict-intent` calls. Mirrors the architectural shape of
`backend/app/services/embedding_client.py` (same provider, same model) but
intentionally simpler — we never embed in batches here, only a single
visitor message at a time.

`input_type="search_query"` is the right choice for short user messages
(matches the document-vs-query distinction used during indexing).

Owner: Person C.
"""

from __future__ import annotations

import logging

from cohere import AsyncClient

logger = logging.getLogger(__name__)

_EMBEDDING_DIM = 1024


class EmbeddingError(RuntimeError):
    """Raised when the embedding provider fails or returns the wrong shape."""


class CohereEmbeddingClient:
    """Single-shot Cohere `embed-english-v3.0` client.

    The model is fixed at construction; swapping models requires a redeploy
    because the trained classifier's input dim is also fixed (1024).
    """

    def __init__(self, api_key: str, model: str = "embed-english-v3.0") -> None:
        if not api_key:
            raise EmbeddingError("COHERE_API_KEY is not set")
        self._client = AsyncClient(api_key=api_key)
        self._model = model

    async def embed_query(self, text: str) -> list[float]:
        """Embed a single visitor message as a query vector.

        Returns a 1024-dim list of floats. Raises `EmbeddingError` on any
        provider failure or dimension mismatch (silent model swap protection).
        """
        try:
            response = await self._client.embed(
                texts=[text],
                model=self._model,
                input_type="search_query",
                embedding_types=["float"],
                request_options={"max_retries": 0},
            )
        except Exception as exc:
            raise EmbeddingError(f"cohere embed failed: {exc}") from exc

        # cohere>=5 returns response.embeddings.float_ (list of list[float]).
        vectors = getattr(response.embeddings, "float_", None) or getattr(
            response.embeddings, "float", None
        )
        if not vectors or len(vectors) != 1:
            raise EmbeddingError("cohere returned no embedding")
        vector = vectors[0]
        if len(vector) != _EMBEDDING_DIM:
            raise EmbeddingError(
                f"cohere returned dim={len(vector)}, expected {_EMBEDDING_DIM} "
                "(model mismatch with trained classifier)"
            )
        return list(vector)

    async def aclose(self) -> None:
        # cohere.AsyncClient owns an httpx client; closing is best-effort.
        close = getattr(self._client, "close", None)
        if close is not None:
            try:
                await close()
            except Exception:
                logger.debug("cohere close() raised; ignoring", exc_info=True)
