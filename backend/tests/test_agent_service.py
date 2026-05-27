"""Unit tests for AgentService bounded-loop invariants.

Mocked LLM, mocked services, no I/O. Validates the architectural contracts
agreed in docs/SPEC.md before wiring any real provider.
"""

from __future__ import annotations

from uuid import UUID

import pytest

from app.core.errors import ExternalServiceError, RateLimitError
from app.services.tools.rag_search import RagChunk, RagSearchResult
from tests.conftest import (
    FakeRagService,
    llm_call,
    llm_empty,
    llm_text,
)


async def test_final_response_without_tool_calls(make_agent, tenant_id, conversation_id):
    """LLM returns content on iteration 1 → return immediately, no dispatches."""
    agent, llm = make_agent(llm_responses=[llm_text("Hello there!")])

    result = await agent.run(
        tenant_id=tenant_id,
        conversation_id=conversation_id,
        user_message="hi",
    )

    assert result.reply == "Hello there!"
    assert result.agent_iterations == 1
    assert result.used_refusal_fallback is False
    assert result.sources == []
    assert len(llm.calls) == 1


async def test_single_tool_call_then_final_response(make_agent, tenant_id, conversation_id):
    """LLM calls rag_search, sources are collected, then final text reply."""
    page_id = UUID("00000000-0000-0000-0000-0000000000a1")
    rag = FakeRagService(
        result=RagSearchResult(
            chunks=[RagChunk(text="Open 9-5.", source_page_id=page_id, score=0.9)],
            total_found=1,
        )
    )
    agent, llm = make_agent(
        llm_responses=[
            llm_call("rag_search", {"query": "hours", "max_chunks": 3}),
            llm_text("We're open 9-5."),
        ],
        rag_service=rag,
    )

    result = await agent.run(
        tenant_id=tenant_id,
        conversation_id=conversation_id,
        user_message="What are your hours?",
    )

    assert result.reply == "We're open 9-5."
    assert result.agent_iterations == 2
    assert result.sources == [page_id]
    assert result.used_refusal_fallback is False
    assert len(llm.calls) == 2


async def test_iteration_cap_fallback(make_agent, tenant_id, conversation_id):
    """LLM keeps calling tools; cap is hit; refusal fallback fires."""
    agent, llm = make_agent(
        llm_responses=[
            llm_call("rag_search", {"query": "x"}, call_id="c1"),
            llm_call("rag_search", {"query": "y"}, call_id="c2"),
            llm_call("rag_search", {"query": "z"}, call_id="c3"),
        ],
        max_iterations=3,
    )

    result = await agent.run(
        tenant_id=tenant_id,
        conversation_id=conversation_id,
        user_message="hi",
    )

    assert result.used_refusal_fallback is True
    assert result.agent_iterations == 3
    assert len(llm.calls) == 3


async def test_refusal_fallback_on_empty_content(make_agent, tenant_id, conversation_id):
    """LLM returns no content and no tool_calls → refusal fallback."""
    agent, llm = make_agent(llm_responses=[llm_empty()])

    result = await agent.run(
        tenant_id=tenant_id,
        conversation_id=conversation_id,
        user_message="hi",
    )

    assert result.used_refusal_fallback is True
    assert result.agent_iterations == 1
    assert len(llm.calls) == 1


async def test_external_service_error_propagates(make_agent, tenant_id, conversation_id):
    """ExternalServiceError from LLMClient is NOT swallowed — orchestrator catches."""
    err = ExternalServiceError(service="llm", reason="provider 503")
    agent, _ = make_agent(llm_responses=[err])

    with pytest.raises(ExternalServiceError):
        await agent.run(
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            user_message="hi",
        )


async def test_repeated_tool_error_exits_with_refusal(make_agent, tenant_id, conversation_id):
    """Same ToolError code twice in a row → graceful refusal at iteration 2."""
    rag = FakeRagService(exc=RateLimitError("rag rate limited"))
    agent, llm = make_agent(
        llm_responses=[
            llm_call("rag_search", {"query": "x"}, call_id="c1"),
            llm_call("rag_search", {"query": "y"}, call_id="c2"),
            llm_call("rag_search", {"query": "z"}, call_id="c3"),  # never reached
        ],
        rag_service=rag,
    )

    result = await agent.run(
        tenant_id=tenant_id,
        conversation_id=conversation_id,
        user_message="hi",
    )

    assert result.used_refusal_fallback is True
    assert result.agent_iterations == 2  # strike rule trips on the 2nd matching code
    assert len(llm.calls) == 2


async def test_unknown_tool_returns_tool_error(make_agent, tenant_id, conversation_id):
    """LLM hallucinates a tool name → ToolError(unknown_tool); LLM recovers next turn."""
    agent, llm = make_agent(
        llm_responses=[
            llm_call("not_a_real_tool", {}, call_id="c1"),
            llm_text("Sorry, I'll answer directly: yes."),
        ],
    )

    result = await agent.run(
        tenant_id=tenant_id,
        conversation_id=conversation_id,
        user_message="hi",
    )

    assert result.reply == "Sorry, I'll answer directly: yes."
    assert result.agent_iterations == 2
    assert result.used_refusal_fallback is False
    assert len(llm.calls) == 2
