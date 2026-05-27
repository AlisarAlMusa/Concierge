# Feature Specification: Agent Service

> **Owner**: Person B — `feature/rag-agent-widget` branch

**Feature Branch**: `009-agent-service`

**Created**: 2026-05-27

**Status**: Implemented (agent loop + tool registry + memory + Groq LLM client; production wiring complete)

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

- The agent is a single tool-calling LLM loop — not a multi-agent graph. `ChatOrchestrator` dispatches; the agent handles what the router cannot.
- `capture_lead` is an unauthenticated, LLM-triggered write — the rate limit and schema validation in the tool implementation are the primary spam controls.
- Session memory stores the last `MEMORY_MAX_ENTRIES` entries (default `40`); older messages are LTRIMmed inside `MemoryService.append`.
- Guardrail input/output checks happen in `ChatOrchestrator` (before and after the agent), not in `AgentService`. The agent never calls the sidecar directly.
- The 15-example tool-selection golden set is hand-labelled by Person B during Day 3; the eval script is wired to CI in feature 016.
- The hosted LLM is Groq (`GroqLLMClient`); a provider swap means replacing `app/services/llm_client.py` only — `AgentService` does not change.

---

## Implementation Addendum (Owner B — frozen contracts)

> Merged from the retired `specs/{agent-service,tool-registry,memory-service,llm-client}/spec.md` files (May 2026). This section is the source of truth for the implemented surfaces.

### A. AgentService (`backend/app/services/agent_service.py`)

```python
class LLMToolCall(BaseModel):
    id: str
    name: str
    arguments: dict[str, Any] | str       # JSON string or already-parsed dict

class LLMResponse(BaseModel):
    content: str | None = None
    tool_calls: list[LLMToolCall] = []

class AgentTurnResult(BaseModel):
    reply: str
    sources: list[UUID] = []
    agent_iterations: int
    used_refusal_fallback: bool = False

class AgentService:
    def __init__(
        self, *,
        llm_client,            # duck-typed; production is GroqLLMClient
        memory_service: MemoryService,
        tool_registry: ToolRegistry,
        max_iterations: int,   # bound: hard cap, not advisory
        max_output_tokens: int,
    ) -> None: ...

    async def run(
        self, *,
        tenant_id: UUID, conversation_id: UUID, user_message: str,
        tenant_persona: str | None = None,
        visitor_session_id: UUID | None = None,
        route_decision: Any = None,    # for logging only; read via getattr
    ) -> AgentTurnResult: ...
```

`LLMResponse` and `LLMToolCall` are the **only** LLM-boundary types. Any provider adapter must produce them. The canonical home is `agent_service.py`; `llm_client.py` imports them from there.

#### Loop invariants (frozen)

1. One LLM, one loop, no recursion. The `for` loop in `run` is the only loop.
2. `max_iterations` and `max_output_tokens` are hard bounds.
3. Sequential tool dispatch within an iteration. No `asyncio.gather`.
4. Empty LLM content **and** empty `tool_calls` → load `prompts/refusal.md`, return with `used_refusal_fallback=True`.
5. Same `ToolError.code` twice in a row across iterations → load refusal, return.
6. Iteration cap reached without a final answer → load refusal, return.
7. `sources` is deduped, order-preserving (first occurrence wins).
8. Memory roles map to LLM roles: `visitor → user`, `assistant → assistant`. Other roles (`tool`) are skipped when building context.
9. `tenant_persona` defaults to `"a helpful, polite assistant for this business"` when not supplied.
10. The agent never writes memory — that's the orchestrator's job.

### B. ToolRegistry (`backend/app/services/tools/`)

```python
class ToolError(BaseModel):
    error: str
    code: str   # rate_limited | validation_error | not_found | unknown_tool

@dataclass
class ToolContext:
    tenant_id: UUID
    conversation_id: UUID
    visitor_session_id: UUID | None = None

@dataclass
class ToolHandler:
    name: str
    description: str
    args_schema: type[BaseModel]
    invoke_fn: Callable[..., Awaitable[BaseModel]]
    def to_openai_spec(self) -> dict[str, Any]: ...

class ToolRegistry:
    def __init__(self, handlers: list[ToolHandler]) -> None: ...
    def tool_specs(self) -> list[dict[str, Any]]: ...
    async def dispatch(
        self, name: str, raw_args: dict[str, Any] | str, ctx: ToolContext,
    ) -> BaseModel | ToolError: ...

def build_registry(
    *, rag_service, lead_service, escalation_service,
) -> ToolRegistry: ...
```

#### Per-tool args/result (matches `docs/SPEC.md §3`)

```python
# rag_search (SPEC §3.1)
class RagSearchArgs(BaseModel):
    query: str
    max_chunks: int = 5         # ge=1, le=10

class RagChunk(BaseModel):
    text: str
    source_page_id: UUID
    score: float

class RagSearchResult(BaseModel):
    chunks: list[RagChunk]
    total_found: int

# capture_lead (SPEC §3.2)
class CaptureLeadArgs(BaseModel):
    name: str | None = None
    email: str | None = None
    phone: str | None = None
    intent: str
    context: str | None = None

class CaptureLeadResult(BaseModel):
    lead_id: UUID
    status: Literal["created"]

# escalate (SPEC §3.3)
class EscalateArgs(BaseModel):
    reason: str
    context: str | None = None

class EscalateResult(BaseModel):
    escalation_id: UUID
    status: Literal["created"]
```

#### Registry invariants

1. `tenant_id`, `conversation_id`, `visitor_session_id` are read **only** from `ToolContext`. They are never accepted as LLM arguments.
2. JSON-string arguments are decoded with `json.loads`; invalid JSON → `ToolError(code="validation_error")`.
3. Pydantic `ValidationError` → `ToolError(code="validation_error")`.
4. `RateLimitError` → `ToolError(code="rate_limited")`.
5. `NotFoundError` → `ToolError(code="not_found")`.
6. Unknown tool name → `ToolError(code="unknown_tool")`.
7. `ExternalServiceError` and any other exception propagate by design.
8. Dispatch is strictly sequential.

### C. MemoryService (`backend/app/services/memory_service.py`)

```python
Role = Literal["visitor", "assistant", "tool"]

class MemoryEntry(BaseModel):
    role: Role
    content_redacted: str
    ts: int                       # unix seconds at write time

class MemoryService:
    def __init__(self, redis, *, ttl_seconds: int, max_entries: int) -> None: ...
    async def append(self, tenant_id: UUID, conversation_id: UUID,
                     role: Role, content: str) -> None: ...
    async def load(self, tenant_id: UUID, conversation_id: UUID,
                   ) -> list[MemoryEntry]: ...
    async def purge_conversation(self, tenant_id: UUID, conversation_id: UUID,
                                 ) -> None: ...
    async def purge_tenant(self, tenant_id: UUID) -> int: ...
```

Redis key: `memory:{tenant_id}:{conversation_id}` (`docs/SPEC.md §10`).

#### Memory invariants

1. Every value written passes through `app.core.redaction.redact` first — no raw content stored.
2. `LTRIM(key, -max_entries, -1)` on every `append`. List never exceeds `max_entries`.
3. `EXPIRE(key, ttl_seconds)` on every `append` (sliding TTL).
4. Identifiers (`tenant_id`, `conversation_id`) appear only in the key, never in the value.
5. `purge_tenant` uses `SCAN` + `UNLINK` (batched at 500). `KEYS` and `DEL` are forbidden.
6. Tool arguments and tool results are never appended to memory.
7. Fail-open on Redis errors: log + swallow (`append`/`purge_*`) or return `[]` (`load`). Turn proceeds without history.

### D. GroqLLMClient (`backend/app/services/llm_client.py`)

The single, provider-isolated implementation of `tool_complete`. The only file that imports the `groq` SDK.

```python
class GroqLLMClient:
    def __init__(
        self, *,
        client: AsyncGroq, model: str,
        max_attempts: int = 3,
        backoff_base_seconds: float = 0.5,
        backoff_max_seconds: float = 5.0,
        temperature: float = 0.2,
    ) -> None: ...

    @classmethod
    def from_api_key(cls, *, api_key: str, model: str,
                     timeout_seconds: float = 30.0, **kwargs) -> "GroqLLMClient": ...

    async def tool_complete(
        self, *,
        messages: list[dict[str, Any]],   # OpenAI-shape
        tools: list[dict[str, Any]],      # OpenAI-shape function specs
        max_tokens: int,
    ) -> LLMResponse: ...
```

#### LLM client invariants

1. SDK retries are **disabled** (`AsyncGroq(max_retries=0)`). The retry loop in this module is the single source of truth.
2. One request per call. No streaming.
3. `tool_choice="auto"` is set only when tools are supplied; otherwise `tools` and `tool_choice` are both omitted.
4. Retry budget bounded by `max_attempts` (default `3`). Backoff: `min(backoff_base * 2**attempt, backoff_max)`.
5. Retryable: `APIConnectionError` (incl. timeouts), `APIStatusError` with status `429` or `5xx`.
6. Non-retryable: any other `4xx`, generic `APIError`, any unexpected `Exception` → `ExternalServiceError(service="groq", …)` immediately.
7. Exhaustion → `ExternalServiceError("max retries (N) exhausted: …")`.
8. Tool-call `arguments` forwarded as the raw JSON string Groq returns.
9. `message.content` may be `None` when only tool calls were emitted; the agent's refusal logic handles that.

### E. Settings (frozen)

| Setting | Default | Source |
|---|---|---|
| `AGENT_MAX_TOOL_ITERATIONS` | `3` | `Settings` |
| `AGENT_MAX_OUTPUT_TOKENS` | `800` | `Settings` |
| `MEMORY_TTL_SECONDS` | `86400` (24h, per SPEC §10) | `Settings` |
| `MEMORY_MAX_ENTRIES` | `40` | `Settings` |
| `REDIS_URL` | `redis://redis:6379/0` (compose default) | `Settings` |
| `GROQ_API_KEY` | (env, secret, required) | `Settings` |
| `LLM_MODEL` | `llama-3.3-70b-versatile` | `Settings` |
| `LLM_TIMEOUT_SECONDS` | `30.0` | `Settings` |

Prompts (loaded via `app.core.prompts.load_prompt`, cached with `lru_cache`):

- `backend/app/prompts/system_agent.md` — agent persona
- `backend/app/prompts/refusal.md` — refusal fallback

### F. Test coverage (frozen counts)

| Surface | File | Scenarios |
|---|---|---|
| `AgentService` | `backend/tests/test_agent_service.py` | 7 |
| `ToolRegistry` | `backend/tests/test_tool_registry.py` | 5 |
| `MemoryService` (planned `fakeredis`) | n/a — implicit via `FakeMemoryService` in `conftest.py` | — |
| `GroqLLMClient` | `backend/tests/test_llm_client.py` | 14 |

### G. Implementation status

| Component | Status |
|---|---|
| `AgentService` bounded loop, sources, refusal/anti-loop | Implemented |
| `LLMResponse` / `LLMToolCall` / `AgentTurnResult` | Implemented |
| `ToolRegistry` + `build_registry` + per-tool handlers | Implemented |
| `MemoryService` (append/load/purge_*) with fail-open | Implemented |
| `GroqLLMClient` with bounded retry + error translation | Implemented |
| Wired into `ChatOrchestrator` via DI | Implemented |
| Real `system_agent.md` / `refusal.md` content | Implemented (`app/prompts/`) |
| `groq>=0.11`, `redis>=5`, prompt files | Pinned in `backend/pyproject.toml` |
| Tool-selection golden set + CI gate | Pending — see feature 016 |

### H. Future integration points

- **Provider swap**: replace `llm_client.py` with `<provider>_llm_client.py` exposing the same `tool_complete` signature. `AgentService` does not change.
- **Tenant persona source**: `ChatOrchestrator` will load `guardrail_configs.tenant_persona` (Person C) and pass it via `tenant_persona=...`. Today it defaults to the platform persona.
- **Long-term memory / summarization**: explicitly out of scope. Any such change requires a new spec, not a patch to this module.
