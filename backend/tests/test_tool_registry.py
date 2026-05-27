"""Unit tests for ToolRegistry dispatch and exception → ToolError translation.

Validates SPEC §3 envelope behavior independently of the agent loop.
"""

from __future__ import annotations

import pytest

from app.core.errors import NotFoundError, RateLimitError
from app.services.tools import ToolContext, ToolError, build_registry
from app.services.tools.rag_search import RagSearchResult
from tests.conftest import (
    CONVO_1,
    TENANT_A,
    FakeEscalationService,
    FakeLeadService,
    FakeRagService,
)


@pytest.fixture
def ctx() -> ToolContext:
    return ToolContext(
        tenant_id=TENANT_A,
        conversation_id=CONVO_1,
        visitor_session_id=None,
    )


def _registry(*, rag=None, lead=None, esc=None):
    return build_registry(
        rag_service=rag or FakeRagService(),
        lead_service=lead or FakeLeadService(),
        escalation_service=esc or FakeEscalationService(),
    )


async def test_successful_dispatch(ctx):
    """Happy path: rag_search returns RagSearchResult."""
    reg = _registry(rag=FakeRagService(result=RagSearchResult(chunks=[], total_found=0)))

    result = await reg.dispatch("rag_search", {"query": "hi", "max_chunks": 3}, ctx)

    assert isinstance(result, RagSearchResult)
    assert result.total_found == 0


async def test_validation_error_translation(ctx):
    """Missing required `intent` for capture_lead → ToolError(validation_error)."""
    reg = _registry()

    result = await reg.dispatch("capture_lead", {"email": "x@y.com"}, ctx)

    assert isinstance(result, ToolError)
    assert result.code == "validation_error"


async def test_rate_limited_translation(ctx):
    """Handler raising RateLimitError → ToolError(rate_limited)."""
    reg = _registry(rag=FakeRagService(exc=RateLimitError("nope")))

    result = await reg.dispatch("rag_search", {"query": "x"}, ctx)

    assert isinstance(result, ToolError)
    assert result.code == "rate_limited"


async def test_not_found_translation(ctx):
    """Handler raising NotFoundError → ToolError(not_found)."""
    reg = _registry(esc=FakeEscalationService(exc=NotFoundError("conversation", "abc")))

    result = await reg.dispatch("escalate", {"reason": "test"}, ctx)

    assert isinstance(result, ToolError)
    assert result.code == "not_found"


async def test_unknown_tool_dispatch(ctx):
    """Unknown tool name → ToolError(unknown_tool); no exception leaks."""
    reg = _registry()

    result = await reg.dispatch("not_a_tool", {}, ctx)

    assert isinstance(result, ToolError)
    assert result.code == "unknown_tool"
