# SPEC.md — Shared Contracts

This file defines all shared contracts agreed on Day 1. All persons must follow these
exactly. Do not change without team consensus and a PR that updates this file.

---

## 1. tenant_id Convention

- Type: `UUID` (Python `uuid.UUID`, Postgres `uuid`)
- Every tenant-owned table carries a `tenant_id` column as a non-nullable FK → `tenants.id`
- **Never** accept `tenant_id` from a public request body or query param
- For authenticated tenant admin routes: derive `tenant_id` from `current_user.tenant_id`
- For public widget routes: derive `tenant_id` from the verified widget session token
- The RLS session variable `app.tenant_id` must be set at request start and reset at request end

---

## 2. Role Model

Three roles, two levels. No permission matrix — roles are checked explicitly.

| Role | Scope | Powers |
|---|---|---|
| `tenant_manager` | Platform | Create/suspend/delete tenants, invite first admin, read aggregate usage + audit logs. No RLS bypass on tenant content. |
| `tenant_admin` | Own tenant | CMS CRUD, agent config, widget config, view own leads/escalations, copy embed snippet |
| `member` | Own tenant | Chat only (used for internal testing via `/chat`; public visitors use signed token, not role) |

Rules:
- `tenant_manager` has `tenant_id = NULL`; all others have a non-null `tenant_id`
- A `tenant_manager` must never be able to read conversations, leads, or messages belonging to any tenant
- Every `tenant_manager` action is written to `audit_logs`

---

## 3. Agent Tool Contracts

These are the three tools the agent may call. Person B implements them. Person C's guardrails
sidecar is called before and after. Schemas below are the authoritative definitions.

### 3.1 rag_search

```python
class RagSearchArgs(BaseModel):
    query: str
    max_chunks: int = 5

class RagChunk(BaseModel):
    text: str
    source_page_id: UUID
    score: float

class RagSearchResult(BaseModel):
    chunks: list[RagChunk]
    total_found: int
```

Behaviour:
- Always tenant-filtered (uses RLS context + explicit `tenant_id` filter in pgvector query)
- Returns empty list, never raises, if no chunks found

### 3.2 capture_lead

```python
class CaptureLeadArgs(BaseModel):
    name: str | None = None
    email: str | None = None
    phone: str | None = None
    intent: str
    context: str | None = None

class CaptureLeadResult(BaseModel):
    lead_id: UUID
    status: Literal["created"]
```

Behaviour:
- Schema-validated before any DB write
- Rate-limited per `visitor_session_id` (max 3 leads/session)
- Writes only to the token's `tenant_id` — never accepts tenant_id as an argument
- Returns structured error (not exception) on rate-limit or validation failure so LLM can recover

### 3.3 escalate

```python
class EscalateArgs(BaseModel):
    reason: str
    context: str | None = None

class EscalateResult(BaseModel):
    escalation_id: UUID
    status: Literal["created"]
```

Behaviour:
- Creates row in `escalations` scoped to token's `tenant_id`
- Updates `conversations.status` to `"escalated"`

### Tool error envelope (all tools)

```python
class ToolError(BaseModel):
    error: str
    code: str  # e.g. "rate_limited", "validation_error", "not_found"
```

All tools return `ToolError` (not raise) for recoverable failures. The agent reads `error` and decides.

---

## 4. Model Server Contract

Service: `model_server` at `MODEL_SERVER_URL` (default `http://model_server:8001`)
Auth: `X-Service-Token: <SERVICE_AUTH_SECRET>` header on every request.

### POST /predict-intent

```python
class PredictRequest(BaseModel):
    message: str
    tenant_id: UUID  # for logging/cost attribution only, not for filtering

class PredictResponse(BaseModel):
    label: str          # "faq" | "sales" | "spam" | "human" | "ambiguous"
    confidence: float   # 0.0–1.0
    model_version: str  # from model_card.json
```

Confidence threshold for routing: `>= 0.75` → deterministic workflow path; `< 0.75` → agent path.
Person C implements; Person B's RouterService calls this.

### POST /predict-lead-score

```python
class LeadScoreRequest(BaseModel):
    message: str
    tenant_id: UUID

class LeadScoreResponse(BaseModel):
    score: float        # 0.0–1.0
    model_version: str
```

Optional — used by LeadService to set `lead_score` on captured leads.

---

## 5. Guardrails Sidecar Contract

Service: `guardrails_sidecar` at `GUARDRAILS_URL` (default `http://guardrails_sidecar:8002`)
Auth: `X-Service-Token: <SERVICE_AUTH_SECRET>` header on every request.

### POST /guardrails/check-input

```python
class CheckInputRequest(BaseModel):
    message: str
    tenant_id: UUID
    conversation_id: UUID | None = None

class CheckInputResponse(BaseModel):
    allowed: bool
    reason: str | None = None         # populated when allowed=False
    safe_reply: str | None = None     # pre-canned reply to return to visitor if blocked
    redacted_text: str                # always present; use this for logs/storage, not raw message
```

### POST /guardrails/check-output

```python
class CheckOutputRequest(BaseModel):
    message: str
    tenant_id: UUID

class CheckOutputResponse(BaseModel):
    allowed: bool
    reason: str | None = None
    redacted_text: str
```

### POST /guardrails/redact

```python
class RedactRequest(BaseModel):
    text: str

class RedactResponse(BaseModel):
    redacted_text: str
```

Platform rails (injection, jailbreak, cross-tenant refusal, PII redaction) are mandatory for all tenants.
Tenant rails (allowed/blocked topics, persona, refusal tone) are loaded from `guardrail_configs` by tenant_id.
Person C implements the sidecar; Person B's AgentService and chat route call it.

---

## 6. RLS Pattern

Person A provides helpers in `backend/app/db/rls.py`. All code that reads tenant-owned data must use this.

```python
# At start of a request that needs tenant context:
await set_tenant_context(session, tenant_id)

# At end (always, even on error — use try/finally or FastAPI dependency teardown):
await reset_tenant_context(session)
```

FastAPI dependency `get_rls_session(tenant_id)` wraps both calls automatically.
Never bypass RLS by connecting as a superuser or using `SET LOCAL` outside this helper.

---

## 7. Service-to-Service Auth

All internal HTTP calls (API → model_server, API → guardrails_sidecar) must include:

```
X-Service-Token: <SERVICE_AUTH_SECRET>
```

The receiving service calls `verify_service_token(token)` from `backend/app/core/security.py`
(or the equivalent in each sidecar). A missing or wrong token → 403.

`SERVICE_AUTH_SECRET` is sourced from HashiCorp Vault (`kv/concierge/service-auth`,
key `token`) at startup when `APP_ENV != "local"` — see [spec 018](../specs/018-service-to-service-auth/spec.md).
In `local` mode the value falls back to `.env` and a warning is logged.

CORS and network adjacency are not authentication.

---

## 8. Widget Session Token Format

JWT signed with `WIDGET_TOKEN_SECRET` (HS256), short-lived (15 min).

Payload:
```json
{
  "tenant_id": "<uuid>",
  "widget_id": "<uuid>",
  "visitor_session_id": "<uuid>",
  "origin": "https://example.com",
  "exp": 1234567890
}
```

Rules:
- Issued only after server-side origin check against `widgets.allowed_origins`
- `tenant_id` in payload is the only authoritative source for the request — never read from body
- A stale or tampered token → 401

---

## 9. API Error Response Format

All error responses from the backend use:

```json
{
  "detail": "Human-readable message",
  "code": "machine_readable_code"
}
```

HTTP status conventions:
- `401` — unauthenticated (no/invalid token)
- `403` — authenticated but not authorized
- `404` — resource not found (within caller's tenant scope)
- `422` — Pydantic validation failure
- `429` — rate limited
- `503` — upstream service (model_server, guardrails) unavailable

Domain exception → HTTP mapping lives in `backend/app/core/errors.py`.

---

## 10. Redis Memory Key Format

```
memory:{tenant_id}:{conversation_id}
```

TTL: 86400 seconds (24 hours). Person B owns MemoryService.
Keys must never contain raw PII. Redact before writing.

---

## 11. Alembic Migration Ownership

- Person A creates the initial migration (tenants + users tables)
- Person B runs `uv run alembic revision --autogenerate -m "add_cms_widget_chat_tables"` after adding their models
- Person C runs `uv run alembic revision --autogenerate -m "add_guardrail_config"` after adding their model
- Never edit another person's migration file
- Always rebase from main before generating a new migration to avoid conflicts
