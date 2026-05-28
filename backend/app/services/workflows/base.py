"""Shared types + RAG-grounded workflow base.

``WorkflowTurnResult`` is the common return shape every workflow service
emits — same fields as a subset of ``AgentTurnResult`` so
``ChatOrchestrator._finalize`` doesn't need to branch on which path produced
the turn.

``_RagGroundedWorkflow`` is the shared body of ``FaqWorkflow`` and
``SalesWorkflow``: exactly one ``rag_search`` + one tool-less LLM call. Kept
private to the package; ``FaqWorkflow`` and ``SalesWorkflow`` are the only
external surface (they differ only in their system prompt filename).

See ``specs/workflow-services/spec.md``. Owner: Person B.
"""

from __future__ import annotations

from typing import Any, Protocol
from uuid import UUID

import structlog
from pydantic import BaseModel

from app.core.prompts import load_prompt, render_prompt
from app.services.router_service import RouteDecision
from app.services.tools.rag_search import RagSearchResult

logger = structlog.get_logger(__name__)

REFUSAL_PROMPT_FILE = "refusal.md"
FAQ_USER_TEMPLATE_FILE = "faq_user.md"
DEFAULT_PERSONA = "a helpful, polite assistant for this business"
DEFAULT_MAX_CHUNKS = 5
DEFAULT_MAX_OUTPUT_TOKENS = 500


class WorkflowTurnResult(BaseModel):
    """Workflow's per-turn output. Same shape subset as ``AgentTurnResult``."""

    reply: str
    sources: list[UUID] = []
    used_refusal_fallback: bool = False


class _RagService(Protocol):
    """Structural type — same one ``RagService.search`` exposes."""

    async def search(self, *, query: str, tenant_id: UUID, max_chunks: int) -> RagSearchResult: ...


class _LLMClient(Protocol):
    """Same shape AgentService consumes from GroqLLMClient.

    Workflows pass ``tools=[]`` so the LLM physically cannot emit a tool call.
    """

    async def tool_complete(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        max_tokens: int,
    ) -> Any: ...


class _RagGroundedWorkflow:
    """One ``rag_search`` + one bounded LLM summarization. No tool loop.

    Subclassed by ``FaqWorkflow`` and ``SalesWorkflow`` to vary only the
    system prompt filename. Anything broader than that (different retrieval
    knobs, different output-token budgets, different post-processing) is a
    constructor arg — subclasses don't override behavior.
    """

    def __init__(
        self,
        *,
        rag_service: _RagService,
        llm_client: _LLMClient,
        system_prompt_file: str,
        max_chunks: int = DEFAULT_MAX_CHUNKS,
        max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS,
    ) -> None:
        self._rag = rag_service
        self._llm = llm_client
        self._system_prompt_file = system_prompt_file
        self._max_chunks = max_chunks
        self._max_output_tokens = max_output_tokens

    async def run(
        self,
        *,
        tenant_id: UUID,
        conversation_id: UUID,
        visitor_session_id: UUID | None,  # noqa: ARG002 — accepted for shape parity
        user_message: str,
        tenant_persona: str | None,
        route_decision: RouteDecision,
    ) -> WorkflowTurnResult:
        # 1. Retrieve.
        rag_result = await self._rag.search(
            query=user_message, tenant_id=tenant_id, max_chunks=self._max_chunks
        )

        if not rag_result.chunks:
            return WorkflowTurnResult(
                reply=load_prompt(REFUSAL_PROMPT_FILE).strip(),
                sources=[],
                used_refusal_fallback=True,
            )

        # 2. Build the one-shot message list. No tools advertised.
        persona = tenant_persona or DEFAULT_PERSONA
        system_msg = render_prompt(self._system_prompt_file, tenant_persona=persona)
        user_msg = render_prompt(
            FAQ_USER_TEMPLATE_FILE,
            query=user_message,
            chunks=_format_chunks(rag_result),
        )
        messages = [
            {"role": "system", "content": system_msg},
            {"role": "user", "content": user_msg},
        ]

        # 3. One LLM call, no tools.
        llm_response = await self._llm.tool_complete(
            messages=messages,
            tools=[],
            max_tokens=self._max_output_tokens,
        )

        content = (llm_response.content or "").strip()
        if not content:
            logger.info(
                "workflow.refusal_empty_content",
                tenant_id=str(tenant_id),
                conversation_id=str(conversation_id),
                workflow=self.__class__.__name__,
                route=route_decision.path,
            )
            return WorkflowTurnResult(
                reply=load_prompt(REFUSAL_PROMPT_FILE).strip(),
                sources=[],
                used_refusal_fallback=True,
            )

        # 4. Dedup-preserving source list.
        seen: set[UUID] = set()
        sources: list[UUID] = []
        for chunk in rag_result.chunks:
            if chunk.source_page_id not in seen:
                seen.add(chunk.source_page_id)
                sources.append(chunk.source_page_id)

        return WorkflowTurnResult(
            reply=content,
            sources=sources,
            used_refusal_fallback=False,
        )


def _format_chunks(result: RagSearchResult) -> str:
    """Render retrieved chunks as a bullet list for the user-message template.

    Page ids are intentionally NOT included in the rendered text — sources are
    surfaced structurally via ``WorkflowTurnResult.sources``, not as inline
    references the LLM might decide to cite verbatim.
    """
    return "\n".join(f"- {chunk.text.strip()}" for chunk in result.chunks)
