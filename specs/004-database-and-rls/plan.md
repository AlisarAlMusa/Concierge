# Implementation Plan: 004 — Database & Row-Level Security

**Branch**: `020-rls-migrations` | **Date**: 2026-05-28 | **Spec**: `specs/004-database-and-rls/spec.md`

## Summary

Write one Alembic migration (`0004_remaining_tables.py`) that creates the 7 tables not yet in the
schema, adds retroactive RLS to `audit_logs` and `cost_events`, and wires the deferred FK from
`cms_chunks.page_id → cms_pages.id`. Then write two integration test files that prove RLS isolates
tenants at the Postgres level and that `reset_tenant_context` clears the session variable.

## Technical Context

**Language/Version**: Python 3.12, PostgreSQL 16 + pgvector

**Primary Dependencies**: Alembic, SQLAlchemy 2.x async, asyncpg, pgvector, pytest-asyncio, pytest

**Storage**: PostgreSQL 16 (`pgvector/pgvector:pg16` image)

**Testing**: pytest + pytest-asyncio; integration tests require live Postgres (`TEST_DATABASE_URL`)

**Target Platform**: Linux (Docker Compose), macOS (local dev)

## Constitution Check

| Principle | Gate | Status |
|---|---|---|
| I — Tenant Isolation | Every tenant-owned table has RLS + tenant_id indexed | ✅ all 10 tables covered post-0004 |
| I — RLS reset | `reset_tenant_context` called in finally block (spec 002 dep) | ✅ already implemented |
| II — Layered Architecture | Migration is DB-only; no service/route code changed | ✅ |
| IV — Async | Migration env uses `create_async_engine` + asyncio | ✅ already in env.py |
| V — No torch | No model weights in migrations | ✅ n/a |

## File Structure

```
backend/
  app/db/migrations/versions/
    0004_remaining_tables.py        ← new migration

  tests/integration/
    test_rls_isolation.py           ← new: cross-tenant data isolation
    test_rls_reset.py               ← new: session var cleared after failed request

specs/004-database-and-rls/
  research.md       ← decisions on RLS pattern, migration strategy
  data-model.md     ← column specs for all 7 new tables + retroactive RLS
  plan.md           ← this file
```

## Implementation Phases

### Phase A — Migration file

**File**: `backend/app/db/migrations/versions/0004_remaining_tables.py`

```
revision = "0004"
down_revision = "0003"
```

**upgrade() steps in order**:

1. Create ENUMs: `page_status`, `conversation_status`, `message_role`, `escalation_status`
2. Create `cms_pages` with RLS
3. Add FK: `ALTER TABLE cms_chunks ADD CONSTRAINT fk_cms_chunks_page_id FOREIGN KEY (page_id) REFERENCES cms_pages(id) ON DELETE CASCADE`
4. Create `widgets` with RLS
5. Create `conversations` with RLS
6. Create `messages` with RLS
7. Create `leads` with RLS
8. Create `escalations` with RLS
9. Create `guardrail_configs` with RLS + UNIQUE(tenant_id)
10. Retroactive: `ALTER TABLE audit_logs ENABLE ROW LEVEL SECURITY` + policy
11. Retroactive: `ALTER TABLE cost_events ENABLE ROW LEVEL SECURITY` + policy

**RLS pattern** (all new tables):
```sql
ALTER TABLE <t> ENABLE ROW LEVEL SECURITY;
CREATE POLICY <t>_tenant_isolation ON <t>
  USING (tenant_id::text = current_setting('app.tenant_id', true))
  WITH CHECK (tenant_id::text = current_setting('app.tenant_id', true));
```

**audit_logs exception** (nullable tenant_id):
```sql
CREATE POLICY audit_logs_tenant_isolation ON audit_logs
  USING (tenant_id IS NOT NULL
         AND tenant_id::text = current_setting('app.tenant_id', true))
  WITH CHECK (tenant_id IS NOT NULL
              AND tenant_id::text = current_setting('app.tenant_id', true));
```

**downgrade() steps** (reverse order):
- Drop policies + disable RLS on audit_logs and cost_events
- Drop guardrail_configs, escalations, leads, messages, conversations, widgets
- Drop FK from cms_chunks.page_id, then drop cms_pages
- Drop ENUMs

### Phase B — Integration tests

**File**: `backend/tests/integration/test_rls_isolation.py`

Tests (all marked `@pytest.mark.integration`, skip if no Postgres):

- `test_tenant_a_cannot_see_tenant_b_leads` — insert leads for both tenants, set context to A, SELECT leads, assert only A rows
- `test_tenant_a_cannot_see_tenant_b_messages` — same for messages
- `test_tenant_a_cannot_see_tenant_b_cms_pages` — same for cms_pages
- `test_unscoped_query_respects_rls` — SELECT without WHERE returns only context tenant's rows
- `test_no_context_returns_zero_rows` — `set_config('app.tenant_id', '', true)`, SELECT leads → empty
- `test_rls_insert_blocked_for_wrong_tenant` — WITH CHECK: insert row with tenant_b id under tenant_a context → Postgres policy violation error
- `test_all_ten_tables_have_rls_enabled` — query `pg_tables` + `pg_policies` to assert 10 tables all have RLS enabled and exactly one policy each

**File**: `backend/tests/integration/test_rls_reset.py`

Tests:

- `test_reset_clears_tenant_context` — set context for tenant A, call `reset_tenant_context`, SELECT leads → empty
- `test_context_cleared_after_simulated_exception` — set context in try block, raise exception in except, finally reset; verify context is cleared on the same connection

### Phase C — Lint and test

- `uv run ruff check .` — clean
- `uv run black --check .` — clean
- `uv run pytest tests/integration/ -m integration` — all integration tests pass (requires Postgres)
- `uv run pytest tests/` (excluding integration) — all unit tests still pass

## Key Constraints

- Do NOT modify migrations 0001–0003 — only append 0004
- Do NOT implement ORM models for Person B/C tables (cms_pages, widgets, etc.) — stubs stay as-is
- Do NOT add `app.models.*` imports to `env.py` for stub-only models — autogenerate will detect nothing to migrate since tables are created manually
- The `message_role` enum mirrors the `MessageRole` Python enum already added to `conversation.py`
- Tests must be runnable without Docker by being skipped when `TEST_DATABASE_URL` is unset
