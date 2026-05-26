# Feature Specification: Auth & Roles

> **Owner**: Person A — `feature/platform-tenancy` branch

**Feature Branch**: `002-auth-and-roles`

**Created**: 2026-05-27

**Updated**: 2026-05-27 — gap review against PLAN.md, ENGINEERING_RULES.md, CLAUDE.md

**Status**: Draft

---

## User Scenarios & Testing *(mandatory)*

### User Story 1 — New User Registers and Logs In (Priority: P1)

A tenant admin or tenant manager creates an account with email and password, receives a JWT, and uses it to access protected routes. Unauthenticated requests are rejected with 401.

**Why this priority**: No other feature can work without identity. Auth is the prerequisite for every protected route and role enforcement.

**Independent Test**: Register via `POST /auth/register`; log in via `POST /auth/login`; call `GET /auth/me` with the returned JWT; confirm user record and role are returned.

**Acceptance Scenarios**:

1. **Given** no existing account for an email, **When** `POST /auth/register` is called with valid email + password, **Then** a user record is created with the specified role and HTTP 201 is returned.
2. **Given** a registered user, **When** `POST /auth/login` is called with correct credentials, **Then** a signed JWT is returned and the user can call `GET /auth/me` to retrieve their profile (id, email, role, tenant_id).
3. **Given** no JWT in the `Authorization: Bearer` header, **When** any protected route is called, **Then** the response is HTTP 401 with `{"detail": "Authentication required", "code": "auth_required"}`.
4. **Given** a valid JWT for a `tenant_admin`, **When** a Tenant Manager–only route is called, **Then** the response is HTTP 403 with `{"detail": "Insufficient role", "code": "permission_denied"}`.
5. **Given** a valid JWT, **When** `POST /auth/logout` is called, **Then** the token's JTI is stored in a revocation store and subsequent use of that token returns 401.
6. **Given** an already-registered email, **When** `POST /auth/register` is called again, **Then** the response is HTTP 409 Conflict.

---

### User Story 2 — Tenant Manager Accesses Platform Routes (Priority: P1)

A Tenant Manager calls platform-level endpoints (e.g., `POST /platform/tenants`) and is granted access because they hold the `tenant_manager` role. A `tenant_admin` calling the same endpoint receives 403.

**Why this priority**: Tenant Manager is the only role that crosses tenant boundaries. Correct enforcement of its privilege boundary is a security invariant.

**Independent Test**: Log in as `tenant_manager`; call `GET /platform/tenants`; confirm 200. Log in as `tenant_admin`; call same endpoint; confirm 403.

**Acceptance Scenarios**:

1. **Given** a JWT for a `tenant_manager`, **When** any `/platform/*` route is called, **Then** the request succeeds (2xx).
2. **Given** a JWT for a `tenant_admin`, **When** any `/platform/*` route is called, **Then** the response is HTTP 403.
3. **Given** a `tenant_manager` JWT, **When** `GET /tenant/config` (tenant-admin–only route) is called, **Then** the response is HTTP 403 — Tenant Manager cannot read tenant content even with a valid JWT.
4. **Given** a `tenant_manager` JWT, **When** any CMS, conversations, or leads route is called, **Then** the response is HTTP 403 — platform role has no RLS bypass on content tables.

---

### User Story 3 — Tenant Admin Accesses Tenant-Scoped Routes (Priority: P2)

A tenant admin authenticates and calls tenant-scoped routes. Their `tenant_id` is derived from their user record — never from a request body field. The RLS session variable is set from this derived value and reset in a `finally` block after the request.

**Why this priority**: Tenant-scoped identity enforcement is the application-layer complement to RLS. Trusting `tenant_id` from a request body is a one-line cross-tenant breach.

**Independent Test**: Log in as `tenant_admin`; call `GET /tenant/config` without any `tenant_id` in the body; confirm the response reflects that admin's tenant data only.

**Acceptance Scenarios**:

1. **Given** a `tenant_admin` JWT, **When** `GET /tenant/config` is called, **Then** the `tenant_id` used for the DB query comes from the user record, not the request body.
2. **Given** a request body that includes a `tenant_id` field for a different tenant, **When** a tenant-scoped endpoint is called by a `tenant_admin`, **Then** the field is ignored and the user's own `tenant_id` is used.
3. **Given** a `tenant_admin` for Tenant A, **When** they call any tenant-scoped endpoint, **Then** data from Tenant B is never returned.
4. **Given** the request completes (success or error), **When** the route dependency tears down, **Then** `reset_tenant_context()` is called unconditionally in a `finally` block.

---

### User Story 4 — Tenant Manager Invites First Admin (Priority: P2)

A Tenant Manager creates a new tenant and then invites the first admin for that tenant. The invited user is created with `tenant_admin` role and the correct `tenant_id`. No user can self-register as `tenant_admin` or `tenant_manager` — role assignment is controlled by the platform.

**Why this priority**: Self-registration as any role would let anyone gain admin access. Invite-based creation is the only safe role assignment path.

**Independent Test**: Log in as `tenant_manager`; call `POST /platform/tenants/{id}/invite-admin` with email; confirm a `tenant_admin` user is created with the correct `tenant_id`. Attempt `POST /auth/register` with `role=tenant_admin` directly; confirm this is rejected or the role field is ignored and defaults to `member`.

**Acceptance Scenarios**:

1. **Given** a `tenant_manager` JWT and a valid tenant, **When** `POST /platform/tenants/{tenant_id}/invite-admin` is called with an email, **Then** a user record is created with `role=tenant_admin` and the correct `tenant_id`.
2. **Given** a self-registration request with `role=tenant_admin` or `role=tenant_manager` in the body, **When** `POST /auth/register` is processed, **Then** the role assignment is ignored and the user is created as `member` (or the request is rejected with 400).
3. **Given** an already-active admin for a tenant, **When** a second invite is issued to the same email, **Then** the response is HTTP 409 Conflict.

---

### User Story 5 — Brute-Force Login Attempts Are Rate-Limited (Priority: P2)

Repeated failed login attempts from the same origin are blocked before they can enumerate valid credentials.

**Why this priority**: Without rate limiting, login is a brute-force surface. ENGINEERING_RULES require auth endpoints to be protected.

**Independent Test**: Send 11 `POST /auth/login` requests with wrong credentials in rapid succession from the same IP; confirm the 11th returns HTTP 429 with a `Retry-After` header.

**Acceptance Scenarios**:

1. **Given** more than 10 failed login attempts from the same IP within 15 minutes, **When** the next attempt is made, **Then** HTTP 429 is returned with a `Retry-After` header indicating when the block expires.
2. **Given** a rate-limit block is in effect, **When** the block window expires, **Then** login attempts are accepted again.

---

### User Story 6 — JWT Secret Sourced from Vault (Priority: P2)

The JWT signing secret is loaded from Vault at startup, not from a hardcoded environment variable. In non-local environments, if Vault is unavailable on boot, the API fails to start rather than falling back to a weak secret.

**Why this priority**: Hardcoded or env-file secrets are a credential-leak risk. Vault ensures the secret is rotatable and auditable.

**Independent Test**: In a non-local environment, start the API with Vault unreachable; confirm the API exits with a non-zero code rather than starting with the `.env` fallback.

**Acceptance Scenarios**:

1. **Given** Vault is available and contains `jwt_secret`, **When** the API starts, **Then** it reads the secret from Vault and initialises successfully.
2. **Given** `APP_ENV != local` and Vault is unavailable, **When** the API starts, **Then** it exits with an error rather than using a hardcoded or `.env` fallback secret.
3. **Given** `APP_ENV = local` and Vault is unavailable, **When** the API starts, **Then** it logs a warning and falls back to the `.env` value — this is acceptable for local development only.

---

### Edge Cases

- What happens when a JWT has expired? → 401 with `{"code": "token_expired"}`.
- What happens when a JWT is signed with the wrong key (tampered)? → 401 with `{"code": "invalid_token"}`.
- What happens if a `tenant_admin` user record has no `tenant_id` set? → The route dependency raises 500 with a clear log error; it never falls through to a DB query with a null tenant.
- What happens if a `tenant_manager` user has a `tenant_id` set (misconfiguration)? → The role check still applies; the extra field is ignored for access control.
- What happens if a `member` role user calls a tenant-admin route? → 403 with `{"code": "permission_denied"}`.
- What happens if CORS headers are present? → CORS is not authentication; the JWT is still required. A request with correct CORS origin but no JWT receives 401.
- What happens if someone passes `tenant_id` in a request body or query parameter? → The field is ignored entirely; `tenant_id` is always derived from the authenticated user record.

---

## Requirements *(mandatory)*

### Functional Requirements

**Identity**

- **FR-001**: The system MUST support email/password registration via `POST /auth/register`, returning a user record with an assigned role.
- **FR-002**: The system MUST issue a signed JWT on successful login via `POST /auth/login`. The JWT MUST be delivered as a Bearer token.
- **FR-003**: The system MUST expose `GET /auth/me` returning the authenticated user's profile: `id`, `email`, `role`, `tenant_id`, `is_active`.
- **FR-004**: The system MUST enforce HTTP 401 on any protected route when no valid JWT is present. The response body MUST be `{"detail": "...", "code": "auth_required"}`.
- **FR-005**: The system MUST enforce HTTP 403 when an authenticated user's role lacks permission for the requested route. The response body MUST be `{"detail": "...", "code": "permission_denied"}`.

**Roles**

- **FR-006**: Exactly three roles MUST be supported: `tenant_manager`, `tenant_admin`, `member`. No other roles may be created.
- **FR-007**: `tenant_manager` users MUST have `tenant_id = NULL`. The system MUST reject any attempt to assign a `tenant_id` to a `tenant_manager`.
- **FR-008**: `tenant_admin` and `member` users MUST have a `tenant_id` foreign-keyed to an active tenant record.
- **FR-009**: `member` role grants access only to authenticated visitor routes; it cannot access any `/tenant/*` or `/platform/*` routes.
- **FR-010**: `tenant_manager` role MUST have no RLS bypass on content tables (conversations, leads, CMS pages, content_chunks). Platform role can provision tenants but cannot read tenant private data.

**Tenant ID Derivation**

- **FR-011**: Route dependencies MUST derive the acting `tenant_id` exclusively from the authenticated user record. Reading `tenant_id` from `request.body`, query parameters, or path parameters for identity purposes is prohibited.
- **FR-012**: The `require_tenant_admin` dependency MUST call `set_tenant_context(tenant_id)` before passing control to the route handler, and MUST call `reset_tenant_context()` in an unconditional `finally` block.

**Token Lifecycle**

- **FR-013**: `POST /auth/logout` MUST store the revoked token's JTI in Redis with a TTL equal to the token's remaining expiry. Subsequent requests presenting a revoked JTI MUST receive HTTP 401.
- **FR-014**: The JWT payload MUST contain only: `sub` (user id), `role`, `jti` (unique token id), `exp` (expiry). It MUST NOT contain email, `tenant_id`, passwords, or any PII.
- **FR-015**: JWT signing secret MUST be loaded from `app.state.secrets["jwt_secret"]` (populated from Vault at startup). In `APP_ENV != local`, if the secret is the `.env` fallback value `change-me-local-dev-only`, the API MUST refuse to start.

**Registration and Invite**

- **FR-016**: `POST /auth/register` MUST only create users with `role=member`. Any `role` field in the request body is ignored or rejected.
- **FR-017**: `tenant_admin` accounts MUST only be created via `POST /platform/tenants/{tenant_id}/invite-admin`, callable only by a `tenant_manager`.
- **FR-018**: `tenant_manager` accounts MUST only be created by seeding or by another `tenant_manager`; self-registration as `tenant_manager` is prohibited.

**Rate Limiting**

- **FR-019**: `POST /auth/login` MUST be rate-limited to a maximum of 10 attempts per IP address per 15-minute window. Excess attempts MUST return HTTP 429 with a `Retry-After` header.

**Audit Logging**

- **FR-020**: All authentication and authorisation events (register, login, logout, failed login, role escalation) MUST be written to the `audit_logs` table asynchronously (non-blocking to the request path).
- **FR-021**: A failed audit log write MUST emit a structured warning log but MUST NOT fail the auth request.

**CORS**

- **FR-022**: CORS headers are not a substitute for authentication. All protected routes require a valid JWT regardless of whether the request origin matches the allowed list.

**Service-to-Service**

- **FR-023**: Internal service-to-service calls (model_server, guardrails_sidecar) MUST authenticate using `SERVICE_AUTH_SECRET` from `app.state.secrets`. User JWTs are not valid for service-to-service auth.

### Key Entities

- **User**: Owns `id`, `email`, `hashed_password`, `role` (`tenant_manager` | `tenant_admin` | `member`), `tenant_id` (nullable for tenant_manager), `is_active`, `created_at`. Managed by fastapi-users with a custom role field.
- **Role**: An enum field on the User record — not a separate table. Three values only.
- **JWT**: Signed Bearer token. Payload contains `sub` (user id), `role`, `jti`, `exp`. No PII. Secret sourced from `app.state.secrets["jwt_secret"]`.
- **TokenRevocationEntry**: Redis entry keyed by `revoked_jti:{jti}`, TTL = remaining token lifetime. Used by auth middleware to reject logged-out tokens.
- **Audit Log Entry**: Records `actor_user_id`, `actor_role`, `tenant_id` (nullable), `action` (`register` | `login` | `logout` | `failed_login` | `invite_admin`), `metadata jsonb`, `created_at`.

---

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A new user can register and log in within 5 seconds end-to-end under normal load.
- **SC-002**: 100% of requests to protected routes without a valid JWT receive a 401 response with the correct error body.
- **SC-003**: 100% of requests from a role without permission for a route receive a 403 response with the correct error body.
- **SC-004**: Zero instances of `tenant_id` being read from request body or query params in any route dependency — verified by code review and test.
- **SC-005**: In `APP_ENV != local`, the API refuses to start within 10 seconds if the JWT secret is the placeholder value, rather than starting insecurely.
- **SC-006**: All auth events (login, logout, failed attempts) appear in the audit log within 1 second of occurrence.
- **SC-007**: More than 10 failed login attempts from the same IP within 15 minutes results in HTTP 429 with `Retry-After` header.
- **SC-008**: A token used after `POST /auth/logout` is rejected with HTTP 401 within 1 second of the logout completing.
- **SC-009**: A `tenant_admin` self-registration attempt (with role override in body) results in a `member` account, not a `tenant_admin` account.

---

## Testing Requirements

The following tests MUST exist and pass in CI (per ENGINEERING_RULES §19):

- `test_register_creates_member_role` — self-registration always produces `member`
- `test_login_returns_jwt` — valid credentials return signed JWT
- `test_protected_route_401_without_jwt` — missing token returns 401
- `test_tenant_admin_403_on_platform_route` — role enforcement
- `test_tenant_manager_403_on_tenant_content` — platform role cannot read content
- `test_tenant_id_never_from_request_body` — body `tenant_id` field is ignored
- `test_logout_revokes_token` — revoked JTI returns 401 on reuse
- `test_login_rate_limit` — 11th failed attempt returns 429
- `test_invite_admin_creates_correct_role` — invited user gets `tenant_admin` + correct `tenant_id`
- `test_jwt_payload_has_no_pii` — token payload contains only sub/role/jti/exp

---

## Assumptions

- fastapi-users is used as the identity library; custom auth from scratch is out of scope.
- `member` / visitor role is effectively unauthenticated for public chat endpoints — the widget uses a signed widget token, not a user JWT. The `member` role exists for future authenticated visitor features.
- Password strength policy defaults to a minimum of 8 characters; stronger policy is a future enhancement.
- Token expiry defaults to 24 hours for development; the value should be configurable via Vault in production.
- Email verification flow is out of scope for Week 8; users are created active immediately.
- The Vault integration uses the HTTP API with the dev root token in local development. In non-local environments, Vault is the sole source of truth for `jwt_secret`.
- Audit log writes are fire-and-forget (async, non-blocking) — a failed write logs a warning but does not fail the request.
- The Redis revocation store uses the shared `app.state.redis` singleton established in the platform foundation feature.
- Rate limiting uses the shared Redis singleton; no separate rate-limit service is required for auth endpoints.
- The first `tenant_manager` account is created by the seed script (`scripts/seed_tenants.py`), not via the registration endpoint.
