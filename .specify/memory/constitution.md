<!--
Sync Impact Report
==================
Version change: 0.0.0 (template) → 1.0.0
Added principles: I–VI (all new)
Added sections: Architecture & Technology Rules, Development Workflow & Quality Gates, Governance
Templates requiring updates:
  ✅ .specify/memory/constitution.md (this file)
  ⚠  .specify/templates/plan-template.md (update Constitution Check references to match principle names)
  ⚠  .specify/templates/spec-template.md (add Tenant Isolation and Security sections as mandatory)
  ⚠  .specify/templates/tasks-template.md (add RLS verification, eval-gate, and redaction task types)
Follow-up TODOs: none — all placeholders resolved.
-->

# Concierge Constitution

## Core Principles

### I. Tenant Isolation (NON-NEGOTIABLE)

Every tenant-owned table MUST carry a `tenant_id` column. Every repository query MUST scope by
`tenant_id` — RLS is the database-level safety net, not the primary filter. Both layers MUST be active:

- Enable PostgreSQL Row-Level Security on every tenant-owned table with a policy using
  `current_setting('app.tenant_id')::uuid`.
- Set `app.tenant_id` at request start via a FastAPI dependency and RESET it unconditionally at request
  end — pooled connections reuse variables and a leftover value is a cross-tenant breach.
- pgvector similarity search MUST include `WHERE tenant_id = $1` — the most common real-world leak
  is a vector search that forgot the tenant filter, not a database query.
- Widget requests MUST derive `tenant_id` from the verified signed token — never from the request body.
  Trusting a client-supplied `tenant_id` is a one-line cross-tenant breach.
- The Tenant Manager role crosses the tenant boundary by design but gets NO RLS bypass on content.
  It can destroy a tenant's data without ever reading it. Every use of the maintenance path is audit-logged.

**Rationale**: Cross-tenant data leakage is the #1 failure mode for multi-tenant AI products. The wall
between tenants is the assignment; everything else is secondary.

### II. Clean Layered Architecture (NON-NEGOTIABLE)

Code MUST be organized into strict vertical layers. Each layer has one job and MUST NOT reach around it:

- **Routes** (`api/routes/`) — HTTP only: parse request, call service, return response. No SQL, no
  business logic, no direct model access.
- **Services** (`services/`) — Business logic only. Call repositories for data, call external clients for
  I/O. Never construct DB sessions or HTTP clients inline.
- **Repositories** (`repositories/`) — All SQL lives here. Every query scopes by `tenant_id`. No
  business logic.
- **Models** (`models/`) — SQLAlchemy ORM definitions only. Separate from Pydantic schemas.
- **Schemas** (`schemas/`) — Pydantic v2 request/response DTOs. Never expose ORM objects directly to
  callers.
- **Core** (`core/`) — Config (pydantic-settings, `extra="forbid"`, no `os.getenv` outside this layer),
  structured logging (structlog), redaction, security helpers, domain exceptions.
- **Prompts** (`prompts/`) — All LLM prompt templates as versioned `.md` files. No prompt strings
  embedded inside service code. Tenant persona is injected at runtime from config — never hardcoded.

Use FastAPI `Depends()` for every shared resource (DB session, authenticated user, Redis, LLM client,
model client). Expensive singletons (DB engine, Redis, HTTP client, LLM client) are initialized once in
FastAPI lifespan — never per-request.

**Rationale**: Mixing concerns is how unscoped queries and raw secret logs get introduced. If every
developer knows exactly which layer owns what, isolation and security violations are visible at a glance.

### III. Security by Default (NON-NEGOTIABLE)

Security rules are mandatory and cannot be weakened by tenant configuration:

- **Widget authentication**: The loader exchanges `public_widget_id` + browser origin for a signed,
  short-lived JWT/HMAC token. All subsequent chat requests carry that token. CORS and
  `Content-Security-Policy: frame-ancestors` are defense-in-depth around the token — never the
  auth boundary. Validate the origin server-side in the request handler and reject mismatches with 403.
- **Guardrails layers**: Platform rails (prompt injection, jailbreak, cross-tenant refusal, PII redaction)
  are mandatory and identical for all tenants. A tenant MUST NOT be able to disable or weaken them.
  Tenant rails (allowed/blocked topics, refusal tone, persona, enabled tools) are configurable per tenant.
- **Service-to-service auth**: API → guardrails sidecar → model-server calls MUST use a service
  credential resolved from Vault. "It's on the internal network" is not authentication.
- **PII redaction**: Emails, phone numbers, API-key-like strings, and bearer tokens MUST be redacted
  before anything leaves the service — logs, traces, Redis memory, MinIO snapshots, error responses.
  A CI test MUST prove a fake API key pasted into chat never appears unredacted anywhere.
- **Secrets from Vault**: No secrets hardcoded or read from environment outside pydantic-settings.
  `WIDGET_TOKEN_SECRET` and `SERVICE_AUTH_SECRET` come from Vault in production paths.
- **Agent loop bound**: The agent MUST cap tool-call iterations (max 3) and tokens per turn. An
  unbounded loop is both a cost failure and a security failure.
- **`capture_lead` write guard**: Schema-validate the payload, rate-limit writes per visitor/session, and
  scope the write strictly to the token's tenant. An injected prompt MUST NOT convert it into a spam
  cannon or write into another tenant's table.
- **Right to erasure**: The delete-tenant path MUST purge Postgres rows, pgvector embeddings, MinIO
  blobs, and Redis sessions. Audit-log the erasure event. Keeping vectors searchable after row deletion
  is a compliance failure.

**Rationale**: Security is not a feature to add at the end. It is the invariant the product is graded on.

### IV. Async All the Way Down

Every I/O operation in a request path MUST be async:

- HTTP: `httpx.AsyncClient` — never `requests`.
- Database: SQLAlchemy 2.x async sessions with `asyncpg`.
- LLM/embedding calls: async SDK methods or `httpx.AsyncClient`.
- Independent I/O calls on the same request: `asyncio.gather` for parallel execution.
- No `time.sleep`, no blocking file I/O, no loading model weights per request.

CPU-heavy work (classifier inference if batched, embedding generation) MUST be offloaded to the
background worker or wrapped in `asyncio.to_thread`.

**Rationale**: A blocking call in a FastAPI async handler blocks the event loop for every concurrent
request on that worker. Under multitenancy, one slow tenant blocks all others.

### V. Lean Containers — No Torch in Production

Production Docker images MUST NOT include `torch` or `transformers`. The classifier is trained offline
(notebook/Colab, ephemeral, never shipped) and served lean:

- DL model exported to ONNX → served via `onnxruntime`.
- Classical model exported to `joblib` → served via `scikit-learn`.
- Model-server image: `onnxruntime + scikit-learn + numpy` only. Target under 500 MB.
- The model-server MUST verify the artifact's SHA-256 against `model_card.json` at boot and refuse
  to start if the hash does not match.
- LLM inference and embeddings are hosted-API calls only — never local weights in any container.

**Rationale**: The 4 GB torch image that breaks docker builds was the training stack leaking into serving.
Train-heavy / serve-light is the production-honest pattern. A fresh `docker compose up` MUST complete
in seconds, not minutes.

### VI. Evals Are the Grade — Every Decision Backed by a Number

CI gates MUST run on every push and block merge on regression. Thresholds are committed in
`evals/eval_thresholds.yaml` and MUST NOT be set to zero or disabled:

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

Every architectural choice in `docs/DESIGN.md` MUST be backed by a measured number on a golden set:
chunking strategy, retrieval improvement, embedding model selection, routing confidence threshold.
"A blog told me to" is not a justification. The three-way classifier comparison (classical ML / DL-ONNX /
LLM zero-shot) MUST be committed alongside the shipped model — the winner MUST NOT silently fall
behind a baseline it once beat.

**Rationale**: CI that does not gate on agent behavior is theater. A polished demo with no working gates
scores below a rougher one whose CI is real.

## Architecture & Technology Rules

**Stack constraints** (non-substitutable without constitution amendment):
- Backend: FastAPI, Pydantic v2, SQLAlchemy 2.x async, Alembic, asyncpg, httpx.AsyncClient,
  structlog, pydantic-settings, tenacity, fastapi-users.
- Database: Postgres 16 + pgvector extension, Redis 7, MinIO, Vault.
- Auth: fastapi-users JWT. Roles: `tenant_manager` / `tenant_admin` / `member`. Do not build a
  configurable permission matrix — three named roles, powers you can count on one hand.
- Python dependency management: `uv` only — no pip, no poetry.
- Linting: ruff. Formatting: black. Both enforced in CI.

**API contract rules**:
- Request bodies and response models MUST use Pydantic schemas — never expose ORM objects.
- Every tool MUST have a typed Pydantic args schema, typed output schema, and a structured error
  result. Tool failures MUST NOT crash the agent — return a structured error so the LLM can recover.
- 401 = unauthenticated. 403 = authenticated but not allowed. Never swap them.
- Domain exceptions (`NotFoundError`, `PermissionDeniedError`, `ToolFailureError`,
  `ExternalServiceError`) MUST be mapped centrally to HTTP responses in `core/errors.py`.

**External call rules**:
- Every external API call (LLM, embeddings, model-server, guardrails sidecar) MUST have: timeout,
  retry with exponential backoff for transient failures (via `tenacity`), no retry on permanent 4xx errors,
  structured error mapping.

**Data rules**:
- No important state lives only in Python memory. Migrations are committed via Alembic. Data survives
  container restarts. Deleting volumes is not a migration strategy.
- Redis session memory key: `memory:{tenant_id}:{conversation_id}`, TTL 24 h. The TTL is not
  arbitrary — it is the privacy/cost tradeoff documented in `docs/DESIGN.md`.

**Logging and tracing**:
- Structured JSON logs only. Fields: event, level, timestamp, service, request_id, trace_id, safe metadata.
  No `print()` in application services.
- Every conversation MUST be traceable. Trace spans: LLM call, tool call, model-server call, RAG
  retrieval, reranking, memory write, errors. The trace ID MUST appear in every log line for that request.

## Development Workflow & Quality Gates

**Spec before code**: A `docs/SPEC.md` for each major component MUST be committed before
implementation starts. The agent's tool contracts, the isolation rules, the role model, and eval thresholds
are written first. Shared contracts between team members are agreed in `docs/SPEC.md` — not in Slack.

**CI must run on every push** (GitHub Actions, `.github/workflows/ci.yml`):
1. `uv run ruff check .`
2. `uv run black --check .`
3. Unit tests: `uv run pytest`
4. Docker image build for each service
5. Compose smoke test (fresh `docker compose up` from clean state)
6. Classifier eval gate (macro-F1 ≥ threshold)
7. RAG golden-set gate (hit@5, faithfulness ≥ thresholds)
8. Agent tool-selection gate (15 examples, accuracy ≥ threshold)
9. Red-team injection/cross-tenant gate (pass rate = 1.0)
10. Redaction test (fake API key never appears unredacted)

**Team file ownership** (coordinate before editing shared files):
- Shared files requiring team approval: `backend/app/main.py`, `backend/app/api/router.py`,
  `docker-compose.yml`, `.env.example`.
- Person A (`feature/platform-tenancy`): DB models, migrations, RLS, auth, tenant provisioning,
  rate limiting, cost tracking, admin Streamlit, CI skeleton.
- Person B (`feature/rag-agent-widget`): CMS, embeddings, pgvector retrieval, RouterService,
  AgentService, Redis memory, widget runtime, signed token flow.
- Person C (`feature/ml-guardrails-evals`): classifier training, model_server, guardrails sidecar,
  redaction, red-team evals, eval scripts.

**Reject code that**:
- Trusts `tenant_id` from request body
- Skips RLS or repository-layer tenant scoping
- Logs raw secrets or unredacted PII
- Puts `torch` or `transformers` in any production container
- Creates unbounded agent loops (no iteration or token cap)
- Allows tenant config to weaken platform guardrails
- Uses `os.getenv` outside `core/config.py`
- Constructs DB sessions or HTTP clients inside route handlers
- Uses `requests` (sync) instead of `httpx.AsyncClient`


## Governance

This constitution supersedes all other project conventions. Any rule conflict is resolved in favor of this
document. Amendments MUST be:
1. Proposed with a rationale and the specific principle or section being changed.
2. Reviewed by at least one teammate before merging.
3. Version-bumped following semantic rules:
   - MAJOR: backward-incompatible removal or redefinition of a principle.
   - MINOR: new principle or section added, or materially expanded guidance.
   - PATCH: clarifications, wording, non-semantic refinements.
4. Propagated to dependent templates (plan-template, spec-template, tasks-template) in the same commit.

All PRs MUST verify compliance with this constitution before merge. The red-team CI gate is the
machine-checkable form of Principle III — if it regresses, the build is broken regardless of other quality.

Runtime development guidance lives in `CLAUDE.md` at the repo root. The full implementation plan
and day-by-day task split live in `docs/PLAN.md`.

**Version**: 1.0.0 | **Ratified**: 2026-05-26 | **Last Amended**: 2026-05-26
