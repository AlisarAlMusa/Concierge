---
description: "Task list for Spec 013 — Cost Tracking & Rate Limiting"
---

# Tasks: Cost Tracking & Rate Limiting

**Input**: `/specs/013-cost-tracking-and-rate-limiting/`

**Prerequisites**: plan.md ✅ | spec.md ✅

**Organization**: Tasks grouped by user story to enable independent implementation and testing of each story.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (US1–US4)

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Add configuration knobs and verify the `cost_events` DB table is in place before any feature work begins.

- [ ] T001 Add pricing constants to `backend/app/core/config.py`: `COST_GROQ_INPUT_PER_TOKEN`, `COST_GROQ_OUTPUT_PER_TOKEN`, `COST_COHERE_INPUT_PER_TOKEN`
- [ ] T002 Add rate-limit settings to `backend/app/core/config.py`: `CHAT_RATE_LIMIT_PER_TENANT`, `CHAT_RATE_LIMIT_PER_WIDGET`, `CHAT_RATE_LIMIT_WINDOW_SECONDS`
- [ ] T003 [P] Verify `cost_events` table and RLS policy exist in `backend/migrations/versions/` (migration 0004) — no new migration needed if present

**Checkpoint**: Config settings merged; DB schema confirmed — all phases can begin.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Core repository layer that both US1 and US2 depend on.

**⚠️ CRITICAL**: Phase 3 (US1) and Phase 4 (US2) both depend on this phase.

- [ ] T004 Implement `insert_cost_event(session, event)` in `backend/app/repositories/cost_repository.py` — adds the ORM object and flushes
- [ ] T005 Implement `get_usage_summary_by_operation(session, tenant_id)` in `backend/app/repositories/cost_repository.py` — GROUP BY operation, returns list of dicts with `operation`, `input_tokens`, `output_tokens`, `cost_usd`
- [ ] T006 [P] Implement `get_total_usage(session, tenant_id)` in `backend/app/repositories/cost_repository.py` — single aggregate row for total tokens and cost

**Checkpoint**: Repository layer ready — cost_service and usage-summary routes can now be built.

---

## Phase 3: User Story 1 — Every LLM and Embedding Call Is Tagged with a Tenant (Priority: P1) 🎯 MVP

**Goal**: Every paid API call produces a `cost_event` row tagged with `tenant_id`, provider, model, token counts, and estimated USD cost. Failures are swallowed and logged — never surfaced to the user.

**Independent Test**: Run a chat request for Tenant A. Query `cost_events WHERE tenant_id = <A>`. Confirm at least one `llm` row exists with the correct `provider`, `model`, `input_tokens`, `output_tokens`, and `estimated_cost_usd > 0`.

### Implementation for User Story 1

- [ ] T007 [P] [US1] Implement `_estimate_cost(provider, input_tokens, output_tokens) -> Decimal` in `backend/app/services/cost_service.py` — reads from `Settings` pricing table; returns 0 for unknown/self-hosted providers
- [ ] T008 [P] [US1] Implement `_write_cost_event(...)` coroutine in `backend/app/services/cost_service.py` — creates `CostEvent`, opens fresh session from `get_session_factory()`, inserts; swallows all exceptions with `log.warning`
- [ ] T009 [US1] Implement `record_event(...)` fire-and-forget entry point in `backend/app/services/cost_service.py` — calls `asyncio.create_task(_write_cost_event(...))` so callers never await it (FR-004)
- [ ] T010 [US1] Extend `LLMResponse` in `backend/app/services/agent_service.py` with `input_tokens: int = 0` and `output_tokens: int = 0` fields (backward-compatible defaults)
- [ ] T011 [US1] Modify `_to_llm_response()` in `backend/app/services/llm_client.py` to extract `completion.usage.prompt_tokens` / `.completion_tokens` from the Groq API response and populate `LLMResponse.input_tokens` / `output_tokens`
- [ ] T012 [US1] Add `_fire_llm_cost_event(tenant_id, llm_client, response)` helper in `backend/app/services/agent_service.py` — skips if both token counts are 0; derives `provider` from client class name; calls `cost_service.record_event`
- [ ] T013 [US1] Call `_fire_llm_cost_event(tenant_id, self._llm, response)` inside `AgentService.run()` after each LLM call in `backend/app/services/agent_service.py`
- [ ] T014 [P] [US1] Write unit tests for `_estimate_cost` (groq, cohere, self-hosted, case-insensitive) in `backend/tests/test_cost_service.py`
- [ ] T015 [P] [US1] Write unit tests for `record_event` (schedules task, zero output_tokens default) in `backend/tests/test_cost_service.py`
- [ ] T016 [US1] Write unit tests for `_write_cost_event` (DB failure swallowed, correct row inserted) in `backend/tests/test_cost_service.py`
- [ ] T017 [US1] Write unit test for tenant isolation — separate tenants get separate `asyncio.create_task` calls in `backend/tests/test_cost_service.py`

**Checkpoint**: US1 fully functional — LLM cost events recorded per tenant, all cost_service tests pass.

---

## Phase 4: User Story 3 — One Noisy Tenant Cannot Starve Others (Priority: P1)

**Goal**: Per-tenant and per-widget fixed-window Redis counters enforce rate limits on `POST /chat`. Exceeding the limit returns HTTP 429 with `Retry-After`. Redis failure is fail-open — requests proceed with a warning log.

**Independent Test**: Send requests from Tenant A's widget above the per-tenant limit. Confirm Tenant A receives 429 after the limit. Confirm Tenant B's requests during the same window are unaffected and return 200.

### Implementation for User Story 3

- [ ] T018 [US3] Implement `RateLimitService.__init__` in `backend/app/services/rate_limit_service.py` accepting `redis`, `tenant_limit`, `widget_limit`, `window_seconds`, `session_lead_limit`, `session_lead_window_seconds`
- [ ] T019 [US3] Implement `_check(scope, identifier, limit, window_seconds)` private method in `backend/app/services/rate_limit_service.py` — atomic `INCR + EXPIRE NX` pipeline; raises `HTTPException(429)` with `Retry-After` header equal to Redis TTL when `count > limit`; catches all Redis exceptions and logs warning (fail-open, FR-010)
- [ ] T020 [P] [US3] Implement `check_tenant_chat_limit(tenant_id)` in `backend/app/services/rate_limit_service.py` — calls `_check("tenant", tenant_id, tenant_limit, window_seconds)` (FR-007)
- [ ] T021 [P] [US3] Implement `check_widget_chat_limit(widget_id)` in `backend/app/services/rate_limit_service.py` — calls `_check("widget", widget_id, widget_limit, window_seconds)` (FR-008)
- [ ] T022 [P] [US3] Implement `check_session_lead_limit(visitor_session_id)` in `backend/app/services/rate_limit_service.py` — calls `_check("session", session_id, session_lead_limit, session_lead_window_seconds)` (FR-009)
- [ ] T023 [US3] Add `get_rate_limit_service` FastAPI dependency in `backend/app/dependencies.py` — depends on `get_redis` and `get_settings`; constructs `RateLimitService` with config values
- [ ] T024 [US3] Apply rate limiting to `POST /chat` in `backend/app/api/routes/chat.py` — inject `rate_limiter: RateLimitService = Depends(get_rate_limit_service)`; call `check_tenant_chat_limit` and `check_widget_chat_limit` before orchestrator
- [ ] T025 [P] [US3] Write unit tests for tenant chat limit (under limit passes, at limit passes, over limit raises 429) in `backend/tests/test_rate_limit_service.py`
- [ ] T026 [P] [US3] Write unit tests for widget chat limit (over limit raises 429, widget key independent of tenant key) in `backend/tests/test_rate_limit_service.py`
- [ ] T027 [P] [US3] Write unit test for tenant isolation (Tenant A rate-limited has zero effect on Tenant B) in `backend/tests/test_rate_limit_service.py`
- [ ] T028 [P] [US3] Write unit test for fail-open (Redis pipeline.execute raises → request proceeds) in `backend/tests/test_rate_limit_service.py`
- [ ] T029 [P] [US3] Write unit tests for `Retry-After` header (reflects Redis TTL; minimum 1 when TTL is 0) in `backend/tests/test_rate_limit_service.py`
- [ ] T030 [P] [US3] Write unit tests for session lead limit (over limit raises 429, under limit passes) in `backend/tests/test_rate_limit_service.py`

**Checkpoint**: US3 fully functional — rate limiting enforced on POST /chat, all rate_limit_service tests pass.

---

## Phase 5: User Story 2 — Tenant Admin Views Their Own Usage Summary (Priority: P2)

**Goal**: A tenant admin calls `GET /tenant/usage-summary` and receives aggregate totals broken down by operation type (llm, embedding, classifier, rerank). Only their own tenant's data is returned.

**Independent Test**: Insert cost events for Tenant A and Tenant B. Call `GET /tenant/usage-summary` authenticated as Tenant A's admin. Confirm only Tenant A totals appear; Tenant B costs are absent.

### Implementation for User Story 2

- [ ] T031 [US2] Add `OperationUsage` Pydantic model to `backend/app/schemas/tenant.py` with fields: `input_tokens: int`, `output_tokens: int`, `cost_usd: Decimal`
- [ ] T032 [US2] Extend `TenantUsageSummary` in `backend/app/schemas/tenant.py` with per-operation breakdown fields: `llm`, `embedding`, `classifier`, `rerank` (each `OperationUsage`)
- [ ] T033 [US2] Update `get_usage_summary(session, tenant_id)` in `backend/app/repositories/tenant_repository.py` to GROUP BY operation and return the richer dict with `total_*` keys and per-operation sub-dicts
- [ ] T034 [US2] Update `get_usage_summary(session, tenant_id)` in `backend/app/services/tenant_service.py` to unpack per-operation sub-dicts into `OperationUsage(**s["llm"])` etc.
- [ ] T035 [US2] Add `GET /tenant/usage-summary` route in `backend/app/api/routes/admin_config.py` — requires `tenant_admin` role; uses same RLS-scoped session; returns `TenantUsageSummary` (FR-005)

**Checkpoint**: US2 fully functional — tenant admins can view their own usage breakdown.

---

## Phase 6: User Story 4 — Tenant Manager Views Aggregate Platform Usage (Priority: P2)

**Goal**: The Tenant Manager can call `GET /platform/tenants/{tenant_id}/usage-summary` for any tenant and see aggregate cost metrics. Response contains only numeric aggregates — no conversation content, lead records, or CMS body.

**Independent Test**: Insert cost events for multiple tenants. Call `GET /platform/tenants/{tenant_id}/usage-summary` as Tenant Manager. Confirm aggregate totals are correct and the response contains no `content`, `message`, or `lead` fields.

### Implementation for User Story 4

- [ ] T036 [US4] Verify `GET /platform/tenants/{tenant_id}/usage-summary` route exists in `backend/app/api/routes/tenants.py` — must require `tenant_manager` role and return `TenantUsageSummary` (FR-006)
- [ ] T037 [US4] Confirm response schema enforces aggregate-only fields — no `content`, `body`, `messages`, or `leads` fields in `TenantUsageSummary` (SC-005)
- [ ] T038 [US4] Wire `get_usage_summary` from `cost_service` (or `tenant_service`) into the platform route in `backend/app/api/routes/tenants.py` if not already using the extended schema

**Checkpoint**: US4 fully functional — Tenant Manager can view platform-wide per-tenant cost metrics.

---

## Phase 7: Polish & Cross-Cutting Concerns

- [ ] T039 [P] Run `uv run ruff check .` and fix all lint errors in `backend/`
- [ ] T040 [P] Run `uv run black --check .` and format any changed files in `backend/`
- [ ] T041 Run full unit test suite `uv run pytest --ignore=tests/integration -q` — confirm 0 failures
- [ ] T042 [P] Document open gaps in plan.md: embedding cost events (FR-002, requires Cohere `meta.billed_units`), classifier cost events (FR-003, self-hosted so cost=0), cost eval gate (FR-023)
- [ ] T043 [P] Add `.env.example` entries for new settings: `CHAT_RATE_LIMIT_PER_TENANT`, `CHAT_RATE_LIMIT_PER_WIDGET`, `CHAT_RATE_LIMIT_WINDOW_SECONDS`, `COST_GROQ_INPUT_PER_TOKEN`, `COST_GROQ_OUTPUT_PER_TOKEN`, `COST_COHERE_INPUT_PER_TOKEN`

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — start immediately
- **Foundational (Phase 2)**: Depends on Phase 1 — **blocks US1 and US2**
- **US1 (Phase 3)**: Depends on Phase 2 (needs `insert_cost_event`)
- **US3 (Phase 4)**: Depends on Phase 1 only — no DB layer needed; can run parallel with US1
- **US2 (Phase 5)**: Depends on Phase 2 (needs `get_usage_summary_by_operation`) and US1 (schema additions)
- **US4 (Phase 6)**: Depends on US2 (needs extended `TenantUsageSummary`)
- **Polish (Phase 7)**: Depends on all story phases complete

### User Story Dependencies

- **US1 (P1)**: Start after Foundational — no dependency on other stories
- **US3 (P1)**: Start after Setup — fully independent of US1/US2/US4
- **US2 (P2)**: Start after Foundational + US1 schema additions are merged
- **US4 (P2)**: Start after US2 (reuses `TenantUsageSummary`)

### Within Each User Story

- Repository before service before route
- Service unit tests parallel with implementation (different files)
- Core implementation before integration wiring

### Parallel Opportunities

- T001, T002, T003 — all setup tasks: run together
- T004, T005, T006 — all repository functions: different methods, run together
- T007, T008 — `_estimate_cost` and `_write_cost_event`: different functions, run together
- T010, T011 — `LLMResponse` extension and Groq extraction: different files, run together
- T014, T015 — first batch of cost_service tests: different test classes, run together
- T020, T021, T022 — three `check_*` methods: different functions, run together
- T025, T026, T027, T028, T029, T030 — rate limit tests: different test classes, run together
- T039, T040, T042, T043 — polish tasks in different files: run together

---

## Parallel Example: User Story 1

```bash
# Run simultaneously (different files/functions):
Task T007: "_estimate_cost in cost_service.py"
Task T008: "_write_cost_event in cost_service.py"
Task T010: "LLMResponse fields in agent_service.py"
Task T011: "Token extraction in llm_client.py"

# After T007+T008 complete:
Task T009: "record_event in cost_service.py"

# Parallel with T009:
Task T014: "Test _estimate_cost"
Task T015: "Test record_event"
```

## Parallel Example: User Story 3

```bash
# Run simultaneously after T018:
Task T020: "check_tenant_chat_limit"
Task T021: "check_widget_chat_limit"
Task T022: "check_session_lead_limit"

# Run simultaneously (all different test files/classes):
Task T025: "Tenant chat limit tests"
Task T026: "Widget chat limit tests"
Task T027: "Tenant isolation test"
Task T028: "Fail-open test"
Task T029: "Retry-After tests"
Task T030: "Session lead limit tests"
```

---

## Implementation Strategy

### MVP First (US1 + US3 — both P1)

1. Complete Phase 1: Setup (T001–T003)
2. Complete Phase 2: Foundational (T004–T006)
3. Complete Phase 3: US1 cost recording (T007–T017)
4. Complete Phase 4: US3 rate limiting (T018–T030) — can overlap with US1
5. **STOP and VALIDATE**: Chat requests produce cost events; rate limits return 429
6. Deploy/demo if ready

### Incremental Delivery

1. Setup + Foundational → DB layer ready
2. US1 → Cost events recorded per LLM call → Deploy
3. US3 → Rate limiting live on POST /chat → Deploy
4. US2 → Tenant admin usage dashboard → Deploy
5. US4 → Platform manager visibility → Deploy

### Parallel Team Strategy

With multiple developers after Phase 2 completes:

- **Developer A**: US1 (T007–T017) — cost service + agent wiring
- **Developer B**: US3 (T018–T030) — rate limit service + chat route
- **Developer C**: US2 (T031–T035) — usage summary + admin route

---

## Notes

- [P] tasks = different files, no shared-state dependencies
- [Story] label maps every task to one user story for traceability
- US1 and US3 are both P1 — both should land before any P2 work begins
- Open gaps documented in plan.md: embedding (FR-002) and classifier (FR-003) cost events are deferred to Person B/C
- Rate limit fail-open is intentional (FR-010) — Redis unavailability must never block user requests
- `tenant_id` is ALWAYS derived from the signed widget token — never from the request body
