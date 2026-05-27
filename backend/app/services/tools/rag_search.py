"""rag_search tool — retrieve tenant CMS content. SPEC §3.1.

Always tenant-filtered (RLS context + explicit tenant_id in the pgvector
query inside RagService). Returns an empty list rather than raising when
nothing matches.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

from app.services.tools.base import ToolContext, ToolHandler


class RagChunk(BaseModel):
    text: str
    source_page_id: UUID
    score: float


class RagSearchArgs(BaseModel):
    query: str = Field(
        ...,
        description="The question to search the tenant's content for.",
    )
    max_chunks: int = Field(5, ge=1, le=10)


class RagSearchResult(BaseModel):
    chunks: list[RagChunk]
    total_found: int


_DESCRIPTION = (
    "Retrieve information from this business's published content to answer a visitor's "
    "question. Use whenever the visitor asks about products, pricing, policies, hours, or "
    "anything that would be on the business's website. Returns an empty list if nothing matches."
)


def build_handler(rag_service: Any) -> ToolHandler:
    """Bind rag_search to a RagService instance.

    rag_service must expose:
        async search(query: str, tenant_id: UUID, max_chunks: int) -> RagSearchResult
    """

    async def invoke(args: RagSearchArgs, ctx: ToolContext) -> RagSearchResult:
        return await rag_service.search(
            query=args.query,
            tenant_id=ctx.tenant_id,
            max_chunks=args.max_chunks,
        )

    return ToolHandler(
        name="rag_search",
        description=_DESCRIPTION,
        args_schema=RagSearchArgs,
        invoke_fn=invoke,
    )
