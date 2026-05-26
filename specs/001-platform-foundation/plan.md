# Implementation Plan: Platform Foundation

**Branch**: `main` | **Date**: 2026-05-26 | **Spec**: [spec.md](./spec.md)

**Note**: This is a **retrospective plan** — the feature is already merged to `main`. The plan documents decisions already made and tracks three follow-up gaps that are not yet implemented.

---

## Summary

The Platform Foundation establishes the runnable skeleton for the entire Concierge SaaS platform: a Docker Compose stack with nine services, a FastAPI application with async lifespan management, pydantic-settings configuration, structlog structured logging, SQLAlchemy 2.x async database sessions, health/readiness endpoints, centralised domain error handling, and an Alembic migration baseline. Three gaps remain open: Redis is not a lifespan singleton, per-request `request_id`/`trace_id` middleware is missing, and CI has no smoke-test job.

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
| III. Security by Default | ✅ Partial | `WIDGET_TOKEN_SECRET` and `SERVICE_AUTH_SECRET` loaded via config. Vault dynamic resolution is a known gap (static token only). |
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
│   ├── main.py                    # FastAPI app, lifespan, error handlers, router
│   ├── dependencies.py            # Shared FastAPI Depends() helpers
│   ├── api/
│   │   ├── router.py              # Central APIRouter aggregation
│   │   └── routes/
│   │       ├── health.py          # GET /health, GET /ready
│   │       └── [stub routes]      # auth, tenants, widgets, cms, chat, leads…
│   ├── core/
│   │   ├── config.py              # pydantic-settings singleton (get_settings)
│   │   ├── errors.py              # Domain exceptions + FastAPI handlers
│   │   ├── logging.py             # structlog configure_logging + get_logger
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
├── tests/
└── pyproject.toml                 # uv-managed deps; ruff + black config

docker-compose.yml                 # 9-service stack with healthchecks
.env.example                       # All required env vars documented
.github/
└── workflows/
    └── ci.yml                     # ruff + black + pytest; eval gates scaffolded (commented)
```

---

## Complexity Tracking

No constitution violations requiring justification. All deviations from the ideal state are tracked as gaps in this plan and the spec.

---

## Open Gaps (Follow-up Required)

The following items are committed as known gaps. They do not block current team feature branches but must be closed before the platform is production-ready.

| Gap | Spec Ref | Owner | Notes |
|-----|----------|-------|-------|
| Redis client is per-service-call, not a lifespan singleton | FR-006 | Person A | Add `redis.asyncio.from_url(settings.REDIS_URL)` to lifespan; expose via `Depends(get_redis)` |
| No `request_id`/`trace_id` middleware | FR-012, SC-004 | Person A | Add `RequestIDMiddleware` that calls `structlog.contextvars.bind_contextvars(request_id=..., trace_id=...)` at request start and `clear_contextvars()` at end |
| CI has no smoke-test job | FR-022 | Person A | Add a job that runs `docker compose up -d`, waits for healthy, hits `GET /health`, tears down |
| MinIO client not initialised | FR-006 | Person B | Create `core/storage.py` singleton when CMS feature lands |
| Vault dynamic secrets | FR-009 | Person A | Deferred; static token acceptable for local/dev envs |
| Eval CI gates commented out | FR-023 | Person C | Uncomment after `scripts/run_evals.sh` is implemented |
| No `.dockerignore` at repo root | — | Any | Minor: only affects build context size, not correctness |
