"""Shared test fakes + fixtures.

Hand-rolled fakes keep the unit tests deterministic and fast — no Redis,
no Postgres, no LLM API. Owner B's surfaces (router, agent, tools,
workflows, persistence, widget auth) are all wired through Protocols, so
matching the protocol shape is enough for the tests to drive them.
"""

from __future__ import annotations

import os
from typing import Any
from uuid import UUID, uuid4

# Settings is constructed at app-import time by some tests (test_chat_route
# imports app.main). Populate the required-fields with dummies BEFORE any
# test module imports them.
os.environ.setdefault("APP_ENV", "local")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://u:p@db/db")
os.environ.setdefault("REDIS_URL", "redis://r:6379/0")
os.environ.setdefault("VAULT_ADDR", "http://v")
os.environ.setdefault("VAULT_TOKEN", "t")
os.environ.setdefault("MINIO_ENDPOINT", "m")
os.environ.setdefault("MINIO_ACCESS_KEY", "k")
os.environ.setdefault("MINIO_SECRET_KEY", "s")
os.environ.setdefault("LLM_PROVIDER", "groq")
os.environ.setdefault("LLM_MODEL", "llama-3.1-70b-versatile")
os.environ.setdefault("EMBEDDING_MODEL", "embed-english-v3.0")
os.environ.setdefault("MODEL_SERVER_URL", "http://model_server:8001")
os.environ.setdefault("GUARDRAILS_URL", "http://guardrails:8002")
os.environ.setdefault("SERVICE_AUTH_SECRET", "service-secret")
os.environ.setdefault("WIDGET_TOKEN_SECRET", "widget-test-secret")

import pytest  # noqa: E402

from app.core.errors import (  # noqa: E402
    ExternalServiceError,
    NotFoundError,
    RateLimitError,
)
from app.services.agent_service import (  # noqa: E402
    AgentService,
    LLMResponse,
    LLMToolCall,
)
from app.services.memory_service import MemoryEntry  # noqa: E402
from app.services.router_service import ClassifierResponse  # noqa: E402
from app.services.tools.capture_lead import CaptureLeadResult  # noqa: E402
from app.services.tools.escalate import EscalateResult  # noqa: E402
from app.services.tools.rag_search import RagSearchResult  # noqa: E402

# ----- Canonical UUIDs used across tests -------------------------------------
TENANT_A = UUID("11111111-1111-1111-1111-111111111111")
TENANT_B = UUID("22222222-2222-2222-2222-222222222222")
CONVO_1 = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
CONVO_2 = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")


# ----- LLM response helpers --------------------------------------------------
def llm_text(content: str) -> LLMResponse:
    """Build an LLMResponse with a final text reply (no tool calls)."""
    return LLMResponse(content=content, tool_calls=[])


def llm_empty() -> LLMResponse:
    """LLMResponse with neither content nor tool calls — triggers refusal fallback."""
    return LLMResponse(content=None, tool_calls=[])


def llm_call(name: str, arguments: dict[str, Any], *, call_id: str | None = None) -> LLMResponse:
    """LLMResponse with one tool call."""
    return LLMResponse(
        content=None,
        tool_calls=[
            LLMToolCall(
                id=call_id or f"call-{uuid4().hex[:8]}",
                name=name,
                arguments=arguments,
            )
        ],
    )


# ----- Fake collaborators ----------------------------------------------------
class FakeLLMClient:
    """Scripted LLM. Pops the next response off ``responses`` each call.

    A bare ``Exception`` instance in the list is *raised* instead of
    returned — used to test ExternalServiceError propagation.
    """

    def __init__(self, responses: list[LLMResponse | Exception] | None = None) -> None:
        self.responses: list[LLMResponse | Exception] = list(responses or [])
        self.calls: list[dict[str, Any]] = []

    async def tool_complete(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        max_tokens: int,
    ) -> LLMResponse:
        self.calls.append({"messages": messages, "tools": tools, "max_tokens": max_tokens})
        if not self.responses:
            raise AssertionError("FakeLLMClient: no more scripted responses")
        nxt = self.responses.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt


class FakeMemoryService:
    """In-memory MemoryService double. Stores entries per (tenant, conv)."""

    def __init__(self, history: list[MemoryEntry] | None = None) -> None:
        self._history = list(history or [])
        self.appended: list[tuple[UUID, UUID, str, str]] = []

    async def load(self, tenant_id: UUID, conversation_id: UUID) -> list[MemoryEntry]:
        return list(self._history)

    async def append(
        self,
        tenant_id: UUID,
        conversation_id: UUID,
        role: str,
        content: str,
    ) -> None:
        self.appended.append((tenant_id, conversation_id, role, content))


class FakeRagService:
    """Returns ``result`` (or raises ``exc``) from ``search``."""

    def __init__(
        self,
        *,
        result: RagSearchResult | None = None,
        exc: Exception | None = None,
    ) -> None:
        self._result = result or RagSearchResult(chunks=[], total_found=0)
        self._exc = exc
        self.calls: list[dict[str, Any]] = []

    async def search(self, *, query: str, tenant_id: UUID, max_chunks: int) -> RagSearchResult:
        self.calls.append({"query": query, "tenant_id": tenant_id, "max_chunks": max_chunks})
        if self._exc is not None:
            raise self._exc
        return self._result


class FakeLeadService:
    """Returns a canned ``CaptureLeadResult`` (or raises) from ``capture``."""

    def __init__(self, *, exc: Exception | None = None) -> None:
        self._exc = exc
        self.calls: list[dict[str, Any]] = []

    async def capture(self, **kwargs: Any) -> CaptureLeadResult:
        self.calls.append(kwargs)
        if self._exc is not None:
            raise self._exc
        return CaptureLeadResult(lead_id=uuid4(), status="created")


class FakeEscalationService:
    """Returns a canned ``EscalateResult`` (or raises) from ``create``."""

    def __init__(self, *, exc: Exception | None = None) -> None:
        self._exc = exc
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> EscalateResult:
        self.calls.append(kwargs)
        if self._exc is not None:
            raise self._exc
        return EscalateResult(escalation_id=uuid4(), status="created")


class FakeClassifierClient:
    """Returns ``response`` (or raises ``exc``) from ``classify``."""

    def __init__(
        self,
        *,
        response: ClassifierResponse | None = None,
        exc: Exception | None = None,
    ) -> None:
        self._response = response
        self._exc = exc
        self.calls: list[str] = []

    async def classify(self, *, text: str) -> ClassifierResponse:
        self.calls.append(text)
        if self._exc is not None:
            raise self._exc
        if self._response is None:
            raise ExternalServiceError(service="classifier", reason="no scripted response")
        return self._response


# Re-export so deep imports work too.
__all__ = [
    "TENANT_A",
    "TENANT_B",
    "CONVO_1",
    "CONVO_2",
    "FakeLLMClient",
    "FakeMemoryService",
    "FakeRagService",
    "FakeLeadService",
    "FakeEscalationService",
    "FakeClassifierClient",
    "llm_text",
    "llm_empty",
    "llm_call",
    "ExternalServiceError",
    "NotFoundError",
    "RateLimitError",
]


# ----- Shared fixtures -------------------------------------------------------
@pytest.fixture
def tenant_id() -> UUID:
    return TENANT_A


@pytest.fixture
def conversation_id() -> UUID:
    return CONVO_1


@pytest.fixture
def make_agent():
    """Factory for AgentService wired with FakeLLMClient + FakeMemoryService.

    Used by ``test_agent_service.py``. The factory returns
    ``(agent, llm_client)`` so tests can inspect ``llm_client.calls``.
    """

    from app.services.tools import ToolRegistry, build_registry

    def _factory(
        *,
        llm_responses: list[LLMResponse | Exception],
        rag_service: Any | None = None,
        lead_service: Any | None = None,
        escalation_service: Any | None = None,
        memory_service: Any | None = None,
        max_iterations: int = 3,
        max_output_tokens: int = 512,
    ) -> tuple[AgentService, FakeLLMClient]:
        llm = FakeLLMClient(responses=llm_responses)
        registry: ToolRegistry = build_registry(
            rag_service=rag_service or FakeRagService(),
            lead_service=lead_service or FakeLeadService(),
            escalation_service=escalation_service or FakeEscalationService(),
        )
        agent = AgentService(
            llm_client=llm,
            memory_service=memory_service or FakeMemoryService(),
            tool_registry=registry,
            max_iterations=max_iterations,
            max_output_tokens=max_output_tokens,
        )
        return agent, llm

    return _factory
