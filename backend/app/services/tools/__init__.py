"""Tool registry for the bounded tool-calling agent.

SPEC §3 fixes the three tools — rag_search, capture_lead, escalate — and
their argument/result schemas. build_registry() wires each tool to its
backing service. Per-turn identifiers (tenant_id, conversation_id,
visitor_session_id) flow through ToolContext, NEVER through LLM args.
"""

from __future__ import annotations

from typing import Any

from app.services.tools import capture_lead, escalate, rag_search
from app.services.tools.base import (
    ToolContext,
    ToolError,
    ToolHandler,
    ToolRegistry,
)

__all__ = [
    "ToolContext",
    "ToolError",
    "ToolHandler",
    "ToolRegistry",
    "build_registry",
]


def build_registry(
    *,
    rag_service: Any,
    lead_service: Any,
    escalation_service: Any,
) -> ToolRegistry:
    """Build the three-tool registry for one app instance.

    Services are bound at construction time; per-turn identifiers flow via
    ToolContext on each dispatch.
    """
    return ToolRegistry(
        [
            rag_search.build_handler(rag_service),
            capture_lead.build_handler(lead_service),
            escalate.build_handler(escalation_service),
        ]
    )
