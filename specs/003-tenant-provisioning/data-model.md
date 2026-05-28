# Data Model: Tenant Provisioning

All entities below are already created in migration `0001_initial`. No new migrations are needed for spec 003.

---

## Tenant

Table: `tenants`

| Column | Type | Constraints |
|---|---|---|
| `id` | UUID | PK, default uuid4 |
| `name` | VARCHAR(255) | NOT NULL |
| `slug` | VARCHAR(100) | NOT NULL, UNIQUE, INDEX — lowercase alphanumeric + hyphens only |
| `status` | ENUM(`active`,`suspended`,`deleting`,`deleted`) | NOT NULL, default `active` |
| `created_at` | TIMESTAMPTZ | NOT NULL, server default now() |
| `updated_at` | TIMESTAMPTZ | NOT NULL, server default now(), onupdate now() |

**State transitions**:
```
active → suspended   (POST /platform/tenants/{id}/suspend)
active → deleting    (DELETE /platform/tenants/{id})
suspended → active   (POST /platform/tenants/{id}/reactivate)
suspended → deleting (DELETE /platform/tenants/{id})
deleting → deleted   (erasure service completes — spec 015)
```

**Invariants**:
- Slug is immutable after creation.
- Slug format: `^[a-z0-9][a-z0-9-]*[a-z0-9]$` (enforced at Pydantic schema layer + DB unique index).
- A `deleted` tenant is never returned from list/get routes (filtered to non-deleted by default).

---

## User (tenant admin)

Table: `users` (existing, extended by fastapi-users)

Relevant columns for spec 003:

| Column | Type | Notes |
|---|---|---|
| `id` | UUID | PK |
| `email` | VARCHAR(320) | UNIQUE |
| `hashed_password` | VARCHAR(1024) | Set at invite time (temp password, not emailed in Week 8) |
| `role` | ENUM(`tenant_manager`,`tenant_admin`,`member`) | `tenant_admin` for invited users |
| `tenant_id` | UUID FK → tenants(id) | NOT NULL for `tenant_admin`, NULL for `tenant_manager` |
| `is_active` | BOOL | False → blocked from auth |

**Invite flow (no separate invite table)**:
- `POST /platform/tenants/{id}/invite-admin` creates a `User` row directly with `role=tenant_admin`, `tenant_id=<id>`, `is_active=True`, temp hashed password.
- No email verification flow in Week 8.

---

## AuditLog

Table: `audit_logs`

| Column | Type | Notes |
|---|---|---|
| `id` | UUID | PK |
| `actor_user_id` | UUID FK → users(id) | SET NULL on delete; nullable (system events) |
| `actor_role` | VARCHAR(50) | NOT NULL |
| `tenant_id` | UUID FK → tenants(id) | SET NULL on delete; nullable (platform-level events) |
| `action` | VARCHAR(100) | NOT NULL, INDEX — e.g. `tenant_created`, `invite_admin` |
| `target_type` | VARCHAR(100) | nullable — e.g. `tenant`, `user` |
| `target_id` | VARCHAR(255) | nullable — UUID as string |
| `metadata_` | JSONB | nullable — extra context |
| `created_at` | TIMESTAMPTZ | NOT NULL, INDEX |

**Actions written by spec 003**:

| Action | Trigger |
|---|---|
| `tenant_created` | `POST /platform/tenants` |
| `invite_admin` | `POST /platform/tenants/{id}/invite-admin` |
| `tenant_suspended` | `POST /platform/tenants/{id}/suspend` |
| `tenant_reactivated` | `POST /platform/tenants/{id}/reactivate` |
| `tenant_delete_triggered` | `DELETE /platform/tenants/{id}` |

---

## CostEvent (read-only for usage summary)

Table: `cost_events` — no writes from spec 003.

Usage summary query:
```sql
SELECT
  SUM(input_tokens)        AS total_input_tokens,
  SUM(output_tokens)       AS total_output_tokens,
  SUM(estimated_cost_usd)  AS total_cost_usd
FROM cost_events
WHERE tenant_id = :tenant_id
```
