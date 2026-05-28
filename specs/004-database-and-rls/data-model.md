# Data Model: 004 — Database & Row-Level Security

## Tables Created by Migration 0004

### Tables Already Covered (no action needed)

| Table | Migration | RLS |
|---|---|---|
| tenants | 0001 | ❌ intentional — root table |
| users | 0001+0003 | ❌ intentional — role-based access |
| audit_logs | 0001 | ⚠️ added in 0004 retroactively |
| cost_events | 0001 | ⚠️ added in 0004 retroactively |
| cms_chunks | 0002 | ✅ done |

---

### cms_pages

Owner: Person B. Used by CMS editor and RAG chunking pipeline.

| Column | Type | Constraints |
|---|---|---|
| id | UUID | PK, default gen_random_uuid() |
| tenant_id | UUID | FK→tenants.id CASCADE, NOT NULL, indexed |
| title | VARCHAR(500) | NOT NULL |
| slug | VARCHAR(200) | NOT NULL |
| body | TEXT | NOT NULL |
| status | ENUM(draft, published, archived) | NOT NULL, default 'draft' |
| created_by | UUID | FK→users.id SET NULL, nullable |
| created_at | TIMESTAMPTZ | NOT NULL, default now() |
| updated_at | TIMESTAMPTZ | NOT NULL, default now() |

RLS: `USING (tenant_id::text = current_setting('app.tenant_id', true)) WITH CHECK (...)`

After table creation: add FK from `cms_chunks.page_id → cms_pages.id (ON DELETE CASCADE)`.

---

### widgets

Owner: Person B. One widget embed per configuration. Public widget ID used in the JS loader.

| Column | Type | Constraints |
|---|---|---|
| id | UUID | PK, default gen_random_uuid() |
| tenant_id | UUID | FK→tenants.id CASCADE, NOT NULL, indexed |
| public_widget_id | VARCHAR(100) | NOT NULL, UNIQUE |
| name | VARCHAR(255) | NOT NULL |
| theme_json | JSONB | nullable |
| greeting | TEXT | nullable |
| allowed_origins | TEXT[] | NOT NULL, default '{}' |
| enabled_tools | JSONB | nullable |
| created_at | TIMESTAMPTZ | NOT NULL, default now() |

RLS: USING + WITH CHECK on tenant_id.

---

### conversations

Owner: Person B. One conversation per visitor session on a widget.

| Column | Type | Constraints |
|---|---|---|
| id | UUID | PK, default gen_random_uuid() |
| tenant_id | UUID | FK→tenants.id CASCADE, NOT NULL, indexed |
| widget_id | UUID | FK→widgets.id CASCADE, NOT NULL |
| visitor_session_id | UUID | NOT NULL |
| status | ENUM(active, closed, escalated) | NOT NULL, default 'active' |
| created_at | TIMESTAMPTZ | NOT NULL, default now() |
| updated_at | TIMESTAMPTZ | NOT NULL, default now() |

Indexes: tenant_id, (tenant_id, widget_id), (tenant_id, visitor_session_id).
RLS: USING + WITH CHECK on tenant_id.

---

### messages

Owner: Person B. Append-only chat log. Content is stored post-redaction.

| Column | Type | Constraints |
|---|---|---|
| id | UUID | PK, default gen_random_uuid() |
| tenant_id | UUID | FK→tenants.id CASCADE, NOT NULL, indexed |
| conversation_id | UUID | FK→conversations.id CASCADE, NOT NULL |
| role | ENUM(visitor, assistant, tool, system) | NOT NULL |
| content_redacted | TEXT | NOT NULL |
| metadata | JSONB | nullable |
| created_at | TIMESTAMPTZ | NOT NULL, default now() |

Indexes: tenant_id, (tenant_id, conversation_id).
RLS: USING + WITH CHECK on tenant_id.

---

### leads

Owner: Person B. Captured leads from the capture_lead agent tool.

| Column | Type | Constraints |
|---|---|---|
| id | UUID | PK, default gen_random_uuid() |
| tenant_id | UUID | FK→tenants.id CASCADE, NOT NULL, indexed |
| conversation_id | UUID | FK→conversations.id SET NULL, nullable |
| name | VARCHAR(255) | nullable |
| email | VARCHAR(320) | nullable |
| phone | VARCHAR(50) | nullable |
| intent | VARCHAR(255) | NOT NULL |
| lead_score | NUMERIC(5,4) | nullable |
| source | VARCHAR(100) | nullable |
| created_at | TIMESTAMPTZ | NOT NULL, default now() |

Indexes: tenant_id, (tenant_id, conversation_id).
RLS: USING + WITH CHECK on tenant_id.

---

### escalations

Owner: Person B. Created by the escalate agent tool.

| Column | Type | Constraints |
|---|---|---|
| id | UUID | PK, default gen_random_uuid() |
| tenant_id | UUID | FK→tenants.id CASCADE, NOT NULL, indexed |
| conversation_id | UUID | FK→conversations.id SET NULL, nullable |
| reason | TEXT | NOT NULL |
| status | ENUM(pending, handled) | NOT NULL, default 'pending' |
| created_at | TIMESTAMPTZ | NOT NULL, default now() |

Indexes: tenant_id.
RLS: USING + WITH CHECK on tenant_id.

---

### guardrail_configs

Owner: Person C. One row per tenant — unique constraint enforced.

| Column | Type | Constraints |
|---|---|---|
| id | UUID | PK, default gen_random_uuid() |
| tenant_id | UUID | FK→tenants.id CASCADE, NOT NULL, UNIQUE, indexed |
| persona | TEXT | nullable |
| allowed_topics | TEXT[] | nullable |
| blocked_topics | TEXT[] | nullable |
| refusal_tone | VARCHAR(100) | nullable |
| enabled_tools | JSONB | nullable |
| updated_at | TIMESTAMPTZ | NOT NULL, default now() |

RLS: USING + WITH CHECK on tenant_id.

---

## Retroactive RLS on Existing Tables

### audit_logs (created in 0001, no RLS)

```sql
ALTER TABLE audit_logs ENABLE ROW LEVEL SECURITY;
CREATE POLICY audit_logs_tenant_isolation ON audit_logs
  USING (tenant_id IS NOT NULL
         AND tenant_id::text = current_setting('app.tenant_id', true))
  WITH CHECK (tenant_id IS NOT NULL
              AND tenant_id::text = current_setting('app.tenant_id', true));
```

Note: `tenant_id IS NOT NULL` guards platform-level events (manager actions with NULL tenant_id)
from being exposed to any tenant context. They are only accessible via direct DB access without RLS.

### cost_events (created in 0001, no RLS)

```sql
ALTER TABLE cost_events ENABLE ROW LEVEL SECURITY;
CREATE POLICY cost_events_tenant_isolation ON cost_events
  USING (tenant_id::text = current_setting('app.tenant_id', true))
  WITH CHECK (tenant_id::text = current_setting('app.tenant_id', true));
```

Note: `cost_events.tenant_id` is NOT NULL (unlike audit_logs), so no NULL guard needed.

---

## ENUMs Created in 0004

| Enum | Values |
|---|---|
| page_status | draft, published, archived |
| conversation_status | active, closed, escalated |
| message_role | visitor, assistant, tool, system |
| escalation_status | pending, handled |

---

## RLS Coverage Summary (post-0004)

| Table | RLS | Notes |
|---|---|---|
| tenants | ❌ | intentional — root table |
| users | ❌ | intentional — role-based in app code |
| audit_logs | ✅ | added retroactively; NULL tenant_id rows hidden |
| cost_events | ✅ | added retroactively |
| cms_pages | ✅ | new in 0004 |
| cms_chunks | ✅ | added in 0002 |
| widgets | ✅ | new in 0004 |
| conversations | ✅ | new in 0004 |
| messages | ✅ | new in 0004 |
| leads | ✅ | new in 0004 |
| escalations | ✅ | new in 0004 |
| guardrail_configs | ✅ | new in 0004 |
