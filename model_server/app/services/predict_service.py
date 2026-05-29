"""Intent prediction service for `model_server` (spec 007 US1).

Pipes a raw visitor message through Cohere → trained classifier → routing
intent. Inference is CPU work, wrapped in `asyncio.to_thread` so concurrent
requests do not block the event loop.

The endpoint always serves from the model marked "deployed" in the loader
(currently the higher-F1 of the classical KNN and the ONNX FFN). The
non-deployed model stays loaded so the eval harness can call it without a
redeploy.
"""

from __future__ import annotations

import asyncio
import logging

import numpy as np

from app.core.model_loader import ModelLoader
from app.schemas import PredictResponse
from app.services.embedding_client import CohereEmbeddingClient, EmbeddingError

logger = logging.getLogger(__name__)


class PredictionError(RuntimeError):
    """Wrapped predict-pipeline failure surfaced as HTTP 503 by the route."""


async def predict_intent(
    message: str,
    loader: ModelLoader,
    embedder: CohereEmbeddingClient,
) -> PredictResponse:
    if not message or not message.strip():
        raise PredictionError("message is empty")

    try:
        embedding_list = await embedder.embed_query(message)
    except EmbeddingError as exc:
        raise PredictionError(f"embedding failed: {exc}") from exc
    except Exception as exc:
        # Defensive: any unexpected provider failure is a 503, not a 500.
        # The route maps PredictionError → 503; we never want a raw provider
        # traceback to leak through the API.
        raise PredictionError(f"embedding failed: {exc}") from exc

    embedding = np.asarray(embedding_list, dtype=np.float64)

    deployed = loader.deployed
    try:
        data_label, confidence = await asyncio.to_thread(deployed.predict, embedding)
    except Exception as exc:
        raise PredictionError(f"inference failed: {exc}") from exc

    routing_intent = loader.label_map.to_routing(data_label)
    if routing_intent not in loader.label_map.routing_intents:
        # Defensive: a future label_map edit that introduces a typo would
        # otherwise leak an unknown label into the router. Collapse to
        # `ambiguous` and log loudly.
        logger.error(
            "label_map produced unknown routing intent %r for data label %r; "
            "falling back to 'ambiguous'",
            routing_intent,
            data_label,
        )
        routing_intent = "ambiguous"

    return PredictResponse(
        label=routing_intent,
        confidence=float(confidence),
        model_version=f"{deployed.name}:{deployed.model_version}",
    )
