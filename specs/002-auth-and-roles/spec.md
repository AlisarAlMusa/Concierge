# Feature Specification: Auth & Roles

> **Owner**: Person A — `feature/platform-tenancy` branch

**Feature Branch**: `002-auth-and-roles`

**Created**: 2026-05-27

**Status**: Draft

---

## User Scenarios & Testing *(mandatory)*

### User Story 1 — New User Registers and Logs In (Priority: P1)

A tenant admin or tenant manager creates an account with email and password, receives a JWT, and uses it to access protected routes. Unauthenticated requests are rejected with 401.

**Why this priority**: No other feature can work without identity. Auth is the prerequisite for every protected route and role enforcement.

**Independent Test**: Register via `POST /auth/register`; log in via `POST /auth/login`; call `GET /auth/me` with the returned JWT; confirm user record and role are returned.

**Acceptance Scenarios**:

1. **Given** no existing account for an email, **When** `POST /auth/register` is called with valid email + password, **Then** a user record is created with the specified role and HTTP 201 is returned.
2. **Given** a registered user, **When** `POST /auth/login` is called with correct credentials, **Then** a signed JWT is returned and the user can call `GET /auth/me` to retrieve their profile.
3. **Given** no JWT in the `Authorization` header, **When** any protected route is called, **Then** the response is HTTP 401 Unauthorized.
4. **Given** a valid JWT for a `tenant_admin`, **When** a Tenant Manager–only route is called, **Then** the response is HTTP 403 Forbidden.
5. **Given** a valid JWT, **When** `POST /auth/logout` is called, **Then** the session token is invalidated and subsequent use of that token is rejected.

---

### User Story 2 — Tenant Manager Accesses Platform Routes (Priority: P1)

A Tenant Manager calls platform-level endpoints (e.g., `POST /platform/tenants`) and is granted access because they hold the `tenant_manager` role. A `tenant_admin` calling the same endpoint receives 403.

**Why this priority**: Tenant Manager is the only role that crosses tenant boundaries. Correct enforcement of its privilege boundary is a security invariant.

**Independent Test**: Log in as `tenant_manager`; call `GET /platform/tenants`; confirm 200. Log in as `tenant_admin`; call same endpoint; confirm 403.

**Acceptance Scenarios**:

1. **Given** a JWT for a `tenant_manager`, **When** any `/platform/*` route is called, **Then** the request succeeds (2xx).
2. **Given** a JWT for a `tenant_admin`, **When** any `/platform/*` route is called, **Then** the response is HTTP 403.
3. **Given** a `tenant_manager` JWT, **When** `GET /tenant/config` (tenant-admin–only route) is called, **Then** the response is HTTP 403 — Tenant Manager cannot read tenant content.

---

### User Story 3 — Tenant Admin Accesses Tenant-Scoped Routes (Priority: P2)

A tenant admin authenticates and calls tenant-scoped routes. Their `tenant_id` is derived from their user record — never from a request body field. The RLS session variable is set from this derived value.

**Why this priority**: Tenant-scoped identity enforcement is the application-layer complement to RLS. Trusting `tenant_id` from a request body is a one-line cross-tenant breach.

**Independent Test**: Log in as `tenant_admin`; call `GET /tenant/config` without any `tenant_id` in the body; confirm the response reflects that admin's tenant data only.

**Acceptance Scenarios**:

1. **Given** a `tenant_admin` JWT, **When** `GET /tenant/config` is called, **Then** the `tenant_id` used for the DB query comes from the user record, not the request body.
2. **Given** a request body that includes a `tenant_id` field for a different tenant, **When** a tenant-scoped endpoint is called by a `tenant_admin`, **Then** the field is ignored and the user's own `tenant_id` is used.
3. **Given** a `tenant_admin` for Tenant A, **When** they call any tenant-scoped endpoint, **Then** data from Tenant B is never returned.

---

### User Story 4 — Token Secret Sourced from Vault (Priority: P2)

The JWT signing secret is loaded from Vault at startup, not from a hardcoded environment variable. If Vault is unavailable on boot, the API fails to start rather than falling back to a weak secret.

**Why this priority**: Hardcoded or env-file secrets are a credential-leak risk. Vault ensures the secret is rotatable and auditable.

**Independent Test**: Start the API with Vault running and confirm `GET /health` returns 200. Start with Vault unreachable; confirm the API fails to start (non-zero exit) rather than starting with a fallback secret.

**Acceptance Scenarios**:

1. **Given** Vault is available and contains the JWT secret, **When** the API starts, **Then** it reads the secret from Vault and initialises successfully.
2. **Given** Vault is unavailable, **When** the API starts, **Then** it exits with an error rather than using a hardcoded or empty secret.

---

### Edge Cases

- What happens when a user registers with an already-registered email? → 409 Conflict.
- What happens when a JWT has expired? → 401 with `token_expired` detail.
- What happens when a JWT is signed with a wrong key (tampered)? → 401 with `invalid_token`.
- What happens if a `tenant_admin` user record has no `tenant_id` set? → The route dependency raises 422 / 500 with a clear log error; never falls through to a DB query with a null tenant.
- What happens if a `tenant_manager` user has a `tenant_id` set (misconfiguration)? → The role check still applies; the extra field is ignored for access control.

---

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST support email/password registration via `POST /auth/register`, returning a user record with an assigned role.
- **FR-002**: The system MUST issue a signed JWT on successful login via `POST /auth/login`.
- **FR-003**: The system MUST expose `GET /auth/me` to return the authenticated user's profile (id, email, role, tenant_id).
- **FR-004**: The system MUST enforce 401 Unauthorized on any protected route when no valid JWT is present.
- **FR-005**: The system MUST enforce 403 Forbidden when an authenticated user's role lacks permission for the requested route.
- **FR-006**: Three roles MUST be supported: `tenant_manager`, `tenant_admin`, `member`. No additional roles may be created.
- **FR-007**: `tenant_manager` users MUST NOT have a required `tenant_id` (they operate at the platform level).
- **FR-008**: `tenant_admin` and `member` users MUST have a `tenant_id` foreign-keyed to an active tenant.
- **FR-009**: Route dependencies MUST derive the acting `tenant_id` from the authenticated user record — never from `request.body` or query parameters.
- **FR-010**: `tenant_manager` role MUST have no RLS bypass on content tables (conversations, leads, CMS pages). It provisions tenants but cannot read their private data.
- **FR-011**: The JWT signing secret MUST be loaded from Vault; the API MUST fail startup if Vault is unreachable.
- **FR-012**: `POST /auth/logout` MUST invalidate the current token.
- **FR-013**: All authentication and authorisation events (login, logout, failed attempts, role escalation) MUST be written to the audit log.

### Key Entities

- **User**: Owns an email, hashed password, role (`tenant_manager` | `tenant_admin` | `member`), optional `tenant_id`, `is_active`, `created_at`. Managed by fastapi-users.
- **Role**: An enum field on the User — not a separate table. Three values only.
- **JWT**: Signed access token encoding user id, role, and expiry. Secret sourced from Vault.
- **Audit Log Entry**: Records actor id, role, action (`login`, `logout`, `register`, `failed_login`), timestamp.

---

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A new user can register and log in within 5 seconds end-to-end under normal load.
- **SC-002**: 100% of requests to protected routes without a valid JWT receive a 401 response.
- **SC-003**: 100% of requests from a role without permission for a route receive a 403 response.
- **SC-004**: Zero instances of `tenant_id` being read from request body in any route dependency.
- **SC-005**: The API fails to start (within 10 seconds) if the Vault JWT secret is unavailable, rather than starting with a fallback.
- **SC-006**: All login, logout, and failed-auth events appear in the audit log within 1 second of occurrence.

---

## Assumptions

- fastapi-users is used as the identity library; custom auth from scratch is out of scope.
- `member` / visitor role is effectively unauthenticated for public chat endpoints — the widget uses a signed widget token, not a user JWT.
- Password strength policy defaults to a minimum of 8 characters; stronger policy is a future enhancement.
- Token expiry defaults to 24 hours for development; production value comes from Vault config.
- Email verification flow is out of scope for Week 8; users are created active immediately.
- The Vault integration uses the Vault HTTP API with a root/dev token in local development (`VAULT_TOKEN=dev-root-token`).
- Audit log writes are fire-and-forget (async, non-blocking to the request path) — a failed write logs a warning but does not fail the auth request.
