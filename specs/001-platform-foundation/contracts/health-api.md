# Contract: Health & Readiness API

**Service**: `api` (port 8000)
**Route file**: `backend/app/api/routes/health.py`
**Status**: Implemented

---

## GET /health

Liveness probe. Returns immediately without any I/O. Used by Docker healthcheck and load balancer liveness checks.

**Authentication**: None required

**Request**: No body, no query params

**Response 200**:
```json
{"status": "ok"}
```

**Never returns non-200**: If the process is alive, this always returns 200. A non-200 indicates the process itself is unhealthy.

---

## GET /ready

Readiness probe. Executes `SELECT 1` against the database to confirm the connection pool is healthy. Used by Docker `depends_on: condition: service_healthy` and orchestrator readiness gates.

**Authentication**: None required

**Request**: No body, no query params

**Response 200** (database reachable):
```json
{"status": "ready"}
```

**Response 503** (database unreachable):
```json
{"detail": "Database connectivity check failed", "code": "external_service_error"}
```

**Docker Compose healthcheck** (configured in `docker-compose.yml`):
```yaml
healthcheck:
  test: ["CMD", "curl", "-f", "http://localhost:8000/health"]
  interval: 10s
  timeout: 5s
  retries: 5
```

Note: The compose healthcheck uses `/health` (not `/ready`) to avoid false-negatives during DB startup — the `depends_on: postgres: condition: service_healthy` gate ensures the DB is up before the API starts.

---

## Error Response Shape (all endpoints)

All domain errors from the API follow this shape, enforced by `core/errors.py`:

```json
{
  "detail": "<human-readable message>",
  "code": "<machine-readable error code>"
}
```

| Exception class | HTTP Status | code value |
|----------------|-------------|-----------|
| NotFoundError | 404 | `not_found` |
| PermissionDeniedError | 403 | `permission_denied` |
| TenantSuspendedError | 403 | `tenant_suspended` |
| RateLimitError | 429 | `rate_limited` |
| ExternalServiceError | 503 | `upstream_error` |
