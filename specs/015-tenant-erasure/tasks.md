# Tasks: 015 — Tenant Erasure

**Input**: Design documents from `specs/015-tenant-erasure/`

**Branch**: `021-tenant-erasure`

---

## Phase 1: Setup

**Purpose**: Add MinIO dependency — required before any erasure code can import the SDK.

- [x] T001 Add `"minio>=7.2"` to `backend/pyproject.toml` dependencies list and run `uv sync` from `backend/`

**Checkpoint**: `uv run python -c "import minio; print('ok')"` prints `ok`.

---

## Phase 2: Foundational — Wire Redis into the Erasure Call Chain

**Purpose**: `purge_tenant` needs the Redis client, which lives on `app.state`. The caller chain (`route → tenant_service → erasure_service`) must thread it through before the real erasure can be implemented.

**⚠️ CRITICAL**: T002 and T003 must complete before T004–T009 (the erasure body).

- [x] T00X Update `backend/app/api/routes/tenants.py` — in `DELETE /platform/tenants/{tenant_id}` handler, add `request: Request` parameter and pass `request.app.state.redis` when calling `tenant_service.delete_tenant`
- [x] T00X Update `backend/app/services/tenant_service.py` — add `redis: aioredis.Redis` parameter to `delete_tenant`; forward it to `asyncio.create_task(purge_tenant(tenant_id, redis))` instead of the current no-arg call

**Checkpoint**: `uv run python -m app.services.tenant_service` imports cleanly (no NameError); ruff passes.

---

## Phase 3: User Story 1 & 2 — Full Erasure, Delete-Only Path (P1)

**Goal**: `purge_tenant` deletes all tenant data from Postgres (9 tables), MinIO, and Redis. The path issues only DELETE operations — no content SELECT queries — satisfying the narrow delete-only constraint.

**Independent Test**: Call `purge_tenant` with a mock session and mock Redis. Assert DELETE was called for all 9 Postgres tables. Assert no SELECT was issued on content tables.

- [x] T00X [US1] Implement early-exit idempotency check in `backend/app/services/erasure_service.py` — open own `AsyncSession` via `get_session_factory()`, query tenant status; if already `deleted`, log and return immediately
- [x] T00X [US1] Implement Postgres purge in `backend/app/services/erasure_service.py` — DELETE from all 9 tenant-owned tables in FK-safe order: messages → escalations → leads → cms_chunks → conversations → widgets → cms_pages → guardrail_configs → cost_events. Wrap in try/except; log each table deleted. Do NOT delete audit_logs rows.
- [x] T00X [US1] Implement MinIO purge in `backend/app/services/erasure_service.py` — use `asyncio.get_event_loop().run_in_executor(None, ...)` to list and delete all objects under prefix `{tenant_id}/` in bucket `concierge-cms`. Read MinIO credentials from `get_settings()`. Handle bucket-not-found as no-op. Wrap in try/except.
- [x] T00X [US1] Implement Redis purge in `backend/app/services/erasure_service.py` — `async for key in redis.scan_iter(f"memory:{tenant_id}:*")`: collect keys and delete in batch. Wrap in try/except.

**Checkpoint**: All purge steps present; no SELECT on content tables; `test_purge_deletes_all_postgres_tables` (written in Phase 5) passes.

---

## Phase 4: User Story 3 — Partial Failure Leaves Tenant in `deleting` (P1)

**Goal**: If any storage layer fails mid-purge, the tenant remains in `deleting` status so retries can complete. Each layer is independently try/excepted.

**Independent Test**: Mock MinIO to raise an exception. Call `purge_tenant`. Assert tenant status is NOT updated to `deleted`. Assert Postgres and Redis purge were still attempted.

- [x] T00X [US3] Add per-layer failure tracking in `backend/app/services/erasure_service.py` — after all three try/except blocks, check if any layer raised. If all succeeded: set tenant status → `deleted`. If any failed: log warning with layer name, leave status as `deleting`, and return without updating status.
- [x] T00X [US3] Verify idempotency of DELETE statements — confirm that a second call with an already-empty tenant (all rows gone) completes cleanly with no errors (DELETE WHERE affecting 0 rows is a no-op in Postgres; no additional guard needed).

**Checkpoint**: `test_purge_stays_deleting_on_minio_failure` passes.

---

## Phase 5: User Story 4 — Compliance Audit Marker (P2)

**Goal**: On successful full erasure, write a minimal audit log entry: actor_role=`"system"`, action=`"tenant_deleted"`, tenant_id, timestamp. No content fields. The entry is written before setting status to `deleted`.

**Independent Test**: Mock all storage layers to succeed. Call `purge_tenant`. Assert `audit_logs` received one INSERT with action=`"tenant_deleted"` and no message/email/CMS body content.

- [x] T01X [US4] Add compliance audit write in `backend/app/services/erasure_service.py` — after all storage layers succeed and before setting status=`deleted`: directly INSERT an `AuditLog` row via the open session with `actor_role="system"`, `action="tenant_deleted"`, `tenant_id=tenant_id`, `metadata_=None`. Do NOT use `write_audit_event` (fire-and-forget; timing not guaranteed). Commit the session.
- [x] T01X [US4] Set tenant status → `deleted` in `backend/app/services/erasure_service.py` — after the audit INSERT commits, update the tenant row: `UPDATE tenants SET status='deleted' WHERE id=tenant_id`.

**Checkpoint**: `test_purge_writes_audit_marker` and `test_purge_sets_status_deleted_on_success` pass.

---

## Phase 6: Polish & Verification

**Purpose**: Unit tests, lint, format, verify existing tests unbroken.

- [x] T01X Write `backend/tests/test_erasure_service.py` with 7 unit tests (all mock-only, no live DB):
  - `test_purge_deletes_all_postgres_tables` — mock session, assert DELETE called for all 9 tables in order
  - `test_purge_skips_if_already_deleted` — mock tenant status=deleted, assert no DELETEs issued
  - `test_purge_clears_redis_keys` — mock scan_iter returns 2 keys, assert delete called
  - `test_purge_sets_status_deleted_on_success` — all layers succeed, assert status updated to `deleted`
  - `test_purge_stays_deleting_on_minio_failure` — MinIO raises, assert status NOT set to `deleted`
  - `test_purge_writes_audit_marker` — assert AuditLog INSERT with action=`tenant_deleted`, no content fields
  - `test_purge_idempotent_no_rows` — all DELETEs affect 0 rows, no exception raised
- [x] T01X Run `uv run ruff check .` from `backend/` and fix any errors in new/modified files
- [x] T01X Run `uv run black .` from `backend/` to format new files
- [x] T01X Run `uv run pytest tests/test_erasure_service.py -v` — all 7 tests pass
- [x] T01X Run `uv run pytest tests/test_tenant_provisioning.py -v` — existing tenant tests still pass (no regressions from tenant_service changes)

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1 (Setup)**: No dependencies — do first
- **Phase 2 (Foundational)**: Depends on Phase 1 — BLOCKS Phases 3–5
- **Phases 3–5 (User Stories)**: Sequential — US3 extends US1/US2; US4 extends US3
- **Phase 6 (Polish)**: Depends on all implementation phases

### Task Execution Order

```
T001 (minio dep)
  → T002 (route: pass redis)
  → T003 (tenant_service: forward redis)
    → T004 (idempotency check)
    → T005 (postgres purge)
    → T006 (minio purge)
    → T007 (redis purge)
      → T008 (per-layer failure tracking + status update)
      → T009 (idempotency verify — no-op)
        → T010 (audit marker)
        → T011 (set status=deleted)
          → T012 (unit tests)
          → T013 (ruff)
          → T014 (black)
          → T015 (pytest erasure)
          → T016 (pytest tenant provisioning)
```

### Parallel Opportunities

T005, T006, T007 implement different storage layers in the same file — they can be drafted in parallel but must be merged into one coherent `purge_tenant` function. T013/T014/T015/T016 can all run in parallel.

---

## Implementation Strategy

### MVP First

1. Complete Phase 1 (T001) — add minio
2. Complete Phase 2 (T002–T003) — wire redis
3. Complete Phase 3 (T004–T007) — core purge body
4. **STOP and VALIDATE**: manually confirm function has no syntax errors, ruff passes
5. Complete Phase 4 (T008–T009) — failure handling
6. Complete Phase 5 (T010–T011) — audit + status
7. Complete Phase 6 (T012–T016) — tests + polish

### Key Constraints

- Do NOT delete `audit_logs` rows for the erased tenant (FR-009)
- Do NOT SELECT content rows anywhere in the purge path (FR-006)
- MinIO SDK calls MUST run via `run_in_executor` (sync SDK on async loop)
- Tenant stays `deleting` until ALL layers confirmed purged (FR-011)
- `write_audit_event` (fire-and-forget) MUST NOT be used for the compliance marker — use direct INSERT
