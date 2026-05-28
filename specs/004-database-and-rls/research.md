# Research: 004 — Database & Row-Level Security

## Gap Analysis Against Existing Migrations

### What 0001–0003 already cover

| Migration | Tables | RLS |
|---|---|---|
| 0001 | tenants, users, audit_logs, cost_events | ❌ none |
| 0002 | cms_chunks (vector 1024) | ✅ policy on cms_chunks |
| 0003 | alters users (created_at, role idx, CHECK) | — |

### Tables with no migration yet (7)

`cms_pages`, `widgets`, `conversations`, `messages`, `leads`, `escalations`, `guardrail_configs`

### Tables with missing RLS (2)

`audit_logs` and `cost_events` were created in 0001 without `ENABLE ROW LEVEL SECURITY`.

### Tables intentionally WITHOUT RLS (spec FR-011)

`tenants` — root ownership table, no tenant_id to filter by.
`users` — role-based access in application code; nullable tenant_id breaks a row-level filter.

---

## Decision 1: Single migration file for all remaining work

**Decision**: One file — `0004_remaining_tables.py` (down_revision = "0003").

**Rationale**: All 7 missing tables + 2 retroactive RLS additions are a single logical step. A single
migration is easier to review, rollback (one `alembic downgrade -1`), and reason about atomically.
The alternative (one file per table) would create 7+ migrations for related work that has no
incremental deployment value.

**Alternatives considered**: Per-table migrations — more granular but unnecessary complexity for
tables that aren't deployed independently.

---

## Decision 2: RLS policy uses text comparison, not ::uuid cast

**Decision**: Continue migration 0002's pattern:
```sql
USING (tenant_id::text = current_setting('app.tenant_id', true))
WITH CHECK (tenant_id::text = current_setting('app.tenant_id', true))
```

**Rationale**: `current_setting('app.tenant_id', true)` with the `true` flag returns `''` (empty string)
when the variable is unset, rather than raising an exception. Casting `''::uuid` would raise a runtime
error. Text comparison handles the empty string gracefully — `tenant_id::text = ''` is always false,
so zero rows are returned. This is the fail-safe default required by spec SC-005.

**Alternative considered**: `tenant_id = current_setting('app.tenant_id')::uuid` — this is what the
spec's FR-004 literally says, but it fails with a Postgres cast error if `app.tenant_id` is unset and the
`true` flag is omitted. The text comparison achieves the same outcome without the cast risk.

---

## Decision 3: audit_logs RLS handles nullable tenant_id

**Decision**: Policy filters to `tenant_id IS NOT NULL AND tenant_id::text = current_setting('app.tenant_id', true)`.

**Rationale**: `audit_logs.tenant_id` is nullable — platform-level audit events (tenant_manager actions)
have `tenant_id = NULL`. With a tenant RLS context set, NULL-tenant rows must NOT be visible
(they're platform events, not tenant content). With no RLS context (tenant_manager path), the
`require_tenant_admin` dependency is not used so `set_config` is never called and these rows are
accessible via direct Postgres queries or service-layer access without RLS context.

The `IS NOT NULL` condition ensures NULL-tenant rows are invisible to any tenant context and cannot
be leaked cross-tenant.

---

## Decision 4: Add FK from cms_chunks.page_id → cms_pages.id in 0004

**Decision**: Migration 0002 created `cms_chunks.page_id` as a plain UUID with a comment:
*"FK to cms_pages added in a later migration once that table exists."* Migration 0004 creates
`cms_pages`, then alters `cms_chunks` to add the foreign key.

**Rationale**: Migration 0002's comment explicitly documents this intent. Adding the FK is safe
because the table is always empty at migration time.

---

## Decision 5: ORM models for Person B/C tables remain as stubs

**Decision**: Migration 0004 creates the DB tables from spec field lists. The ORM models in
`app/models/` (cms_pages, widgets, conversations, messages, leads, escalations, guardrail_configs)
remain as TODO stubs — they are owned by Person B and Person C and implemented in later specs.

**Rationale**: The DB schema must exist for RLS isolation tests and for Person B/C to implement
against. The ORM is not required for the migration to run or for the tests to prove RLS works.

---

## Decision 6: RLS integration tests require real Postgres

**Decision**: `tests/integration/test_rls_isolation.py` and `tests/integration/test_rls_reset.py`
use `asyncpg` directly against a live Postgres. Tests are marked `@pytest.mark.integration` and
skipped when `TEST_DATABASE_URL` is not set or when Postgres is unreachable.

**Rationale**: SQLite does not support `set_config()` or `ENABLE ROW LEVEL SECURITY`. The RLS
guarantee can only be tested against a real Postgres. The tests run in CI via the Docker Compose
`postgres` service. Local developers can run them with `docker compose up postgres -d`.

**Alternatives considered**: Mocking — provides no value; the whole point is to test the actual
Postgres policy enforcement.

---

## Decision 7: guardrail_configs has UNIQUE(tenant_id) — one config per tenant

**Decision**: `ALTER TABLE guardrail_configs ADD CONSTRAINT uq_guardrail_configs_tenant UNIQUE (tenant_id)`.

**Rationale**: The spec says *"one guardrail config per tenant"*. A unique constraint enforces this
at the database level. The service layer UPSERT pattern can rely on this constraint.
