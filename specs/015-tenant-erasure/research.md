# Research: 015 — Tenant Erasure

## Decision 1: Single async background task, no separate worker

**Decision**: `purge_tenant` runs as an `asyncio.create_task` already fired by `tenant_service.delete_tenant`. Spec 015 replaces the stub body. No separate Celery/ARQ worker.

**Rationale**: The spec explicitly says "async task triggered by the API route; it does not require a separate worker service for Week 8." The hook point already exists in `tenant_service.delete_tenant`.

**Alternatives considered**: Dedicated ARQ worker — overkill for Week 8; would require adding a `worker/` service to docker-compose.

---

## Decision 2: Postgres purge via direct DELETE, not CASCADE

**Decision**: Issue explicit `DELETE FROM <table> WHERE tenant_id = $1` for each of the 9 tenant-owned content tables, in FK-safe order. Do NOT rely on `ON DELETE CASCADE` from the tenants row.

**Rationale**: The tenants row is NOT deleted during erasure — it stays with status `deleted` so the compliance audit marker and the tenant record can be inspected post-erasure. CASCADE from tenants.id would require deleting the tenant row, which violates the audit retention requirement (FR-009). Explicit DELETEs are also clearer and retry-safe.

**Order** (FK-safe, child tables before parents):
1. messages (→ conversations)
2. escalations (→ conversations, nullable)
3. leads (→ conversations, nullable)
4. cms_chunks (→ cms_pages)
5. conversations (→ widgets)
6. widgets
7. cms_pages
8. guardrail_configs
9. cost_events

**audit_logs is intentionally excluded** — rows are retained as the compliance proof (spec FR-009).

---

## Decision 3: MinIO client — use `minio` SDK via asyncio executor

**Decision**: The `minio` Python SDK is not in `pyproject.toml` yet. Add it as a dependency. Use `asyncio.get_event_loop().run_in_executor(None, ...)` to run synchronous MinIO calls off the async event loop.

**Rationale**: MinIO SDK is synchronous. The async MinIO client (`miniopy-async`) is a thin wrapper with less community support. Using `run_in_executor` keeps the dependency simpler. The erasure task is already background; blocking the executor for a few seconds is acceptable.

**Tenant prefix convention**: All CMS blobs are stored under `{tenant_id}/` in the `concierge-cms` bucket (derived from how `cms_service` uploads). Erasure lists and deletes all objects under that prefix.

**Bucket not found**: If the bucket or prefix does not exist, treat as a no-op (FR-010 idempotency).

---

## Decision 4: Redis purge via SCAN + DEL pattern

**Decision**: Use `redis.scan_iter("memory:{tenant_id}:*")` to find all session keys, then delete them in batches.

**Rationale**: Redis `KEYS` is blocking and can stall the server on large keyspaces. `SCAN`-based iteration is non-blocking and safe in production. The pattern `memory:{tenant_id}:*` matches the memory key format documented in CLAUDE.md.

**Redis client**: Reuse the connection from `app.state.redis` — the erasure task does not have a request context, so it receives the redis client as a parameter injected at task-creation time.

---

## Decision 5: Compliance audit marker written with a new DB session

**Decision**: `purge_tenant` opens its own `AsyncSession` (via `get_session_factory()`) for the compliance audit write and the final `status = deleted` update. It does NOT reuse the request session (which is already closed by the time the background task runs).

**Rationale**: Background tasks run after the request lifecycle ends. The request session is closed. The erasure task must manage its own DB connections.

---

## Decision 6: Idempotency — all DELETEs are inherently safe

**Decision**: No explicit "already erased" guard needed per table. `DELETE WHERE tenant_id = $1` is a no-op if no rows exist. MinIO prefix listing returns empty if already purged. Redis SCAN returns no keys if already deleted.

**Rationale**: Retry-safety comes for free from the delete semantics. The only state to check is the tenant's current status — if already `deleted`, skip straight to success.

---

## Decision 7: Error handling — per-layer try/except, keep going

**Decision**: Each storage layer (Postgres, MinIO, Redis) is wrapped in its own try/except. A failure in one layer logs a warning and continues to the next. After all layers, if any failed, the tenant status is NOT updated to `deleted` (stays `deleting`). This allows retries.

**Rationale**: Spec US3 requires the tenant to remain in `deleting` status on partial failure so retries can complete. The spec also says "Redis unavailability does not block Postgres and MinIO erasure."

---

## Decision 8: No new route or schema needed

**Decision**: The existing `DELETE /platform/tenants/{tenant_id}` route in `tenants.py` already triggers `purge_tenant`. No new API endpoint, no new Pydantic schema, no new migration.

**Rationale**: Spec 003 already wired the route and fires the task. Spec 015 is purely `erasure_service.py` implementation + `minio` dependency + unit tests.
