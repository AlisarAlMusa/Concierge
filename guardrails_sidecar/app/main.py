# guardrails_sidecar — Person C implements rails.py and redaction.py
# This shell provides health check and stub endpoints so docker-compose builds cleanly.
from fastapi import FastAPI, Header, HTTPException

from app.schemas import (
    CheckInputRequest,
    CheckInputResponse,
    CheckOutputRequest,
    CheckOutputResponse,
    RedactRequest,
    RedactResponse,
)

app = FastAPI(title="Guardrails Sidecar", version="0.1.0")


def _verify_service_token(token: str) -> None:
    import os

    expected = os.getenv("SERVICE_AUTH_SECRET", "")
    if not expected or token != expected:
        raise HTTPException(status_code=403, detail="Invalid service token")


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.post("/guardrails/check-input", response_model=CheckInputResponse)
async def check_input(
    request: CheckInputRequest,
    x_service_token: str = Header(..., alias="X-Service-Token"),
) -> CheckInputResponse:
    _verify_service_token(x_service_token)
    # TODO: Person C — call rails.check_input(request)
    return CheckInputResponse(allowed=True, redacted_text=request.message)


@app.post("/guardrails/check-output", response_model=CheckOutputResponse)
async def check_output(
    request: CheckOutputRequest,
    x_service_token: str = Header(..., alias="X-Service-Token"),
) -> CheckOutputResponse:
    _verify_service_token(x_service_token)
    # TODO: Person C — call rails.check_output(request)
    return CheckOutputResponse(allowed=True, redacted_text=request.message)


@app.post("/guardrails/redact", response_model=RedactResponse)
async def redact(
    request: RedactRequest,
    x_service_token: str = Header(..., alias="X-Service-Token"),
) -> RedactResponse:
    _verify_service_token(x_service_token)
    # TODO: Person C — call redaction.redact(request.text)
    return RedactResponse(redacted_text=request.text)
