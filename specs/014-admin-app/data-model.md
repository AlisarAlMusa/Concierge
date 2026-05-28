# Data Model: Admin App (Streamlit) — Spec 014

The admin app has **no database**. All persistent data lives in the FastAPI backend. This document describes the in-memory session state schema and the data shapes returned by the API.

---

## Session State Schema (`st.session_state`)

| Key | Type | Set by | Cleared by | Description |
|-----|------|--------|-----------|-------------|
| `token` | `str` | Login page on success | Logout button | JWT Bearer token |
| `user_email` | `str` | Login page on success | Logout button | Authenticated user email |
| `user_role` | `str` | Login page on success | Logout button | `tenant_admin` or `tenant_manager` |
| `tenant_id` | `str` | Login page (from `/auth/me`) | Logout button | UUID of the user's tenant |
| `api_error` | `str \| None` | Any page on API failure | Page load | Last API error message |

---

## API Response Shapes (used by the admin app)

### Auth

**`POST /auth/login`** request:
```json
{ "username": "email@example.com", "password": "secret" }
```
Response:
```json
{ "access_token": "eyJ...", "token_type": "bearer" }
```

**`GET /auth/me`** response:
```json
{
  "id": "uuid",
  "email": "admin@tenant.com",
  "role": "tenant_admin",
  "tenant_id": "uuid",
  "is_active": true
}
```

---

### CMS Pages

**`GET /cms/`** response:
```json
[
  {
    "id": "uuid",
    "tenant_id": "uuid",
    "title": "About Us",
    "slug": "about-us",
    "status": "published",
    "created_at": "2026-05-01T12:00:00Z",
    "updated_at": "2026-05-01T12:00:00Z"
  }
]
```

**`POST /cms/`** request:
```json
{ "title": "string", "body": "string (markdown)", "slug": "string", "status": "draft|published" }
```

**`GET /cms/{id}`** response: same as list item + `"body": "full markdown content"`

**`PUT /cms/{id}`** request: same as POST

---

### Guardrail Config

**`GET /tenant/config`** response:
```json
{
  "tenant_id": "uuid",
  "config": {
    "persona": "string",
    "allowed_topics": ["string"],
    "blocked_topics": ["string"],
    "refusal_tone": "string",
    "enabled_tools": ["rag_search", "capture_lead", "escalate"]
  }
}
```

**`PATCH /tenant/config`** request: partial update of any config fields above

---

### Leads

**`GET /leads/`** response:
```json
[
  {
    "id": "uuid",
    "tenant_id": "uuid",
    "visitor_session_id": "uuid",
    "name": "string",
    "email": "string",
    "phone": "string|null",
    "intent_summary": "string",
    "score": 0.0,
    "status": "new|contacted|converted|closed",
    "created_at": "datetime"
  }
]
```

**`PATCH /leads/{id}`** request:
```json
{ "status": "contacted", "notes": "string" }
```

---

### Escalations

**`GET /escalations/`** response:
```json
[
  {
    "id": "uuid",
    "tenant_id": "uuid",
    "conversation_id": "uuid",
    "reason": "string",
    "status": "open|resolved",
    "created_at": "datetime"
  }
]
```

**`PATCH /escalations/{id}`** request:
```json
{ "status": "resolved" }
```

---

### Widgets (Embed Snippet)

**`GET /widgets/`** response:
```json
[
  {
    "id": "uuid",
    "public_widget_id": "pub_wid_abc123",
    "tenant_id": "uuid",
    "name": "string",
    "allowed_origins": ["https://example.com"]
  }
]
```

---

### Platform (Tenant Manager only)

**`GET /platform/tenants/`** response:
```json
[
  {
    "id": "uuid",
    "name": "string",
    "status": "active|suspended|deleted",
    "created_at": "datetime"
  }
]
```

**`GET /platform/tenants/{id}/usage-summary`** response:
```json
{
  "tenant_id": "uuid",
  "total_input_tokens": 0,
  "total_output_tokens": 0,
  "total_cost_usd": "0.00",
  "llm": { "input_tokens": 0, "output_tokens": 0, "cost_usd": "0.00" },
  "embedding": { "input_tokens": 0, "output_tokens": 0, "cost_usd": "0.00" },
  "classifier": { "input_tokens": 0, "output_tokens": 0, "cost_usd": "0.00" },
  "rerank": { "input_tokens": 0, "output_tokens": 0, "cost_usd": "0.00" }
}
```

**`GET /platform/audit-logs`** response:
```json
[
  {
    "id": "uuid",
    "actor_role": "tenant_manager",
    "event": "tenant.created",
    "tenant_id": "uuid",
    "created_at": "datetime"
  }
]
```
