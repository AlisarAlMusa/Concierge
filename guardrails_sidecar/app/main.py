"""guardrails_sidecar entry point.

Person C implements `rails.py` and `redaction.py`. This shell provides health
and stub endpoints so docker-compose builds cleanly. Service-to-service auth
is enforced via the shared `require_service_token` dependency (spec 018).
"""

from fastapi import Depends, FastAPI

from app.core.security import require_service_token
from app.schemas import (
    CheckInputRequest,
    CheckInputResponse,
    CheckOutputRequest,
    CheckOutputResponse,
    RedactRequest,
    RedactResponse,
)

app = FastAPI(title="Guardrails Sidecar", version="0.1.0")


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.post(
    "/guardrails/check-input",
    response_model=CheckInputResponse,
    dependencies=[Depends(require_service_token)],
)
async def check_input(request: CheckInputRequest) -> CheckInputResponse:
    # TODO: Person C — call rails.check_input(request)
    return CheckInputResponse(allowed=True, redacted_text=request.message)


@app.post(
    "/guardrails/check-output",
    response_model=CheckOutputResponse,
    dependencies=[Depends(require_service_token)],
)
async def check_output(request: CheckOutputRequest) -> CheckOutputResponse:
    # TODO: Person C — call rails.check_output(request)
    return CheckOutputResponse(allowed=True, redacted_text=request.message)


@app.post(
    "/guardrails/redact",
    response_model=RedactResponse,
    dependencies=[Depends(require_service_token)],
)
async def redact(request: RedactRequest) -> RedactResponse:
    # TODO: Person C — call redaction.redact(request.text)
    return RedactResponse(redacted_text=request.text)
