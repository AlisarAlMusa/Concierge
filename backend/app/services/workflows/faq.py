"""FaqWorkflow — one rag_search + one tool-less LLM call.

Selected by ``ChatOrchestrator`` when ``RouterService`` returns
``path="faq"`` with high confidence. Skips the agent loop entirely so
high-confidence FAQ traffic gets a faster, more predictable answer than the
multi-iteration tool-calling agent.

See ``specs/workflow-services/spec.md §3``.
"""

from __future__ import annotations

from app.services.workflows.base import (
    DEFAULT_MAX_CHUNKS,
    DEFAULT_MAX_OUTPUT_TOKENS,
    _LLMClient,
    _RagGroundedWorkflow,
    _RagService,
)

FAQ_SYSTEM_PROMPT_FILE = "system_faq.md"


class FaqWorkflow(_RagGroundedWorkflow):
    def __init__(
        self,
        *,
        rag_service: _RagService,
        llm_client: _LLMClient,
        max_chunks: int = DEFAULT_MAX_CHUNKS,
        max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS,
    ) -> None:
        super().__init__(
            rag_service=rag_service,
            llm_client=llm_client,
            system_prompt_file=FAQ_SYSTEM_PROMPT_FILE,
            max_chunks=max_chunks,
            max_output_tokens=max_output_tokens,
        )
