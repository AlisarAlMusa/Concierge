# Feature Specification: Tenant Erasure (GDPR/CCPA Right to Delete)

> **Owner**: Person A — `feature/platform-tenancy` branch
> *(Security/compliance aspects reviewed by Person C)*

**Feature Branch**: `015-tenant-erasure`

**Created**: 2026-05-27

**Status**: Draft

---

## User Scenarios & Testing *(mandatory)*

### User Story 1 — Tenant Manager Triggers Full Tenant Erasure (Priority: P1)

The Tenant Manager calls `DELETE /platform/tenants/{tenant_id}`. The tenant status moves to `deleting`. The erasure service purges all tenant data across every storage layer. The Tenant Manager never reads the content — they trigger the deletion without access to what is deleted.

**Why this priority**: GDPR/CCPA right-to-erasure is a contractual obligation for any SaaS with EU or California users. "We deleted the row but the embeddings are still searchable" is a compliance failure.

**Independent Test**: Create a tenant with CMS pages, leads, conversations, and MinIO blobs. Trigger deletion. Confirm the tenant status moves to `deleting`, then `deleted`. Confirm zero rows remain across all 10 tenant-owned tables. Confirm pgvector chunks are gone. Confirm MinIO blobs are purged. Confirm Redis sessions are cleared.

**Acceptance Scenarios**:

1. **Given** an active tenant with data in all storage layers, **When** `DELETE /platform/tenants/{tenant_id}` is called, **Then** the tenant status moves to `deleting` and the erasure job is triggered asynchronously.
2. **Given** the erasure job completes, **When** any tenant-owned table is queried for that tenant's id, **Then** zero rows are returned.
3. **Given** the erasure job completes, **When** pgvector is queried for that tenant's chunks, **Then** zero embedding rows are returned.
4. **Given** the erasure job completes, **When** MinIO is checked for that tenant's blobs, **Then** the bucket/prefix is empty.
5. **Given** the erasure job completes, **When** Redis is checked for that tenant's session memory keys, **Then** zero keys matching `memory:{tenant_id}:*` exist.
6. **Given** erasure completes, **When** the tenant record is checked, **Then** status is `deleted` and an audit marker is recorded (without private content).

---

### User Story 2 — Erasure Runs Through a Narrow Delete-Only Path (Priority: P1)

The Tenant Manager can destroy a tenant's data without ever reading it. The erasure service operates as a narrow write/delete-only path — it issues DELETE queries scoped by `tenant_id` but does not SELECT content rows.

**Why this priority**: Resolves the "no content access, but must erase" tension. The Tenant Manager must not be able to use the erasure flow to exfiltrate content.

**Independent Test**: Add logging to the erasure service. Trigger erasure. Confirm no SELECT statements on content tables are logged for the Tenant Manager actor — only DELETE statements.

**Acceptance Scenarios**:

1. **Given** the erasure service running, **When** a tenant is deleted, **Then** the service issues DELETE queries (not SELECT queries) for all tenant-owned tables.
2. **Given** the erasure service, **When** it runs, **Then** it does not return content rows to the caller — only a completion status.
3. **Given** an audit log, **When** the erasure event is checked, **Then** it records the Tenant Manager actor id, action `tenant_deleted`, target tenant id, and timestamp — but no content.

---

### User Story 3 — Partial Erasure Does Not Leave Orphaned Data (Priority: P1)

If the erasure job fails partway through (e.g., MinIO is unavailable), the data that was already deleted is not restored, and the job can be retried. The tenant remains in `deleting` status until erasure completes fully.

**Why this priority**: Partial erasure is a compliance failure — orphaned vectors or blobs are still searchable/accessible.

**Independent Test**: Interrupt the erasure job after Postgres rows are deleted but before MinIO purge. Confirm the tenant status remains `deleting`. Retry the job. Confirm all remaining data is purged.

**Acceptance Scenarios**:

1. **Given** a partial erasure failure, **When** the tenant status is checked, **Then** it is still `deleting` (not `deleted`).
2. **Given** a partial failure, **When** the erasure job is retried, **Then** it picks up from the remaining storage layers and completes cleanly.
3. **Given** erasure retries, **When** already-deleted rows are targeted, **Then** the DELETE is idempotent (no error on deleting already-absent rows).

---

### User Story 4 — Audit Log Retains a Compliance Marker Without Private Content (Priority: P2)

After full erasure, a minimal audit record exists in the audit log confirming that the tenant was deleted, by whom, and when. The log entry contains no private content (no message text, no lead contact info, no CMS body).

**Why this priority**: Compliance audits require proof that erasure occurred. The audit log must be retained for legal reasons while containing no personal data itself.

**Independent Test**: Delete a tenant. Query the audit log. Confirm an entry exists with actor id, action `tenant_deleted`, tenant id, and timestamp. Confirm no content fields are present.

**Acceptance Scenarios**:

1. **Given** a completed erasure, **When** the audit log is queried for that tenant, **Then** a `tenant_deleted` entry exists with actor id and timestamp.
2. **Given** the audit entry, **When** all its fields are inspected, **Then** no private content (messages, emails, CMS body) is present.
3. **Given** the audit log, **When** the admin queries it, **Then** the entry is retained even after the tenant row is deleted.

---

### Edge Cases

- What happens when erasure is requested for a tenant that does not exist? → 404.
- What happens when erasure is requested for a tenant already in `deleting` status? → Idempotent; the existing erasure job continues (no duplicate).
- What happens when Redis is unavailable during erasure? → The job skips Redis cleanup, logs a warning, and marks the step for retry; it does not block Postgres and MinIO erasure.
- What happens when a tenant has blobs in MinIO but no bucket was ever created? → The MinIO purge step is a no-op; the job continues.
- What happens to in-flight requests for a tenant that is being deleted? → RLS returns zero rows for `deleting` tenants; the tenant status check in the auth dependency rejects new requests with 403.

---

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: `DELETE /platform/tenants/{tenant_id}` (Tenant Manager only) MUST set tenant status to `deleting` and trigger the erasure job.
- **FR-002**: The erasure job MUST delete all rows from all tenant-owned tables: cms_pages, content_chunks, widgets, conversations, messages, leads, escalations, guardrail_configs, cost_events.
- **FR-003**: The erasure job MUST delete all pgvector embedding rows where `tenant_id = $1`.
- **FR-004**: The erasure job MUST delete all MinIO blobs/objects in the tenant's prefix/bucket.
- **FR-005**: The erasure job MUST delete all Redis keys matching `memory:{tenant_id}:*`.
- **FR-006**: The erasure job MUST operate as a delete-only path — it MUST NOT SELECT content rows for the Tenant Manager.
- **FR-007**: After full erasure, the tenant record status MUST be updated to `deleted`.
- **FR-008**: A compliance audit marker MUST be written to the audit log: actor id, action `tenant_deleted`, tenant id, timestamp — no private content.
- **FR-009**: Audit log entries for deleted tenants MUST be retained (not deleted with the tenant).
- **FR-010**: The erasure job MUST be idempotent — retrying on a partially erased tenant MUST complete cleanly without errors.
- **FR-011**: The tenant status MUST remain `deleting` until all storage layers are confirmed purged.
- **FR-012**: Requests from a `deleting` or `deleted` tenant's users MUST be rejected with 403.

### Key Entities

- **Erasure Job**: Triggered by `DELETE /platform/tenants/{tenant_id}`. Executes as a background task (worker or async task). Covers Postgres, pgvector, MinIO, Redis.
- **Storage Layers**: (1) Postgres tenant-owned tables, (2) pgvector (content_chunks.embedding), (3) MinIO (blobs in tenant's prefix), (4) Redis (session memory keys).
- **Compliance Audit Marker**: A minimal audit_log entry — actor id, `tenant_deleted` action, tenant id, timestamp. No content fields.

---

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: After erasure completes, zero rows remain in any tenant-owned table for the deleted tenant (automated test).
- **SC-002**: After erasure completes, zero pgvector chunks remain for the deleted tenant.
- **SC-003**: After erasure completes, zero Redis memory keys remain for the deleted tenant.
- **SC-004**: The erasure audit marker is retained in the audit log after all tenant data is purged.
- **SC-005**: Erasure is idempotent — a retry on a partially erased tenant completes without error in 100% of test cases.
- **SC-006**: The erasure service issues zero SELECT queries on content tables (verified by query log inspection).

---

## Assumptions

- The erasure job runs as an async task triggered by the API route; it does not require a separate worker service for Week 8.
- MinIO blob management assumes all tenant blobs are stored under a `tenant_id/` prefix; the erasure job deletes the entire prefix.
- Audit log retention after tenant deletion is implemented by not including `audit_logs` in the tenant-scoped erasure tables. The `tenant_id` column in `audit_logs` acts as a reference only, not a foreign key with CASCADE DELETE.
- Traces and structured logs outside Postgres (e.g., stdout logs captured by the Docker runtime) are out of scope for the automated erasure; these are covered by log retention policy documented in `docs/SECURITY.md`.
- The erasure endpoint is restricted to `tenant_manager` role; no tenant admin can trigger their own erasure in Week 8.
