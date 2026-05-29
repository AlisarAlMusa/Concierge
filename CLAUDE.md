# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

---

## Project Overview

**Concierge** is a secure multi-tenant AI SaaS platform. Businesses (tenants) manage CMS content, configure an embeddable AI chat widget, and capture leads from public visitors. The central engineering problem is **tenant isolation** — Tenant A must never access Tenant B's data, content, or pgvector chunks.

The full implementation plan is in `concierge_CLAUDE_plan.md`. Follow it. Do not invent architecture outside it.

---

## Non-Negotiable Rules

1. Every tenant-owned table must include `tenant_id`.
2. Every repository query must scope by `tenant_id`.
3. PostgreSQL Row-Level Security must enforce isolation at the DB level.
4. pgvector retrieval must always filter by `tenant_id`.
5. Widget API derives `tenant_id` from a signed short-lived token — never from the request body.
6. CORS is not authentication.
7. Platform guardrails are mandatory — tenant admins cannot weaken them.
8. Service-to-service calls use credentials from Vault.
9. No `torch` or `transformers` in production containers.
10. Logs, traces, Redis memory, and eval outputs must redact secrets/PII.
11. Always reset `app.tenant_id` after each request (pooled DB connections are reused).

Reject code that: trusts `tenant_id` from request body, skips RLS, logs raw secrets, puts torch in production Docker, creates unbounded agent loops, or lets tenant config weaken platform guardrails.

---

## Development Commands

```bash
# Start everything
cp .env.example .env
docker compose up --build

# Backend only (from backend/)
uv sync
uv run alembic upgrade head
uv run uvicorn app.main:app --reload

# Linting and formatting
uv run ruff check .
uv run black --check .

# Tests
uv run pytest

# Evals
bash scripts/run_evals.sh

# Release tag
git tag v0.1.0-week8 && git push origin v0.1.0-week8
```

Use `uv` (not pip) for all Python dependency management.

---

## Architecture

### Services (ports)

| Service | Stack | Port |
|---|---|---|
| `api` | FastAPI + SQLAlchemy 2.x async | 8000 |
| `model_server` | FastAPI + scikit-learn/ONNX | 8001 |
| `guardrails_sidecar` | FastAPI | 8002 |
| `admin_app` | Streamlit | 8501 |
| `widget` | React + Vite | (served via api) |
| `postgres` | pgvector/pgvector:pg16 | 5432 |
| `redis` | redis:7 | 6379 |
| `minio` | minio/minio | 9000/9001 |
| `vault` | hashicorp/vault | 8200 |

### Chat Request Flow

```
Visitor → Widget (signed token) → POST /public/chat
  → Verify token → derive tenant_id + widget_id (never from body)
  → SET app.tenant_id (RLS context)
  → GuardrailService: POST /guardrails/check-input
  → RouterService → POST /predict-intent (model_server)
      ├─ spam         → drop
      ├─ faq/support  → RagService (tenant-filtered pgvector)
      ├─ sales        → LeadService (rate-limited)
      ├─ human        → EscalationService
      └─ ambiguous    → AgentService (LLM, max 3 tool iterations)
                            tools: rag_search, capture_lead, escalate
  → GuardrailService: POST /guardrails/check-output
  → Reset app.tenant_id
  → Response to widget
```

### Tenant Isolation Layers

1. **PostgreSQL RLS** — `ALTER TABLE t ENABLE ROW LEVEL SECURITY` + policy using `current_setting('app.tenant_id')::uuid`
2. **Repository filters** — all queries scope by `tenant_id`
3. **Widget token** — signed token encodes `tenant_id`; never trust request body
4. **pgvector** — always include `WHERE tenant_id = $1` in similarity search

### Key Backend Layers

- `app/models/` — SQLAlchemy ORM (12 tables: tenants, users, cms_pages, content_chunks, widgets, conversations, messages, leads, escalations, guardrail_configs, audit_logs, cost_events)
- `app/repositories/` — data access, always tenant-scoped
- `app/services/` — business logic; key services: RouterService, AgentService, WidgetTokenService, GuardrailService, EmbeddingService, RagService
- `app/api/routes/` — FastAPI routes; public widget routes are `/public/*`
- `app/core/` — config (pydantic-settings), logging (structlog), security, redaction, errors
- `app/db/rls.py` — set/reset `app.tenant_id` context helpers
- `app/prompts/` — LLM prompt templates (`.md` files)

### Redis Memory Key Format

```
memory:{tenant_id}:{conversation_id}  # TTL: 24h
```

### Widget Token Flow

1. Browser loads `<script src=".../widget.js" data-widget-id="pub_wid_abc123">`
2. Loader → `POST /public/widgets/session` (public_widget_id + origin)
3. API validates origin against tenant `allowed_origins[]`
4. API returns signed short-lived JWT/HMAC token
5. All chat requests include that token — never raw `tenant_id`

---

## CI Gates (must all pass)

```yaml
classifier:    macro_f1_min: 0.75
rag:           hit_at_5_min: 0.70, faithfulness_min: 0.80
agent:         tool_selection_accuracy_min: 0.80
security:      red_team_pass_rate: 1.0, redaction_pass_rate: 1.0
```

---

## Team File Ownership

Coordinate before editing shared files: `backend/app/main.py`, `backend/app/api/router.py`, `docker-compose.yml`, `.env.example`.

- **Person A** (`feature/platform-tenancy`) — DB models, migrations, RLS, auth, tenant provisioning, rate limiting, cost tracking, admin Streamlit, CI
- **Person B** (`feature/rag-agent-widget`) — CMS, embeddings, pgvector retrieval, RouterService, AgentService, Redis memory, widget runtime, signed token flow
- **Person C** (`feature/ml-guardrails-evals`) — classifier training, model_server, guardrails sidecar, redaction, red-team evals, eval scripts

Shared contracts are defined in `docs/SPEC.md`. Rebase from main daily.

---

## Implementation Order

Docker Compose → health endpoint → DB connection → Alembic → tenant model → RLS proof → auth → CMS → embeddings → model_server → router → agent tools → guardrails → widget token → public chat → leads/escalations → cost tracking → erasure → evals → CI gates → docs/demo.

<!-- SPECKIT START -->
For additional context about technologies to be used, project structure,
shell commands, and other important information, read the current plan
at `specs/015-tenant-erasure/plan.md`.
<!-- SPECKIT END -->
