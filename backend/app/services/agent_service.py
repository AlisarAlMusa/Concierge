"""Bounded tool-calling agent.

Invoked by ChatOrchestrator on the `agent` branch of RouteDecision. Runs a
single-LLM, single-loop conversation with the three SPEC §3 tools.

Invariants (architectural):
  - One agent, one loop, one LLM. No sub-agents, no planning stage.
  - Sequential tool dispatch within an iteration; no asyncio.gather over tools.
  - Bounded by max_iterations and max_output_tokens.
  - Same-code ToolError twice in a row → graceful refusal (anti-loop safety).
  - Empty LLM content or iteration-cap → refusal fallback from prompts/refusal.md.
  - Reads memory; never writes it. Never calls guardrails (orchestrator does).
  - Never calls RouterService.

Owner: Person B.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

import structlog
from opentelemetry import trace
from opentelemetry.trace import StatusCode
from pydantic import BaseModel

from app.core.prompts import load_prompt, render_prompt
from app.services.memory_service import MemoryEntry, MemoryService
from app.services.tools import ToolContext, ToolError, ToolRegistry
from app.services.tools.rag_search import RagSearchResult

_tracer = trace.get_tracer(__name__)

logger = structlog.get_logger(__name__)

SYSTEM_PROMPT_FILE = "system_agent.md"
REFUSAL_PROMPT_FILE = "refusal.md"
DEFAULT_PERSONA = "a helpful, polite assistant for this business"


# ----- LLM-side normalized shapes (provider-agnostic boundary) ---------------
class LLMToolCall(BaseModel):
    """One tool call as emitted by the LLM. Arguments may arrive as a JSON string."""

    id: str
    name: str
    arguments: dict[str, Any] | str


class LLMResponse(BaseModel):
    """Normalized LLM response. LLMClient produces this; provider details hidden."""

    content: str | None = None
    tool_calls: list[LLMToolCall] = []
    # Token usage extracted from the provider response (Spec 013 FR-001).
    # Defaults to 0 so existing tests that don't set them keep working.
    input_tokens: int = 0
    output_tokens: int = 0


# ----- Agent output shape ----------------------------------------------------
class AgentTurnResult(BaseModel):
    """What the agent returns to ChatOrchestrator."""

    reply: str
    sources: list[UUID] = []
    agent_iterations: int
    used_refusal_fallback: bool = False


# ----- Service ---------------------------------------------------------------
class AgentService:
    """Single-loop tool-calling agent. See module docstring for invariants."""

    def __init__(
        self,
        *,
        llm_client: Any,
        memory_service: MemoryService,
        tool_registry: ToolRegistry,
        max_iterations: int,
        max_output_tokens: int,
    ) -> None:
        self._llm = llm_client
        self._memory = memory_service
        self._tools = tool_registry
        self._max_iterations = max_iterations
        self._max_output_tokens = max_output_tokens

    async def run(
        self,
        *,
        tenant_id: UUID,
        conversation_id: UUID,
        user_message: str,
        tenant_persona: str | None = None,
        visitor_session_id: UUID | None = None,
        route_decision: Any = None,
    ) -> AgentTurnResult:
        """Execute one bounded turn."""
        ctx = ToolContext(
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            visitor_session_id=visitor_session_id,
        )

        history = await self._memory.load(tenant_id, conversation_id)
        messages = self._build_context(history, user_message, tenant_persona)
        tools_spec = self._tools.tool_specs()

        sources: list[UUID] = []
        last_error_code: str | None = None
        same_error_strikes = 0

        for iteration in range(self._max_iterations):
            # Spec 017 FR-018 — one span per loop iteration carrying the
            # zero-based iteration index. Tool spans live as children below.
            with _tracer.start_as_current_span("agent.iteration") as iter_span:
                iter_span.set_attribute("agent.iteration_index", iteration)

                response: LLMResponse = await self._llm.tool_complete(
                    messages=messages,
                    tools=tools_spec,
                    max_tokens=self._max_output_tokens,
                )

                # Fire-and-forget cost event for this LLM call (Spec 013 FR-001).
                _fire_llm_cost_event(tenant_id, self._llm, response)

                # Natural exit: no tool calls → final answer.
                if not response.tool_calls:
                    iter_span.set_attribute("agent.tool_calls_count", 0)
                    iter_span.set_attribute("agent.final_iteration", True)
                    reply = response.content or load_prompt(REFUSAL_PROMPT_FILE)
                    used_fallback = not response.content
                    self._log_completion(
                        tenant_id,
                        conversation_id,
                        iteration + 1,
                        used_fallback,
                        route_decision,
                    )
                    return AgentTurnResult(
                        reply=reply,
                        sources=_dedupe(sources),
                        agent_iterations=iteration + 1,
                        used_refusal_fallback=used_fallback,
                    )

                iter_span.set_attribute(
                    "agent.tool_calls_count", len(response.tool_calls)
                )

                # Tool-call path: assistant turn + sequential dispatch + tool results.
                messages.append(_assistant_tool_call_message(response.tool_calls))

                for call in response.tool_calls:
                    logger.info(
                        "agent_iteration",
                        tenant_id=str(tenant_id),
                        conversation_id=str(conversation_id),
                        iteration=iteration + 1,
                        tool=call.name,
                    )
                    # Spec 017 FR-018 — per-tool span with success flag and
                    # exception recording on the unexpected-error path. Tool
                    # arguments are NOT captured (privacy + readability).
                    with _tracer.start_as_current_span(
                        f"tool.{call.name.lower()}"
                    ) as tool_span:
                        tool_span.set_attribute("tool.name", call.name)
                        try:
                            result = await self._tools.dispatch(
                                call.name, call.arguments, ctx
                            )
                        except Exception as exc:
                            tool_span.set_attribute("tool.success", False)
                            tool_span.record_exception(exc)
                            tool_span.set_status(StatusCode.ERROR, str(exc))
                            raise
                        # ToolError is a structured-result-as-error, not an
                        # exception — surface it on the span but don't mark
                        # the span as a hard error.
                        tool_span.set_attribute(
                            "tool.success", not isinstance(result, ToolError)
                        )
                        if isinstance(result, ToolError):
                            tool_span.set_attribute("tool.error_code", result.code)

                    if isinstance(result, RagSearchResult):
                        sources.extend(chunk.source_page_id for chunk in result.chunks)

                    messages.append(_tool_result_message(call.id, call.name, result))

                    if isinstance(result, ToolError):
                        same_error_strikes = (
                            same_error_strikes + 1 if result.code == last_error_code else 1
                        )
                        last_error_code = result.code
                        if same_error_strikes >= 2:
                            logger.warning(
                                "agent_repeated_tool_error",
                                tenant_id=str(tenant_id),
                                conversation_id=str(conversation_id),
                                code=result.code,
                            )
                            return AgentTurnResult(
                                reply=load_prompt(REFUSAL_PROMPT_FILE),
                                sources=_dedupe(sources),
                                agent_iterations=iteration + 1,
                                used_refusal_fallback=True,
                            )
                    else:
                        same_error_strikes = 0
                        last_error_code = None

        # Iteration cap hit without a final message.
        logger.warning(
            "agent_iteration_cap_hit",
            tenant_id=str(tenant_id),
            conversation_id=str(conversation_id),
            max_iterations=self._max_iterations,
        )
        return AgentTurnResult(
            reply=load_prompt(REFUSAL_PROMPT_FILE),
            sources=_dedupe(sources),
            agent_iterations=self._max_iterations,
            used_refusal_fallback=True,
        )

    # ----- helpers -----------------------------------------------------------
    def _build_context(
        self,
        history: list[MemoryEntry],
        user_message: str,
        tenant_persona: str | None,
    ) -> list[dict[str, Any]]:
        """Compose system prompt + history + current user message."""
        system = render_prompt(
            SYSTEM_PROMPT_FILE,
            tenant_persona=tenant_persona or DEFAULT_PERSONA,
        )
        messages: list[dict[str, Any]] = [{"role": "system", "content": system}]
        for entry in history:
            # Stored "tool" entries lack tool_call_id correlation the LLM API
            # requires — orchestrator does not write them today, but be defensive.
            if entry.role == "tool":
                continue
            llm_role = "user" if entry.role == "visitor" else "assistant"
            messages.append({"role": llm_role, "content": entry.content_redacted})
        messages.append({"role": "user", "content": user_message})
        return messages

    @staticmethod
    def _log_completion(
        tenant_id: UUID,
        conversation_id: UUID,
        iterations: int,
        used_refusal_fallback: bool,
        route_decision: Any,
    ) -> None:
        logger.info(
            "agent_turn_completed",
            tenant_id=str(tenant_id),
            conversation_id=str(conversation_id),
            iterations=iterations,
            used_refusal_fallback=used_refusal_fallback,
            route_path=getattr(route_decision, "path", None),
            route_confidence=getattr(route_decision, "confidence", None),
        )


# ----- module-level helpers --------------------------------------------------
def _assistant_tool_call_message(tool_calls: list[LLMToolCall]) -> dict[str, Any]:
    """Build the assistant message that holds the LLM's tool calls."""
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": tc.id,
                "type": "function",
                "function": {
                    "name": tc.name,
                    "arguments": (
                        tc.arguments if isinstance(tc.arguments, str) else json.dumps(tc.arguments)
                    ),
                },
            }
            for tc in tool_calls
        ],
    }


def _tool_result_message(
    tool_call_id: str,
    tool_name: str,
    result: BaseModel | ToolError,
) -> dict[str, Any]:
    """Build the tool response message to feed back to the LLM."""
    return {
        "role": "tool",
        "tool_call_id": tool_call_id,
        "name": tool_name,
        "content": result.model_dump_json(),
    }


def _dedupe(items: list[UUID]) -> list[UUID]:
    seen: set[UUID] = set()
    out: list[UUID] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def _fire_llm_cost_event(tenant_id: UUID, llm_client: Any, response: LLMResponse) -> None:
    """Fire-and-forget cost event for one LLM call (Spec 013 FR-001).

    Reads provider/model from the LLM client via duck-typed attribute access
    so this helper works with any client that exposes ``_model``.
    Failures are swallowed inside cost_service.record_event.
    """
    if response.input_tokens == 0 and response.output_tokens == 0:
        return  # no usage data — skip rather than record a zero row

    try:
        from app.models.cost_event import CostOperation
        from app.services import cost_service

        model = getattr(llm_client, "_model", "unknown")
        # Determine provider from the client class name (groq → "groq").
        provider = type(llm_client).__name__.lower().replace("llmclient", "")

        cost_service.record_event(
            tenant_id=tenant_id,
            provider=provider,
            model=model,
            operation=CostOperation.llm,
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
        )
    except Exception:  # noqa: BLE001
        pass  # cost recording must never surface to callers
