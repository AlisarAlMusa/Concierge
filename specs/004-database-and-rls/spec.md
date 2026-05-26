# Feature Specification: Database & Row-Level Security

> **Owner**: Person A — `feature/platform-tenancy` branch

**Feature Branch**: `004-database-and-rls`

**Created**: 2026-05-27

**Status**: Draft

---

## User Scenarios & Testing *(mandatory)*

### User Story 1 — Developer Runs Migrations on a Fresh DB (Priority: P1)

A developer clones the repo, runs `docker compose up`, and applies all Alembic migrations. All 12 tables are created with the correct columns, constraints, and RLS policies active.

**Why this priority**: The database schema is the foundation every other feature builds on. A broken migration blocks all downstream work.

**Independent Test**: Start Postgres from scratch; run `alembic upgrade head`; inspect the schema — all 12 tables exist with their columns, foreign keys, indexes, and RLS policies enabled.

**Acceptance Scenarios**:

1. **Given** an empty database, **When** all Alembic migrations are applied, **Then** all 12 tables exist: `tenants`, `users`, `cms_pages`, `content_chunks`, `widgets`, `conversations`, `messages`, `leads`, `escalations`, `guardrail_configs`, `audit_logs`, `cost_events`.
2. **Given** migrations are applied, **When** the schema is inspected, **Then** every tenant-owned table has a `tenant_id` column (UUID, not null, foreign key to `tenants.id`).
3. **Given** migrations are applied, **When** RLS policies are queried, **Then** every tenant-owned table has RLS enabled and a policy using `current_setting('app.tenant_id')::uuid`.
4. **Given** migrations have already been applied, **When** `alembic upgrade head` is run again, **Then** it is idempotent (no error, no duplicate objects).

---

### User Story 2 — Request Uses RLS to Enforce Tenant Isolation (Priority: P1)

A FastAPI dependency sets `app.tenant_id` to the authenticated user's tenant at the start of every request. Postgres RLS uses this value to filter all queries. At the end of the request (including on error), the context is reset.

**Why this priority**: RLS is the database-level isolation guarantee. If the session variable is not set correctly — or not reset — cross-tenant data leakage is possible.

**Independent Test**: Insert rows for Tenant A and Tenant B. Set `app.tenant_id` to Tenant A's id. Query any tenant-owned table. Confirm only Tenant A rows are returned. Then reset the variable and confirm it is cleared.

**Acceptance Scenarios**:

1. **Given** `app.tenant_id` is set to Tenant A's id, **When** any tenant-owned table is queried, **Then** only rows belonging to Tenant A are returned.
2. **Given** `app.tenant_id` is set to Tenant A's id, **When** an INSERT is attempted for Tenant B's id, **Then** Postgres rejects the row with a policy violation.
3. **Given** a request completes (success or exception), **When** the request lifecycle ends, **Then** `app.tenant_id` is reset to an empty/null value so the pooled connection cannot leak context to the next request.
4. **Given** `app.tenant_id` is not set (empty string), **When** any RLS-protected table is queried, **Then** zero rows are returned (not all rows — fail-safe default).

---

### User Story 3 — Isolation Test: Tenant A Cannot Read Tenant B Data (Priority: P1)

An automated test inserts data for two tenants, then asserts that querying with Tenant A's context never returns Tenant B's rows — even for direct SQL queries that omit a WHERE clause.

**Why this priority**: RLS isolation must be verified by a test that fails if the policy is accidentally dropped or misconfigured. A passing test proves the database enforces the wall.

**Independent Test**: Automated test (`test_rls_isolation.py`) — insert data for both tenants, query without an explicit tenant filter, assert Tenant B data never appears when Tenant A context is set.

**Acceptance Scenarios**:

1. **Given** Tenant A context set in the DB session, **When** `SELECT * FROM leads` is executed, **Then** only Tenant A leads are returned.
2. **Given** Tenant A context, **When** a cross-tenant read is attempted via a direct SQL join, **Then** Postgres RLS blocks Tenant B rows from appearing.
3. **Given** a new developer adds a query without a `tenant_id` filter, **When** the RLS test suite runs, **Then** the tests still pass — RLS catches the unscoped query.

---

### User Story 4 — Pooled-Connection Reset Is Guaranteed (Priority: P1)

The `app.tenant_id` session variable is reset at the end of every request, regardless of success or failure. A test proves that a request failure does not leave a stale tenant context on the connection.

**Why this priority**: Connection pooling means the same Postgres connection is reused across requests. A stale `app.tenant_id` from a failed Tenant A request, reused for a Tenant B request, is a cross-tenant breach.

**Independent Test**: Simulate a request for Tenant A that raises an exception mid-handler. After the exception, execute a new query on the same connection without setting context. Confirm no rows are returned (context was reset).

**Acceptance Scenarios**:

1. **Given** a request for Tenant A raises an exception, **When** the next request uses the same connection, **Then** `app.tenant_id` is empty (not Tenant A's id).
2. **Given** the FastAPI dependency that sets `app.tenant_id`, **When** the request ends (normal or exception), **Then** the reset runs in a `finally` block or equivalent guaranteed cleanup.

---

### Edge Cases

- What happens if a migration fails partway through? → Alembic wraps migrations in transactions; partial changes are rolled back. The developer must fix the migration and re-run.
- What happens if `pgvector` extension is not installed? → The migration that creates the `content_chunks` embedding column will fail. The Postgres image used is `pgvector/pgvector:pg16` which includes it.
- What happens if a tenant row is deleted while an RLS context is set to that tenant? → Foreign key constraints and cascades handle referential integrity; RLS then returns zero rows for the deleted tenant.
- What happens if `current_setting('app.tenant_id', true)` returns `null` or empty string? → The RLS policy must treat this as "no access" — zero rows returned, not all rows.

---

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: All 12 tables MUST be created via Alembic migrations (version-controlled, never ad-hoc SQL).
- **FR-002**: Every tenant-owned table MUST have a `tenant_id` UUID column, non-null, with a foreign key to `tenants.id`.
- **FR-003**: RLS MUST be enabled on every tenant-owned table (`ALTER TABLE … ENABLE ROW LEVEL SECURITY`).
- **FR-004**: Each tenant-owned table MUST have an RLS USING policy: `tenant_id = current_setting('app.tenant_id')::uuid`.
- **FR-005**: The `pgvector` extension MUST be enabled; `content_chunks.embedding` MUST be a `vector` column with the appropriate dimension.
- **FR-006**: A FastAPI dependency MUST set `app.tenant_id` at the start of every authenticated request via `SELECT set_config('app.tenant_id', $1, true)`.
- **FR-007**: The same dependency MUST reset `app.tenant_id` at the end of every request (success or exception) in a guaranteed cleanup block.
- **FR-008**: Repository-layer queries MUST also filter by `tenant_id` as a defence-in-depth measure (RLS is not the only check).
- **FR-009**: An automated test MUST verify that querying with Tenant A context returns zero Tenant B rows across all tenant-owned tables.
- **FR-010**: Migrations MUST be idempotent (`alembic upgrade head` on an already-migrated DB must succeed without error).
- **FR-011**: The `tenants` table is NOT covered by RLS (it is the root ownership table); the `users` table uses role-based access, not tenant RLS.
- **FR-012**: Indexes MUST be created on `tenant_id` columns for query performance.

### Key Entities

- **Tenants table**: Root ownership table. No RLS. Fields: id, name, slug, status, created_at, updated_at.
- **Users table**: Role-based access. Fields include: id, email (unique), hashed_password, role, tenant_id (nullable for `tenant_manager`), is_active, created_at.
- **Tenant-owned tables** (RLS-protected): cms_pages, content_chunks, widgets, conversations, messages, leads, escalations, guardrail_configs, audit_logs, cost_events.
- **RLS Policy**: Per-table policy using `current_setting('app.tenant_id')::uuid`. Fail-safe: empty context → zero rows.
- **Alembic Migration**: Each schema change is a versioned migration file. No manual SQL applied outside Alembic.

---

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: `alembic upgrade head` completes in under 30 seconds on a fresh database.
- **SC-002**: 100% of tenant-owned tables have RLS enabled and a correct policy (verified by automated test).
- **SC-003**: Isolation test (`test_rls_isolation.py`) passes: zero cross-tenant rows returned across all 10 tenant-owned tables.
- **SC-004**: 100% of requests reset `app.tenant_id` after completion — proven by a test that inspects connection state after a failed request.
- **SC-005**: A new unscoped query (no explicit `tenant_id` WHERE clause) added by a developer still returns only the correct tenant's rows due to RLS.

---

## Assumptions

- Postgres 16 with the `pgvector/pgvector:pg16` Docker image is the target database — `pgvector` extension availability is guaranteed.
- Alembic is the only migration tool; no manual schema changes are applied outside of it.
- SQLAlchemy 2.x async (`asyncpg` driver) is used for all DB access.
- Connection pooling is handled by SQLAlchemy's async pool; the reset dependency must be compatible with async context managers.
- The `users` table is managed by fastapi-users and extended with a `role` and `tenant_id` column via a custom User model; the RLS strategy for users is role-based access control (checked in application code), not row-level policy.
- The 12-table schema is the complete schema for Week 8; no additional tables are added without a new migration.
- The `content_chunks.embedding` vector dimension is 1536 (compatible with `text-embedding-3-small`); this is configurable via the migration.
