# Research: Platform Foundation

**Date**: 2026-05-26
**Status**: Retrospective — documents decisions already implemented, not hypotheses under evaluation.

---

## Decision 1: Async Engine as a Module-Level Singleton (not DI-injected class)

**Decision**: The async engine and session factory are module-level globals in `db/session.py`, initialised lazily on first call and warmed up explicitly in the FastAPI lifespan.

**Rationale**: SQLAlchemy's `create_async_engine` is expensive and connection-pool-bearing. Creating one per request would exhaust connections and add latency. A module-level singleton avoids the overhead of a DI-managed class while still allowing `close_engine()` to be called cleanly in the lifespan shutdown hook.

**Alternatives considered**:
- Dependency-injected engine object (more testable in isolation, but adds boilerplate for a resource that is effectively global anyway)
- Per-request engine (rejected: destroys connection pooling)

---

## Decision 2: structlog with stdlib Bridge (not native structlog only)

**Decision**: `configure_logging` wires structlog through the stdlib logging bridge (`ProcessorFormatter`), not purely via `structlog.get_logger` with `PrintLoggerFactory`.

**Rationale**: Third-party libraries (SQLAlchemy, uvicorn, httpx) emit logs via `logging.getLogger`. Bridging through stdlib means those logs also pick up structlog processors (log level, timestamp, context vars) and appear in the same JSON stream in production. Without the bridge, library logs would be unformatted plain text mixed with structured JSON.

**Alternatives considered**:
- Native structlog only (rejected: library logs would bypass processors)
- Standard `logging` with JSON formatter (rejected: loses structlog's contextvar-merge capability)

---

## Decision 3: pydantic-settings with `extra="forbid"`

**Decision**: `Settings` uses `extra="forbid"` — any unknown environment variable causes a validation error at startup.

**Rationale**: Silent misconfiguration (a misspelled env var that is simply ignored) is a frequent production failure mode. Failing loudly at startup is always preferable to silently running with a wrong value. The tradeoff is that adding a new config key requires a code change, which is acceptable and desirable.

**Alternatives considered**:
- `extra="ignore"` (rejected: masks typos and accidental env var pollution)
- `extra="allow"` (rejected: turns the config object into an untyped grab-bag)

---

## Decision 4: RLS Context via `set_config` with Transaction Scope

**Decision**: `db/rls.py` sets `app.tenant_id` using `set_config(..., true)` (transaction-local scope) and unconditionally resets it in a `finally` block via `get_tenant_db_session`.

**Rationale**: The `true` flag for `set_config` makes the value transaction-local rather than session-local. This means the value is automatically cleared when the transaction ends, providing a second safety net beyond the explicit reset. The explicit reset in `finally` handles the case where a connection is reused without an explicit transaction boundary (pooled idle connections).

**Alternatives considered**:
- Session-local `set_config` (rejected: connection reuse in pool would leak tenant_id to next request)
- Middleware-level reset (rejected: middleware doesn't have access to the DB session; the `finally` in the dependency is the right place)

---

## Decision 5: ConsoleRenderer in Local, JSONRenderer Elsewhere

**Decision**: Log format switches on `APP_ENV == "local"`.

**Rationale**: Human-readable console output speeds up local debugging; JSON output is required by log aggregators (Datadog, CloudWatch, Grafana Loki) in all other environments. The switch is a single conditional in `configure_logging` and requires no other code changes.

**Alternatives considered**:
- Always JSON (rejected: poor developer experience locally)
- Always console (rejected: unparseable by log aggregators)

---

## Decision 6: Health vs Readiness Endpoint Split

**Decision**: `/health` is a pure liveness check (no DB); `/ready` probes the database with `SELECT 1`.

**Rationale**: Container orchestrators (Kubernetes, Docker Compose) distinguish liveness (is the process alive?) from readiness (can it serve traffic?). Mixing the two causes a DB outage to kill a healthy process via liveness restart. The split allows orchestrators to route traffic away from an unready instance without restarting it.

**Alternatives considered**:
- Single `/health` endpoint that probes DB (rejected: fails liveness during DB restarts, causing unnecessary container restarts)

---

## Gap Analysis: Why Redis is Not a Singleton Yet

**Current state**: Services that need Redis (e.g., `MemoryService`, `RateLimitService`) construct a `redis.asyncio.Redis` client inline using `settings.REDIS_URL`.

**Problem**: Each construction opens a new connection or connection pool. Under concurrent requests, this can exhaust the Redis server's connection limit and add per-request latency.

**Required fix**: In `main.py` lifespan, call `redis.asyncio.from_url(settings.REDIS_URL)` once, store in `app.state.redis`, and expose via a `get_redis()` FastAPI dependency. Services receive the client via `Depends(get_redis)`.

---

## Gap Analysis: Why request_id Middleware is Missing

**Current state**: `configure_logging` sets up structlog with `merge_contextvars`, but no middleware calls `structlog.contextvars.bind_contextvars(request_id=..., trace_id=...)`. Log lines lack request correlation.

**Problem**: Without `request_id` and `trace_id`, it is impossible to correlate all log lines belonging to a single request — debugging multi-step flows (widget chat → guardrails → RAG → LLM) requires manual log scanning.

**Required fix**: Add `RequestIDMiddleware` (Starlette `BaseHTTPMiddleware`) that:
1. Reads `X-Request-ID` header or generates a UUID.
2. Reads `X-Trace-ID` header or generates a UUID.
3. Calls `structlog.contextvars.bind_contextvars(request_id=..., trace_id=...)`.
4. Calls `structlog.contextvars.clear_contextvars()` after the response.

---

## Gap Analysis: Why the CI Smoke Test Job is Missing

**Current state**: CI runs lint + format + pytest only. There is no job that verifies `docker compose up` succeeds and the API responds to `GET /health`.

**Problem**: A broken `Dockerfile`, missing `ENV` var, or misconfigured `docker-compose.yml` would not be caught by unit tests. The smoke test is the only gate that validates the full stack startup.

**Required fix**: Add a CI job (after `lint-and-test`) that:
1. Copies `.env.example` to `.env`.
2. Runs `docker compose up -d --build`.
3. Waits for `api` to be healthy (`docker compose ps` or polling).
4. Calls `curl -f http://localhost:8000/health`.
5. Runs `docker compose down -v`.
