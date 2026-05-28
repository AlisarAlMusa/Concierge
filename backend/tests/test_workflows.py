"""Unit tests for FAQ / Sales / Human workflows.

Validates the single-step, RAG-grounded shape of the FAQ and Sales paths
(``specs/workflow-services/spec.md §3 / §4``) and the escalation-finalization
shape of the Human path (§5). No real LLM, no real DB, no real RAG — every
collaborator is a hand-rolled fake.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import pytest

from app.services.router_service import RouteDecision
from app.services.tools.rag_search import RagChunk, RagSearchResult
from app.services.workflows import FaqWorkflow, HumanWorkflow, SalesWorkflow
from app.services.workflows.faq import FAQ_SYSTEM_PROMPT_FILE
from app.services.workflows.sales import SALES_SYSTEM_PROMPT_FILE

TENANT = UUID("00000000-0000-0000-0000-00000000000a")
CONVO = UUID("00000000-0000-0000-0000-00000000c001")


# ----- Fakes ----------------------------------------------------------------
class _FakeRag:
    def __init__(self, result: RagSearchResult) -> None:
        self._result = result
        self.calls: list[dict[str, Any]] = []

    async def search(self, *, query: str, tenant_id: UUID, max_chunks: int) -> RagSearchResult:
        self.calls.append({"query": query, "tenant_id": tenant_id, "max_chunks": max_chunks})
        return self._result


class _LLMResponse:
    def __init__(self, content: str | None) -> None:
        self.content = content


class _FakeLLM:
    def __init__(self, response: _LLMResponse | Exception) -> None:
        self._response = response
        self.calls: list[dict[str, Any]] = []

    async def tool_complete(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        max_tokens: int,
    ) -> _LLMResponse:
        self.calls.append(
            {"messages": list(messages), "tools": list(tools), "max_tokens": max_tokens}
        )
        if isinstance(self._response, Exception):
            raise self._response
        return self._response


class _FakeEscalation:
    def __init__(self, exc: Exception | None = None) -> None:
        self._exc = exc
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if self._exc is not None:
            raise self._exc
        return type("R", (), {"escalation_id": uuid4(), "status": "created"})()


# ----- Helpers --------------------------------------------------------------
def _rag_result(*page_ids: UUID) -> RagSearchResult:
    chunks = [
        RagChunk(text=f"chunk-{i} content", source_page_id=pid, score=0.9 - 0.1 * i)
        for i, pid in enumerate(page_ids)
    ]
    return RagSearchResult(chunks=chunks, total_found=len(chunks))


def _decision(path: str) -> RouteDecision:
    return RouteDecision(
        path=path,  # type: ignore[arg-type]
        reason=path,  # type: ignore[arg-type]
        confidence=0.9,
        classifier_label=path,
    )


# ----- FaqWorkflow ----------------------------------------------------------
async def test_faq_workflow_happy_path_returns_llm_content_and_sources() -> None:
    page = uuid4()
    rag = _FakeRag(_rag_result(page))
    llm = _FakeLLM(_LLMResponse("We open at 7am every day."))
    wf = FaqWorkflow(rag_service=rag, llm_client=llm)

    result = await wf.run(
        tenant_id=TENANT,
        conversation_id=CONVO,
        visitor_session_id=None,
        user_message="when do you open?",
        tenant_persona="a bakery concierge",
        route_decision=_decision("faq"),
    )

    assert result.reply == "We open at 7am every day."
    assert result.sources == [page]
    assert result.used_refusal_fallback is False
    # Exactly one rag_search and one LLM call.
    assert len(rag.calls) == 1
    assert len(llm.calls) == 1


async def test_faq_workflow_passes_no_tools_to_llm() -> None:
    rag = _FakeRag(_rag_result(uuid4()))
    llm = _FakeLLM(_LLMResponse("answer"))
    wf = FaqWorkflow(rag_service=rag, llm_client=llm)

    await wf.run(
        tenant_id=TENANT,
        conversation_id=CONVO,
        visitor_session_id=None,
        user_message="anything",
        tenant_persona=None,
        route_decision=_decision("faq"),
    )

    assert llm.calls[0]["tools"] == [], "workflow must advertise NO tools to the LLM"


async def test_faq_workflow_uses_faq_system_prompt() -> None:
    rag = _FakeRag(_rag_result(uuid4()))
    llm = _FakeLLM(_LLMResponse("hi"))
    wf = FaqWorkflow(rag_service=rag, llm_client=llm)

    await wf.run(
        tenant_id=TENANT,
        conversation_id=CONVO,
        visitor_session_id=None,
        user_message="anything",
        tenant_persona="custom persona",
        route_decision=_decision("faq"),
    )
    # The system message renders the FAQ system prompt; check a stable phrase.
    system_msg = llm.calls[0]["messages"][0]
    assert system_msg["role"] == "system"
    assert "custom persona" in system_msg["content"]
    # Sanity check the prompt file constant the workflow uses.
    assert FAQ_SYSTEM_PROMPT_FILE == "system_faq.md"


async def test_faq_workflow_no_chunks_returns_refusal_without_calling_llm() -> None:
    rag = _FakeRag(RagSearchResult(chunks=[], total_found=0))
    llm = _FakeLLM(_LLMResponse("should not be called"))
    wf = FaqWorkflow(rag_service=rag, llm_client=llm)

    result = await wf.run(
        tenant_id=TENANT,
        conversation_id=CONVO,
        visitor_session_id=None,
        user_message="?",
        tenant_persona=None,
        route_decision=_decision("faq"),
    )

    assert result.used_refusal_fallback is True
    assert result.sources == []
    assert llm.calls == [], "no LLM call when retrieval is empty"


async def test_faq_workflow_empty_llm_content_falls_back_to_refusal() -> None:
    rag = _FakeRag(_rag_result(uuid4()))
    llm = _FakeLLM(_LLMResponse(""))
    wf = FaqWorkflow(rag_service=rag, llm_client=llm)

    result = await wf.run(
        tenant_id=TENANT,
        conversation_id=CONVO,
        visitor_session_id=None,
        user_message="x",
        tenant_persona=None,
        route_decision=_decision("faq"),
    )
    assert result.used_refusal_fallback is True
    assert result.reply != ""  # refusal text is non-empty


async def test_faq_workflow_dedups_source_page_ids() -> None:
    same_page = uuid4()
    chunks = RagSearchResult(
        chunks=[
            RagChunk(text="a", source_page_id=same_page, score=0.9),
            RagChunk(text="b", source_page_id=same_page, score=0.8),
            RagChunk(text="c", source_page_id=uuid4(), score=0.7),
        ],
        total_found=3,
    )
    rag = _FakeRag(chunks)
    llm = _FakeLLM(_LLMResponse("answer"))
    wf = FaqWorkflow(rag_service=rag, llm_client=llm)

    result = await wf.run(
        tenant_id=TENANT,
        conversation_id=CONVO,
        visitor_session_id=None,
        user_message="?",
        tenant_persona=None,
        route_decision=_decision("faq"),
    )

    assert len(result.sources) == 2  # dedup applied
    assert result.sources[0] == same_page


async def test_faq_workflow_propagates_rag_exception() -> None:
    class _Boom(Exception):
        pass

    class _ExplodingRag:
        async def search(self, **_: Any) -> RagSearchResult:
            raise _Boom("rag is down")

    wf = FaqWorkflow(rag_service=_ExplodingRag(), llm_client=_FakeLLM(_LLMResponse("x")))
    with pytest.raises(_Boom):
        await wf.run(
            tenant_id=TENANT,
            conversation_id=CONVO,
            visitor_session_id=None,
            user_message="x",
            tenant_persona=None,
            route_decision=_decision("faq"),
        )


# ----- SalesWorkflow --------------------------------------------------------
async def test_sales_workflow_uses_sales_system_prompt() -> None:
    rag = _FakeRag(_rag_result(uuid4()))
    llm = _FakeLLM(_LLMResponse("answer + invitation"))
    wf = SalesWorkflow(rag_service=rag, llm_client=llm)

    await wf.run(
        tenant_id=TENANT,
        conversation_id=CONVO,
        visitor_session_id=None,
        user_message="how much is X?",
        tenant_persona="sales concierge",
        route_decision=_decision("sales"),
    )

    system_msg = llm.calls[0]["messages"][0]
    assert "sales concierge" in system_msg["content"]
    assert SALES_SYSTEM_PROMPT_FILE == "system_sales.md"


# ----- HumanWorkflow --------------------------------------------------------
async def test_human_workflow_happy_path_creates_escalation_and_returns_canned() -> None:
    escalation = _FakeEscalation()
    wf = HumanWorkflow(escalation_service=escalation)

    result = await wf.run(
        tenant_id=TENANT,
        conversation_id=CONVO,
        visitor_session_id=None,
        user_message="I need a person",
        tenant_persona=None,
        route_decision=_decision("human"),
    )

    assert "human teammate" in result.reply
    assert result.used_refusal_fallback is False
    assert len(escalation.calls) == 1
    assert escalation.calls[0]["tenant_id"] == TENANT
    assert escalation.calls[0]["conversation_id"] == CONVO


async def test_human_workflow_degrades_when_escalation_fails() -> None:
    escalation = _FakeEscalation(exc=RuntimeError("db down"))
    wf = HumanWorkflow(escalation_service=escalation)

    result = await wf.run(
        tenant_id=TENANT,
        conversation_id=CONVO,
        visitor_session_id=None,
        user_message="I need a person",
        tenant_persona=None,
        route_decision=_decision("human"),
    )

    assert "wasn't able" in result.reply
    assert result.used_refusal_fallback is True
