# Tasks: 004 — Database & Row-Level Security

**Input**: Design documents from `specs/004-database-and-rls/`

**Branch**: `020-rls-migrations`

---

## Phase 1: Setup (Verify Migration Infrastructure)

**Purpose**: Confirm Alembic is wired correctly before writing the new migration.

- [x] T001 Verify `backend/app/db/migrations/env.py` imports Base correctly and asyncio migration runner is present (read-only check, no edit needed unless broken)
- [x] T002 Verify `backend/alembic.ini` script_location points to `app/db/migrations` (read-only check)

---

## Phase 2: Foundational — Migration 0004

**Purpose**: Single migration file that creates the 7 missing tables, adds retroactive RLS to `audit_logs` and `cost_events`, and wires the deferred FK from `cms_chunks.page_id → cms_pages.id`.

**⚠️ CRITICAL**: All user story tests depend on this migration existing and being syntactically correct.

- [x] T003 Write `backend/app/db/migrations/versions/0004_remaining_tables.py` — revision="0004", down_revision="0003":
  - upgrade(): create ENUMs (page_status, conversation_status, message_role, escalation_status)
  - upgrade(): create cms_pages with tenant RLS
  - upgrade(): ALTER TABLE cms_chunks ADD CONSTRAINT fk_cms_chunks_page_id FK page_id → cms_pages.id CASCADE
  - upgrade(): create widgets with tenant RLS
  - upgrade(): create conversations with tenant RLS
  - upgrade(): create messages with tenant RLS
  - upgrade(): create leads with tenant RLS
  - upgrade(): create escalations with tenant RLS
  - upgrade(): create guardrail_configs with tenant RLS + UNIQUE(tenant_id)
  - upgrade(): ALTER TABLE audit_logs ENABLE RLS + policy (IS NOT NULL guard for nullable tenant_id)
  - upgrade(): ALTER TABLE cost_events ENABLE RLS + policy
  - downgrade(): reverse all of the above in order

**Checkpoint**: `0004_remaining_tables.py` exists and passes a syntax check (`python -c "import app.db.migrations.versions.0004_remaining_tables"` equivalent). All upgrade/downgrade steps present.

---

## Phase 3: User Story 1 — Migrations on a Fresh DB (P1)

**Goal**: `alembic upgrade head` on a fresh Postgres creates all 12 tables with correct columns, FKs, indexes, and RLS policies.

**Independent Test**: Inspect `pg_tables` and `pg_policies` — 10 tables have RLS, all tenant-owned tables have a policy, all expected tables exist.

- [x] T004 [US1] Add schema introspection assertions in `backend/tests/integration/test_rls_isolation.py` — query `pg_tables` to assert all 12 tables exist; query `pg_policies` to assert exactly 10 tables have an RLS policy with name `*_tenant_isolation`

**Checkpoint**: Test `test_all_tables_exist` and `test_all_rls_tables_have_policy` defined (may fail until Postgres is running).

---

## Phase 4: User Story 2 — RLS Enforces Tenant Isolation (P1)

**Goal**: Querying any RLS-protected table with Tenant A context never returns Tenant B rows. INSERT under wrong tenant context is blocked. Empty context → zero rows.

**Independent Test**: Insert rows for two tenants, set app.tenant_id, SELECT without WHERE → only own rows returned.

- [x] T005 [US2] Implement `backend/tests/integration/test_rls_isolation.py` with full test suite:
  - `test_tenant_a_cannot_see_tenant_b_leads` — set_config to A, select leads, assert zero B rows
  - `test_tenant_a_cannot_see_tenant_b_messages` — same for messages
  - `test_tenant_a_cannot_see_tenant_b_cms_pages` — same for cms_pages
  - `test_unscoped_query_blocked_by_rls` — SELECT * without WHERE returns only context-tenant rows
  - `test_no_context_returns_zero_rows` — set_config('app.tenant_id',''), select leads → empty
  - `test_insert_blocked_for_wrong_tenant` — WITH CHECK: insert lead with tenant_b id under tenant_a context → raises RLS violation
  - Fixture: `asyncpg_conn` — raw asyncpg connection to TEST_DATABASE_URL; skip if not set
  - Fixture: `two_tenants` — insert two tenant rows directly, yield (tenant_a_id, tenant_b_id), cleanup

**Checkpoint**: All 6 test functions defined; `pytest -m integration tests/integration/test_rls_isolation.py --co` lists them without import errors.

---

## Phase 5: User Story 3 — Cross-Tenant Isolation Proof (P1)

**Goal**: Automated test proves a query without an explicit `tenant_id` WHERE clause still returns only the correct tenant's rows (RLS catches unscoped queries).

**Independent Test**: `test_rls_isolation.py::test_unscoped_query_blocked_by_rls` — included in T005 above. This phase verifies that test is sufficient and covers all 10 tables.

- [x] T006 [P] [US3] Extend `backend/tests/integration/test_rls_isolation.py` with parametrized test `test_rls_covers_all_ten_tables` — parametrize over all 10 RLS-protected table names; for each: insert one row for tenant_a and one for tenant_b, set context to tenant_a, `SELECT * FROM <table>`, assert only 1 row returned and its tenant_id matches tenant_a

**Checkpoint**: 10 parametrized test cases covering every RLS table.

---

## Phase 6: User Story 4 — Pooled-Connection Reset Guaranteed (P1)

**Goal**: `app.tenant_id` is cleared after every request regardless of success or failure. A stale context on a pooled connection cannot leak to the next request.

**Independent Test**: Set tenant context, simulate exception in handler, execute query on same connection without re-setting context — assert zero rows returned.

- [x] T007 [US4] Write `backend/tests/integration/test_rls_reset.py`:
  - `test_reset_clears_tenant_context` — use `set_tenant_context(session, tenant_a_id)`, then `reset_tenant_context(session)`, then `SELECT * FROM leads` → empty (zero rows)
  - `test_context_cleared_after_exception` — open session, set tenant A context in try block, raise ValueError, in finally call reset; after exception verify context is '' via `SELECT current_setting('app.tenant_id', true)`
  - `test_reset_uses_finally_block` — read `backend/app/db/rls.py` `get_tenant_db_session` and `backend/app/dependencies.py` `require_tenant_admin` to assert `reset_tenant_context` is called inside a `finally` block (static inspection test)
  - Fixture: `pg_session` — AsyncSession with live Postgres; skip if TEST_DATABASE_URL not set

**Checkpoint**: 3 test functions defined in test_rls_reset.py.

---

## Phase 7: Polish & Verification

**Purpose**: Lint, format, and verify all tests are importable.

- [x] T008 Run `uv run ruff check .` from `backend/` and fix any errors introduced by 0004 migration or test files
- [x] T009 Run `uv run black .` from `backend/` to format new files
- [x] T010 Run `uv run pytest tests/ -x --ignore=tests/integration` to confirm existing unit tests still pass
- [x] T011 Run `uv run pytest tests/integration/ --collect-only -m integration` to confirm integration tests are collected without import errors (no Postgres needed for collection)

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1 (Setup)**: No dependencies — read-only verification
- **Phase 2 (Migration)**: Depends on Phase 1 — BLOCKS all test phases
- **Phase 3–6 (Tests)**: Depend on Phase 2 migration existing; T005/T006/T007 can be written in parallel once T003 is done
- **Phase 7 (Polish)**: Depends on all test files being written

### Parallel Opportunities

```
T003 (migration) → T004 [US1]
                 → T005 [US2] ← can run in parallel with T006, T007
                 → T006 [US3]
                 → T007 [US4]
```

Once T003 is complete, T004, T005, T006, and T007 can all be written simultaneously (different files).

---

## Implementation Strategy

### MVP First

1. Complete Phase 1–2: migration file (T001–T003)
2. Complete Phase 7: lint + unit tests pass
3. **STOP and VALIDATE**: migration syntax is clean; existing tests unbroken
4. Write integration tests (T004–T007) for full proof

### Key Constraints

- Do NOT edit migrations 0001–0003 — only add 0004
- Do NOT implement ORM models for Person B/C stub tables
- Integration tests MUST be skipped (not fail) when `TEST_DATABASE_URL` is not set — use `pytest.importorskip` or `pytest.mark.skipif`
- Use `@pytest.mark.integration` on all integration tests and `asyncpg` directly (not SQLAlchemy) for raw SET/SELECT operations in isolation tests
