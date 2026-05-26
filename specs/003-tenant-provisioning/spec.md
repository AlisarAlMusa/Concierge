# Feature Specification: Tenant Provisioning

> **Owner**: Person A — `feature/platform-tenancy` branch

**Feature Branch**: `003-tenant-provisioning`

**Created**: 2026-05-27

**Status**: Draft

---

## User Scenarios & Testing *(mandatory)*

### User Story 1 — Tenant Manager Creates a New Tenant (Priority: P1)

The Tenant Manager calls a platform route to create a new tenant with a name and slug. The tenant starts in `active` status. The action is audit-logged with the Tenant Manager's actor id.

**Why this priority**: Provisioning is the entry point for every new customer. Nothing tenant-specific works until the tenant row exists.

**Independent Test**: Authenticate as `tenant_manager`; call `POST /platform/tenants` with `{name, slug}`; confirm a tenant row is created in `active` status, the response returns the tenant id, and an audit log entry is written.

**Acceptance Scenarios**:

1. **Given** a `tenant_manager` JWT and a unique slug, **When** `POST /platform/tenants` is called, **Then** a tenant row is created with status `active` and HTTP 201 is returned.
2. **Given** a `tenant_admin` JWT, **When** `POST /platform/tenants` is called, **Then** the response is HTTP 403.
3. **Given** a slug that already exists, **When** `POST /platform/tenants` is called, **Then** the response is HTTP 409 Conflict.
4. **Given** a newly created tenant, **When** the audit log is queried, **Then** an entry exists recording the `tenant_manager` actor, action `tenant_created`, and target tenant id.

---

### User Story 2 — Tenant Manager Invites First Tenant Admin (Priority: P1)

After creating a tenant, the Tenant Manager invites the first admin for that tenant by providing an email address. The invited user's account is created with role `tenant_admin` and the correct `tenant_id`. The Tenant Manager never logs into the tenant to configure it.

**Why this priority**: Without this flow, tenants cannot self-configure. It also enforces the privacy line — the platform operator provisions, tenants run themselves.

**Independent Test**: Create a tenant; call `POST /platform/tenants/{tenant_id}/invite-admin` with an email; confirm a user record is created with `role=tenant_admin` and the correct `tenant_id`.

**Acceptance Scenarios**:

1. **Given** an existing tenant, **When** `POST /platform/tenants/{tenant_id}/invite-admin` is called with a valid email, **Then** a `tenant_admin` user is created for that tenant and HTTP 201 is returned.
2. **Given** an invitation is sent, **When** the invited user calls `POST /auth/login`, **Then** they can authenticate and access `/tenant/config` for their own tenant only.
3. **Given** a `tenant_admin` JWT, **When** `POST /platform/tenants/{tenant_id}/invite-admin` is called, **Then** the response is HTTP 403.

---

### User Story 3 — Tenant Manager Suspends and Reactivates a Tenant (Priority: P2)

The Tenant Manager can suspend a tenant (status → `suspended`), which blocks all authenticated requests from that tenant's users. Reactivation restores `active` status.

**Why this priority**: Suspension is the mechanism for handling non-payment, abuse, or compliance issues without data deletion.

**Independent Test**: Suspend a tenant; confirm that `tenant_admin` login for that tenant returns 403 / 401. Reactivate; confirm login works again.

**Acceptance Scenarios**:

1. **Given** an `active` tenant, **When** `POST /platform/tenants/{tenant_id}/suspend` is called by `tenant_manager`, **Then** the tenant status becomes `suspended`.
2. **Given** a `suspended` tenant, **When** any route is called with that tenant's admin JWT, **Then** the response is HTTP 403 with a `tenant_suspended` reason.
3. **Given** a `suspended` tenant, **When** `POST /platform/tenants/{tenant_id}/reactivate` is called, **Then** the tenant status returns to `active` and its users can authenticate again.

---

### User Story 4 — Tenant Manager Views Tenant List and Usage (Priority: P2)

The Tenant Manager can list all tenants and view aggregate usage (cost/token) per tenant. They cannot read tenant content (conversations, leads, CMS pages).

**Why this priority**: Operational visibility for the platform operator. The read boundary (aggregate-only, no content) is a security invariant.

**Independent Test**: Authenticate as `tenant_manager`; call `GET /platform/tenants`; confirm the list excludes private content. Call `GET /platform/tenants/{id}/usage-summary`; confirm it returns aggregate cost metrics, not message content.

**Acceptance Scenarios**:

1. **Given** multiple tenants, **When** `GET /platform/tenants` is called by `tenant_manager`, **Then** all tenants are returned with status, name, slug, and created_at — no CMS, conversations, or leads data.
2. **Given** a tenant with cost events, **When** `GET /platform/tenants/{id}/usage-summary` is called, **Then** aggregate token and cost totals are returned — no conversation content.
3. **Given** a `tenant_admin` JWT, **When** `GET /platform/tenants` is called, **Then** the response is HTTP 403.

---

### User Story 5 — Tenant Manager Deletes a Tenant (Priority: P3)

The Tenant Manager triggers the deletion flow for a tenant (`DELETE /platform/tenants/{tenant_id}`). The tenant status moves to `deleting`, then the erasure service purges all associated data. The Tenant Manager can trigger this without ever having read the content.

**Why this priority**: GDPR/CCPA right-to-erasure compliance. The "delete without read" tension must be resolved explicitly.

**Independent Test**: Delete a tenant; confirm status moves to `deleting`; confirm the erasure service is triggered; confirm the tenant's rows, vectors, blobs, and Redis sessions are purged.

**Acceptance Scenarios**:

1. **Given** an `active` tenant, **When** `DELETE /platform/tenants/{tenant_id}` is called, **Then** the tenant status moves to `deleting` and the erasure job is triggered.
2. **Given** a `deleting` tenant, **When** the erasure job completes, **Then** the tenant status is `deleted` and no private data remains.
3. **Given** a `deleted` tenant id, **When** any tenant-scoped route is called, **Then** the response is HTTP 404.

---

### Edge Cases

- What happens if `invite-admin` is called for a tenant that is `suspended`? → 422 / 400 with `tenant_not_active`.
- What happens if `invite-admin` is called with an email that already has an account? → 409 Conflict.
- What happens if a `suspend` is called on an already-`suspended` tenant? → Idempotent, returns 200 with current status.
- What happens if a slug contains invalid characters (spaces, uppercase)? → 422 Validation Error with detail.
- What happens if deletion is requested for a tenant already in `deleting` state? → 409 or idempotent 200.

---

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST allow only `tenant_manager` to call any `/platform/*` route.
- **FR-002**: `POST /platform/tenants` MUST create a tenant with a unique slug and status `active`.
- **FR-003**: `POST /platform/tenants/{tenant_id}/invite-admin` MUST create a `tenant_admin` user scoped to the given tenant.
- **FR-004**: `POST /platform/tenants/{tenant_id}/suspend` MUST set tenant status to `suspended`; suspended tenants' users MUST be blocked.
- **FR-005**: `POST /platform/tenants/{tenant_id}/reactivate` MUST restore tenant status to `active`.
- **FR-006**: `DELETE /platform/tenants/{tenant_id}` MUST set status to `deleting` and trigger the erasure service.
- **FR-007**: `GET /platform/tenants` and `GET /platform/tenants/{tenant_id}` MUST return no private content (no conversations, leads, or CMS bodies).
- **FR-008**: `GET /platform/tenants/{tenant_id}/usage-summary` MUST return aggregate cost/token metrics only.
- **FR-009**: `GET /platform/audit-logs` MUST be restricted to `tenant_manager` and return the audit trail.
- **FR-010**: Every Tenant Manager action (create, invite, suspend, reactivate, delete) MUST produce an audit log entry with actor id, action type, target tenant id, and timestamp.
- **FR-011**: Provisioning and erasure MUST run through a narrow write/delete-only maintenance path — the `tenant_manager` role MUST NOT have an RLS bypass on content tables.
- **FR-012**: Tenant slugs MUST be lowercase alphanumeric with hyphens only; uniqueness enforced at the DB level.

### Key Entities

- **Tenant**: id (UUID), name, slug (unique), status (`active` | `suspended` | `deleting` | `deleted`), created_at, updated_at.
- **Audit Log Entry**: id, actor_user_id, actor_role, tenant_id (nullable), action, target_type, target_id, metadata (jsonb), created_at.
- **Tenant Admin Invitation**: Not a separate table — results in a `User` row with `role=tenant_admin` and the target `tenant_id`.

---

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Tenant creation and first-admin invitation complete in under 3 seconds end-to-end.
- **SC-002**: 100% of `/platform/*` calls from non-`tenant_manager` roles are rejected with 403.
- **SC-003**: 100% of Tenant Manager create/suspend/delete actions produce a corresponding audit log entry.
- **SC-004**: Zero instances of the Tenant Manager reading tenant conversation, lead, or CMS content through platform routes.
- **SC-005**: Suspended tenants' authentication is blocked within one request of the suspension being applied (no lag window).

---

## Assumptions

- Initial admin password is set at invitation time (no email invite/magic-link flow in Week 8); email verification is out of scope.
- Erasure detail (purging rows, vectors, blobs, Redis) is specified in feature `015-tenant-erasure` — this spec only covers the trigger and status transition.
- Audit log writes are async and non-blocking; a failed write warns but does not roll back the provisioning action.
- Tenant slugs are used as human-readable identifiers in URLs and embed snippets; they cannot be changed post-creation.
- The `tenant_manager` role is seeded as a single platform user; there is no UI for creating multiple Tenant Managers in Week 8.
