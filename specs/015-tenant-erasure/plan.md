# Implementation Plan: 015 — Tenant Erasure

**Branch**: `021-tenant-erasure` | **Date**: 2026-05-29 | **Spec**: `specs/015-tenant-erasure/spec.md`

## Summary

Replace the 19-line stub in `backend/app/services/erasure_service.py` with a real `purge_tenant`
coroutine that deletes all tenant data from Postgres (9 tables), MinIO (blob prefix), and Redis
(session keys), then writes a compliance audit marker and sets tenant status to `deleted`. Add
`minio` to backend dependencies. Write unit tests for the purge logic.

## Technical Context

**Language/Version**: Python 3.12, FastAPI async, SQLAlchemy 2.x async, asyncpg, redis-py async

**Primary Dependencies**: SQLAlchemy 2.x, redis-py (already installed), minio SDK (to add)

**Storage layers to purge**:
1. PostgreSQL — 9 tenant-owned tables via direct DELETE
2. MinIO — objects under `{tenant_id}/` prefix in `concierge-cms` bucket
3. Redis — keys matching `memory:{tenant_id}:*`

**No new migrations, routes, or schemas** — the hook point already exists in `tenant_service.py`.

## Constitution Check

| Principle | Gate | Status |
|---|---|---|
| I — Tenant Isolation | DELETE scoped by tenant_id; no SELECT on content | ✅ delete-only path |
| I — Audit retained | audit_logs excluded from erasure per FR-009 | ✅ |
| II — Layered Architecture | Logic in services/erasure_service.py; no route changes | ✅ |
| III — Security | Tenant Manager erases without reading content | ✅ |
| IV — Async | Background task; MinIO via run_in_executor | ✅ |
| V — No torch | Not applicable | ✅ |

## File Structure

```
backend/
  app/services/
    erasure_service.py          ← replace stub with real implementation

  tests/
    test_erasure_service.py     ← new: unit tests for purge_tenant

  pyproject.toml                ← add minio dependency
```

## Implementation Phases

### Phase A — Dependency

Add `minio>=7.2` to `backend/pyproject.toml` dependencies and run `uv sync`.

### Phase B — erasure_service.py

Replace stub with real `purge_tenant(tenant_id, redis)` coroutine.

**Steps inside purge_tenant**:

1. Open own DB session via `get_session_factory()()`
2. Check tenant status — if already `deleted`, return early (idempotent)
3. Purge Postgres: DELETE from each table in FK-safe order:
   messages → escalations → leads → cms_chunks → conversations → widgets → cms_pages → guardrail_configs → cost_events
4. Purge MinIO: list objects under `{tenant_id}/` prefix in `concierge-cms` bucket, delete in batches via `run_in_executor`
5. Purge Redis: `SCAN memory:{tenant_id}:*`, delete matching keys
6. Write compliance audit marker: `action="tenant_deleted"`, `actor_role="system"`, `tenant_id` — no content fields
7. Set tenant status → `deleted`
8. On any layer failure: log warning, do NOT set status to `deleted`, allow retry

**Calling convention update**: `tenant_service.delete_tenant` receives `redis` and forwards it to the task.

### Phase C — Unit Tests

File: `backend/tests/test_erasure_service.py`

- `test_purge_deletes_all_postgres_tables` — assert DELETE called for all 9 tables
- `test_purge_skips_if_already_deleted` — tenant status=deleted → no DELETEs
- `test_purge_clears_redis_keys` — scan_iter returns keys, delete called
- `test_purge_sets_status_deleted_on_success` — assert final status update to `deleted`
- `test_purge_stays_deleting_on_minio_failure` — MinIO raises, status NOT set to `deleted`
- `test_purge_writes_audit_marker` — audit entry written with action `tenant_deleted`, no content
- `test_purge_idempotent_no_rows` — DELETEs affect 0 rows, no error raised

### Phase D — Lint and Test

- `uv run ruff check .` — clean
- `uv run black --check .` — clean
- `uv run pytest tests/test_erasure_service.py -v` — all tests pass

## Key Constraints

- Do NOT delete `audit_logs` rows for the tenant (FR-009 — retained as compliance proof)
- Do NOT SELECT content rows in the erasure path (FR-006, US2)
- Each storage layer MUST be independently try/excepted so one failure does not block others
- Tenant stays in `deleting` status until all layers confirmed purged (FR-011)
- MinIO calls MUST run in executor (synchronous SDK, async event loop)
