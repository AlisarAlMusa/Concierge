# Feature Specification: Router Service

> **Owner**: Person B — `feature/rag-agent-widget` branch

**Feature Branch**: `008-router-service`

**Created**: 2026-05-27

**Status**: Implemented (router + orchestrator + workflows; classifier adapter pending Person C)

---

## User Scenarios & Testing *(mandatory)*

### User Story 1 — Spam Message Is Dropped Without LLM Call (Priority: P1)

A visitor sends a spam message (e.g., "buy followers cheap"). The router classifies it as spam with sufficient confidence and drops it — no RAG, no agent, no LLM call. The visitor receives a polite refusal.

**Why this priority**: Spam drops must happen before any paid API call. A spam cannon that bypasses the router would run up LLM costs at the platform's expense.

**Independent Test**: Send a known-spam message through the router. Confirm no LLM call is made, no lead is written, and a refusal response is returned. Check no cost event for `llm` is logged.

**Acceptance Scenarios**:

1. **Given** a message classified as `spam` with confidence ≥ threshold, **When** the router processes it, **Then** the message is dropped, a refusal reply is returned, and no LLM call is made.
2. **Given** a spam drop, **When** cost events are checked, **Then** only a classifier cost event exists — no LLM cost event.
3. **Given** a low-confidence spam classification (below threshold), **When** the router processes it, **Then** it is escalated to the agent rather than dropped.

---

### User Story 2 — FAQ / Support Message Is Answered via RAG (Priority: P1)

A visitor asks a clear support question ("What are your opening hours?"). The router classifies it as `faq_support` with high confidence, runs a RAG search against the tenant's chunks, and returns the answer — no agent, no lead capture.

**Why this priority**: Most traffic is FAQ-style. Routing these directly to RAG avoids an expensive agent turn and keeps the cheap path as the majority path.

**Independent Test**: Send a question that matches a CMS page. Confirm the response uses the page content, no agent tool-call loop runs, and the answer is returned directly.

**Acceptance Scenarios**:

1. **Given** a message classified as `faq_support` with confidence ≥ threshold, **When** the router processes it, **Then** a RAG search is run against the tenant's chunks and the answer is returned without an agent call.
2. **Given** a RAG response, **When** the reply is checked, **Then** it is grounded in the retrieved chunks and does not hallucinate tenant-specific facts.
3. **Given** a `faq_support` classification but no matching chunks, **When** the router processes it, **Then** a graceful "I don't have information on that" reply is returned.

---

### User Story 3 — Sales / Contact Message Triggers Lead Capture (Priority: P1)

A visitor expresses clear purchase intent ("I want pricing, call me"). The router classifies it as `sales_contact` and directly calls the LeadService to write a lead — no full agent turn.

**Why this priority**: High-confidence sales intent is a deterministic workflow. Sending it to the agent adds latency and cost without adding intelligence.

**Independent Test**: Send a message with clear sales intent. Confirm a lead row is created in the tenant's table, no agent is invoked, and a confirmation reply is returned.

**Acceptance Scenarios**:

1. **Given** a message classified as `sales_contact` with confidence ≥ threshold, **When** the router processes it, **Then** LeadService is called to capture the lead and a confirmation reply is returned.
2. **Given** a lead captured by the router, **When** the lead is inspected, **Then** it is scoped to the correct tenant and has a valid `source=router` field.
3. **Given** the visitor session has already hit the lead rate limit, **When** a sales message is routed, **Then** the lead is not written and a polite "already noted" reply is returned.

---

### User Story 4 — Explicit Human Request Triggers Escalation (Priority: P1)

A visitor asks to speak to a person ("I need to talk to someone"). The router classifies it as `human_request` and creates an escalation record — no agent, no RAG.

**Why this priority**: Human escalation is a deterministic signal. It should never loop through an agent that might argue back.

**Independent Test**: Send "Can I speak to a person?" Confirm an escalation row is created, no agent is invoked, and a handoff reply is returned.

**Acceptance Scenarios**:

1. **Given** a message classified as `human_request` with confidence ≥ threshold, **When** the router processes it, **Then** an escalation is created and a handoff reply is returned.
2. **Given** an escalation created by the router, **When** the record is inspected, **Then** it is scoped to the correct tenant and conversation.

---

### User Story 5 — Ambiguous Message Is Escalated to the Agent (Priority: P2)

A message that is ambiguous or multi-step ("I'm interested but also have a question about your refund policy") is passed to the AgentService for tool-calling reasoning.

**Why this priority**: The agent earns its slot only for turns the workflow cannot resolve. Ensuring the easy cases don't reach the agent is as important as ensuring the hard ones do.

**Independent Test**: Send a message designed to be ambiguous (low classifier confidence or multi-step). Confirm the AgentService is invoked. Confirm simple FAQ and spam messages are never routed to the agent.

**Acceptance Scenarios**:

1. **Given** a message with classifier confidence below the routing threshold (or label `ambiguous`), **When** the router processes it, **Then** the AgentService is invoked.
2. **Given** a message with a clearly deterministic label at high confidence, **When** the router processes it, **Then** the AgentService is NOT invoked.
3. **Given** 100 test messages spanning all 5 intents, **When** the router processes them, **Then** the agent handles fewer than 30% (the expensive path is the minority).

---

### Edge Cases

- What happens when the model server is unavailable? → The router falls back to routing all messages to the agent (safe degradation, not a hard failure).
- What happens when the confidence threshold is at the boundary (exactly equal)? → Messages at or above threshold take the deterministic path; below goes to agent.
- What happens when a `sales_contact` message contains no extractable contact information? → The lead is created with the available intent and a note; the visitor is asked for contact details.
- What happens when the RLS context is not set when the router runs? → This is a programming error — the router must always run within an authenticated request context.

---

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The RouterService MUST call the classifier client (`ClassifierClient.classify`) for every inbound message. In production this client is the HTTP adapter that targets the model server's `/predict-intent` endpoint.
- **FR-002**: The RouterService MUST route based on the returned label and a configurable confidence threshold.
- **FR-003**: `spam` at confidence ≥ threshold MUST be returned as `RouteDecision(path="drop")`. The dispatcher (ChatOrchestrator) returns a canned refusal and writes no memory / lead / escalation.
- **FR-004**: `faq` at confidence ≥ threshold MUST be returned as `RouteDecision(path="faq")`. The dispatcher routes to `FaqWorkflow` (one-shot RAG-grounded LLM call, no tools).
- **FR-005**: `sales` at confidence ≥ threshold MUST be returned as `RouteDecision(path="sales")`. The dispatcher routes to `SalesWorkflow` (sales-leaning persona, same single-call shape as FAQ).
- **FR-006**: `human` at confidence ≥ threshold MUST be returned as `RouteDecision(path="human")`. The dispatcher routes to `HumanWorkflow` which calls `EscalationService.create` and returns the canned handoff reply.
- **FR-007**: `ambiguous` label OR any label with confidence below threshold MUST be returned as `RouteDecision(path="agent")`. The dispatcher routes to `AgentService` (the bounded tool-calling loop).
- **FR-008**: The classifier client MUST be the only place that owns timeout/retry. When it raises `ExternalServiceError`, RouterService MUST fail open with `RouteDecision(path="agent", reason="classifier_unavailable")`.
- **FR-009**: The routing decision (`path`, `reason`, `classifier_label`, `confidence`) MUST be emitted as a structured log event (`router.decision` or `router.classifier_unavailable`). The raw user message MUST NOT appear in the log.
- **FR-010**: The RouterService MUST operate entirely within the tenant context set by the request lifecycle — it MUST NOT read `tenant_id` from message content.
- **FR-011**: The confidence threshold MUST be configurable via `Settings.ROUTER_CONFIDENCE_THRESHOLD` (default `0.75`, per `docs/SPEC.md §4`) without a code change.
- **FR-012**: RouterService MUST be a pure decision function. It MUST NOT call `RagService`, `LeadService`, `EscalationService`, `AgentService`, Postgres, or Redis. Dispatching is the sole responsibility of `ChatOrchestrator`.

### Key Entities

- **Routing Decision** (`RouteDecision`): `path` (`drop` | `faq` | `sales` | `human` | `agent`), `reason` (one of `faq` | `sales` | `human` | `spam` | `ambiguous` | `low_confidence` | `unknown_label` | `classifier_unavailable`), `classifier_label` (raw string from classifier, preserved for observability), `confidence` (float, `None` only on `classifier_unavailable`).
- **Confidence Threshold**: Configurable float (default `0.75`); determines when a label is "certain enough" for the deterministic path.
- **Closed Label Set**: `{faq, sales, human, spam, ambiguous}`. Any label outside this set → `path="agent"`, `reason="unknown_label"`.

---

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: The router keeps ≥ 70% of test messages on the deterministic path (off the agent).
- **SC-002**: Spam messages incur zero LLM cost events (100% dropped before LLM invocation).
- **SC-003**: Router total latency (classifier call + routing logic) adds ≤ 150ms to the request in p95.
- **SC-004**: Safe degradation: when the model server is unavailable, 100% of messages fall through to the agent (zero hard failures).
- **SC-005**: Routing decision is logged for every inbound message — 100% coverage, enabling cost and path analysis.

---

## Assumptions

- The confidence threshold default of `0.75` is a starting point; the team validates it against the golden set during eval.
- The router does not persist its routing decision as a DB row in Week 8; it is logged for observability but not stored.
- The router is implemented as a service class (`RouterService`) called by `ChatOrchestrator` — not as a separate microservice.
- The FAQ workflow produces its grounded reply by issuing one `LLMClient.tool_complete` call with `tools=[]` and the retrieved chunks injected into the user message. The workflow is single-shot by design.
- The Sales workflow mirrors the FAQ workflow's single-shot shape with a sales-leaning persona. It does not call `LeadService.capture` directly; lead capture remains the agent's job for ambiguous turns. (Direct workflow capture is a deferred Phase 2 option once the assignment's "single deterministic step" requirement is met.)
- The Human workflow wraps `EscalationService.create` only — no LLM, no RAG.

---

## Implementation Addendum (Owner B — frozen contracts)

> Merged from the retired `specs/{router-service,chat-orchestrator,workflow-services}/spec.md` files (May 2026). This section is the source of truth for the implemented surfaces; the FRs above are the assignment-level requirements those surfaces satisfy.

### A. RouterService (`backend/app/services/router_service.py`)

Pure decision function. No DB, no Redis, no HTTP, no dispatch.

```python
class ClassifierResponse(BaseModel):
    label: str
    confidence: float          # 0.0 .. 1.0

class ClassifierClient(Protocol):
    async def classify(self, *, text: str) -> ClassifierResponse: ...

RoutePath = Literal["faq", "sales", "human", "agent", "drop"]
RouteReason = Literal[
    "faq", "sales", "human", "spam", "ambiguous",
    "low_confidence", "unknown_label", "classifier_unavailable",
]

class RouteDecision(BaseModel):
    path: RoutePath
    reason: RouteReason
    confidence: float | None = None
    classifier_label: str | None = None

KNOWN_LABELS = frozenset({"faq", "sales", "human", "spam", "ambiguous"})

class RouterService:
    def __init__(self, *, classifier_client: ClassifierClient,
                 confidence_threshold: float) -> None: ...
    async def decide(self, *, text: str, tenant_id: UUID,
                     conversation_id: UUID) -> RouteDecision: ...
```

#### Routing policy (frozen)

| Condition | `path` | `reason` |
|---|---|---|
| Classifier raises `ExternalServiceError` | `agent` | `classifier_unavailable` |
| `confidence < threshold` (any label) | `agent` | `low_confidence` |
| Label not in `KNOWN_LABELS` | `agent` | `unknown_label` |
| Confident `"spam"` | `drop` | `spam` |
| Confident `"ambiguous"` | `agent` | `ambiguous` |
| Confident `"faq"` | `faq` | `faq` |
| Confident `"sales"` | `sales` | `sales` |
| Confident `"human"` | `human` | `human` |

#### Invariants

1. Confidence guard runs **before** label dispatch — a low-confidence `"spam"` label routes to `agent`, never `drop`.
2. Threshold is inclusive on the upper side: `confidence >= threshold` passes.
3. Constructor rejects thresholds outside `[0.0, 1.0]` with `ValueError`.
4. Only `ExternalServiceError` is treated as recoverable; other exceptions propagate.
5. `confidence` and `classifier_label` are `None` only on the `classifier_unavailable` path.

### B. ChatOrchestrator (`backend/app/services/chat_orchestrator.py`)

The single turn coordinator. Owns: guardrail sequencing, router dispatch, memory writes, SQL message persistence, and the dispatch fanout to workflows / agent.

```python
class ChatOrchestrator:
    def __init__(
        self, *,
        router_service: RouterService,
        agent_service: AgentService,
        memory_service: MemoryService,
        escalation_service: EscalationService,
        conversation_service: ConversationService,
        faq_workflow: FaqWorkflow | None = None,
        sales_workflow: SalesWorkflow | None = None,
        human_workflow: HumanWorkflow | None = None,
        guardrail_client: GuardrailClient | None = None,  # passthrough by default
    ) -> None: ...

    async def handle_turn(
        self, *,
        tenant_id: UUID, user_message: str,
        conversation_id: UUID | None = None,
        visitor_session_id: UUID | None = None,
        widget_id: UUID | None = None,
        tenant_persona: str | None = None,
    ) -> ChatTurn: ...

class ChatTurn(BaseModel):
    reply: str
    conversation_id: UUID
    route: RouteDecision
    sources: list[UUID] = []
    used_refusal_fallback: bool = False
    agent_iterations: int = 0
```

#### Dispatch table (frozen)

| `RouteDecision.path` | Handler | Memory write | SQL message write |
|---|---|---|---|
| `drop` | canned reply | **skipped** | **skipped** |
| `human` | `HumanWorkflow.run` (else inline `EscalationService.create`) | written | written |
| `faq` | `FaqWorkflow.run` (else `AgentService.run`) | written | written |
| `sales` | `SalesWorkflow.run` (else `AgentService.run`) | written | written |
| `agent` | `AgentService.run` | written | written |

#### Invariants

1. One router call per turn. No re-routing inside the agent or after dispatch.
2. Memory is written by the orchestrator only. `AgentService` reads memory; the workflows neither read nor write.
3. Memory and SQL persistence are skipped on `drop` (spam must not pollute history).
4. `conversation_id` is minted via `ConversationService.get_or_create` so message inserts have a valid FK from turn one.
5. `input_check.redacted_text` flows into router / agent / memory — never the raw user message.
6. `output_check.redacted_text` flows into memory + the response.
7. `EscalationService` failures don't crash the turn — a canned fallback reply is returned and logged.
8. The orchestrator never silently catches `ExternalServiceError` from `AgentService` — that propagates so the global handler can return 503.

### C. Workflow services (`backend/app/services/workflows/`)

Every workflow exposes the same async surface:

```python
class WorkflowTurnResult(BaseModel):
    reply: str
    sources: list[UUID] = []
    used_refusal_fallback: bool = False

class WorkflowService(Protocol):
    async def run(
        self, *,
        tenant_id: UUID, conversation_id: UUID,
        visitor_session_id: UUID | None,
        user_message: str, tenant_persona: str | None,
        route_decision: RouteDecision,
    ) -> WorkflowTurnResult: ...
```

`FaqWorkflow` (`workflows/faq.py`): `rag_service.search` → if no chunks return refusal → otherwise build one-shot message list (`system_faq.md` + `faq_user.md`) → `llm_client.tool_complete(tools=[], …)` → return reply + deduped sources.

`SalesWorkflow` (`workflows/sales.py`): identical to FAQ but with `system_sales.md`.

`HumanWorkflow` (`workflows/human.py`): pure `EscalationService.create` wrapper. No LLM, no RAG. On exception → canned failure reply, `used_refusal_fallback=True`.

#### Invariants

1. **No tools.** Workflows call `tool_complete(messages, tools=[], …)`. `ToolRegistry` is not constructed for workflow paths.
2. One LLM call per turn (FAQ / Sales) or zero (Human).
3. Workflows write neither memory nor SQL — the orchestrator keeps owning persistence.
4. `RouterService` stays pure: workflows are constructed by DI and dispatched by `ChatOrchestrator`; the router never knows about them.
5. Empty LLM content → refusal fallback (`prompts/refusal.md`), `used_refusal_fallback=True`.

### D. Settings (frozen)

| Setting | Default | Source |
|---|---|---|
| `ROUTER_CONFIDENCE_THRESHOLD` | `0.75` | `Settings` (per `docs/SPEC.md §4`) |
| `CLASSIFIER_ENABLED` | `False` (Week 8 default; flips to `True` once model_server runs) | `Settings` |
| `MEMORY_TTL_SECONDS` | `86400` | `Settings` → `MemoryService` |
| `MEMORY_MAX_ENTRIES` | `40` | `Settings` → `MemoryService` |

The orchestrator itself takes no settings — every knob is consumed by the collaborator that owns it.

### E. Test coverage (frozen counts)

| Surface | File | Scenarios |
|---|---|---|
| `RouterService` | `backend/tests/test_router_service.py` | 10 |
| `ChatOrchestrator` | `backend/tests/test_chat_orchestrator.py` | 13 (11 original + 2 workflow dispatch) |
| `FaqWorkflow`/`SalesWorkflow`/`HumanWorkflow` | `backend/tests/test_workflows.py` | 10 |
| Classifier DI | `backend/tests/test_classifier_di.py` | 2 |

### F. Implementation status

| Component | Status |
|---|---|
| `RouterService` + closed-label policy | Implemented |
| `ClassifierResponse` / `ClassifierClient` Protocol | Implemented |
| `UnavailableClassifierClient` stub | Implemented |
| `HttpClassifierClient` (env-conditional via `Settings.CLASSIFIER_ENABLED`) | Implemented |
| `ChatOrchestrator.handle_turn` | Implemented |
| `FaqWorkflow` / `SalesWorkflow` / `HumanWorkflow` | Implemented |
| Dispatch table (drop/faq/sales/human/agent) | Implemented |
| DI providers (`get_router_service`, `get_chat_orchestrator`, `get_*_workflow`) | Implemented |
| Real guardrails sidecar wiring (`HttpGuardrailClient`) | Pending — Person C |
| Real `HttpClassifierClient` enabled in compose | Pending — model_server must be up |

### G. Future integration points

- **Guardrails sidecar (Person C)**: build a `HttpGuardrailClient` mirroring `HttpClassifierClient` and bind it in `get_chat_orchestrator`. `PassthroughGuardrailClient` remains the test + local fallback.
- **Classifier (Person C)**: flip `Settings.CLASSIFIER_ENABLED=True` once the model_server is up; DI already returns `HttpClassifierClient` in that case. The router fails open if it's not.
- **Direct lead capture in `SalesWorkflow`**: deferred Phase 2 option. Requires extracting contact info from the user message without breaking the single-step deterministic contract.
