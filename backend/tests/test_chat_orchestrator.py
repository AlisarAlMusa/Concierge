"""Unit tests for ChatOrchestrator.

Validates the turn-coordinator contract from
``app/services/chat_orchestrator.py``:

* dispatch fan-out matches ``RouteDecision.path``,
* memory writes happen for agent/human paths and are skipped for drop,
* conversation id is minted when missing,
* input/output guardrails are sequenced around the dispatch,
* escalation failures degrade gracefully,
* the agent is invoked with the correct kwargs and its result is propagated.

No real Redis, DB, or LLM. Every collaborator is a hand-rolled fake that
exposes only the surface the orchestrator touches.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

from app.services.agent_service import AgentTurnResult
from app.services.chat_orchestrator import (
    ChatOrchestrator,
    GuardrailDecision,
    PassthroughGuardrailClient,
)
from app.services.router_service import RouteDecision
from app.services.tools.escalate import EscalateResult

TENANT = UUID("00000000-0000-0000-0000-00000000000a")


# ----- Fakes ----------------------------------------------------------------
class _FakeRouter:
    def __init__(self, decision: RouteDecision) -> None:
        self._decision = decision
        self.calls: list[dict[str, Any]] = []

    async def decide(self, *, text: str, tenant_id: UUID, conversation_id: UUID) -> RouteDecision:
        self.calls.append(
            {"text": text, "tenant_id": tenant_id, "conversation_id": conversation_id}
        )
        return self._decision


class _FakeAgent:
    def __init__(self, result: AgentTurnResult) -> None:
        self._result = result
        self.calls: list[dict[str, Any]] = []

    async def run(self, **kwargs: Any) -> AgentTurnResult:
        self.calls.append(kwargs)
        return self._result


class _FakeMemory:
    def __init__(self) -> None:
        self.appended: list[tuple[UUID, UUID, str, str]] = []

    async def append(self, tenant_id: UUID, conversation_id: UUID, role: str, content: str) -> None:
        self.appended.append((tenant_id, conversation_id, role, content))


class _FakeEscalation:
    def __init__(self, exc: Exception | None = None) -> None:
        self._exc = exc
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> EscalateResult:
        self.calls.append(kwargs)
        if self._exc is not None:
            raise self._exc
        return EscalateResult(escalation_id=uuid4(), status="created")


class _BlockingGuardrail:
    """Input guardrail that blocks every message with a safe_reply."""

    def __init__(self, *, safe_reply: str = "Blocked.") -> None:
        self._safe_reply = safe_reply

    async def check_input(self, **kwargs: Any) -> GuardrailDecision:
        return GuardrailDecision(
            allowed=False,
            redacted_text=kwargs["message"],
            safe_reply=self._safe_reply,
            reason="policy",
        )

    async def check_output(self, **kwargs: Any) -> GuardrailDecision:
        return GuardrailDecision(allowed=True, redacted_text=kwargs["message"])


class _RedactingGuardrail:
    """Approves input/output but redacts both, to verify the orchestrator
    consults the guardrail's ``redacted_text`` rather than the raw message."""

    async def check_input(self, **kwargs: Any) -> GuardrailDecision:
        return GuardrailDecision(allowed=True, redacted_text="[REDACTED_IN]")

    async def check_output(self, **kwargs: Any) -> GuardrailDecision:
        return GuardrailDecision(allowed=True, redacted_text="[REDACTED_OUT]")


# ----- Helpers --------------------------------------------------------------
def _agent_turn(
    reply: str = "hello there",
    sources: list[UUID] | None = None,
    iterations: int = 1,
    used_refusal: bool = False,
) -> AgentTurnResult:
    return AgentTurnResult(
        reply=reply,
        sources=sources or [],
        agent_iterations=iterations,
        used_refusal_fallback=used_refusal,
    )


def _build(
    *,
    decision: RouteDecision,
    agent_result: AgentTurnResult | None = None,
    escalation_exc: Exception | None = None,
    guardrail_client: Any = None,
) -> tuple[
    ChatOrchestrator,
    _FakeRouter,
    _FakeAgent,
    _FakeMemory,
    _FakeEscalation,
]:
    router = _FakeRouter(decision)
    agent = _FakeAgent(agent_result or _agent_turn())
    memory = _FakeMemory()
    escalation = _FakeEscalation(exc=escalation_exc)
    orch = ChatOrchestrator(
        router_service=router,  # type: ignore[arg-type]
        agent_service=agent,  # type: ignore[arg-type]
        memory_service=memory,  # type: ignore[arg-type]
        escalation_service=escalation,  # type: ignore[arg-type]
        guardrail_client=guardrail_client or PassthroughGuardrailClient(),
    )
    return orch, router, agent, memory, escalation


# ----- agent path -----------------------------------------------------------
async def test_agent_path_invokes_agent_and_writes_memory():
    page_id = uuid4()
    orch, router, agent, memory, _ = _build(
        decision=RouteDecision(
            path="agent", reason="ambiguous", confidence=0.4, classifier_label="ambiguous"
        ),
        agent_result=_agent_turn(reply="The bakery is open 7-3.", sources=[page_id]),
    )

    turn = await orch.handle_turn(tenant_id=TENANT, user_message="when do you open?")

    assert turn.reply == "The bakery is open 7-3."
    assert turn.route.path == "agent"
    assert turn.sources == [page_id]
    assert len(agent.calls) == 1
    assert agent.calls[0]["user_message"] == "when do you open?"
    assert agent.calls[0]["tenant_id"] == TENANT
    # Visitor + assistant turns both written.
    roles = [entry[2] for entry in memory.appended]
    contents = [entry[3] for entry in memory.appended]
    assert roles == ["visitor", "assistant"]
    assert contents == ["when do you open?", "The bakery is open 7-3."]
    # Router was called with the same conversation_id we minted.
    assert router.calls[0]["conversation_id"] == turn.conversation_id


async def test_faq_and_sales_fall_back_to_agent_when_workflows_not_wired():
    """When the orchestrator is built without workflow services (the unit-test
    default), faq/sales fall back to the agent loop. The *route decision* is
    still propagated so telemetry shows the would-be workflow path."""
    for path, reason in (("faq", "faq"), ("sales", "sales")):
        orch, _, agent, memory, _ = _build(
            decision=RouteDecision(
                path=path,  # type: ignore[arg-type]
                reason=reason,  # type: ignore[arg-type]
                confidence=0.9,
                classifier_label=path,
            ),
        )
        turn = await orch.handle_turn(tenant_id=TENANT, user_message="ping")

        assert len(agent.calls) == 1, f"{path} should fall back to the agent"
        assert turn.route.path == path
        assert turn.route.classifier_label == path
        assert len(memory.appended) == 2


async def test_faq_path_dispatches_to_faq_workflow_when_wired():
    """With a FaqWorkflow wired in, the faq path bypasses the agent loop."""
    from app.services.workflows.base import WorkflowTurnResult

    class _FakeFaq:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        async def run(self, **kwargs: Any) -> WorkflowTurnResult:
            self.calls.append(kwargs)
            return WorkflowTurnResult(reply="faq answer", sources=[], used_refusal_fallback=False)

    decision = RouteDecision(path="faq", reason="faq", confidence=0.9, classifier_label="faq")
    fake_faq = _FakeFaq()

    router = _FakeRouter(decision)
    agent = _FakeAgent(_agent_turn())
    memory = _FakeMemory()
    escalation = _FakeEscalation()
    orch = ChatOrchestrator(
        router_service=router,  # type: ignore[arg-type]
        agent_service=agent,  # type: ignore[arg-type]
        memory_service=memory,  # type: ignore[arg-type]
        escalation_service=escalation,  # type: ignore[arg-type]
        faq_workflow=fake_faq,  # type: ignore[arg-type]
        guardrail_client=PassthroughGuardrailClient(),
    )

    turn = await orch.handle_turn(tenant_id=TENANT, user_message="hours?")

    assert turn.reply == "faq answer"
    assert len(fake_faq.calls) == 1, "faq workflow must be invoked when wired"
    assert agent.calls == [], "agent must not run when faq workflow is wired"
    assert turn.agent_iterations == 1


async def test_human_path_dispatches_to_human_workflow_when_wired():
    """With a HumanWorkflow wired in, the inline _handle_human path is skipped."""
    from app.services.workflows.base import WorkflowTurnResult

    class _FakeHuman:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        async def run(self, **kwargs: Any) -> WorkflowTurnResult:
            self.calls.append(kwargs)
            return WorkflowTurnResult(
                reply="ack from workflow", sources=[], used_refusal_fallback=False
            )

    decision = RouteDecision(path="human", reason="human", confidence=0.9, classifier_label="human")
    fake_human = _FakeHuman()

    router = _FakeRouter(decision)
    agent = _FakeAgent(_agent_turn())
    memory = _FakeMemory()
    escalation = _FakeEscalation()
    orch = ChatOrchestrator(
        router_service=router,  # type: ignore[arg-type]
        agent_service=agent,  # type: ignore[arg-type]
        memory_service=memory,  # type: ignore[arg-type]
        escalation_service=escalation,  # type: ignore[arg-type]
        human_workflow=fake_human,  # type: ignore[arg-type]
        guardrail_client=PassthroughGuardrailClient(),
    )

    turn = await orch.handle_turn(tenant_id=TENANT, user_message="I need a person")

    assert turn.reply == "ack from workflow"
    assert len(fake_human.calls) == 1
    # The orchestrator did NOT also hit the inline _handle_human path
    # (which would have called escalation.create directly).
    assert escalation.calls == []
    assert [entry[2] for entry in memory.appended] == ["visitor", "assistant"]


async def test_agent_path_propagates_iterations_and_refusal_flag():
    orch, _, _, _, _ = _build(
        decision=RouteDecision(path="agent", reason="low_confidence", confidence=0.1),
        agent_result=_agent_turn(reply="I'm not sure.", iterations=3, used_refusal=True),
    )

    turn = await orch.handle_turn(tenant_id=TENANT, user_message="?")

    assert turn.agent_iterations == 3
    assert turn.used_refusal_fallback is True


# ----- drop path ------------------------------------------------------------
async def test_drop_path_returns_canned_and_skips_memory():
    orch, _, agent, memory, escalation = _build(
        decision=RouteDecision(path="drop", reason="spam"),
    )

    turn = await orch.handle_turn(tenant_id=TENANT, user_message="buy crypto now")

    assert turn.route.path == "drop"
    assert "not able to help" in turn.reply.lower()
    assert agent.calls == [], "agent must not run on drop"
    assert memory.appended == [], "spam must not pollute memory"
    assert escalation.calls == []
    assert turn.used_refusal_fallback is True


# ----- human path -----------------------------------------------------------
async def test_human_path_creates_escalation_and_writes_memory():
    orch, _, agent, memory, escalation = _build(
        decision=RouteDecision(
            path="human", reason="human", confidence=0.9, classifier_label="human"
        ),
    )

    turn = await orch.handle_turn(tenant_id=TENANT, user_message="I need a human")

    assert turn.route.path == "human"
    assert len(escalation.calls) == 1
    assert escalation.calls[0]["tenant_id"] == TENANT
    assert escalation.calls[0]["context"] == "I need a human"
    assert agent.calls == [], "human path does not invoke the agent"
    # Visitor + canned assistant reply persisted.
    assert [entry[2] for entry in memory.appended] == ["visitor", "assistant"]
    assert "human teammate" in turn.reply


async def test_human_path_degrades_gracefully_when_escalation_fails():
    orch, _, _, memory, _ = _build(
        decision=RouteDecision(path="human", reason="human"),
        escalation_exc=RuntimeError("escalations table missing"),
    )

    turn = await orch.handle_turn(tenant_id=TENANT, user_message="I need a human")

    assert "wasn't able to flag" in turn.reply
    assert [entry[2] for entry in memory.appended] == ["visitor", "assistant"]


# ----- conversation id ------------------------------------------------------
async def test_mints_conversation_id_when_missing():
    orch, router, _, _, _ = _build(
        decision=RouteDecision(path="agent", reason="ambiguous"),
    )

    turn = await orch.handle_turn(tenant_id=TENANT, user_message="hi")

    assert isinstance(turn.conversation_id, UUID)
    assert router.calls[0]["conversation_id"] == turn.conversation_id


async def test_uses_incoming_conversation_id_when_provided():
    incoming = uuid4()
    orch, router, _, memory, _ = _build(
        decision=RouteDecision(path="agent", reason="ambiguous"),
    )

    turn = await orch.handle_turn(tenant_id=TENANT, conversation_id=incoming, user_message="hi")

    assert turn.conversation_id == incoming
    assert router.calls[0]["conversation_id"] == incoming
    assert all(entry[1] == incoming for entry in memory.appended)


# ----- guardrail sequencing -------------------------------------------------
async def test_input_guardrail_block_short_circuits_router_and_agent():
    orch, router, agent, memory, _ = _build(
        decision=RouteDecision(path="agent", reason="ambiguous"),
        guardrail_client=_BlockingGuardrail(safe_reply="Try rephrasing."),
    )

    turn = await orch.handle_turn(tenant_id=TENANT, user_message="malicious payload")

    assert turn.reply == "Try rephrasing."
    assert turn.route.path == "drop"
    assert router.calls == [], "blocked input must not reach the router"
    assert agent.calls == []
    assert memory.appended == []


async def test_output_guardrail_redacted_text_never_reaches_user_or_memory():
    """The output guardrail's ``redacted_text`` is a log-grade scrub and must
    never be the visitor-visible reply nor the value persisted to short-term
    memory (subsequent LLM turns must see the original conversation context).
    The durable ``messages.content_redacted`` SQL column stays redacted via
    ``ChatOrchestrator``'s own ``redact()`` call on the way out — that's a
    separate compliance lane and is exercised by ``test_persistence.py``."""
    orch, _, _, memory, _ = _build(
        decision=RouteDecision(path="agent", reason="ambiguous"),
        agent_result=_agent_turn(reply="raw assistant text"),
        guardrail_client=_RedactingGuardrail(),
    )

    turn = await orch.handle_turn(tenant_id=TENANT, user_message="hello")

    # Visitor sees the original assistant reply, NOT ``[REDACTED_OUT]``.
    assert turn.reply == "raw assistant text"
    # Short-term memory stores raw visitor + raw assistant content so the
    # LLM sees real context (e.g. business emails) on later turns.
    visitor_entry = memory.appended[0]
    assistant_entry = memory.appended[1]
    assert visitor_entry == (TENANT, turn.conversation_id, "visitor", "hello")
    assert assistant_entry == (
        TENANT,
        turn.conversation_id,
        "assistant",
        "raw assistant text",
    )


# ----- agent kwargs ---------------------------------------------------------
async def test_agent_receives_route_decision_and_optional_persona():
    decision = RouteDecision(
        path="agent", reason="low_confidence", confidence=0.2, classifier_label="faq"
    )
    orch, _, agent, _, _ = _build(decision=decision)

    visitor_session = uuid4()
    await orch.handle_turn(
        tenant_id=TENANT,
        user_message="hi",
        visitor_session_id=visitor_session,
        tenant_persona="a friendly bakery concierge",
    )

    kwargs = agent.calls[0]
    assert kwargs["tenant_persona"] == "a friendly bakery concierge"
    assert kwargs["visitor_session_id"] == visitor_session
    assert kwargs["route_decision"] is decision
