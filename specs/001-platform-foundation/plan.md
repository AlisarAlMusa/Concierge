# Implementation Plan: Platform Foundation

**Branch**: `main` | **Date**: 2026-05-26 | **Spec**: [spec.md](./spec.md)

**Note**: This is a **retrospective plan** — the feature is already merged to `main`. The plan documents decisions already made and tracks three follow-up gaps that are not yet implemented.

---

## Summary

The Platform Foundation establishes the runnable skeleton for the entire Concierge SaaS platform: a Docker Compose stack with nine services, a FastAPI application with async lifespan management, pydantic-settings configuration, structlog structured logging, SQLAlchemy 2.x async database sessions, health/readiness endpoints, centralised domain error handling, and an Alembic migration baseline.

All original gaps have been closed in follow-up commits: Redis is now a lifespan singleton, `RequestIDMiddleware` binds `request_id`/`trace_id` per request, CI has a smoke-test job, and Vault is now fully wired — secrets are seeded automatically from `.env` at stack startup and the app reads all secrets from Vault at runtime with `.env` values as local-dev fallbacks. `docker-compose.yml` contains zero hardcoded credentials. Alembic migrations run automatically on container start via `entrypoint.sh`.

---

## Technical Context

**Language/Version**: Python 3.12 (container), 3.11 pinned in CI via `actions/setup-python`

**Primary Dependencies**: FastAPI, pydantic-settings, SQLAlchemy 2.x async (asyncpg), Alembic, structlog, uvicorn

**Storage**: PostgreSQL 16 + pgvector extension (primary), Redis 7-alpine (cache/session), MinIO (object storage), Vault (secrets)

**Testing**: pytest (via `uv run pytest`); currently `|| true` in CI until real tests exist

**Target Platform**: Linux containers (Docker Compose v2+); backend is Python/FastAPI web service

**Project Type**: Multi-tenant web service (FastAPI backend + multiple sidecar services)

**Performance Goals**: Health endpoint < 50 ms; stack startup < 60 s on clean environment

**Constraints**: No `os.getenv` outside `core/config.py`; no `torch`/`transformers` in any production image; all I/O must be async; shared clients must be lifespan singletons

**Scale/Scope**: Initial week-8 skeleton; 3-person team split across three feature branches

---

## Constitution Check

*Gate evaluated against [constitution.md](../../.specify/memory/constitution.md) v1.0.0*

| Principle | Status | Notes |
|-----------|--------|-------|
| I. Tenant Isolation | ✅ Partial | `db/rls.py` implements `set_tenant_context` / `reset_tenant_context` with guaranteed reset in `finally`. Full RLS policies deferred to tenancy feature. |
| II. Clean Layered Architecture | ✅ Pass | Routes → Services → Repositories → Models → Schemas → Core layers all scaffolded. `extra="forbid"` on config; no `os.getenv` in app code. `Depends()` used for DB session. |
| III. Security by Default | ✅ Pass | All secrets (`jwt_secret`, `service_auth_secret`, `widget_token_secret`, `minio_secret_key`, `openai_api_key`, `anthropic_api_key`, `azure_openai_api_key`) are seeded into Vault via `scripts/vault-init.sh` at stack startup and read back into `app.state.secrets` at app startup. `docker-compose.yml` contains zero hardcoded credentials — all referenced via `${VAR}` from `.env`. |
| IV. Async All the Way Down | ✅ Pass | `create_async_engine`, `AsyncSession`, `async_sessionmaker`, async lifespan, no `time.sleep` or blocking I/O found. |
| V. Lean Containers — No Torch | ✅ Pass | `pyproject.toml` has no torch/transformers. Model server is a separate image. |
| VI. Evals Are the Grade | ⚠ Gap | Eval CI gates are commented out pending Person C's scripts. Scaffold is committed; gates are not yet enforced. |

**Post-design re-check**: No new violations introduced by this feature's design artifacts.

---

## Project Structure

### Documentation (this feature)

```text
specs/001-platform-foundation/
├── plan.md              # This file
├── research.md          # Technical decisions already made
├── data-model.md        # Initial schema — tenants, users, audit_logs, cost_events
├── contracts/
│   └── health-api.md    # Health & readiness endpoint contracts
└── checklists/
    └── requirements.md  # Spec quality checklist
```

### Source Code (repository root)

```text
backend/
├── app/
│   ├── main.py                    # FastAPI app, lifespan (Vault fetch, Redis, DB engine), middleware, router
│   ├── dependencies.py            # Shared Depends() helpers: get_redis, get_secrets, get_session, role guards
│   ├── api/
│   │   ├── router.py              # Central APIRouter aggregation
│   │   └── routes/
│   │       ├── health.py          # GET /health, GET /ready
│   │       └── [stub routes]      # auth, tenants, widgets, cms, chat, leads…
│   ├── core/
│   │   ├── config.py              # pydantic-settings singleton; all env vars declared here
│   │   ├── errors.py              # Domain exceptions + FastAPI handlers
│   │   ├── logging.py             # structlog configure_logging, get_logger, RequestIDMiddleware
│   │   ├── vault.py               # fetch_vault_secrets() — reads secret/concierge via httpx
│   │   ├── redaction.py           # PII redaction (Person C integration point)
│   │   └── security.py            # Auth helpers
│   ├── db/
│   │   ├── session.py             # Async engine + session factory + get_db_session
│   │   ├── rls.py                 # set/reset app.tenant_id, get_tenant_db_session
│   │   ├── base.py                # SQLAlchemy declarative base
│   │   └── migrations/
│   │       ├── env.py             # Alembic async env
│   │       └── versions/
│   │           └── 0001_initial.py  # pgvector, pgcrypto, tenants, users, audit_logs, cost_events
│   ├── models/                    # SQLAlchemy ORM (12 tables — see data-model.md)
│   ├── repositories/              # Tenant-scoped data access layer
│   ├── services/                  # Business logic layer
│   └── schemas/                   # Pydantic v2 request/response DTOs
├── entrypoint.sh                  # Runs `alembic upgrade head` then starts uvicorn
├── tests/
└── pyproject.toml                 # uv-managed deps; ruff + black config

scripts/
└── vault-init.sh                  # Seeds all secrets from .env into Vault at stack startup

docker-compose.yml                 # 9-service stack + vault-init one-shot; zero hardcoded secrets
.env.example                       # All required env vars documented; secrets are placeholders only
.github/
└── workflows/
    └── ci.yml                     # ruff + black + pytest + smoke-test; eval gates scaffolded (commented)
```

---

## Complexity Tracking

No constitution violations requiring justification. All deviations from the ideal state are tracked as gaps in this plan and the spec.

---

## Closed Gaps

| Gap | Spec Ref | Closed by |
|-----|----------|-----------|
| Redis client not a lifespan singleton | FR-006 | `main.py` lifespan calls `aioredis.from_url`; `get_redis` dependency exposes it |
| No `request_id`/`trace_id` middleware | FR-012, SC-004 | `RequestIDMiddleware` in `core/logging.py`; registered in `main.py` |
| CI has no smoke-test job | FR-022 | `smoke-test` job added to `ci.yml`; polls `/health` after `docker compose up -d` |
| No `.dockerignore` files | — | Per-service `.dockerignore` files created; root `.dockerignore` extended |
| Vault dynamic secrets — static token only | FR-009 | `scripts/vault-init.sh` seeds all secrets from `.env` into Vault on every `docker compose up`; `core/vault.py` fetches them at app startup; `app.state.secrets` is the single runtime source for all secrets; `docker-compose.yml` has zero hardcoded credentials |
| Alembic migrations required manual step | FR-019 | `entrypoint.sh` runs `alembic upgrade head` before uvicorn starts |

## Open Gaps (Follow-up Required)

| Gap | Spec Ref | Owner | Notes |
|-----|----------|-------|-------|
| MinIO client not initialised | FR-006 | Person B | Create `core/storage.py` singleton when CMS feature lands |
| Eval CI gates commented out | FR-023 | Person C | Uncomment after `scripts/run_evals.sh` is implemented |
