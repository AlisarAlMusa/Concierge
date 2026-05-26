# Concierge Week 8 Project Plan — CLAUDE.md / plan.md

## Purpose
This file is the implementation plan for **Concierge**, a secure multi-tenant AI SaaS where businesses sign up as tenants, manage their own CMS content, configure an embeddable AI concierge widget, and allow public visitors to chat with an agent that can answer from tenant content, capture leads, and escalate to humans.

This file is intended to be used by Claude / AI coding agents as the main project guide. Follow it strictly.

---

## 1. Product Summary
Concierge is not just a chatbot. It is a multi-tenant AI SaaS platform.

Each tenant/business gets:
- Its own isolated tenant space.
- CMS content used by both the public website and the AI agent.
- Agent configuration: persona, greeting, enabled tools, guardrails, widget theme.
- Leads and escalation records.
- An embeddable public chat widget.

The central engineering problem is **tenant isolation**:
- Tenant A must never access Tenant B data.
- Tenant A visitor must never retrieve Tenant B chunks from pgvector.
- Tenant Manager can create/suspend/delete tenants but must not read tenant private content.
- Widget requests must derive tenant identity from a signed token, never from request body.

---

## 2. Non-Negotiable Rules
1. Every tenant-owned table must include `tenant_id`.
2. Every repository query must scope by `tenant_id`.
3. Postgres Row-Level Security must enforce tenant isolation.
4. pgvector retrieval must filter by `tenant_id`.
5. Widget API must use a signed short-lived token.
6. CORS is not authentication.
7. Platform guardrails are mandatory and tenant admins cannot weaken them.
8. Service-to-service calls must use credentials from Vault.
9. No torch or transformers in production containers.
10. Classifier is trained offline and served lean through model-server.
11. Every external API call must have timeout, retry, and structured error handling.
12. Logs, traces, Redis memory, and eval outputs must redact secrets/PII.
13. CI must include lint, tests, image build, smoke test, eval gates, red-team gates.

---

## 3. Recommended Tech Stack
### Backend
- FastAPI
- Pydantic v2
- SQLAlchemy 2.x async
- Alembic
- asyncpg
- httpx.AsyncClient
- structlog
- pydantic-settings
- tenacity
- fastapi-users for auth

### Database / Storage
- Postgres 16
- pgvector extension
- Redis
- MinIO
- Vault

### AI / ML
- Hosted LLM API
- Hosted embedding API
- scikit-learn classical classifier
- optional DL model exported to ONNX
- onnxruntime in model-server
- NeMo Guardrails or lightweight custom guardrails sidecar

### Frontend
- React widget using Vite
- Streamlit admin dashboard

### DevOps
- Docker Compose
- GitHub Actions
- uv for Python dependency management
- ruff
- black
- pytest
- mypy optional but recommended

---

## 4. System Components
### 4.1 API Backend
Main FastAPI service. Owns:
- Auth
- Tenant provisioning
- CMS CRUD
- Chat endpoint
- Widget token exchange
- Lead capture
- Escalation records
- Cost attribution
- Rate limiting
- Calls to model-server, guardrails, LLM, embeddings

### 4.2 Admin App
Streamlit app for tenant admins and platform manager:
- Tenant Manager creates tenants and invites first admin.
- Tenant admin manages CMS content.
- Tenant admin configures persona, greeting, tools, guardrails, widget theme.
- Tenant admin views leads and escalations.
- Tenant admin copies embed snippet.

### 4.3 Public Widget
React widget:
- Loaded through `/widget.js`.
- Host page includes one script tag.
- Loader exchanges `widget_id + origin` for signed session token.
- Chat requests include signed token.
- Widget never sends raw `tenant_id`.

### 4.4 Model Server
FastAPI microservice:
- Serves intent classifier.
- Loads joblib classical model and/or ONNX DL model.
- Validates model artifact hash against model card.
- No torch, no transformers.

### 4.5 Guardrails Sidecar
Separate FastAPI service:
- Checks input before LLM.
- Checks output before returning to user.
- Blocks injection/jailbreak/cross-tenant attempts.
- Redacts PII/secrets from text before logs/traces.

### 4.6 Background Worker
Optional but recommended:
- Embedding jobs.
- CMS re-indexing.
- Tenant erasure cleanup.
- Eval jobs if needed.

### 4.7 Infrastructure Services
- Postgres + pgvector
- Redis
- MinIO
- Vault

---

## 5. Project Structure
```text
concierge/
├── backend/
│   ├── app/
│   │   ├── api/
│   │   │   ├── routes/
│   │   │   │   ├── auth.py
│   │   │   │   ├── tenants.py
│   │   │   │   ├── cms.py
│   │   │   │   ├── widgets.py
│   │   │   │   ├── chat.py
│   │   │   │   ├── leads.py
│   │   │   │   ├── escalations.py
│   │   │   │   ├── admin_config.py
│   │   │   │   ├── costs.py
│   │   │   │   └── health.py
│   │   │   └── router.py
│   │   ├── core/
│   │   │   ├── config.py
│   │   │   ├── logging.py
│   │   │   ├── security.py
│   │   │   ├── redaction.py
│   │   │   └── errors.py
│   │   ├── db/
│   │   │   ├── session.py
│   │   │   ├── base.py
│   │   │   ├── rls.py
│   │   │   └── migrations/
│   │   ├── models/
│   │   │   ├── tenant.py
│   │   │   ├── user.py
│   │   │   ├── cms.py
│   │   │   ├── chunk.py
│   │   │   ├── widget.py
│   │   │   ├── conversation.py
│   │   │   ├── lead.py
│   │   │   ├── escalation.py
│   │   │   ├── audit_log.py
│   │   │   ├── cost_event.py
│   │   │   └── guardrail_config.py
│   │   ├── schemas/
│   │   │   ├── tenant.py
│   │   │   ├── cms.py
│   │   │   ├── widget.py
│   │   │   ├── chat.py
│   │   │   ├── lead.py
│   │   │   ├── escalation.py
│   │   │   └── common.py
│   │   ├── repositories/
│   │   │   ├── tenant_repository.py
│   │   │   ├── cms_repository.py
│   │   │   ├── chunk_repository.py
│   │   │   ├── widget_repository.py
│   │   │   ├── conversation_repository.py
│   │   │   ├── lead_repository.py
│   │   │   ├── escalation_repository.py
│   │   │   ├── audit_repository.py
│   │   │   └── cost_repository.py
│   │   ├── services/
│   │   │   ├── tenant_service.py
│   │   │   ├── auth_service.py
│   │   │   ├── cms_service.py
│   │   │   ├── embedding_service.py
│   │   │   ├── rag_service.py
│   │   │   ├── router_service.py
│   │   │   ├── agent_service.py
│   │   │   ├── widget_token_service.py
│   │   │   ├── lead_service.py
│   │   │   ├── escalation_service.py
│   │   │   ├── guardrail_service.py
│   │   │   ├── model_client.py
│   │   │   ├── llm_client.py
│   │   │   ├── memory_service.py
│   │   │   ├── rate_limit_service.py
│   │   │   ├── cost_service.py
│   │   │   └── erasure_service.py
│   │   ├── prompts/
│   │   │   ├── system_agent.md
│   │   │   ├── rag_answer.md
│   │   │   └── refusal.md
│   │   ├── dependencies.py
│   │   └── main.py
│   ├── tests/
│   │   ├── test_rls_isolation.py
│   │   ├── test_repositories_tenant_scope.py
│   │   ├── test_widget_token.py
│   │   ├── test_chat_router.py
│   │   ├── test_redaction.py
│   │   └── test_tenant_erasure.py
│   ├── Dockerfile
│   └── pyproject.toml
├── model_server/
│   ├── app/
│   │   ├── main.py
│   │   ├── config.py
│   │   ├── schemas.py
│   │   ├── model_loader.py
│   │   └── predict_service.py
│   ├── artifacts/
│   │   ├── classifier.joblib
│   │   ├── classifier.onnx
│   │   └── model_card.json
│   ├── Dockerfile
│   └── pyproject.toml
├── guardrails_sidecar/
│   ├── app/
│   │   ├── main.py
│   │   ├── config.py
│   │   ├── schemas.py
│   │   ├── rails.py
│   │   └── redaction.py
│   ├── Dockerfile
│   └── pyproject.toml
├── admin_app/
│   ├── app.py
│   ├── pages/
│   │   ├── tenant_manager.py
│   │   ├── cms.py
│   │   ├── agent_config.py
│   │   ├── leads.py
│   │   └── embed_snippet.py
│   ├── Dockerfile
│   └── pyproject.toml
├── widget/
│   ├── src/
│   │   ├── main.tsx
│   │   ├── Widget.tsx
│   │   ├── api.ts
│   │   ├── types.ts
│   │   └── styles.css
│   ├── public/
│   ├── package.json
│   ├── vite.config.ts
│   └── Dockerfile
├── worker/
│   ├── app/
│   │   ├── main.py
│   │   ├── tasks.py
│   │   └── dependencies.py
│   └── Dockerfile
├── evals/
│   ├── eval_thresholds.yaml
│   ├── classifier/
│   │   ├── test_set.csv
│   │   └── evaluate_classifier.py
│   ├── rag/
│   │   ├── golden_set.yaml
│   │   └── evaluate_rag.py
│   ├── agent/
│   │   ├── tool_selection_golden.yaml
│   │   └── evaluate_agent_tools.py
│   └── security/
│       ├── red_team_prompts.yaml
│       └── evaluate_red_team.py
├── docs/
│   ├── DESIGN.md
│   ├── SPEC.md
│   ├── DECISIONS.md
│   ├── RUNBOOK.md
│   ├── EVALS.md
│   └── SECURITY.md
├── scripts/
│   ├── seed_tenants.py
│   ├── seed_cms.py
│   ├── create_vault_secrets.sh
│   ├── run_evals.sh
│   └── smoke_test.sh
├── .github/
│   └── workflows/
│       └── ci.yml
├── docker-compose.yml
├── .env.example
├── README.md
├── CLAUDE.md
└── plan.md
```

---

## 6. Database Tables
### tenants
Fields:
- `id`
- `name`
- `slug`
- `status`: active/suspended/deleting/deleted
- `created_at`
- `updated_at`

### users
Use fastapi-users base fields plus:
- `role`: tenant_manager / tenant_admin / member
- `tenant_id`: nullable for tenant_manager, required for tenant roles

### cms_pages
Fields:
- `id`
- `tenant_id`
- `title`
- `slug`
- `body`
- `status`
- `created_by`
- `created_at`
- `updated_at`

### content_chunks
Fields:
- `id`
- `tenant_id`
- `cms_page_id`
- `chunk_text`
- `embedding vector`
- `metadata jsonb`
- `created_at`

### widgets
Fields:
- `id`
- `tenant_id`
- `public_widget_id`
- `name`
- `theme_json`
- `greeting`
- `allowed_origins text[]`
- `enabled_tools jsonb`
- `created_at`

### conversations
Fields:
- `id`
- `tenant_id`
- `widget_id`
- `visitor_session_id`
- `status`
- `created_at`
- `updated_at`

### messages
Fields:
- `id`
- `tenant_id`
- `conversation_id`
- `role`: visitor/assistant/tool/system
- `content_redacted`
- `metadata jsonb`
- `created_at`

### leads
Fields:
- `id`
- `tenant_id`
- `conversation_id`
- `name`
- `email`
- `phone`
- `intent`
- `lead_score`
- `source`
- `created_at`

### escalations
Fields:
- `id`
- `tenant_id`
- `conversation_id`
- `reason`
- `status`
- `created_at`

### guardrail_configs
Fields:
- `id`
- `tenant_id`
- `persona`
- `allowed_topics`
- `blocked_topics`
- `refusal_tone`
- `enabled_tools`
- `updated_at`

### audit_logs
Fields:
- `id`
- `actor_user_id`
- `actor_role`
- `tenant_id`
- `action`
- `target_type`
- `target_id`
- `metadata jsonb`
- `created_at`

### cost_events
Fields:
- `id`
- `tenant_id`
- `provider`
- `model`
- `operation`: llm/embedding/rerank/classifier
- `input_tokens`
- `output_tokens`
- `estimated_cost_usd`
- `created_at`

---

## 7. RLS Strategy
Enable RLS on every tenant-owned table.

Pattern:
```sql
ALTER TABLE cms_pages ENABLE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation_cms_pages
ON cms_pages
USING (tenant_id = current_setting('app.tenant_id')::uuid);
```

Backend must:
- Set tenant context at request start.
- Reset tenant context at request end.
- Never trust tenant_id from request body.
- For widget requests, derive tenant_id from verified token.
- For authenticated tenant_admin, derive tenant_id from user record.
- For Tenant Manager maintenance/delete path, use narrow write/delete-only operations, not general read bypass.

Important pooled connection rule:
- Always reset `app.tenant_id` after request because pooled DB connections may be reused.

---

## 8. API Routes
### Health
```text
GET /health
GET /ready
```

### Auth
```text
POST /auth/register
POST /auth/login
POST /auth/logout
GET /auth/me
```

### Tenant Manager Routes
Only platform Tenant Manager can call these.
```text
POST /platform/tenants
GET /platform/tenants
GET /platform/tenants/{tenant_id}
POST /platform/tenants/{tenant_id}/invite-admin
POST /platform/tenants/{tenant_id}/suspend
POST /platform/tenants/{tenant_id}/reactivate
DELETE /platform/tenants/{tenant_id}
GET /platform/tenants/{tenant_id}/usage-summary
GET /platform/audit-logs
```

### Tenant Admin Config
```text
GET /tenant/config
PATCH /tenant/config
GET /tenant/usage-summary
```

### CMS
```text
POST /cms/pages
GET /cms/pages
GET /cms/pages/{page_id}
PATCH /cms/pages/{page_id}
DELETE /cms/pages/{page_id}
POST /cms/pages/{page_id}/publish
POST /cms/pages/{page_id}/reindex
POST /cms/reindex-all
```

### Widget Management
```text
POST /widgets
GET /widgets
GET /widgets/{widget_id}
PATCH /widgets/{widget_id}
DELETE /widgets/{widget_id}
GET /widgets/{widget_id}/embed-snippet
```

### Public Widget Runtime
```text
GET /widget.js
POST /public/widgets/session
GET /public/widgets/config
POST /public/chat
```

Important:
- `/public/widgets/session` receives `public_widget_id` and browser origin.
- It validates origin against tenant allowed origins.
- It returns signed short-lived token.
- `/public/chat` requires that token.

### Chat / Conversations
```text
POST /chat
GET /conversations
GET /conversations/{conversation_id}
GET /conversations/{conversation_id}/messages
```

For public visitor use `/public/chat`.  
For authenticated tenant admin testing, use `/chat`.

### Leads
```text
GET /leads
GET /leads/{lead_id}
PATCH /leads/{lead_id}
DELETE /leads/{lead_id}
```

### Escalations
```text
GET /escalations
GET /escalations/{escalation_id}
PATCH /escalations/{escalation_id}
```

### Internal Service Routes
Protected by service credential.
```text
POST /internal/embeddings/reindex-page
POST /internal/usage/cost-event
POST /internal/tenant-erasure/{tenant_id}
```

---

## 9. Model Server Routes
```text
GET /health
POST /predict-intent
POST /predict-lead-score
```

### Request
```json
{
  "tenant_id": "uuid",
  "message": "I want pricing for your service"
}
```

### Response
```json
{
  "label": "sales",
  "confidence": 0.91,
  "model_version": "classifier-v1"
}
```

---

## 10. Guardrails Sidecar Routes
Protected by service credential.
```text
GET /health
POST /guardrails/check-input
POST /guardrails/check-output
POST /guardrails/redact
```

### Input Check Response
```json
{
  "allowed": true,
  "reason": null,
  "redacted_text": "cleaned text"
}
```

### Blocked Response
```json
{
  "allowed": false,
  "reason": "prompt_injection_attempt",
  "safe_reply": "I can’t help with that request."
}
```

---

## 11. Service Responsibilities
### TenantService
- Create tenant.
- Suspend/reactivate tenant.
- Invite first admin.
- Trigger erasure.
- Write audit logs.

### CMSService
- Create/update/delete CMS pages.
- Validate content.
- Trigger chunking and embedding.
- Publish content.

### EmbeddingService
- Chunk CMS content.
- Call hosted embedding API.
- Store embeddings in pgvector with tenant_id.
- Log embedding cost.

### RagService
- Retrieve chunks by query embedding.
- Always filter by tenant_id.
- Optional improvement: metadata filter or rerank.
- Return context chunks to agent/answer service.

### RouterService
- Calls model-server classifier.
- Applies confidence threshold.
- Routes:
  - spam → drop
  - faq/support → RAG answer
  - sales/contact → capture lead
  - human request → escalate
  - ambiguous/low confidence → agent

### AgentService
- Bounded tool-calling LLM.
- Tools:
  - `rag_search`
  - `capture_lead`
  - `escalate`
- Max tool iterations: 3.
- Max tokens per turn.
- Uses Redis short-term memory.
- Uses guardrails before and after LLM.

### LeadService
- Validates lead payload.
- Writes lead row scoped to tenant_id.
- Rate-limits unauthenticated lead capture per visitor session.
- Optional lead scoring.

### EscalationService
- Creates escalation row.
- Updates conversation status.
- Optionally notifies tenant admin.

### WidgetTokenService
- Validates public widget id.
- Validates request origin.
- Issues short-lived signed JWT/HMAC token.
- Verifies token on chat request.
- Derives tenant_id and widget_id from token.

### GuardrailService
- Calls guardrails sidecar.
- Blocks unsafe input.
- Blocks unsafe output.
- Redacts logs/traces.

### ModelClient
- Calls model-server.
- Uses service credential from Vault.
- Has timeout/retry/backoff.

### LLMClient
- Calls hosted LLM API.
- Logs cost event.
- Has timeout/retry/backoff.
- Never logs raw PII/secrets.

### MemoryService
- Stores short-term session messages in Redis.
- Key format:
  - `memory:{tenant_id}:{conversation_id}`
- TTL recommendation:
  - 24 hours for demo.
  - Explain privacy/cost tradeoff in DESIGN.md.

### RateLimitService
- Per-tenant limits.
- Per-widget limits.
- Per-visitor/session limits.
- Protects lead capture and chat from spam.

### CostService
- Records LLM, embedding, rerank, classifier usage.
- Supports per-tenant dashboard.

### ErasureService
Deletes:
- tenant rows
- CMS pages
- content chunks / vectors
- conversations
- messages
- leads
- escalations
- Redis session memory
- MinIO blobs
- related audit marker

Do not delete audit logs blindly. Keep minimal compliance audit event without private content.

---

## 12. Docker Setup
### Services in docker-compose
```yaml
services:
  api:
    build: ./backend
    depends_on:
      - postgres
      - redis
      - vault
      - model_server
      - guardrails_sidecar
    ports:
      - "8000:8000"

  model_server:
    build: ./model_server
    ports:
      - "8001:8001"

  guardrails_sidecar:
    build: ./guardrails_sidecar
    ports:
      - "8002:8002"

  admin_app:
    build: ./admin_app
    depends_on:
      - api
    ports:
      - "8501:8501"

  widget:
    build: ./widget

  worker:
    build: ./worker
    depends_on:
      - postgres
      - redis
      - api

  postgres:
    image: pgvector/pgvector:pg16
    environment:
      POSTGRES_DB: concierge
      POSTGRES_USER: concierge
      POSTGRES_PASSWORD: concierge
    ports:
      - "5432:5432"
    volumes:
      - postgres_data:/var/lib/postgresql/data

  redis:
    image: redis:7
    ports:
      - "6379:6379"

  minio:
    image: minio/minio
    command: server /data --console-address ":9001"
    ports:
      - "9000:9000"
      - "9001:9001"

  vault:
    image: hashicorp/vault:latest
    ports:
      - "8200:8200"
    cap_add:
      - IPC_LOCK

volumes:
  postgres_data:
  minio_data:
```

### Docker Rules
- Use one `.dockerignore` at repo root and service-specific `.dockerignore` if needed.
- Never copy `.env` into image.
- Containers read env vars at runtime through `env_file` or Compose environment.
- No torch/transformers in production images.
- Use uv for Python installs.
- Use separate dependency groups per service to avoid installing ML libraries everywhere.

---

## 13. Setup Requirements
### Local Setup
```bash
cp .env.example .env
docker compose up --build
```

### Backend Setup
```bash
cd backend
uv sync
uv run alembic upgrade head
uv run uvicorn app.main:app --reload
```

### Model Training
Training is offline in notebook/Colab:
1. Pick small public labeled text classification dataset.
2. Train:
   - TF-IDF + Logistic Regression
   - Small DL model
   - LLM zero-shot baseline
3. Evaluate macro-F1, latency, cost.
4. Export:
   - classical model to `classifier.joblib`
   - DL model to `classifier.onnx`
5. Generate `model_card.json` with artifact SHA-256.
6. Commit only small artifact if allowed by project rules.

### Environment Variables
Use `.env.example`:
```env
APP_ENV=local
DATABASE_URL=postgresql+asyncpg://concierge:concierge@postgres:5432/concierge
REDIS_URL=redis://redis:6379/0
VAULT_ADDR=http://vault:8200
VAULT_TOKEN=dev-root-token
MINIO_ENDPOINT=minio:9000
MINIO_ACCESS_KEY=minioadmin
MINIO_SECRET_KEY=minioadmin
LLM_PROVIDER=openai_or_azure
LLM_MODEL=example-model
EMBEDDING_MODEL=text-embedding-3-small
MODEL_SERVER_URL=http://model_server:8001
GUARDRAILS_URL=http://guardrails_sidecar:8002
SERVICE_AUTH_SECRET=from-vault-in-real-flow
WIDGET_TOKEN_SECRET=from-vault-in-real-flow
```

---

## 14. CI/CD Requirements
GitHub Actions must run:
1. Install dependencies.
2. Lint with ruff.
3. Format check with black.
4. Type check if configured.
5. Unit tests.
6. Build Docker images.
7. Compose smoke test.
8. Classifier eval gate.
9. RAG eval gate.
10. Agent tool-selection eval gate.
11. Red-team injection/cross-tenant eval gate.
12. Redaction test.

### eval_thresholds.yaml
```yaml
classifier:
  macro_f1_min: 0.75

rag:
  hit_at_5_min: 0.70
  faithfulness_min: 0.80

agent:
  tool_selection_accuracy_min: 0.80

security:
  red_team_pass_rate: 1.0
  redaction_pass_rate: 1.0
```

---

## 15. Example Flow: User Input to User Output
### Scenario
A visitor opens Tenant A website and asks:

> “Hi, I’m interested in your pricing. Can someone contact me? My email is sara@example.com.”

### Full Flow
1. Browser loads tenant website.
2. Website has:
   ```html
   <script src="https://api.example.com/widget.js" data-widget-id="pub_wid_abc123"></script>
   ```
3. Loader reads `data-widget-id`.
4. Loader calls:
   ```text
   POST /public/widgets/session
   ```
   with:
   - public widget id
   - browser origin
5. API checks:
   - widget exists
   - origin is in tenant allowed origins
6. API returns signed short-lived widget session token.
7. Visitor sends message through widget.
8. Widget calls:
   ```text
   POST /public/chat
   ```
   with token and message.
9. API verifies token.
10. API derives:
   - `tenant_id`
   - `widget_id`
   - `visitor_session_id`
11. API sets Postgres RLS context:
   ```sql
   SELECT set_config('app.tenant_id', '<tenant-a-id>', true);
   ```
12. Input is sent to guardrails sidecar:
   ```text
   POST /guardrails/check-input
   ```
13. Guardrails allows input and redacts sensitive text for logs.
14. API stores redacted user message in `messages`.
15. RouterService calls model-server:
   ```text
   POST /predict-intent
   ```
16. Model-server returns:
   ```json
   {
     "label": "sales",
     "confidence": 0.91
   }
   ```
17. Router sees high-confidence sales/contact intent.
18. Router directly calls LeadService, not full agent.
19. LeadService extracts:
   - intent = pricing/contact
   - email = sara@example.com
20. LeadService validates schema.
21. LeadService rate-limits visitor session.
22. LeadService writes lead to `leads` with Tenant A tenant_id.
23. API generates safe reply:
   > “Thanks! I captured your request. Our team will contact you soon about pricing.”
24. Output is checked by guardrails sidecar.
25. API stores assistant reply.
26. Widget displays reply to visitor.
27. CostService records classifier and optional LLM cost.
28. Postgres tenant context is reset after request.
29. Tenant A admin sees the new lead in Streamlit.
30. Tenant B can never see this lead because:
   - RLS blocks it.
   - repository filters block it.
   - widget token belongs to Tenant A.
   - pgvector retrieval is tenant-filtered.

---

## 16. Recommended Team Split — Team of 3
Goal: avoid conflicts by giving each member clear ownership and separate files.

### Branch Naming
- Person A: `feature/platform-tenancy`
- Person B: `feature/rag-agent-widget`
- Person C: `feature/ml-guardrails-evals`

### PR Rule
- Each PR must be reviewed by one teammate.
- Do not edit another teammate’s files without asking.
- Shared interfaces must be agreed in `schemas/` and `docs/SPEC.md`.

---

## 17. Person A — Platform, Tenancy, Admin, CI
### Owns
- Backend skeleton
- Docker Compose
- Database models
- Alembic migrations
- RLS
- Auth
- Roles
- Tenant provisioning
- Admin Streamlit basic pages
- CI skeleton

### Main Files
```text
backend/app/models/tenant.py
backend/app/models/user.py
backend/app/models/audit_log.py
backend/app/models/cost_event.py
backend/app/db/*
backend/app/api/routes/auth.py
backend/app/api/routes/tenants.py
backend/app/api/routes/costs.py
backend/app/repositories/tenant_repository.py
backend/app/repositories/audit_repository.py
backend/app/services/tenant_service.py
backend/app/services/rate_limit_service.py
backend/app/services/cost_service.py
admin_app/*
.github/workflows/ci.yml
docker-compose.yml
.env.example
```

### Exact Tasks
#### Day 1
- Create repo skeleton.
- Create Docker Compose with api, postgres, redis, vault, minio.
- Create backend FastAPI app.
- Add settings using pydantic-settings.
- Add structured logging.
- Add SQLAlchemy async session.
- Add Alembic.
- Create tenant and user models.
- Add RLS helper.
- Seed two tenants.
- Create CI skeleton with lint/test placeholder.

#### Day 2
- Add fastapi-users auth.
- Add roles:
  - tenant_manager
  - tenant_admin
  - member
- Add tenant provisioning endpoint.
- Add invite first admin endpoint.
- Add audit log repository/service.
- Add RLS policies for first tables.

#### Day 3
- Add rate limiting service.
- Add cost events table/service.
- Add tenant usage summary endpoint.
- Help Person B verify tenant-filtered queries.
- Add isolation tests.

#### Day 4
- Add tenant erasure route.
- Add admin Streamlit pages:
  - login placeholder
  - tenant config
  - CMS list integration placeholder
  - leads page integration placeholder
- Finish CI smoke test.

#### Day 5
- Final docs:
  - DESIGN.md isolation section
  - RUNBOOK.md setup section
- Demo responsibility:
  - Show RLS isolation.
  - Show Tenant Manager cannot read tenant content.
  - Show tenant creation and admin configuration.

---

## 18. Person B — RAG, Router, Agent, Widget
### Owns
- CMS routes/services
- Embedding service
- pgvector retrieval
- Router service
- Agent service
- Redis memory
- Widget runtime
- Signed widget session flow

### Main Files
```text
backend/app/models/cms.py
backend/app/models/chunk.py
backend/app/models/widget.py
backend/app/models/conversation.py
backend/app/models/lead.py
backend/app/models/escalation.py
backend/app/api/routes/cms.py
backend/app/api/routes/widgets.py
backend/app/api/routes/chat.py
backend/app/repositories/cms_repository.py
backend/app/repositories/chunk_repository.py
backend/app/repositories/widget_repository.py
backend/app/repositories/conversation_repository.py
backend/app/services/cms_service.py
backend/app/services/embedding_service.py
backend/app/services/rag_service.py
backend/app/services/router_service.py
backend/app/services/agent_service.py
backend/app/services/widget_token_service.py
backend/app/services/memory_service.py
backend/app/prompts/*
widget/*
```

### Exact Tasks
#### Day 1
- Define CMS schema.
- Define widget schema.
- Define tool contracts:
  - rag_search
  - capture_lead
  - escalate
- Create prompt files.
- Create hello-world widget.

#### Day 2
- Implement CMS CRUD.
- Implement chunking.
- Implement hosted embedding API client.
- Store chunks in pgvector with tenant_id.
- Implement tenant-filtered retrieval.
- Create small RAG golden set.

#### Day 3
- Implement RouterService.
- Integrate model-server client from Person C.
- Implement direct workflow cases:
  - spam
  - FAQ/RAG
  - sales/lead
  - human/escalate
- Implement bounded AgentService for ambiguous cases.
- Implement Redis memory TTL.

#### Day 4
- Implement `/widget.js`.
- Implement widget session token exchange.
- Implement origin allowlist check.
- Implement `/public/chat`.
- Integrate widget with chat API.

#### Day 5
- Final docs:
  - DECISIONS.md agent-vs-workflow section
  - EVALS.md RAG section
- Demo responsibility:
  - Show widget loads on allowed origin.
  - Show chat answer from tenant CMS.
  - Show agent captures lead and escalates.

---

## 19. Person C — Classifier, Guardrails, Security, Evals
### Owns
- Offline classifier training
- Model-server
- Guardrails sidecar
- Service-to-service auth
- Redaction
- Red-team tests
- Eval gates

### Main Files
```text
model_server/*
guardrails_sidecar/*
backend/app/services/model_client.py
backend/app/services/guardrail_service.py
backend/app/core/redaction.py
backend/app/core/security.py
evals/*
scripts/run_evals.sh
backend/tests/test_redaction.py
backend/tests/test_chat_router.py
backend/tests/test_widget_token.py
```

### Exact Tasks
#### Day 1
- Create model-server FastAPI shell.
- Create guardrails sidecar shell.
- Create service credential check.
- Create eval folder structure.
- Create redaction utility.

#### Day 2
- Pick public text classification dataset.
- Train classical TF-IDF + Logistic Regression.
- Train/prepare one small DL model and export ONNX.
- Run LLM zero-shot baseline on small held-out set.
- Compare macro-F1, latency, cost.
- Save model card with SHA-256.

#### Day 3
- Implement `/predict-intent`.
- Implement artifact hash verification.
- Implement guardrail input checks:
  - prompt injection keywords/patterns
  - cross-tenant attempts
  - system prompt extraction attempts
- Implement redaction:
  - emails
  - phone numbers
  - API-key-like strings
  - bearer tokens

#### Day 4
- Implement eval scripts:
  - classifier eval
  - agent tool-selection eval
  - RAG eval
  - red-team eval
  - redaction eval
- Wire eval scripts into CI.
- Add fake malicious prompts.

#### Day 5
- Final docs:
  - SECURITY.md
  - model card
  - EVALS.md classifier/security sections
- Demo responsibility:
  - Show red-team prompt refused.
  - Show fake API key redacted.
  - Show model-server returns intent.
  - Show CI gate results.

---

## 20. Merge Conflict Avoidance Rules
1. Person A owns infrastructure and DB foundation.
2. Person B owns product behavior and widget.
3. Person C owns ML/security/evals.
4. Shared contracts go into `docs/SPEC.md` first.
5. Before editing shared files:
   - `backend/app/main.py`
   - `backend/app/api/router.py`
   - `docker-compose.yml`
   - `.env.example`
   ask in team chat.
6. Prefer small PRs.
7. Rebase from main daily.
8. Never commit `.env`, secrets, local DB files, or large model training outputs.
9. Use `.env.example` only.
10. Keep production Docker images lean.

---

## 21. Implementation Order
Follow this exact order:
1. Docker Compose foundation.
2. Backend health endpoint.
3. Database connection.
4. Alembic baseline.
5. Tenant model.
6. RLS policy proof.
7. Auth and roles.
8. CMS CRUD.
9. Embedding and pgvector.
10. Model-server classifier.
11. Router workflow.
12. Agent tools.
13. Guardrails sidecar.
14. Widget token flow.
15. Public widget chat.
16. Leads and escalations.
17. Cost tracking.
18. Tenant erasure.
19. Evals.
20. CI gates.
21. Docs and demo polish.

---

## 22. Minimum Demo Script
Demo must show:
1. Two tenants seeded.
2. Tenant A CMS content.
3. Tenant B CMS content.
4. Widget for Tenant A.
5. Tenant A visitor asks a question.
6. Answer uses Tenant A content.
7. Cross-tenant injection attempt is refused.
8. Lead capture creates Tenant A lead.
9. Tenant B admin cannot see Tenant A lead.
10. CI red-team test passes.

---

## 23. Claude / AI Agent Working Instructions
When using Claude or any coding agent:
1. Do not let it invent architecture outside this file.
2. Ask it to implement one component at a time.
3. Always request tests with each component.
4. Always ask it to preserve tenant isolation.
5. Ask it to explain which files it changed.
6. Review generated code manually.
7. Reject code that:
   - trusts `tenant_id` from request body
   - skips RLS
   - logs raw secrets
   - puts torch in production Docker
   - creates unbounded agent loops
   - allows tenant config to weaken platform rails

Recommended prompt style:
```text
Implement only [component].
Follow CLAUDE.md exactly.
Do not edit unrelated files.
Add tests.
Use async FastAPI and SQLAlchemy 2.x.
Preserve tenant isolation.
Explain all changed files.
```

---

## 24. Final Deliverables
Required files:
- `README.md`
- `CLAUDE.md`
- `plan.md`
- `docs/DESIGN.md`
- `docs/SPEC.md`
- `docs/DECISIONS.md`
- `docs/RUNBOOK.md`
- `docs/EVALS.md`
- `docs/SECURITY.md`
- `evals/eval_thresholds.yaml`
- `.env.example`
- `docker-compose.yml`
- GitHub Actions workflow
- model card
- seeded demo data

Final tag:
```bash
git tag v0.1.0-week8
git push origin v0.1.0-week8
```

---

## 25. Success Criteria
The project is successful if:
- Fresh clone works with Docker Compose.
- Two tenants are seeded.
- Tenant isolation is enforced by RLS and repository filters.
- Widget uses signed token and server-side origin check.
- RAG retrieves only same-tenant content.
- Router keeps easy cases off the agent.
- Agent can call tools but is bounded.
- Lead capture and escalation work.
- Guardrails block injection and cross-tenant attempts.
- Redaction test passes.
- CI gates are real.
- Team can explain every architectural decision.
