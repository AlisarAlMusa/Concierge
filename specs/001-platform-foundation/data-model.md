# Data Model: Platform Foundation

**Date**: 2026-05-26
**Migration**: `backend/app/db/migrations/versions/0001_initial.py`
**Status**: Implemented — tables exist in the database after `uv run alembic upgrade head`.

---

## Database Extensions

| Extension | Purpose |
|-----------|---------|
| `vector` (pgvector) | Enables `VECTOR(n)` column type for embedding similarity search |
| `pgcrypto` | Provides `gen_random_uuid()` for UUID primary key defaults |

---

## Entities Created in Migration 0001

### tenants

The root entity. Every tenant-owned table has a foreign key back to this table.

| Column | Type | Constraints | Notes |
|--------|------|-------------|-------|
| id | UUID | PRIMARY KEY | Platform-generated |
| name | VARCHAR(255) | NOT NULL | Display name |
| slug | VARCHAR(100) | NOT NULL, UNIQUE | URL-safe identifier |
| status | tenant_status ENUM | NOT NULL, DEFAULT 'active' | Values: active, suspended, deleting, deleted |
| created_at | TIMESTAMPTZ | NOT NULL, DEFAULT now() | |
| updated_at | TIMESTAMPTZ | NOT NULL, DEFAULT now() | |

**Indexes**: `ix_tenants_slug`

**State transitions**:
```
active → suspended  (admin suspension)
active → deleting   (erasure request initiated)
suspended → active  (reinstatement)
deleting → deleted  (erasure complete)
```

---

### users

Platform users. `tenant_id` is nullable to support the `tenant_manager` role which operates cross-tenant.

| Column | Type | Constraints | Notes |
|--------|------|-------------|-------|
| id | UUID | PRIMARY KEY | |
| email | VARCHAR(320) | NOT NULL, UNIQUE | |
| hashed_password | VARCHAR(1024) | NOT NULL | bcrypt via fastapi-users |
| is_active | BOOLEAN | NOT NULL, DEFAULT true | |
| is_superuser | BOOLEAN | NOT NULL, DEFAULT false | |
| is_verified | BOOLEAN | NOT NULL, DEFAULT false | |
| role | user_role ENUM | NOT NULL, DEFAULT 'member' | Values: tenant_manager, tenant_admin, member |
| tenant_id | UUID | FK → tenants.id CASCADE, NULLABLE | NULL for tenant_manager role |

**Indexes**: `ix_users_email`, `ix_users_tenant_id`

**Role semantics**:
- `tenant_manager` — crosses tenant boundary; can create/destroy tenants; no RLS bypass on content
- `tenant_admin` — full access within their tenant; manages users, config, widgets
- `member` — standard access within their tenant

---

### audit_logs

Append-only ledger for security-relevant events. Written by services, never updated or deleted.

| Column | Type | Constraints | Notes |
|--------|------|-------------|-------|
| id | UUID | PRIMARY KEY | |
| actor_user_id | UUID | FK → users.id SET NULL, NULLABLE | NULL for system-generated events |
| actor_role | VARCHAR(50) | NOT NULL | Denormalised at write time |
| tenant_id | UUID | FK → tenants.id SET NULL, NULLABLE | SET NULL on tenant deletion (preserves audit trail) |
| action | VARCHAR(100) | NOT NULL | e.g., `tenant.create`, `user.suspend`, `data.erase` |
| target_type | VARCHAR(100) | NULLABLE | e.g., `tenant`, `widget`, `lead` |
| target_id | VARCHAR(255) | NULLABLE | String to accommodate UUIDs and slugs |
| metadata | JSONB | NULLABLE | Arbitrary structured context |
| created_at | TIMESTAMPTZ | NOT NULL, DEFAULT now() | |

**Indexes**: `ix_audit_logs_tenant_id`, `ix_audit_logs_action`, `ix_audit_logs_created_at`

**Design notes**:
- `tenant_id` uses SET NULL on delete (not CASCADE) so audit entries survive tenant erasure — required for compliance.
- `actor_user_id` uses SET NULL on delete for the same reason.

---

### cost_events

Per-operation token and cost ledger. Written after every LLM, embedding, rerank, or classifier call.

| Column | Type | Constraints | Notes |
|--------|------|-------------|-------|
| id | UUID | PRIMARY KEY | |
| tenant_id | UUID | FK → tenants.id CASCADE, NOT NULL | Scoped per tenant |
| provider | VARCHAR(100) | NOT NULL | e.g., `anthropic`, `openai` |
| model | VARCHAR(100) | NOT NULL | e.g., `claude-sonnet-4-6` |
| operation | cost_operation ENUM | NOT NULL | Values: llm, embedding, rerank, classifier |
| input_tokens | INTEGER | NOT NULL, DEFAULT 0 | |
| output_tokens | INTEGER | NOT NULL, DEFAULT 0 | |
| estimated_cost_usd | NUMERIC(10,6) | NOT NULL, DEFAULT 0 | |
| created_at | TIMESTAMPTZ | NOT NULL, DEFAULT now() | |

**Indexes**: `ix_cost_events_tenant_id`, `ix_cost_events_created_at`

---

## Entities in ORM (models/) — Pending Migrations

The following ORM models exist in `backend/app/models/` but are **not yet in a migration**. They will be added in subsequent feature migrations.

| Model file | Table name | Notes |
|-----------|-----------|-------|
| cms.py | cms_pages | CMS content — Person B feature |
| chunk.py | content_chunks | pgvector embeddings — Person B feature |
| widget.py | widgets | Widget config — Person B feature |
| conversation.py | conversations, messages | Chat history — Person B feature |
| lead.py | leads | Lead capture — Person B feature |
| escalation.py | escalations | Human handoff — Person B feature |
| guardrail_config.py | guardrail_configs | Per-tenant guardrail tuning — Person C feature |

**All of these tables will require `tenant_id` and RLS policies when their migrations are written.**

---

## Row-Level Security Contracts

RLS policies are not yet enabled on any table (deferred to the tenancy feature). The foundational helper is in place:

```python
# backend/app/db/rls.py
await session.execute(
    text("SELECT set_config('app.tenant_id', :tid, true)"),
    {"tid": str(tenant_id)},
)
```

When the tenancy feature lands, every tenant-owned table must have:
```sql
ALTER TABLE <table> ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON <table>
  USING (tenant_id = current_setting('app.tenant_id')::uuid);
```
