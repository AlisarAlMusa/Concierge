"""model_server entry point.

Loads both classical (joblib KNN) and ONNX FFN artifacts at lifespan, picks
the higher-F1 one as "deployed", and serves `POST /predict-intent` from it.
The Cohere embedder is constructed once and reused per request.

Service-to-service auth is enforced via the shared `require_service_token`
dependency (spec 018). `/health` stays open for Docker healthchecks.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException

from app.core.config import get_settings
from app.core.model_loader import ModelLoader, load_all
from app.core.security import require_service_token
from app.dependencies import get_embedder, get_loader
from app.schemas import PredictRequest, PredictResponse
from app.services.embedding_client import CohereEmbeddingClient
from app.services.predict_service import PredictionError, predict_intent

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    artifacts_dir = Path(settings.ARTIFACTS_DIR)
    app.state.loader = load_all(artifacts_dir)
    app.state.embedder = CohereEmbeddingClient(
        api_key=settings.COHERE_API_KEY,
        model=settings.EMBEDDING_MODEL,
    )
    logger.info(
        "model_server ready (artifacts=%s deployed=%s)",
        artifacts_dir,
        app.state.loader.deployed_name,
    )
    yield
    await app.state.embedder.aclose()


app = FastAPI(title="Model Server", version="0.1.0", lifespan=lifespan)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.post(
    "/predict-intent",
    response_model=PredictResponse,
    dependencies=[Depends(require_service_token)],
)
async def predict_intent_route(
    request: PredictRequest,
    loader: ModelLoader = Depends(get_loader),
    embedder: CohereEmbeddingClient = Depends(get_embedder),
) -> PredictResponse:
    try:
        return await predict_intent(request.message, loader, embedder)
    except PredictionError as exc:
        # SPEC.md §9 / §4: upstream provider failures → 503 with the error
        # envelope. The router treats this as "ambiguous" and escalates.
        raise HTTPException(status_code=503, detail=str(exc)) from exc
