"""HTTP client for the guardrails sidecar (spec 010 FR-024 / FR-025).

Reads the tenant's `guardrails_config` from Postgres, reads the recent
short-term history from `MemoryService`, attaches `X-Service-Token` via
the lifespan-shared `httpx.AsyncClient` (spec 018), and POSTs to the
sidecar with a 2-second timeout and a single retry on connect-error.

Fail policy is fail-closed by default (spec 010 Edge Cases). Flipping to
fail-open requires `GUARDRAILS_FAIL_OPEN=true` AND a recorded entry in
`docs/DECISIONS.md`.

Wired into `ChatOrchestrator` by `dependencies.py` replacing
`PassthroughGuardrailClient`. The Protocol surface in
`chat_orchestrator.py` is unchanged — this is a single-line DI swap.
"""

from __future__ import annotations

import logging
import os
from typing import Any
from uuid import UUID

import httpx
from opentelemetry import trace
from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories import tenant_repository
from app.services.chat_orchestrator import GuardrailDecision
from app.services.memory_service import MemoryService

logger = logging.getLogger(__name__)
_tracer = trace.get_tracer(__name__)


def _record_guardrails(span, decision: GuardrailDecision) -> None:
    """Attach `guardrails.allowed` / `guardrails.reason` to the span.

    Spec 017 FR-019. Kept as a helper so both check_input and check_output
    use identical attribute names.
    """
    span.set_attribute("guardrails.allowed", bool(decision.allowed))
    if decision.reason:
        span.set_attribute("guardrails.reason", str(decision.reason))

DEFAULT_TIMEOUT_SECONDS = 2.0
DEFAULT_HISTORY_TURNS = 6


def _fail_open() -> bool:
    return os.environ.get("GUARDRAILS_FAIL_OPEN", "").lower() in {"1", "true", "yes"}


def _history_turns() -> int:
    raw = os.environ.get("GUARDRAILS_HISTORY_TURNS", "")
    if not raw:
        return DEFAULT_HISTORY_TURNS
    try:
        return max(0, int(raw))
    except ValueError:
        return DEFAULT_HISTORY_TURNS


def _fail_closed_decision(reason: str, original_text: str) -> GuardrailDecision:
    return GuardrailDecision(
        allowed=False,
        redacted_text=original_text,
        safe_reply="I'm not able to help with that right now. Please try again shortly.",
        reason=reason,
    )


def _fail_open_decision(reason: str, original_text: str) -> GuardrailDecision:
    logger.warning(
        "GuardrailService fail-open: reason=%s — review docs/DECISIONS.md",
        reason,
    )
    return GuardrailDecision(allowed=True, redacted_text=original_text, reason=reason)


class GuardrailService:
    """Real implementation of the Protocol declared in chat_orchestrator.py."""

    def __init__(
        self,
        *,
        http: httpx.AsyncClient,
        sidecar_base_url: str,
        session: AsyncSession,
        memory: MemoryService,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._http = http
        self._base_url = sidecar_base_url.rstrip("/")
        self._session = session
        self._memory = memory
        self._timeout = timeout_seconds

    async def _post(self, path: str, json: dict[str, Any]) -> dict[str, Any]:
        url = f"{self._base_url}{path}"
        for attempt in range(2):  # 1 retry on connect-error
            try:
                response = await self._http.post(
                    url, json=json, timeout=self._timeout
                )
                response.raise_for_status()
                return response.json()
            except httpx.ConnectError as exc:
                if attempt == 0:
                    logger.warning(
                        "guardrails sidecar connect-error, retrying once: %s", exc
                    )
                    continue
                raise
        raise RuntimeError("unreachable")

    async def check_input(
        self,
        *,
        message: str,
        tenant_id: UUID,
        conversation_id: UUID,
    ) -> GuardrailDecision:
        # Spec 017 FR-019 — business span carrying the verdict (allowed +
        # reason). The outbound HTTPX client span remains a child of this.
        with _tracer.start_as_current_span("guardrails.check_input") as span:
            tenant_config = await tenant_repository.get_guardrails_config(
                self._session, tenant_id
            )

            history_entries = await self._memory.load(tenant_id, conversation_id)
            cutoff = _history_turns()
            if cutoff:
                history_entries = history_entries[-cutoff:]
            history_payload = [
                {
                    "role": "visitor" if e.role == "visitor" else "assistant",
                    "content": e.content_redacted,
                }
                for e in history_entries
                if e.role in {"visitor", "assistant"}
            ]

            payload = {
                "message": message,
                "tenant_id": str(tenant_id),
                "conversation_id": str(conversation_id),
                "tenant_config": tenant_config or {},
                "conversation_history": history_payload,
            }

            try:
                data = await self._post("/guardrails/check-input", payload)
            except httpx.HTTPError as exc:
                logger.error("guardrails check-input failed: %s", exc)
                fallback = (
                    _fail_open_decision("sidecar_unreachable", message)
                    if _fail_open()
                    else _fail_closed_decision("sidecar_unreachable", message)
                )
                _record_guardrails(span, fallback)
                return fallback

            decision = GuardrailDecision(
                allowed=bool(data.get("allowed", False)),
                redacted_text=str(data.get("redacted_text", message)),
                safe_reply=data.get("safe_reply"),
                reason=data.get("reason"),
            )
            _record_guardrails(span, decision)
            return decision

    async def check_output(
        self,
        *,
        message: str,
        tenant_id: UUID,
    ) -> GuardrailDecision:
        with _tracer.start_as_current_span("guardrails.check_output") as span:
            payload = {"message": message, "tenant_id": str(tenant_id)}
            try:
                data = await self._post("/guardrails/check-output", payload)
            except httpx.HTTPError as exc:
                logger.error("guardrails check-output failed: %s", exc)
                fallback = (
                    _fail_open_decision("sidecar_unreachable", message)
                    if _fail_open()
                    else _fail_closed_decision("sidecar_unreachable", message)
                )
                _record_guardrails(span, fallback)
                return fallback

            decision = GuardrailDecision(
                allowed=bool(data.get("allowed", False)),
                redacted_text=str(data.get("redacted_text", message)),
                safe_reply=data.get("safe_reply"),
                reason=data.get("reason"),
            )
            _record_guardrails(span, decision)
            return decision
