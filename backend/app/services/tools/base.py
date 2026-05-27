"""Tool registry plumbing for the bounded tool-calling agent.

SPEC §3 fixes the three tools (rag_search, capture_lead, escalate), their
args/result Pydantic schemas, and the ToolError envelope. This module owns
the registry, dispatch, and exception → ToolError translation. Concrete
tool wrappers live one file per tool.

Owner: Person B.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import structlog
from pydantic import BaseModel, ValidationError

from app.core.errors import NotFoundError, RateLimitError

logger = structlog.get_logger(__name__)


class ToolError(BaseModel):
    """SPEC §3 error envelope. Tools return this for recoverable failures."""

    error: str
    code: str  # rate_limited | validation_error | not_found | unknown_tool


@dataclass
class ToolContext:
    """Per-turn request-scoped context passed to every tool handler.

    Carries the only authoritative identifiers — these are NEVER read from
    LLM-supplied arguments (SPEC §1).
    """

    tenant_id: UUID
    conversation_id: UUID
    visitor_session_id: UUID | None = None


@dataclass
class ToolHandler:
    """One tool: name, description, args schema, and the bound invoke function."""

    name: str
    description: str
    args_schema: type[BaseModel]
    invoke_fn: Callable[..., Awaitable[BaseModel]]

    def to_openai_spec(self) -> dict[str, Any]:
        """OpenAI-style descriptor. LLMClient may reshape per provider if needed."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.args_schema.model_json_schema(),
            },
        }


class ToolRegistry:
    """Bound tool handlers for one app instance. Sequential dispatch only.

    Invariants:
      - The agent passes raw_args from the LLM; the registry validates against
        the tool's Pydantic args schema before invoking.
      - Domain exceptions (RateLimitError, NotFoundError) translate to
        ToolError; ExternalServiceError and unexpected exceptions propagate
        so the orchestrator can surface a polite reply.
      - ToolContext (tenant_id, conversation_id, visitor_session_id) is the
        only authoritative source of per-turn identity — never the LLM args.
    """

    def __init__(self, handlers: list[ToolHandler]) -> None:
        self._handlers: dict[str, ToolHandler] = {h.name: h for h in handlers}

    def tool_specs(self) -> list[dict[str, Any]]:
        return [h.to_openai_spec() for h in self._handlers.values()]

    async def dispatch(
        self,
        name: str,
        raw_args: dict[str, Any] | str,
        ctx: ToolContext,
    ) -> BaseModel | ToolError:
        """Look up by name, validate args, invoke. Translate domain exceptions."""
        handler = self._handlers.get(name)
        if handler is None:
            logger.warning(
                "tool_unknown",
                tool=name,
                tenant_id=str(ctx.tenant_id),
            )
            return ToolError(error=f"Unknown tool: {name}", code="unknown_tool")

        # OpenAI returns arguments as a JSON string; accept a dict too for tests.
        if isinstance(raw_args, str):
            try:
                raw_args = json.loads(raw_args or "{}")
            except json.JSONDecodeError as exc:
                return ToolError(
                    error=f"Invalid JSON arguments: {exc}",
                    code="validation_error",
                )

        try:
            args = handler.args_schema.model_validate(raw_args)
        except ValidationError as exc:
            return ToolError(error=str(exc), code="validation_error")

        try:
            return await handler.invoke_fn(args, ctx)
        except RateLimitError as exc:
            return ToolError(error=str(exc), code="rate_limited")
        except NotFoundError as exc:
            return ToolError(error=str(exc), code="not_found")
        # ExternalServiceError and unexpected exceptions propagate by design —
        # the orchestrator catches and returns a polite visitor-facing reply.
