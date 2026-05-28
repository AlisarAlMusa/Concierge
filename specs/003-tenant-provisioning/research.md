# Research: Tenant Provisioning

**Branch**: `019-tenant-provisioning` | **Date**: 2026-05-28

---

## Decision 1: Slug validation rule

**Decision**: Enforce lowercase alphanumeric + hyphens only (reject underscores).

**Rationale**: FR-012 specifies "lowercase alphanumeric with hyphens only." The existing `TenantCreate.slug_format` validator accepts underscores — that is a bug to fix. DB-level uniqueness is already in the migration (unique index on `tenants.slug`).

**Alternatives considered**: Allowing underscores — rejected, spec is explicit.

---

## Decision 2: Suspension enforcement point

**Decision**: Add a tenant-status check inside `require_tenant_admin`. When `user.tenant_id` is set, eagerly load the tenant and raise 403 (`tenant_suspended`) if `status != active`.

**Rationale**: SC-005 requires suspension to take effect within one request. Checking at the dependency layer (before any service code runs) is the earliest possible enforcement point. Checking inside services would be too late for routes that compose multiple services.

**Alternatives considered**: Middleware — rejected because middleware runs before auth and doesn't have `user.tenant_id` without extra DB lookups. Token revocation at suspend time — heavier, doesn't cover all token variants.

---

## Decision 3: Erasure trigger for DELETE

**Decision**: `TenantService.delete_tenant()` sets status to `deleting`, then fires `asyncio.create_task(ErasureService.purge_tenant(...))`. The route returns 202 immediately.

**Rationale**: Erasure involves multiple I/O operations (Postgres rows, pgvector, MinIO, Redis). Doing it synchronously in the request path would time out. The spec only requires the trigger here; the purge detail lives in spec 015.

**Alternatives considered**: Celery/background queue — out of scope for Week 8, no queue infrastructure in Docker Compose.

---

## Decision 4: Usage summary scope

**Decision**: `GET /platform/tenants/{id}/usage-summary` aggregates `cost_events` rows by `tenant_id` using a single SQL `SUM` query. Returns `total_input_tokens`, `total_output_tokens`, `total_cost_usd`. No conversation content is returned.

**Rationale**: FR-008 and SC-004. The `cost_events` table already has `tenant_id` and numeric columns. A direct aggregate query is the simplest correct implementation.

---

## Decision 5: Audit log route scope

**Decision**: `GET /platform/audit-logs` is paginated (limit/offset), filtered to `tenant_manager` only. No content-table joins.

**Rationale**: FR-009. Audit logs are already in their own table with no content FKs. Pagination prevents large dumps.

---

## What already exists (no re-implementation needed)

| Component | Status |
|---|---|
| `Tenant` ORM model + `TenantStatus` enum | Complete |
| `User` ORM model + `UserRole` enum | Complete |
| `AuditLog` ORM model | Complete |
| DB migration 0001 (tenants, users, audit_logs) | Complete |
| `require_tenant_manager` dependency | Complete |
| `require_tenant_admin` dependency | Complete (needs suspension check) |
| `invite_admin()` in `auth_service.py` | Complete |
| `write_audit_event()` fire-and-forget helper | Complete |
| `schemas/tenant.py` — `TenantRead`, `TenantCreate`, `TenantUpdate` | Exists (slug validator bug to fix) |
| `api/routes/tenants.py` — `GET /` stub + `POST /{id}/invite-admin` | Exists (needs full routes) |
| `services/erasure_service.py` | Exists (stub, called for delete) |

## What is missing (must implement)

| Component | Gap |
|---|---|
| `repositories/tenant_repository.py` | Entirely TODO |
| `repositories/audit_repository.py` | Entirely TODO |
| `services/tenant_service.py` | Entirely TODO |
| `schemas/tenant.py` — `TenantUsageSummary` | Missing |
| `schemas/audit_log.py` — `AuditLogRead` | Missing |
| Route: `POST /platform/tenants` | Missing |
| Route: `GET /platform/tenants/{id}` | Missing |
| Route: `POST /platform/tenants/{id}/suspend` | Missing |
| Route: `POST /platform/tenants/{id}/reactivate` | Missing |
| Route: `DELETE /platform/tenants/{id}` | Missing |
| Route: `GET /platform/tenants/{id}/usage-summary` | Missing |
| Route: `GET /platform/audit-logs` | Missing |
| Suspension check in `require_tenant_admin` | Missing |
| Tests: `tests/test_tenant_provisioning.py` | Missing |
