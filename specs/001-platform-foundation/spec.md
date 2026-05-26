# Feature Specification: Platform Foundation

> **Owner**: Person A — `feature/platform-tenancy` branch

**Feature Branch**: `main` *(already implemented — documentation spec)*

**Created**: 2026-05-26

**Status**: Implemented (gaps documented for follow-up)

**Input**: User description: "Platform Foundation — documents what was built and captures gaps for follow-up work."

---

## User Scenarios & Testing *(mandatory)*

### User Story 1 — Platform Operator Deploys the Full Stack (Priority: P1)

A platform operator runs a single command to bring up the entire Concierge service stack. Every service starts in the correct order, passes its healthcheck, and the API is ready to accept requests before any dependent service attempts to connect.

**Why this priority**: This is the foundational capability — nothing else works until the stack comes up cleanly and deterministically.

**Independent Test**: Run `docker compose up --build`; all services reach `healthy` status; `GET /health` returns `{"status": "ok"}`; `GET /ready` returns 200 after the database accepts connections.

**Acceptance Scenarios**:

1. **Given** a fresh clone with `.env` copied from `.env.example`, **When** `docker compose up --build` is run, **Then** all services (api, model_server, guardrails_sidecar, admin_app, worker, postgres, redis, minio, vault) reach `healthy` or `running` status within a reasonable startup window.
2. **Given** the stack is running, **When** `GET /health` is called, **Then** the response is `{"status": "ok"}` with HTTP 200.
3. **Given** the stack is running and PostgreSQL is healthy, **When** `GET /ready` is called, **Then** the response confirms database connectivity with HTTP 200.
4. **Given** PostgreSQL is not yet healthy, **When** the `api` service starts, **Then** the API waits for the `postgres` and `redis` healthchecks before initialising (enforced by `depends_on` conditions).

---

### User Story 2 — Developer Reads Structured Request Logs (Priority: P2)

A developer debugging an issue can trace a request end-to-end through logs by filtering on a unique request identifier. Every log line emitted during a request includes a consistent `request_id` and `trace_id`.

**Why this priority**: Without per-request correlation IDs, debugging multi-service issues is impractical. This is a prerequisite for any meaningful observability.

**Independent Test**: Send a request to any API endpoint; confirm that every log line produced during that request carries the same `request_id` and `trace_id` in the structured output.

**Acceptance Scenarios**:

1. **Given** the API is running in local mode, **When** any request is processed, **Then** all log lines for that request include `request_id` and `trace_id` fields bound via middleware.
2. **Given** the API is running in a non-local environment, **When** a request is processed, **Then** logs are emitted as structured JSON (not human-readable console output) and include `request_id`, `trace_id`, `log_level`, `logger_name`, and ISO timestamp.
3. **Given** a request completes, **When** the next request arrives, **Then** `request_id` and `trace_id` are reset — they do not carry over from the previous request.

---

### User Story 3 — API Client Receives Structured Error Responses (Priority: P2)

An API client (widget, admin UI, or external integrator) that triggers a known error condition receives a consistent, machine-readable JSON response with a human-readable `detail` field and a stable `code` field it can branch on.

**Why this priority**: Consistent error contracts allow all consumers to handle failures predictably without custom parsing per endpoint.

**Independent Test**: Trigger each domain error type (not found, permission denied, tenant suspended, rate limit, external service failure) and verify the response shape is `{"detail": "...", "code": "..."}` with the correct HTTP status.

**Acceptance Scenarios**:

1. **Given** a request references a resource that does not exist, **When** the handler raises a not-found condition, **Then** the response is HTTP 404 with `{"detail": "...", "code": "not_found"}`.
2. **Given** a request is made by an actor without permission, **When** the handler raises a permission-denied condition, **Then** the response is HTTP 403 with `{"detail": "...", "code": "permission_denied"}`.
3. **Given** a tenant's account is suspended, **When** any request for that tenant is processed, **Then** the response is HTTP 403 with `{"detail": "...", "code": "tenant_suspended"}`.
4. **Given** a caller exceeds the allowed request rate, **When** the handler raises a rate-limit condition, **Then** the response is HTTP 429 with `{"detail": "...", "code": "rate_limit"}`.
5. **Given** an upstream dependency (model server, guardrails sidecar) is unavailable, **When** the handler raises an external-service condition, **Then** the response is HTTP 503 with `{"detail": "...", "code": "external_service_error"}`.

---

### User Story 4 — Developer Runs Linting, Formatting, and Tests in CI (Priority: P3)

A developer pushing a commit gets automated feedback on code style and test correctness within the CI pipeline before any review begins.

**Why this priority**: Enforces a quality baseline across all contributors with no manual overhead.

**Independent Test**: Push a commit with a style violation; CI fails the `ruff` or `black` step and reports the violation. Push a clean commit; CI passes lint, format, and test steps.

**Acceptance Scenarios**:

1. **Given** a pushed commit contains a linting violation, **When** CI runs, **Then** the `ruff check` step fails with a clear report of the violation.
2. **Given** a pushed commit contains unformatted code, **When** CI runs, **Then** the `black --check` step fails.
3. **Given** a pushed commit is clean, **When** CI runs `pytest`, **Then** all existing tests pass.

---

### Edge Cases

- What happens when the database container restarts while the API is running? The `pool_pre_ping=True` setting on the async engine should detect dead connections and reconnect transparently.
- What happens when an environment variable required by the config is missing? The pydantic-settings `extra="forbid"` model raises a validation error at startup, preventing a misconfigured service from starting silently.
- What happens when `GET /ready` is called before the database connection pool is initialised? The endpoint should return a non-200 status indicating the service is not yet ready.
- What happens when secrets are loaded in a non-local environment with a static `VAULT_TOKEN`? Currently the system falls back to the static token — this is a known gap (see Gaps section).

---

## Requirements *(mandatory)*

### Functional Requirements

**Infrastructure**

- **FR-001**: The system MUST provide a single-command startup (`docker compose up`) that brings up all services with healthchecks.
- **FR-002**: The `api` service MUST NOT start accepting traffic until `postgres` and `redis` pass their healthchecks.
- **FR-003**: All service configuration MUST be loaded from environment variables declared in `.env.example`; no hardcoded secrets or connection strings in source code.

**Application Bootstrap**

- **FR-004**: The application MUST initialise a shared async database engine on startup and dispose of it cleanly on shutdown.
- **FR-005**: API documentation endpoints MUST be disabled in non-local environments.
- **FR-006**: All shared I/O clients (database, Redis, MinIO, HTTP) MUST be initialised as lifespan singletons — not constructed per request or per service call.

**Configuration**

- **FR-007**: Configuration MUST be loaded once via a cached singleton; repeated access MUST NOT re-read environment variables.
- **FR-008**: The config schema MUST reject unknown environment variables (`extra="forbid"`), preventing silent misconfiguration.
- **FR-009**: In non-local environments, secrets MUST be resolved from Vault rather than static environment variables. *(Gap: currently static token only.)*

**Structured Logging**

- **FR-010**: Every log line MUST include `log_level`, `logger_name`, and an ISO-format timestamp.
- **FR-011**: In local environments, logs MUST use a human-readable console format; in all other environments, logs MUST use structured JSON format.
- **FR-012**: Every log line emitted within a request context MUST carry `request_id` and `trace_id` bound via per-request middleware. *(Gap: middleware not yet implemented.)*

**Database Access**

- **FR-013**: All database access MUST use the shared async session factory; direct engine usage outside the session dependency is not permitted.
- **FR-014**: The async engine MUST use `pool_pre_ping=True` to detect and recover from stale connections.

**Health & Readiness**

- **FR-015**: The system MUST expose `GET /health` returning `{"status": "ok"}` with HTTP 200 for basic liveness checks.
- **FR-016**: The system MUST expose `GET /ready` that executes a database probe and returns HTTP 200 only when the database is reachable.

**Error Handling**

- **FR-017**: All domain errors MUST return a consistent JSON body `{"detail": "...", "code": "..."}` with the appropriate HTTP status code.
- **FR-018**: The following error types MUST be handled: `NotFoundError` (404), `PermissionDeniedError` (403), `TenantSuspendedError` (403), `RateLimitError` (429), `ExternalServiceError` (503).

**Database Migrations**

- **FR-019**: Schema changes MUST be managed through versioned migration files; the first migration MUST establish the initial schema baseline.
- **FR-020**: Migrations MUST be runnable against the async database engine without requiring a separate synchronous connection.

**CI**

- **FR-021**: Every push to any branch MUST trigger automated linting (`ruff check`), format checking (`black --check`), and test execution (`pytest`).
- **FR-022**: CI MUST include a smoke-test job that verifies the API health endpoint responds correctly after stack startup. *(Gap: smoke test job is missing.)*
- **FR-023**: Eval gates (classifier, RAG, agent, security) MUST be enforced in CI once eval scripts are available from Person C. *(Gap: currently commented out.)*

### Key Entities

- **Settings**: Singleton configuration object loaded once from environment; covers app environment, database, cache, object storage, secret management, LLM provider, and service-to-service credentials.
- **AsyncEngine**: Shared SQLAlchemy async engine; single instance per process lifetime.
- **AsyncSessionFactory**: Produces per-request database sessions from the shared engine.
- **RedisClient**: Shared async Redis client; single instance per process lifetime. *(Gap: not yet a singleton.)*
- **MinIOClient**: Shared object-storage client; single instance per process lifetime. *(Gap: not yet initialised.)*

---

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: All services reach a healthy state within 60 seconds of `docker compose up --build` on a clean environment.
- **SC-002**: `GET /health` responds in under 50 ms under normal load.
- **SC-003**: `GET /ready` correctly reports unavailability within 5 seconds of the database becoming unreachable.
- **SC-004**: 100% of log lines emitted during a request carry a `request_id` and `trace_id` matching the originating request. *(Currently 0% — gap.)*
- **SC-005**: CI lint, format, and test steps complete within 3 minutes for a typical commit.
- **SC-006**: Any commit introducing an unknown environment variable causes startup to fail with a clear error message — 0 silent misconfiguration failures.
- **SC-007**: All five domain error types produce the correct HTTP status and `{"detail", "code"}` response shape — 100% coverage verified by automated tests.

---

## Assumptions

- The platform runs on Linux-compatible container infrastructure (Docker Engine / Docker Compose v2+).
- Local development uses the `.env.example` file as the source of truth for required variables; developers copy it to `.env` before first run.
- `uv` is the sole Python dependency manager; `pip` is not used directly.
- The initial Alembic migration (`0001_initial`) establishes a known-good schema baseline; individual table migrations for tenant models, RLS policies, etc. are handled by subsequent features.
- Redis is used exclusively for short-lived session/memory data in the initial phase; persistent message queuing is out of scope for this feature.
- MinIO is provisioned in the Docker Compose stack but client initialisation and bucket setup are deferred to the feature that first requires object storage (CMS file uploads).
- Vault dynamic secret resolution (lease-based credentials) is deferred; the static `VAULT_TOKEN` fallback is acceptable for local and development environments only.
- CI eval gates are owned by Person C and will be uncommented once `scripts/run_evals.sh` is implemented; this feature only provides the commented scaffold.
- The `.dockerignore` at the repo root is missing; this is a known gap but does not affect correctness — only build context size.
