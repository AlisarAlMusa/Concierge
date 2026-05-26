---
description: "Task list for Platform Foundation — retrospective + gap closure"
---

# Tasks: Platform Foundation

**Input**: Design documents from `specs/001-platform-foundation/`

**Status note**: Phases 1–2 are already implemented on `main`. Tasks in those phases are marked ~~DONE~~ for traceability. Phase 3 (verification) and Phases 4–6 (gap closure) are the actionable work.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Maps to user stories in spec.md (US1–US4)
- **~~strikethrough~~**: Already implemented

---

## Phase 1: Setup — Stack & Skeleton ✅ DONE

**Purpose**: Docker Compose stack, FastAPI app skeleton, CI pipeline skeleton — all implemented in the initial commit.

- [x] T001 ~~[DONE] Create Docker Compose stack with 9 services (api, model_server, guardrails_sidecar, admin_app, worker, postgres, redis, minio, vault) in docker-compose.yml~~
- [x] T002 ~~[DONE] Add healthchecks to all services and `depends_on: condition: service_healthy` for api in docker-compose.yml~~
- [x] T003 ~~[DONE] [P] Create backend FastAPI app entry point in backend/app/main.py with lifespan, docs gating, error handlers, router~~
- [x] T004 ~~[DONE] [P] Create .env.example with all required environment variable keys~~
- [x] T005 ~~[DONE] [P] Create CI workflow .github/workflows/ci.yml with ruff, black, pytest steps~~

**Checkpoint**: Stack starts, API process boots, CI runs lint + test.

---

## Phase 2: Foundational — Core Infrastructure ✅ DONE

**Purpose**: Config singleton, structured logging, async DB session, health endpoints, domain errors, Alembic migrations, RLS helpers — all implemented.

- [x] T006 ~~[DONE] Implement pydantic-settings config singleton with extra="forbid" in backend/app/core/config.py~~
- [x] T007 ~~[DONE] [P] Implement structlog configure_logging() with ConsoleRenderer/JSONRenderer switch in backend/app/core/logging.py~~
- [x] T008 ~~[DONE] [P] Implement async SQLAlchemy engine singleton and get_db_session() dependency in backend/app/db/session.py~~
- [x] T009 ~~[DONE] [P] Implement GET /health and GET /ready endpoints in backend/app/api/routes/health.py~~
- [x] T010 ~~[DONE] [P] Implement domain exception classes and FastAPI error handlers in backend/app/core/errors.py~~
- [x] T011 ~~[DONE] [P] Implement set_tenant_context / reset_tenant_context / get_tenant_db_session in backend/app/db/rls.py~~
- [x] T012 ~~[DONE] Wire Alembic async env in backend/app/db/migrations/env.py~~
- [x] T013 ~~[DONE] Write initial migration (pgvector, pgcrypto, tenants, users, audit_logs, cost_events) in backend/app/db/migrations/versions/0001_initial.py~~

**Checkpoint**: Foundation ready — config loads, DB connects, health endpoints respond, errors are structured, RLS helpers exist.

---

## Phase 3: User Story 1 — Stack Verification (Priority: P1) 🎯

**Goal**: Confirm the already-built stack actually runs end-to-end from a clean environment.

**Independent Test**: `docker compose up --build`; all services reach healthy status; `GET /health` returns `{"status": "ok"}`; `GET /ready` returns 200.

### Implementation for User Story 1

- [ ] T014 [US1] Copy .env.example to .env and run `docker compose up --build`; confirm all 9 services reach healthy/running status *(manual — run locally before demo)*
- [ ] T015 [US1] Verify `GET http://localhost:8000/health` returns `{"status": "ok"}` with HTTP 200 *(manual — run locally before demo)*
- [ ] T016 [US1] Verify `GET http://localhost:8000/ready` returns `{"status": "ready"}` with HTTP 200 *(manual — run locally before demo)*
- [ ] T017 [US1] Verify `GET http://localhost:8001/health` (model_server) and `GET http://localhost:8002/health` (guardrails_sidecar) return 200 *(manual — run locally before demo)*
- [ ] T018 [US1] Run `docker compose down -v` to clean up *(manual)*

**Checkpoint**: Stack verified — confirmed runnable from a clean checkout. CI smoke-test (T026-T028) automates this on every push.

---

## Phase 4: User Story 2 — Redis Lifespan Singleton (Priority: P2)

**Goal**: Redis client is initialised once at app startup and injected via `Depends()` — not constructed per service call.

**Independent Test**: Start the app; confirm `app.state.redis` is set after lifespan startup; send any request that touches Redis and verify only one connection pool exists (no per-call construction).

### Implementation for User Story 2

- [x] T019 [US2] Add `redis.asyncio` import to backend/app/main.py; in lifespan startup call `aioredis.from_url(settings.REDIS_URL)` and store on `app.state.redis`; in lifespan shutdown call `await app.state.redis.aclose()`
- [x] T020 [US2] Add `get_redis()` async dependency function in backend/app/dependencies.py that returns `request.app.state.redis`; annotate return type as `aioredis.Redis`
- [x] T021 [US2] No existing services construct Redis inline — N/A for current codebase; future services MUST use `Depends(get_redis)`
- [x] T022 [US2] Verified: no `from_url` calls exist outside `main.py` — grep confirms empty result

**Checkpoint**: Redis is a lifespan singleton; `Depends(get_redis)` is the only injection point.

---

## Phase 5: User Story 2 — Request ID / Trace ID Middleware (Priority: P2)

**Goal**: Every log line emitted during a request carries `request_id` and `trace_id` bound via per-request middleware.

**Independent Test**: Send a request to any API endpoint; verify all log lines for that request include `request_id` and `trace_id`; send a second request and verify the IDs are fresh (not carried over).

### Implementation for User Story 2 (continued)

- [x] T023 [US2] Added `RequestIDMiddleware` to backend/app/core/logging.py; reads X-Request-ID/X-Trace-ID headers (falls back to uuid4); binds via `structlog.contextvars.bind_contextvars`; clears in `finally`
- [x] T024 [US2] Registered `RequestIDMiddleware` in backend/app/main.py via `app.add_middleware(RequestIDMiddleware)` before router include
- [x] T025 [US2] Added `logger.info("health_check")` probe to health endpoint; `request_id` and `trace_id` will appear in every log line emitted during the request

**Checkpoint**: All request-scoped log lines include `request_id` and `trace_id`; IDs reset between requests.

---

## Phase 6: User Story 4 — CI Smoke Test Job (Priority: P3)

**Goal**: Every push runs a CI job that builds the full Docker Compose stack, verifies the API health endpoint responds, then tears down.

**Independent Test**: Push a commit that breaks the Dockerfile or docker-compose.yml; the smoke test job must fail. Push a clean commit; the job must pass with a 200 from `GET /health`.

### Implementation for User Story 4

- [x] T026 [US4] Added `smoke-test` job to .github/workflows/ci.yml with `needs: lint-and-test`; steps: checkout, cp .env.example .env, docker compose up -d --build, polling curl loop (30×5s), verify /health and /ready JSON, docker compose down -v
- [x] T027 [US4] `timeout-minutes: 10` added to smoke-test job
- [ ] T028 [US4] Verify smoke-test passes by pushing a clean commit and checking Actions run *(push to trigger CI)*

**Checkpoint**: CI enforces that the stack builds and starts successfully on every push.

---

## Phase 7: Polish & Cross-Cutting Concerns

**Purpose**: Tidy up items that span multiple phases.

- [x] T029 [P] `.dockerignore` already existed at repo root; added `.venv`, `*.egg-info`, `dist`, `build`, `specs/` entries to complete coverage
- [ ] T030 Remove `|| true` from the pytest step in .github/workflows/ci.yml once at least one real test exists *(do not remove until backend/tests/ has passing tests)*

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1 (Setup)**: ✅ Complete
- **Phase 2 (Foundational)**: ✅ Complete
- **Phase 3 (Stack Verification)**: Depends on nothing — can run immediately
- **Phase 4 (Redis Singleton)**: Can start after Phase 3 verification; no dependency on Phases 5–6
- **Phase 5 (Request ID Middleware)**: Can start after Phase 3 verification; no dependency on Phase 4
- **Phase 6 (CI Smoke Test)**: Can start after Phase 3 verification; no dependency on Phases 4–5
- **Phase 7 (Polish)**: After all gap phases are complete

### User Story Dependencies

- **US1 (Stack Verification)**: No dependencies — start immediately
- **US2 (Redis + Middleware)**: No cross-story dependencies; T019–T022 (Redis) and T023–T025 (Middleware) are independent of each other and can be done in parallel
- **US4 (CI Smoke Test)**: No cross-story dependencies; can be done in parallel with US2

### Within Each User Story

- US2: Redis tasks (T019–T022) and Middleware tasks (T023–T025) touch different files and can run in parallel
- US4: T026 → T027 (same file, sequential) → T028 (validation)

### Parallel Opportunities

- Phases 4, 5, and 6 are all independent — a three-person team can work them simultaneously after Phase 3 verification
- Within Phase 4: T019 (main.py) and T020 (dependencies.py) touch different files → parallel
- Within Phase 5: T023 (logging.py) is independent of T024 (main.py) → parallel

---

## Parallel Example: Gap Closure (Phases 4–6)

```bash
# All three gap phases can be assigned simultaneously:
Developer A: Phase 4 — Redis singleton (T019–T022)
Developer B: Phase 5 — Request ID middleware (T023–T025)
Developer C: Phase 6 — CI smoke test job (T026–T028)
```

---

## Implementation Strategy

### Immediate Priority (Today)

1. ✅ Phases 1–2 already complete
2. Run Phase 3 verification (T014–T018) — confirm the stack is healthy from a clean checkout
3. Close all three gaps (Phases 4–6) — each is 2–4 hours of work, all independent

### Gap Closure Order (if solo)

1. Phase 5 (Request ID Middleware) — highest observability value, simplest to implement
2. Phase 4 (Redis Singleton) — required for memory and rate-limit services to be production-correct
3. Phase 6 (CI Smoke Test) — enforces #1 and #2 stay working on every future push

---

## Notes

- `[P]` tasks touch different files and have no shared state — safe to parallelize
- `[DONE]` / `[x]` tasks are already implemented; listed for traceability only
- Do not remove the `|| true` from pytest (T030) until real tests exist
- The eval gate tasks (FR-023) are tracked in plan.md as Person C's responsibility — not listed here
