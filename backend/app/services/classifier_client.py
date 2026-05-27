"""Classifier client — adapter for ``model_server`` POST /predict-intent.

This module is the boundary that ``RouterService`` reaches the intent
classifier through. ``RouterService`` is duck-typed against the
``ClassifierClient`` protocol (see ``router_service.py``); this file provides
the concrete implementations DI will inject.

Two implementations are exposed:

* ``UnavailableClassifierClient`` — used today. Every call raises
  ``ExternalServiceError``, which ``RouterService`` translates into a
  fail-open ``RouteDecision(path="agent", reason="classifier_unavailable")``.
  This is the correct behavior while ``model_server`` is not yet running:
  user traffic flows to the bounded agent, no message is dropped.

* ``HttpClassifierClient`` — the real implementation against
  ``model_server`` per ``docs/SPEC.md §4``. Activated once ``model_server`` is
  reachable and ``LLM_PROVIDER``/MODEL_SERVER_URL settings point at it. Lives
  in this same file so swapping is a single-line DI change.

Owner: Person B.
"""

from __future__ import annotations

import httpx
import structlog

from app.core.errors import ExternalServiceError
from app.services.router_service import ClassifierResponse

logger = structlog.get_logger(__name__)

_PREDICT_INTENT_PATH = "/predict-intent"


class UnavailableClassifierClient:
    """Always raises ``ExternalServiceError`` so RouterService fails open.

    Use until ``model_server`` is wired. The fail-open posture is intentional —
    a missing classifier must not drop user messages.
    """

    async def classify(self, *, text: str) -> ClassifierResponse:
        raise ExternalServiceError(
            service="model_server",
            reason="classifier_unavailable (model_server not configured)",
        )


class HttpClassifierClient:
    """HTTP adapter for ``model_server`` POST /predict-intent.

    Uses the shared ``X-Service-Token`` header (SPEC §7). Errors are translated
    to ``ExternalServiceError`` so RouterService's fail-open path activates on
    any HTTP, transport, or schema failure — never a 5xx leaking into the
    chat route.
    """

    def __init__(
        self,
        *,
        base_url: str,
        service_token: str,
        timeout_seconds: float = 5.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._service_token = service_token
        self._timeout = timeout_seconds

    async def classify(self, *, text: str) -> ClassifierResponse:
        url = f"{self._base_url}{_PREDICT_INTENT_PATH}"
        headers = {"X-Service-Token": self._service_token}
        body = {"message": text}

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(url, headers=headers, json=body)
                response.raise_for_status()
                data = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning(
                "classifier.http_error",
                error=str(exc),
                error_type=type(exc).__name__,
            )
            raise ExternalServiceError(
                service="model_server",
                reason=f"{type(exc).__name__}: {exc}",
            ) from exc

        try:
            return ClassifierResponse(label=data["label"], confidence=data["confidence"])
        except (KeyError, TypeError) as exc:
            logger.warning(
                "classifier.bad_shape",
                error=str(exc),
                error_type=type(exc).__name__,
            )
            raise ExternalServiceError(
                service="model_server",
                reason=f"unexpected response shape: {exc}",
            ) from exc
