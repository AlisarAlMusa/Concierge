"""ChatOrchestrator — one turn of the chat conversation, end to end.

Sequences the per-turn pipeline: route → dispatch → write memory → return.
Owned by Person B alongside ``AgentService`` and ``RouterService``.

Architectural invariants (frozen):

* ``RouterService`` stays a *pure decision* function. The orchestrator owns
  the ``RoutePath`` → handler fan-out. Spam is dropped, ``human`` creates an
  escalation directly, every other path runs through ``AgentService``.
* ``AgentService`` is the only path that exercises the tool registry.
* Memory writes are the orchestrator's responsibility. ``AgentService`` reads
  history; it does not write turns back. Writing here keeps the contract for
  reads vs. writes unambiguous.
* Guardrails are sequenced here (input → route → dispatch → output). The
  guardrails sidecar belongs to Person C; this class accepts a
  ``GuardrailClient`` Protocol so the chain wires once and the implementation
  swaps in by DI when it lands.
* Conversation id management: a new turn with no incoming id mints a fresh
  ``UUID4`` so memory keys are well-defined from the first turn.

Out of scope this phase: streaming, long-term memory, hybrid retrieval,
guardrail sidecar (Protocol stub only), workflow services for faq/sales
(routed to the agent today; dedicated handlers land later).

Owner: Person B.
"""

from __future__ import annotations

from typing import Any, Protocol
from uuid import UUID, uuid4

import structlog
from opentelemetry import trace
from pydantic import BaseModel

from app.core.redaction import redact
from app.core.tracing import set_request_baggage
from app.models.conversation import MessageRole
from app.services.agent_service import AgentService, AgentTurnResult
from app.services.conversation_service import ConversationService
from app.services.memory_service import MemoryService
from app.services.router_service import RouteDecision, RouterService
from app.services.tools.escalate import EscalateResult
from app.services.workflows import (
    FaqWorkflow,
    HumanWorkflow,
    SalesWorkflow,
    WorkflowTurnResult,
)

logger = structlog.get_logger(__name__)
_tracer = trace.get_tracer(__name__)

# Canned visitor-facing replies for the non-agent route paths. Kept inline so
# the orchestrator stays a single self-contained turn coordinator. Promote to
# prompts/*.md if/when these need persona-aware variants.
_SPAM_REPLY = "I'm not able to help with that."
_HUMAN_REPLY = (
    "I've flagged this conversation for a human teammate — someone will follow up with you shortly."
)
_ESCALATION_FAILURE_REPLY = (
    "I wasn't able to flag this for a human just now. Please try again in a moment "
    "or use the business's contact form."
)


# ----- Duck-typed collaborators ---------------------------------------------
class LeadService(Protocol):
    """Subset of LeadService the orchestrator depends on (via the tool registry)."""

    # No methods on the orchestrator surface — the agent calls capture(...) via
    # the tool registry, not the orchestrator. Kept here so DI can type-check
    # the wiring chain in one place.


class EscalationService(Protocol):
    """``human``-path handler. Same shape as the ``escalate`` tool service."""

    async def create(
        self,
        *,
        tenant_id: UUID,
        conversation_id: UUID,
        reason: str,
        context: str | None,
    ) -> EscalateResult: ...


class GuardrailClient(Protocol):
    """Person C's sidecar (SPEC §5). Stubbed today via ``PassthroughGuardrailClient``."""

    async def check_input(
        self, *, message: str, tenant_id: UUID, conversation_id: UUID
    ) -> "GuardrailDecision": ...

    async def check_output(self, *, message: str, tenant_id: UUID) -> "GuardrailDecision": ...


class GuardrailDecision(BaseModel):
    """Local mirror of the guardrails sidecar response shape (SPEC §5).

    Kept inline so the orchestrator does not depend on Person C's schemas
    landing first. Promoted to ``schemas/`` if/when more code needs it.
    """

    allowed: bool
    redacted_text: str
    safe_reply: str | None = None
    reason: str | None = None


class PassthroughGuardrailClient:
    """Default guardrail client until the sidecar is wired.

    Approves every input and output unchanged. The orchestrator's behavior is
    identical to "no guardrails" — Person C's real client substitutes in via
    DI without any change to this file.
    """

    async def check_input(
        self, *, message: str, tenant_id: UUID, conversation_id: UUID
    ) -> GuardrailDecision:
        return GuardrailDecision(allowed=True, redacted_text=message)

    async def check_output(self, *, message: str, tenant_id: UUID) -> GuardrailDecision:
        return GuardrailDecision(allowed=True, redacted_text=message)


# ----- Public output shape --------------------------------------------------
class ChatTurn(BaseModel):
    """What ``handle_turn`` returns to the chat route.

    Mirrors ``app.schemas.chat.ChatResponse`` but carries native UUIDs so the
    route can format them however the API contract requires.
    """

    reply: str
    conversation_id: UUID
    route: RouteDecision
    sources: list[UUID] = []
    used_refusal_fallback: bool = False
    agent_iterations: int = 0


# ----- Orchestrator ---------------------------------------------------------
class ChatOrchestrator:
    """One-turn coordinator. Stateless beyond its constructor arguments.

    Construction shape:

    .. code-block:: python

        ChatOrchestrator(
            router_service=...,
            agent_service=...,
            memory_service=...,
            escalation_service=...,
            guardrail_client=PassthroughGuardrailClient(),
        )
    """

    def __init__(
        self,
        *,
        router_service: RouterService,
        agent_service: AgentService,
        memory_service: MemoryService,
        escalation_service: EscalationService,
        conversation_service: ConversationService | None = None,
        faq_workflow: FaqWorkflow | None = None,
        sales_workflow: SalesWorkflow | None = None,
        human_workflow: HumanWorkflow | None = None,
        guardrail_client: GuardrailClient | None = None,
    ) -> None:
        self._router = router_service
        self._agent = agent_service
        self._memory = memory_service
        self._escalation = escalation_service
        # ConversationService is optional so unit tests can construct the
        # orchestrator without an AsyncSession. Production DI always provides
        # one; a None instance disables durable persistence (Redis-only).
        self._conversations = conversation_service
        # Workflow services are optional too — unit tests without these fall
        # back to the inline ``_handle_human`` and agent dispatch (the
        # pre-workflow behavior). Production DI always wires all three.
        self._faq = faq_workflow
        self._sales = sales_workflow
        self._human = human_workflow
        self._guardrails = guardrail_client or PassthroughGuardrailClient()

    async def handle_turn(
        self,
        *,
        tenant_id: UUID,
        user_message: str,
        conversation_id: UUID | None = None,
        visitor_session_id: UUID | None = None,
        widget_id: UUID | None = None,
        tenant_persona: str | None = None,
    ) -> ChatTurn:
        """Run one chat turn end to end.

        Flow:

        1. Mint conversation id if absent.
        2. Input guardrail check. Blocked → safe_reply, no router/agent call.
        3. Router decision (pure; classifier outages fail open to agent).
        4. Dispatch fanout on ``decision.path``.
        5. Output guardrail check on the assistant reply.
        6. Write redacted visitor + assistant turns to memory (skipped for
           ``drop`` so spam doesn't pollute conversation history).
        7. Return ``ChatTurn``.

        Spec 017 FR-016 — wrapped in ``chat.handle_turn`` root span carrying
        ``chat.visitor_message.length``, ``chat.tenant_id``,
        ``chat.conversation_id``. The conversation_id baggage is attached as
        soon as the id is resolved/minted so every child span downstream
        (router, guardrails, agent, tool, HTTPX client to sidecars) inherits
        it via ``BaggageSpanProcessor``.
        """
        with _tracer.start_as_current_span("chat.handle_turn") as _root_span:
            return await self._handle_turn_inner(
                _root_span,
                tenant_id=tenant_id,
                user_message=user_message,
                conversation_id=conversation_id,
                visitor_session_id=visitor_session_id,
                widget_id=widget_id,
                tenant_persona=tenant_persona,
            )

    async def _handle_turn_inner(
        self,
        _root_span: Any,
        *,
        tenant_id: UUID,
        user_message: str,
        conversation_id: UUID | None,
        visitor_session_id: UUID | None,
        widget_id: UUID | None,
        tenant_persona: str | None,
    ) -> ChatTurn:
        _root_span.set_attribute(
            "chat.visitor_message.length", len(user_message or "")
        )
        _root_span.set_attribute("chat.tenant_id", str(tenant_id))

        conv_id = conversation_id or uuid4()
        # Resolve / mint a durable conversation row up front so every
        # message we persist below has a valid FK target. Skipped when the
        # orchestrator was constructed without a ConversationService (unit
        # tests; Redis-only mode).
        if self._conversations is not None:
            conversation = await self._conversations.get_or_create(
                tenant_id=tenant_id,
                conversation_id=conv_id,
                widget_id=widget_id,
                visitor_session_id=visitor_session_id,
            )
            conv_id = conversation.id

        # Spec 017 FR-014 — attach conversation_id to baggage so every
        # downstream span inherits it. tenant_id was set at the route layer.
        set_request_baggage({"conversation_id": str(conv_id)})
        _root_span.set_attribute("chat.conversation_id", str(conv_id))

        log = logger.bind(
            tenant_id=str(tenant_id),
            conversation_id=str(conv_id),
            visitor_session_id=str(visitor_session_id) if visitor_session_id else None,
        )

        # 1. Input guardrail. Blocked → safe_reply; do not invoke router/agent.
        input_check = await self._guardrails.check_input(
            message=user_message, tenant_id=tenant_id, conversation_id=conv_id
        )
        if not input_check.allowed:
            log.warning("chat.input_blocked", reason=input_check.reason)
            return await self._finalize(
                conversation_id=conv_id,
                tenant_id=tenant_id,
                user_message=input_check.redacted_text,
                user_message_original=user_message,
                reply=input_check.safe_reply or _SPAM_REPLY,
                route=RouteDecision(path="drop", reason="spam"),
                sources=[],
                used_refusal_fallback=True,
                agent_iterations=0,
                write_memory=False,
            )

        # 2. Route. RouterService is pure and may fail open to agent.
        decision = await self._router.decide(
            text=input_check.redacted_text,
            tenant_id=tenant_id,
            conversation_id=conv_id,
        )

        # 3. Dispatch.
        if decision.path == "drop":
            log.info("chat.dispatch_drop")
            return await self._finalize(
                conversation_id=conv_id,
                tenant_id=tenant_id,
                user_message=input_check.redacted_text,
                user_message_original=user_message,
                reply=_SPAM_REPLY,
                route=decision,
                sources=[],
                used_refusal_fallback=True,
                agent_iterations=0,
                write_memory=False,
            )

        if decision.path == "human":
            if self._human is not None:
                workflow_turn = await self._human.run(
                    tenant_id=tenant_id,
                    conversation_id=conv_id,
                    visitor_session_id=visitor_session_id,
                    user_message=input_check.redacted_text,
                    tenant_persona=tenant_persona,
                    route_decision=decision,
                )
                return await self._finalize(
                    conversation_id=conv_id,
                    tenant_id=tenant_id,
                    user_message=input_check.redacted_text,
                    user_message_original=user_message,
                    reply=workflow_turn.reply,
                    route=decision,
                    sources=workflow_turn.sources,
                    used_refusal_fallback=workflow_turn.used_refusal_fallback,
                    agent_iterations=0,
                    write_memory=True,
                )
            # Legacy inline path (preserved for tests built before the
            # workflow services existed).
            reply, sources = await self._handle_human(
                tenant_id=tenant_id,
                conversation_id=conv_id,
                user_message=input_check.redacted_text,
            )
            return await self._finalize(
                conversation_id=conv_id,
                tenant_id=tenant_id,
                user_message=input_check.redacted_text,
                user_message_original=user_message,
                reply=reply,
                route=decision,
                sources=sources,
                used_refusal_fallback=False,
                agent_iterations=0,
                write_memory=True,
            )

        # faq → FaqWorkflow if wired, else agent. sales → SalesWorkflow if
        # wired, else agent. Both keep the same finalize path so memory and
        # persistence behavior is identical.
        if decision.path == "faq" and self._faq is not None:
            return await self._dispatch_workflow(
                self._faq,
                conv_id=conv_id,
                tenant_id=tenant_id,
                user_message=input_check.redacted_text,
                user_message_original=user_message,
                visitor_session_id=visitor_session_id,
                tenant_persona=tenant_persona,
                decision=decision,
            )
        if decision.path == "sales" and self._sales is not None:
            return await self._dispatch_workflow(
                self._sales,
                conv_id=conv_id,
                tenant_id=tenant_id,
                user_message=input_check.redacted_text,
                user_message_original=user_message,
                visitor_session_id=visitor_session_id,
                tenant_persona=tenant_persona,
                decision=decision,
            )

        # Default: agent loop. Used for path="agent" always, and for faq/sales
        # if the workflow services aren't wired in this construction.
        turn = await self._agent.run(
            tenant_id=tenant_id,
            conversation_id=conv_id,
            user_message=input_check.redacted_text,
            tenant_persona=tenant_persona,
            visitor_session_id=visitor_session_id,
            route_decision=decision,
        )
        return await self._finalize(
            conversation_id=conv_id,
            tenant_id=tenant_id,
            user_message=input_check.redacted_text,
            user_message_original=user_message,
            reply=turn.reply,
            route=decision,
            sources=turn.sources,
            used_refusal_fallback=turn.used_refusal_fallback,
            agent_iterations=turn.agent_iterations,
            write_memory=True,
            agent_turn=turn,
        )

    # ----- internals --------------------------------------------------------
    async def _dispatch_workflow(
        self,
        workflow: Any,
        *,
        conv_id: UUID,
        tenant_id: UUID,
        user_message: str,
        visitor_session_id: UUID | None,
        tenant_persona: str | None,
        decision: RouteDecision,
        user_message_original: str | None = None,
    ) -> ChatTurn:
        """Run a workflow service and shape the result into a ChatTurn.

        Shared by the faq + sales paths so the finalize/persist code lives in
        one place. ``workflow`` is duck-typed to ``WorkflowService`` (see
        ``services/workflows/base.py``). ``user_message_original`` (raw
        visitor text) is forwarded to ``_finalize`` so short-term memory
        stores the unredacted version and subsequent turns of the same
        conversation never echo ``[REDACTED_*]`` back to the LLM.
        """
        result: WorkflowTurnResult = await workflow.run(
            tenant_id=tenant_id,
            conversation_id=conv_id,
            visitor_session_id=visitor_session_id,
            user_message=user_message,
            tenant_persona=tenant_persona,
            route_decision=decision,
        )
        return await self._finalize(
            conversation_id=conv_id,
            tenant_id=tenant_id,
            user_message=user_message,
            user_message_original=user_message_original,
            reply=result.reply,
            route=decision,
            sources=result.sources,
            used_refusal_fallback=result.used_refusal_fallback,
            agent_iterations=1,  # workflows are single-step; surfaced for telemetry parity
            write_memory=True,
        )

    async def _handle_human(
        self,
        *,
        tenant_id: UUID,
        conversation_id: UUID,
        user_message: str,
    ) -> tuple[str, list[UUID]]:
        """``human`` route: create an escalation, return a canned acknowledgement."""
        try:
            await self._escalation.create(
                tenant_id=tenant_id,
                conversation_id=conversation_id,
                reason="router classified intent as human",
                context=user_message,
            )
        except Exception as exc:  # noqa: BLE001 — orchestrator must never crash a turn
            logger.warning(
                "chat.human_escalation_failed",
                tenant_id=str(tenant_id),
                conversation_id=str(conversation_id),
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return _ESCALATION_FAILURE_REPLY, []
        return _HUMAN_REPLY, []

    async def _finalize(
        self,
        *,
        conversation_id: UUID,
        tenant_id: UUID,
        user_message: str,
        reply: str,
        route: RouteDecision,
        sources: list[UUID],
        used_refusal_fallback: bool,
        agent_iterations: int,
        write_memory: bool,
        agent_turn: AgentTurnResult | None = None,
        user_message_original: str | None = None,
    ) -> ChatTurn:
        """Run the output guardrail, persist memory, log, and shape the response.

        ``user_message`` is the post-input-guardrail string used for the current
        turn's routing/dispatch. ``user_message_original`` (when provided by the
        outer turn handler) is the raw visitor text and is preferred for the
        short-term memory write so subsequent LLM turns see real conversation
        context rather than ``[REDACTED_*]`` placeholders. The durable
        ``messages.content_redacted`` SQL column is still written through
        ``redact(...)`` below, preserving the compliance contract for the
        audit-readable conversation history.
        """
        output_check = await self._guardrails.check_output(message=reply, tenant_id=tenant_id)
        # When the output guardrail allows the reply we ship the *original*
        # assistant text to the visitor; ``output_check.redacted_text`` is a
        # log-grade scrub (regex PIIRedactor — sees a real business email as
        # ``[REDACTED_EMAIL]``) and must never reach the user-facing payload.
        # Blocked replies still fall back to the sidecar's ``safe_reply`` or
        # the canned spam line.
        if output_check.allowed:
            final_reply = reply
        else:
            final_reply = output_check.safe_reply or _SPAM_REPLY

        # Short-term memory stores the visitor's raw text + the assistant's
        # original reply so the next LLM turn sees real conversation context.
        memory_user_message = (
            user_message_original if user_message_original is not None else user_message
        )

        if write_memory:
            await self._memory.append(tenant_id, conversation_id, "visitor", memory_user_message)
            await self._memory.append(tenant_id, conversation_id, "assistant", final_reply)
            if self._conversations is not None:
                # Durable ``messages.content_redacted`` SQL column stays
                # redacted (compliance-readable audit history). ``redact()`` is
                # idempotent so applying it to either the original visitor
                # text or the input-guardrail-redacted variant yields the
                # same stored value.
                msg_meta = {
                    "route": route.path,
                    "route_reason": route.reason,
                    "classifier_label": route.classifier_label,
                    "confidence": route.confidence,
                    "agent_iterations": agent_iterations,
                    "used_refusal_fallback": used_refusal_fallback,
                    "sources": [str(s) for s in sources],
                }
                await self._conversations.append_message(
                    tenant_id=tenant_id,
                    conversation_id=conversation_id,
                    role=MessageRole.visitor,
                    content_redacted=redact(memory_user_message),
                    metadata={"role_source": "visitor"},
                )
                await self._conversations.append_message(
                    tenant_id=tenant_id,
                    conversation_id=conversation_id,
                    role=MessageRole.assistant,
                    content_redacted=redact(final_reply),
                    metadata=msg_meta,
                )

        logger.info(
            "chat.turn_completed",
            tenant_id=str(tenant_id),
            conversation_id=str(conversation_id),
            route=route.path,
            route_reason=route.reason,
            confidence=route.confidence,
            classifier_label=route.classifier_label,
            agent_iterations=agent_iterations,
            used_refusal_fallback=used_refusal_fallback,
            sources_count=len(sources),
            wrote_memory=write_memory,
            agent_used=agent_turn is not None,
        )
        return ChatTurn(
            reply=final_reply,
            conversation_id=conversation_id,
            route=route,
            sources=sources,
            used_refusal_fallback=used_refusal_fallback,
            agent_iterations=agent_iterations,
        )
