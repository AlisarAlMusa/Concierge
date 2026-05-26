# Feature Specification: Agent Service

> **Owner**: Person B — `feature/rag-agent-widget` branch

**Feature Branch**: `009-agent-service`

**Created**: 2026-05-27

**Status**: Draft

---

## User Scenarios & Testing *(mandatory)*

### User Story 1 — Agent Handles a Multi-Step Ambiguous Turn (Priority: P1)

A visitor sends a message that is neither purely FAQ nor purely sales ("I have questions about your product but also want someone to follow up"). The router escalates it to the agent. The agent calls `rag_search` to answer the product question and then `capture_lead` to record the follow-up request — all in one bounded turn.

**Why this priority**: Multi-tool turns are the agent's reason for existing. A turn the router can resolve deterministically must never reach the agent; a turn requiring sequenced tools must always reach it.

**Independent Test**: Send a two-part message to the agent directly. Confirm it calls at least two tools in sequence, returns a coherent answer, and writes a lead row — all within the iteration cap.

**Acceptance Scenarios**:

1. **Given** an ambiguous multi-intent message, **When** the AgentService runs, **Then** it calls the appropriate tools in sequence (up to the iteration cap) and returns a coherent reply.
2. **Given** the agent calls `rag_search`, **When** the search runs, **Then** only tenant-scoped chunks are retrieved.
3. **Given** the agent calls `capture_lead`, **When** the lead is written, **Then** it is scoped to the request's tenant and conversation.
4. **Given** a turn where no tool is appropriate, **When** the agent runs, **Then** it returns a reply without calling any tool.

---

### User Story 2 — Agent Is Bounded: Loop Cap and Token Limit Enforced (Priority: P1)

The agent cannot run more than 3 tool-call iterations per turn and is subject to a per-turn token budget. A hostile visitor who sends a message designed to force long tool chains hits the cap, receives a graceful reply, and does not drive up costs.

**Why this priority**: An unbounded agent is a cost control failure and a security risk. The cap is both a cost control and a safety control.

**Independent Test**: Construct a message that would require 5 tool calls to fully resolve. Confirm the agent stops after 3 iterations, returns a partial-but-graceful reply, and logs the cap being hit.

**Acceptance Scenarios**:

1. **Given** a scenario requiring more than 3 tool calls, **When** the agent reaches iteration 3, **Then** it stops tool-calling and returns a graceful reply (not an error).
2. **Given** a turn approaching the token budget, **When** the budget is exhausted, **Then** the agent stops and returns a truncated-but-safe reply.
3. **Given** a cap hit, **When** cost events are checked, **Then** no more than 3 LLM tool-call rounds are billed for that turn.

---

### User Story 3 — Agent Uses Short-Term Session Memory (Priority: P2)

The agent reads prior messages in the conversation from Redis before each turn. The visitor's context from earlier in the session is available to the agent, enabling coherent multi-turn conversations. The Redis key is scoped by tenant and conversation; TTL is 24 hours.

**Why this priority**: A concierge that forgets the visitor's name one turn after they gave it is useless. Storing anonymous visitor chat forever is a privacy liability — 24h TTL is the tradeoff.

**Independent Test**: Send two messages in sequence. In the second message, reference something from the first. Confirm the agent's reply demonstrates memory of the first turn. Confirm the Redis key uses `memory:{tenant_id}:{conversation_id}` format with a 24h TTL.

**Acceptance Scenarios**:

1. **Given** a prior message in the session, **When** the agent processes the next message, **Then** the prior context is included in the LLM call.
2. **Given** a Redis key at `memory:{tenant_id}:{conversation_id}`, **When** its TTL is checked, **Then** it is ≤ 86400 seconds (24 hours).
3. **Given** a session memory entry, **When** it is inspected, **Then** it contains no unredacted PII (guardrail redaction was applied before storage).

---

### User Story 4 — Prompts Are Version-Controlled; Tenant Persona Is Injected at Runtime (Priority: P2)

The system prompt and reply prompt live in `prompts/` as versioned markdown files. The tenant's configured persona and guardrail config are injected into the system prompt at runtime — never hardcoded.

**Why this priority**: A prompt change with no diff history is an outage you cannot bisect. Tenant persona must be configurable without a code deploy.

**Independent Test**: Change the prompt file and verify git tracks the diff. Change a tenant's persona via the config endpoint and verify the next agent call uses the new persona without a redeploy.

**Acceptance Scenarios**:

1. **Given** a prompt file in `prompts/`, **When** it is modified, **Then** git diff shows the change.
2. **Given** a tenant with a configured persona, **When** the agent runs, **Then** the persona text from the tenant's `guardrail_configs` record is present in the LLM system prompt.
3. **Given** a tenant with no persona configured, **When** the agent runs, **Then** a default platform persona is used from the prompt file.

---

### Edge Cases

- What happens when the LLM API is unavailable? → The agent returns a graceful fallback reply and logs the failure. No tool actions are taken.
- What happens when `capture_lead` is called with incomplete contact information (no email, no phone)? → The lead is written with available fields; the agent asks the visitor for missing contact details.
- What happens when `escalate` is called by the agent? → An escalation row is created, the conversation status is updated, and a handoff reply is returned. The agent does not continue tool-calling after escalation.
- What happens when Redis is unavailable? → The agent proceeds without session memory (graceful degradation); it does not fail the turn.
- What happens when the tenant's guardrail config blocks a topic the visitor asked about? → The guardrails sidecar blocks the output; the agent returns the tenant's configured refusal message.

---

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The AgentService MUST support exactly three tools: `rag_search`, `capture_lead`, `escalate`.
- **FR-002**: The agent MUST cap tool-call iterations at 3 per turn. On hitting the cap, a graceful reply is returned.
- **FR-003**: The agent MUST enforce a per-turn token budget (configurable; default 2000 output tokens). On exceeding the budget, the turn ends gracefully.
- **FR-004**: `rag_search` MUST delegate to RagService which retrieves only tenant-scoped chunks.
- **FR-005**: `capture_lead` MUST schema-validate its payload, write the lead scoped to the request tenant, and be rate-limited per visitor session.
- **FR-006**: `escalate` MUST create an escalation record and update the conversation status. After calling `escalate`, the agent MUST NOT make further tool calls.
- **FR-007**: Session memory MUST be read from Redis at the start of each turn using key `memory:{tenant_id}:{conversation_id}` and written back at the end.
- **FR-008**: The Redis session key TTL MUST be 24 hours. Memory entries MUST contain only redacted content (PII removed).
- **FR-009**: Agent prompts MUST live in version-controlled files under `prompts/`. Tenant persona MUST be injected from `guardrail_configs` at runtime.
- **FR-010**: Every LLM call by the agent MUST be preceded by a guardrail input check and followed by a guardrail output check.
- **FR-011**: Every LLM call MUST produce a `cost_event` row tagged with the tenant's id and `operation=llm`.
- **FR-012**: The agent MUST implement timeout, retry with backoff, and structured error handling for the hosted LLM API call.
- **FR-013**: The agent MUST NOT be invoked for turns that the router can resolve deterministically.

### Key Entities

- **Agent Turn**: A single visitor message → tool-calling LLM loop (max 3 iterations) → reply.
- **Tool**: One of `rag_search`, `capture_lead`, `escalate`. Each has a typed input/output schema.
- **Session Memory Entry**: List of prior `{role, content_redacted}` pairs stored in Redis, keyed by `memory:{tenant_id}:{conversation_id}`, TTL 24h.
- **Prompt Files**: `prompts/system_agent.md`, `prompts/rag_answer.md`, `prompts/refusal.md`.

---

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Agent tool-selection accuracy ≥ 0.80 on the 15-example golden set (CI gate).
- **SC-002**: 100% of agent turns with more than 3 required tool calls end gracefully within the cap — zero infinite loops.
- **SC-003**: Session memory correctly recalls context from the prior turn in 100% of two-turn test scenarios.
- **SC-004**: All tenant-facing prompts reflect the tenant's configured persona within one request of a persona config change.
- **SC-005**: Zero instances of unredacted PII stored in Redis session memory (verified by redaction test).
- **SC-006**: 100% of agent LLM calls produce a corresponding cost event tagged with the correct tenant.

---

## Assumptions

- The agent is a single tool-calling LLM loop — not a multi-agent graph. The router is the orchestrator; the agent handles what the router cannot.
- `capture_lead` is an unauthenticated, LLM-triggered write — the rate limit and schema validation in the tool implementation are the primary spam controls.
- Session memory stores the last N messages (configurable, default 10 turns); older messages are truncated to stay within the token budget.
- Guardrail input/output checks are synchronous HTTP calls to the guardrails sidecar; if the sidecar is unavailable, the agent applies a fail-open policy with a warning log (Week 8 scope).
- The 15-example tool-selection golden set is hand-labelled by Person B during Day 3; the eval script is wired to CI in feature 016.
