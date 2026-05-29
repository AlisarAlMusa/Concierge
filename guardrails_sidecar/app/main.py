"""guardrails_sidecar — input rails (semantic) + output rails (regex).

Lifespan loads the MiniLM ONNX embedder and builds a `RailsEngine` that
holds the platform-rail jailbreak corpus and the registered topic-similarity
action. Per-request paths reuse both via `app.state`.

All business routes require `X-Service-Token` (spec 018). `/health` stays
open for Docker.

Spec 010: see plan.md §1 for the architectural deviation from NeMo — we
implement the same input/output rails contract using our ONNX MiniLM
directly, because every available NeMo embedding provider either pulls
torch/transformers (Constitution V violation) or adds heavy native
dependencies. The behavioral contract for `POST /guardrails/check-input` is
unchanged.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Request

from app.actions import set_embedder
from app.core.config import get_settings
from app.core.rails_engine import HistoryTurn, RailsEngine
from app.core.redaction import redact
from app.core.security import require_service_token
from app.core.topic_similarity import build_embedder
from app.schemas import (
    CheckInputRequest,
    CheckInputResponse,
    CheckOutputRequest,
    CheckOutputResponse,
    RedactRequest,
    RedactResponse,
)

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    models_dir = Path(settings.MODELS_DIR)
    embedder = build_embedder(models_dir)
    set_embedder(embedder)
    app.state.embedder = embedder
    app.state.rails = RailsEngine.build(embedder)
    logger.info("guardrails_sidecar ready (models=%s)", models_dir)
    yield


app = FastAPI(title="Guardrails Sidecar", version="0.1.0", lifespan=lifespan)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.post(
    "/guardrails/check-input",
    response_model=CheckInputResponse,
    dependencies=[Depends(require_service_token)],
)
async def check_input(request: CheckInputRequest, http: Request) -> CheckInputResponse:
    rails: RailsEngine = http.app.state.rails
    history = [
        HistoryTurn(role=e.role, content=e.content)
        for e in request.conversation_history
    ]
    verdict = rails.evaluate_input(
        message=request.message,
        blocked_topics=request.tenant_config.blocked_topics,
        history=history,
    )
    return CheckInputResponse(
        allowed=verdict.allowed,
        reason=verdict.reason,
        safe_reply=verdict.safe_reply,
        # FR-019: redacted_text is always present, even on a blocked input,
        # so callers can log without re-redacting.
        redacted_text=redact(request.message),
    )


@app.post(
    "/guardrails/check-output",
    response_model=CheckOutputResponse,
    dependencies=[Depends(require_service_token)],
)
async def check_output(request: CheckOutputRequest) -> CheckOutputResponse:
    """Phase 1 output rails are regex-only (spec 010 FR-020).

    Semantic checks for "system prompt content in replies" / cross-tenant
    data leakage in replies are documented as a Phase-2 follow-up in
    plan.md `Open Gaps`. The visible-text field is always redacted.
    """
    return CheckOutputResponse(
        allowed=True,
        reason=None,
        redacted_text=redact(request.message),
    )


@app.post(
    "/guardrails/redact",
    response_model=RedactResponse,
    dependencies=[Depends(require_service_token)],
)
async def redact_route(request: RedactRequest) -> RedactResponse:
    return RedactResponse(redacted_text=redact(request.text))
