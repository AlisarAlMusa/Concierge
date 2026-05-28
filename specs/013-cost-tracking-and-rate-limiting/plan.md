# Implementation Plan: Cost Tracking & Rate Limiting

**Branch**: `main` | **Date**: 2026-05-28 | **Spec**: [spec.md](./spec.md)

**Owner**: Person A — `feature/platform-tenancy`

---

## Summary

Implements per-tenant cost tracking for every LLM, embedding, and classifier call, and enforces fixed-window Redis rate limits at the chat endpoint (per-tenant and per-widget). Usage-summary endpoints expose aggregate-only metrics to tenant admins and the platform manager.

---

## Technical Context

**Language/Version**: Python 3.12

**Primary Dependencies**: FastAPI, SQLAlchemy 2.x async, Redis (aioredis), pydantic-settings, structlog

**Rate limit strategy**: Fixed window (INCR + EXPIRE NX) — same pattern as the existing login rate limiter in `auth_service.py`. A sliding window was considered but rejected: fixed windows are simpler, already proven in this codebase, and the ≤5ms overhead requirement (SC-003) is trivially met.

**Cost estimation**: Static pricing table in `Settings` (Spec 013 assumption). No live pricing API. Table is source-of-truth; values are env-overridable via `.env`.

**Fire-and-forget pattern**: `asyncio.create_task()` with a fresh `get_session_factory()` session — mirrors `auth_service.write_audit_event`. The request path never awaits cost writes; failures are logged as warnings and swallowed (FR-004).

**LLM token extraction**: `completion.usage.prompt_tokens` / `.completion_tokens` from the Groq API response. Added `input_tokens` / `output_tokens` to `LLMResponse` (defaulting to 0 for backward compat). `AgentService` fires a cost event after each `tool_complete` call.

---

## Constitution Check

| Principle | Status | Notes |
|-----------|--------|-------|
| I. Tenant Isolation | ✅ Pass | Rate limit keys are `ratelimit:{scope}:{id}` — Tenant A's key is physically separate from Tenant B's. Cost events are tagged with `tenant_id` (FK). Usage-summary endpoints derive `tenant_id` from JWT, never from request body. RLS on `cost_events` enforces DB-level isolation. |
| II. Clean Layered Architecture | ✅ Pass | `cost_repository` owns SQL. `cost_service` owns business logic (pricing, fire-and-forget). `rate_limit_service` owns Redis counters. Routes own HTTP only. No `os.getenv`. |
| III. Security by Default | ✅ Pass | No new secrets. Rate limiting added as a defence layer. `tenant_id` never from body. |
| IV. Async All the Way Down | ✅ Pass | All DB writes use `async with factory()`. Redis pipeline uses `await pipe.execute()`. No `time.sleep`. |
| V. Lean Containers | ✅ Pass | No new production dependencies with ML payloads. |
| VI. Evals Are the Grade | ⚠ Gap | Cost tracking has no eval gate yet — deferred to Person C's eval scripts. |

---

## Project Structure

### New Files

```text
backend/app/repositories/cost_repository.py      # DB layer: insert, aggregate
backend/app/services/cost_service.py             # Business logic: pricing, fire-and-forget
backend/app/services/rate_limit_service.py       # Redis fixed-window rate limiter
backend/tests/test_cost_service.py               # Unit tests: pricing, fire-and-forget
backend/tests/test_rate_limit_service.py         # Unit tests: limits, fail-open, isolation
```

### Modified Files

```text
backend/app/core/config.py                       # Resolved merge conflict; added rate-limit + pricing settings
backend/app/schemas/tenant.py                    # TenantUsageSummary extended with OperationUsage breakdown
backend/app/repositories/tenant_repository.py   # get_usage_summary returns per-operation breakdown
backend/app/services/tenant_service.py          # get_usage_summary populates per-operation fields
backend/app/services/agent_service.py           # LLMResponse gains input/output_tokens; _fire_llm_cost_event hook
backend/app/services/llm_client.py              # _to_llm_response extracts completion.usage token counts
backend/app/api/routes/admin_config.py          # GET /tenant/usage-summary (tenant admin, FR-005)
backend/app/api/routes/chat.py                  # Rate limit checks before orchestrator (FR-007, FR-008)
backend/app/dependencies.py                      # get_rate_limit_service dependency
```

---

## Key Design Decisions

### 1. Fixed-window vs. Sliding-window Rate Limiting

**Decision**: Fixed window (INCR + EXPIRE NX).

**Rationale**: The existing `check_login_rate_limit` in `auth_service.py` uses the identical pattern and it is proven in production. Sliding windows (sorted-set ZADDs) are more accurate at window boundaries but add overhead. The spec says ≤5ms p95 overhead (SC-003) — a two-command pipeline achieves this. Burst behaviour at window boundaries is a known tradeoff; documented in this plan.

### 2. Cost Recording Integration Point

**Decision**: Fire cost events from `AgentService.run()` after each `tool_complete()` call.

**Rationale**: `AgentService` has `tenant_id` and owns the LLM call loop. The alternative (recording in `llm_client.py`) would require the client to know about tenants, violating the separation of concerns. `GroqLLMClient` is intentionally provider-isolated and tenant-agnostic.

**Tradeoff**: Embedding and classifier cost recording are not yet wired (they lack token-count return paths). This is an Open Gap.

### 3. RLS + Explicit Filter (Belt-and-Suspenders)

**Decision**: Usage-summary queries use an explicit `WHERE tenant_id = :id` filter AND benefit from the RLS policy on `cost_events`.

**Rationale**: Defence in depth. The explicit filter provides the first check; RLS provides the DB-enforced second layer. This matches the pattern used in all other tenant-scoped queries.

---

## Open Gaps (Follow-up Required)

| Gap | Spec Ref | Owner | Notes |
|-----|----------|-------|-------|
| Embedding cost events not recorded | FR-002 | Person B | `CohereEmbeddingClient` does not return token counts; requires extracting `meta.billed_units.input_tokens` from Cohere response |
| Classifier cost events not recorded | FR-003 | Person B/C | `ClassifierResponse` has no token count; model_server cost is 0 (self-hosted) so this is low urgency |
| Cost tracking eval gate | FR-023 | Person C | Uncomment after eval scripts land |
