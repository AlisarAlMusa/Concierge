# Feature Specification: Cost Tracking & Rate Limiting

> **Owner**: Person A — `feature/platform-tenancy` branch

**Feature Branch**: `013-cost-tracking-and-rate-limiting`

**Created**: 2026-05-27

**Status**: Draft

---

## User Scenarios & Testing *(mandatory)*

### User Story 1 — Every LLM and Embedding Call Is Tagged with a Tenant (Priority: P1)

Every call to the hosted LLM API and the embedding API produces a `cost_event` row tagged with the tenant's id, the provider, the model, the operation type, and the token counts. A Tenant Manager can answer "what did Tenant X cost us this week?" from aggregate queries.

**Why this priority**: An agent that calls paid APIs is a cost centre per customer. Not tracking per-tenant cost is how SaaS startups die quietly.

**Independent Test**: Run a chat request for Tenant A. Query `cost_events` filtered by Tenant A's id. Confirm at least one `llm` event exists with the correct token counts, model, and tenant_id.

**Acceptance Scenarios**:

1. **Given** a chat request that triggers an LLM call, **When** the call completes, **Then** a `cost_event` row is created with `tenant_id`, `provider`, `model`, `operation=llm`, `input_tokens`, `output_tokens`, and `estimated_cost_usd`.
2. **Given** a CMS publish that triggers embedding, **When** the embedding API call completes, **Then** a `cost_event` row is created with `operation=embedding` and the correct tenant_id.
3. **Given** a classifier call, **When** it completes, **Then** a `cost_event` row is created with `operation=classifier` and the correct tenant_id.
4. **Given** two tenants with chat activity, **When** cost events are queried per tenant, **Then** their costs are correctly isolated — Tenant A's cost is never attributed to Tenant B.

---

### User Story 2 — Tenant Admin Views Their Own Usage Summary (Priority: P2)

A tenant admin calls `GET /tenant/usage-summary` and sees their aggregate LLM, embedding, and classifier costs for the current period. They see totals and breakdowns by operation type — never another tenant's costs.

**Why this priority**: Tenant admins need to understand their usage to manage their own budgets and anticipate invoices.

**Independent Test**: Create cost events for Tenant A and Tenant B. Call `GET /tenant/usage-summary` as Tenant A. Confirm only Tenant A totals are returned.

**Acceptance Scenarios**:

1. **Given** cost events for a tenant, **When** `GET /tenant/usage-summary` is called by that tenant's admin, **Then** aggregate totals by operation type are returned.
2. **Given** cost events for two tenants, **When** each admin calls their own usage summary, **Then** each sees only their own costs.

---

### User Story 3 — One Noisy Tenant Cannot Starve Others (Priority: P1)

Per-tenant and per-widget rate limits ensure that a single tenant hammering the chat endpoint does not exhaust resources for other tenants. When a tenant exceeds their rate limit, subsequent requests receive a 429 response until the window resets.

**Why this priority**: The noisy-neighbour problem only exists under multi-tenancy. Without rate limiting, one misbehaving tenant can degrade service for all others.

**Independent Test**: Send requests from Tenant A's widget at a rate above the per-tenant limit. Confirm that after the limit is reached, Tenant A receives 429. Confirm Tenant B's requests during the same window are unaffected.

**Acceptance Scenarios**:

1. **Given** a per-tenant rate limit (e.g., 100 chat requests/minute), **When** Tenant A exceeds it, **Then** subsequent requests from Tenant A receive HTTP 429.
2. **Given** Tenant A is rate-limited, **When** Tenant B sends a request, **Then** Tenant B's request is processed normally.
3. **Given** a rate limit window expires, **When** Tenant A sends a new request, **Then** the request is processed normally.
4. **Given** a per-widget rate limit, **When** a specific widget exceeds its limit, **Then** only that widget's requests are rate-limited (not the entire tenant).

---

### User Story 4 — Tenant Manager Views Aggregate Platform Usage (Priority: P2)

The Tenant Manager can call `GET /platform/tenants/{tenant_id}/usage-summary` for any tenant and see aggregate cost metrics. This is aggregate-only: no conversation content, no leads, no messages.

**Why this priority**: Operational visibility for billing and capacity planning. Enforces the "aggregate yes, content no" access rule for the Tenant Manager role.

**Independent Test**: Create cost events for multiple tenants. Call the platform usage summary as Tenant Manager. Confirm aggregate totals are returned, and no message content or lead data is included.

**Acceptance Scenarios**:

1. **Given** cost events for Tenant X, **When** `GET /platform/tenants/{tenant_id}/usage-summary` is called by Tenant Manager, **Then** aggregate cost metrics are returned.
2. **Given** the same endpoint, **When** the response is inspected, **Then** it contains no conversation messages, lead records, or CMS body content.

---

### Edge Cases

- What happens when a cost event write fails (DB unavailable)? → The API logs a warning and continues; the user request is not failed due to a cost tracking failure. The event is dropped (not queued for retry in Week 8).
- What happens when `estimated_cost_usd` is zero for a classifier call? → This is valid — the model server is self-hosted; cost is recorded as 0.
- What happens when the rate limit counter store (Redis) is unavailable? → The rate limiter fails open (requests proceed) with a warning log; this is documented as a known tradeoff.
- What happens when a tenant is suspended? → Cost events are no longer written for that tenant (no requests reach the cost tracking layer).

---

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Every LLM API call MUST produce a `cost_event` row with: `tenant_id`, `provider`, `model`, `operation=llm`, `input_tokens`, `output_tokens`, `estimated_cost_usd`, `created_at`.
- **FR-002**: Every embedding API call MUST produce a `cost_event` row with `operation=embedding`.
- **FR-003**: Every classifier call MUST produce a `cost_event` row with `operation=classifier`.
- **FR-004**: Cost event writes MUST be async and non-blocking to the request path; a failed write MUST warn but not fail the user request.
- **FR-005**: `GET /tenant/usage-summary` MUST return aggregate totals by operation type for the calling tenant only.
- **FR-006**: `GET /platform/tenants/{tenant_id}/usage-summary` MUST be restricted to `tenant_manager` and return aggregate metrics only — no content.
- **FR-007**: Per-tenant rate limiting MUST be enforced on the chat endpoint. Default limit: 100 requests/minute/tenant (configurable).
- **FR-008**: Per-widget rate limiting MUST be enforced on `POST /public/chat`. Default limit: 60 requests/minute/widget.
- **FR-009**: Per-visitor-session rate limiting MUST be enforced on `capture_lead`. Default: 5 writes/session/hour.
- **FR-010**: Rate limits MUST be backed by Redis counters with TTL-based windows.
- **FR-011**: When a rate limit is exceeded, HTTP 429 MUST be returned with a `Retry-After` header.
- **FR-012**: Rate limits MUST be tenant-independent — one tenant's limit exhaustion MUST NOT affect another tenant.

### Key Entities

- **Cost Event**: id, tenant_id, provider, model, operation (`llm` | `embedding` | `classifier` | `rerank`), input_tokens, output_tokens, estimated_cost_usd, created_at.
- **Rate Limit Counter**: Redis key `ratelimit:{scope}:{tenant_id|widget_id|session_id}:{window}` with TTL. Scope = `tenant` | `widget` | `session`.
- **Usage Summary**: Aggregate view — total_llm_cost, total_embedding_cost, total_classifier_cost, total_tokens, period.

---

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of LLM and embedding calls produce a corresponding cost event tagged with the correct tenant.
- **SC-002**: Per-tenant rate limiting correctly isolates tenants — Tenant A exceeding limits has zero impact on Tenant B response times.
- **SC-003**: Rate limit enforcement adds ≤ 5ms to request latency (p95).
- **SC-004**: Usage summary endpoint returns correct aggregates with ≤ 1 second response time for up to 10,000 cost events.
- **SC-005**: A Tenant Manager usage summary call returns zero content fields — only numeric aggregates.

---

## Assumptions

- Cost estimation uses a static cost-per-token table committed in config (e.g., `$0.000002/token` for embeddings). Real-time pricing APIs are out of scope.
- Rate limit windows are sliding windows (Redis sorted sets) or fixed windows (Redis counters with TTL); the implementation choice is Person A's to justify.
- The rerank operation type is included in the schema for future use; it is not required to produce events in Week 8.
- Cost events are append-only; there is no update or delete for individual events (only full tenant erasure covers deletion).
