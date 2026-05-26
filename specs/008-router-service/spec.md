# Feature Specification: Router Service

> **Owner**: Person B — `feature/rag-agent-widget` branch

**Feature Branch**: `008-router-service`

**Created**: 2026-05-27

**Status**: Draft

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

- **FR-001**: The RouterService MUST call the model server's `/predict-intent` endpoint for every inbound message.
- **FR-002**: The RouterService MUST route based on the returned label and a configurable confidence threshold.
- **FR-003**: `spam` at confidence ≥ threshold MUST be dropped (no LLM, no lead, no agent). A refusal reply is returned.
- **FR-004**: `faq_support` at confidence ≥ threshold MUST invoke the RagService and return the answer directly.
- **FR-005**: `sales_contact` at confidence ≥ threshold MUST invoke the LeadService to write a lead and return a confirmation reply.
- **FR-006**: `human_request` at confidence ≥ threshold MUST invoke the EscalationService and return a handoff reply.
- **FR-007**: `ambiguous` label OR any label with confidence below threshold MUST invoke the AgentService.
- **FR-008**: The model server call MUST have a timeout and retry; on failure, route to agent (safe degradation).
- **FR-009**: The routing decision and the classifier label + confidence MUST be logged as a structured event (for eval and cost analysis).
- **FR-010**: The RouterService MUST operate entirely within the tenant context set by the request lifecycle — it MUST NOT read `tenant_id` from message content.
- **FR-011**: The confidence threshold MUST be configurable (default 0.75) without a code change.

### Key Entities

- **Routing Decision**: label, confidence, routing_path (`spam_drop` | `rag_answer` | `lead_capture` | `escalation` | `agent`), classifier latency_ms.
- **Confidence Threshold**: Configurable float (default 0.75); determines when a label is "certain enough" for the deterministic path.

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

- The confidence threshold default of 0.75 is a starting point; the team validates it against the golden set during eval.
- The router does not persist its routing decision as a DB row in Week 8; it is logged for observability but not stored.
- The router is implemented as a service class (`RouterService`) called by the chat endpoint — not as a separate microservice.
- RAG answer is generated by calling the LLM with the retrieved chunks as context; the RouterService delegates this to `RagService` which calls `LLMClient`.
- `sales_contact` lead capture by the router uses a simplified extraction (intent + any visible email/name from the message); full contact extraction is the agent's job for ambiguous cases.
