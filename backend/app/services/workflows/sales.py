"""SalesWorkflow — RAG-grounded reply with a sales-leaning system prompt.

Same single-LLM-call shape as ``FaqWorkflow``; differs only in the system
prompt (``system_sales.md``), which biases the model toward confirming
interest and inviting the visitor to leave contact details.

Active capture of lead details still happens through the agent path's
``capture_lead`` tool. A future revision can call ``LeadService`` directly
from here once a contact-info detector is in place; deferred to keep the
deterministic shape rigid this phase.

See ``specs/workflow-services/spec.md §4``.
"""

from __future__ import annotations

from app.services.workflows.base import (
    DEFAULT_MAX_CHUNKS,
    DEFAULT_MAX_OUTPUT_TOKENS,
    _LLMClient,
    _RagGroundedWorkflow,
    _RagService,
)

SALES_SYSTEM_PROMPT_FILE = "system_sales.md"


class SalesWorkflow(_RagGroundedWorkflow):
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
            system_prompt_file=SALES_SYSTEM_PROMPT_FILE,
            max_chunks=max_chunks,
            max_output_tokens=max_output_tokens,
        )
