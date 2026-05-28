"""GroqLLMClient — Groq-backed implementation of the bounded agent's LLM boundary.

The normalized boundary types (LLMResponse / LLMToolCall) live in
``app.services.agent_service`` to keep AgentService provider-agnostic. This
module is the ONLY place inside the backend that imports the Groq SDK or
speaks Groq's wire format. To swap providers later we replace this file and
nothing else.

Design invariants (frozen):
* The agent already emits OpenAI-shape ``messages`` and OpenAI-shape ``tools``
  (see ``app.services.tools.base.ToolHandler.to_openai_spec``). Groq's
  ``/chat/completions`` is OpenAI-compatible, so both flow through unchanged.
* Retries and provider failure classification live here. AgentService sees
  either an LLMResponse or an ExternalServiceError — never a raw SDK error.
* No streaming. ``tool_complete`` returns one normalized response.
* No framework abstractions (no LangChain/LangGraph/CrewAI).

Owner: Person B.
"""

from __future__ import annotations

import asyncio
from typing import Any

import structlog
from groq import APIConnectionError, APIError, APIStatusError, AsyncGroq

from app.core.errors import ExternalServiceError
from app.services.agent_service import LLMResponse, LLMToolCall

logger = structlog.get_logger(__name__)

DEFAULT_TIMEOUT_SECONDS = 30.0
DEFAULT_TEMPERATURE = 0.2

# Status codes that justify a retry. 429 (rate limit) and 5xx (server error).
# Everything else in 4xx is a deterministic client/programming error.
_RETRYABLE_5XX = range(500, 600)


class GroqLLMClient:
    """Bounded, retrying LLM client backed by Groq's chat completions endpoint.

    Why a class (not a plain function): the retry budget, model id, and the
    underlying ``AsyncGroq`` client are configuration that the caller (DI
    later, tests now) owns once per app instance.
    """

    def __init__(
        self,
        *,
        client: AsyncGroq,
        model: str,
        max_attempts: int = 3,
        backoff_base_seconds: float = 0.5,
        backoff_max_seconds: float = 5.0,
        temperature: float = DEFAULT_TEMPERATURE,
    ) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")
        if backoff_base_seconds < 0 or backoff_max_seconds < 0:
            raise ValueError("backoff seconds must be >= 0")
        self._client = client
        self._model = model
        self._max_attempts = max_attempts
        self._backoff_base = backoff_base_seconds
        self._backoff_max = backoff_max_seconds
        self._temperature = temperature

    @classmethod
    def from_api_key(
        cls,
        *,
        api_key: str,
        model: str,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        max_attempts: int = 3,
        backoff_base_seconds: float = 0.5,
        backoff_max_seconds: float = 5.0,
        temperature: float = DEFAULT_TEMPERATURE,
    ) -> GroqLLMClient:
        """Convenience factory used by DI when wiring lands. Disables SDK retries
        because our retry loop here is the single source of truth."""
        client = AsyncGroq(
            api_key=api_key,
            timeout=timeout_seconds,
            max_retries=0,
        )
        return cls(
            client=client,
            model=model,
            max_attempts=max_attempts,
            backoff_base_seconds=backoff_base_seconds,
            backoff_max_seconds=backoff_max_seconds,
            temperature=temperature,
        )

    async def tool_complete(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        max_tokens: int,
    ) -> LLMResponse:
        """Execute one chat-completions call with bounded retries.

        Retry policy:
          - APIConnectionError (network/DNS/timeout): retry.
          - APIStatusError with 429 or 5xx: retry.
          - APIStatusError with other 4xx: surface immediately (deterministic).
          - Any other APIError or unexpected exception: surface immediately.
        """
        request_kwargs: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": self._temperature,
        }
        if tools:
            request_kwargs["tools"] = tools
            request_kwargs["tool_choice"] = "auto"

        last_exc: BaseException | None = None

        for attempt in range(self._max_attempts):
            try:
                completion = await self._client.chat.completions.create(**request_kwargs)
                return _to_llm_response(completion)
            except APIConnectionError as exc:
                # Network or timeout. Retryable.
                last_exc = exc
            except APIStatusError as exc:
                status = getattr(exc, "status_code", None)
                if status == 429 or (status is not None and status in _RETRYABLE_5XX):
                    last_exc = exc
                else:
                    logger.error(
                        "llm.groq.client_error",
                        status_code=status,
                        error=str(exc),
                    )
                    raise ExternalServiceError(
                        service="groq",
                        reason=f"HTTP {status}: {exc}",
                    ) from exc
            except APIError as exc:
                # Other SDK-level API errors (e.g. malformed response). Not retryable.
                logger.error("llm.groq.api_error", error=str(exc))
                raise ExternalServiceError(service="groq", reason=str(exc)) from exc
            except Exception as exc:
                # Unknown failure mode (programming error, asyncio cancel, etc.).
                logger.exception("llm.groq.unexpected_error")
                raise ExternalServiceError(service="groq", reason=str(exc)) from exc

            if attempt + 1 < self._max_attempts:
                delay = min(self._backoff_base * (2**attempt), self._backoff_max)
                logger.warning(
                    "llm.groq.retry",
                    attempt=attempt + 1,
                    max_attempts=self._max_attempts,
                    delay_seconds=delay,
                    error=str(last_exc),
                )
                if delay > 0:
                    await asyncio.sleep(delay)

        # All attempts exhausted on transient errors.
        logger.error(
            "llm.groq.retries_exhausted",
            attempts=self._max_attempts,
            error=str(last_exc),
        )
        raise ExternalServiceError(
            service="groq",
            reason=f"max retries ({self._max_attempts}) exhausted: {last_exc}",
        ) from last_exc


# ----- response normalization ------------------------------------------------
def _to_llm_response(completion: Any) -> LLMResponse:
    """Translate a Groq ChatCompletion into the agent's LLMResponse.

    Groq returns ``message.content`` as ``None`` when the model emitted tool
    calls only; ``message.tool_calls`` is ``None`` (not ``[]``) when absent.
    Tool-call ``arguments`` arrive as a JSON string per the OpenAI/Groq
    contract; AgentService's tool registry already handles both string and
    dict forms, so we forward the string unchanged.
    """
    choice = completion.choices[0]
    message = choice.message

    content = getattr(message, "content", None)
    raw_tool_calls = getattr(message, "tool_calls", None) or []

    tool_calls: list[LLMToolCall] = []
    for raw in raw_tool_calls:
        fn = getattr(raw, "function", None)
        if fn is None:
            continue
        tool_calls.append(
            LLMToolCall(
                id=raw.id,
                name=fn.name,
                arguments=fn.arguments,
            )
        )

    usage = getattr(completion, "usage", None)
    input_tokens = getattr(usage, "prompt_tokens", 0) or 0
    output_tokens = getattr(usage, "completion_tokens", 0) or 0

    return LLMResponse(
        content=content,
        tool_calls=tool_calls,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )
