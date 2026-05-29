"""RouterService — pure decision function for inbound chat turns.

Owner B (agent architecture + routing).

Architecture invariants (frozen, see docs/SPEC.md and concierge_CLAUDE_plan.md):
* RouterService is a *pure decision* service. It does NOT dispatch.
* No DB, Redis, or HTTP I/O performed here. The classifier is reached through
  a duck-typed `classifier_client.classify(text=...)` whose concrete adapter
  for `model_server` is owned by the clients layer and wired by DI later.
* The orchestrator (ChatOrchestrator, future) inspects RouteDecision.path and
  dispatches to RagService / SalesService / EscalationService / AgentService.
* On classifier failure we fail open to the agent: a degraded classifier must
  never silently drop user traffic.

Routing rules (frozen):
    spam              → drop
    low confidence    → agent
    unknown label     → agent
    ambiguous         → agent
    faq               → faq
    sales             → sales
    human             → human
"""

from __future__ import annotations

from typing import Literal, Protocol
from uuid import UUID

import structlog
from opentelemetry import trace
from pydantic import BaseModel, Field

from app.core.errors import ExternalServiceError

_tracer = trace.get_tracer(__name__)

# Closed set of labels the classifier may return. Anything outside this set is
# treated as `unknown_label` and routed to the agent for safe handling.
KNOWN_LABELS: frozenset[str] = frozenset({"faq", "sales", "human", "spam", "ambiguous"})


# `path` is the dispatch branch ChatOrchestrator will use. AgentService already
# reads this attribute via getattr(route_decision, "path", None) for logging,
# so renaming would silently break observability there.
RoutePath = Literal["faq", "sales", "human", "agent", "drop"]

RouteReason = Literal[
    "faq",
    "sales",
    "human",
    "spam",
    "ambiguous",
    "low_confidence",
    "unknown_label",
    "classifier_unavailable",
]


class ClassifierResponse(BaseModel):
    """Boundary contract for the classifier client.

    The concrete adapter to `model_server` (clients layer, future) is
    responsible for translating the upstream HTTP shape into this model.
    """

    label: str
    confidence: float = Field(ge=0.0, le=1.0)


class ClassifierClient(Protocol):
    """Duck-typed classifier interface. Any object with this awaitable works."""

    async def classify(self, *, text: str) -> ClassifierResponse: ...


class RouteDecision(BaseModel):
    """Result of one router call. Read-only data passed to ChatOrchestrator."""

    path: RoutePath
    reason: RouteReason
    confidence: float | None = None
    classifier_label: str | None = None


class RouterService:
    """Decide which downstream path handles a turn — pure over classifier output.

    The service is intentionally stateless beyond its constructor arguments so
    it stays trivially testable and safe to use as a request-scoped dependency.
    """

    def __init__(
        self,
        *,
        classifier_client: ClassifierClient,
        confidence_threshold: float,
    ) -> None:
        if not 0.0 <= confidence_threshold <= 1.0:
            raise ValueError("confidence_threshold must be in [0.0, 1.0]")
        self._classifier = classifier_client
        self._threshold = confidence_threshold
        self._log = structlog.get_logger(__name__)

    async def decide(
        self,
        *,
        text: str,
        tenant_id: UUID,
        conversation_id: UUID,
    ) -> RouteDecision:
        # Spec 017 FR-017 — wrap the routing decision in a span carrying the
        # intent label and confidence so Phoenix shows the chosen path inline
        # with the rest of the chat trace. The span name is `router.classify`
        # per the FR (the method is `decide` in this codebase — same operation).
        with _tracer.start_as_current_span("router.classify") as span:
            log = self._log.bind(
                tenant_id=str(tenant_id),
                conversation_id=str(conversation_id),
            )

            try:
                classification = await self._classifier.classify(text=text)
            except ExternalServiceError as exc:
                # Fail open: a degraded classifier must not drop user traffic.
                decision = RouteDecision(path="agent", reason="classifier_unavailable")
                log.warning(
                    "router.classifier_unavailable",
                    error=str(exc),
                    decision=decision.path,
                    reason=decision.reason,
                )
                span.set_attribute("router.intent_label", "")
                span.set_attribute("router.confidence", 0.0)
                span.set_attribute("router.reason", decision.reason)
                return decision

            decision = self._decide_from_classification(classification)

            log.info(
                "router.decision",
                decision=decision.path,
                reason=decision.reason,
                confidence=decision.confidence,
                classifier_label=decision.classifier_label,
            )
            span.set_attribute(
                "router.intent_label", decision.classifier_label or ""
            )
            span.set_attribute(
                "router.confidence", float(decision.confidence or 0.0)
            )
            span.set_attribute("router.path", decision.path)
            span.set_attribute("router.reason", decision.reason)
            return decision

    def _decide_from_classification(self, classification: ClassifierResponse) -> RouteDecision:
        """Pure mapping from a successful classification to a RouteDecision."""
        label = classification.label
        confidence = classification.confidence

        # Order matters: confidence and label-validity guards run BEFORE the
        # label dispatch. A low-confidence "spam" classification must not drop
        # a user's message — we route to the agent so the user still gets a
        # response and a human can review the conversation if needed.
        if confidence < self._threshold:
            return RouteDecision(
                path="agent",
                reason="low_confidence",
                confidence=confidence,
                classifier_label=label,
            )
        if label not in KNOWN_LABELS:
            return RouteDecision(
                path="agent",
                reason="unknown_label",
                confidence=confidence,
                classifier_label=label,
            )

        path: RoutePath
        reason: RouteReason
        if label == "spam":
            path, reason = "drop", "spam"
        elif label == "ambiguous":
            path, reason = "agent", "ambiguous"
        elif label == "faq":
            path, reason = "faq", "faq"
        elif label == "sales":
            path, reason = "sales", "sales"
        else:  # label == "human" (only remaining KNOWN_LABEL)
            path, reason = "human", "human"

        return RouteDecision(
            path=path,
            reason=reason,
            confidence=confidence,
            classifier_label=label,
        )
