# model_server — Person C implements model_loader.py and predict_service.py
# This shell provides health check so docker-compose builds cleanly.
from fastapi import FastAPI

from app.schemas import PredictRequest, PredictResponse

app = FastAPI(title="Model Server", version="0.1.0")


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.post("/predict-intent", response_model=PredictResponse)
async def predict_intent(request: PredictRequest) -> PredictResponse:
    # TODO: Person C — call predict_service.predict(request.message)
    return PredictResponse(
        label="ambiguous",
        confidence=0.0,
        model_version="stub-v0",
    )
