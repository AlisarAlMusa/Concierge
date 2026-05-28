# Research: Auth & Roles

**Feature**: 002-auth-and-roles | **Date**: 2026-05-27

---

## Decision 1: JWT Transport — Bearer vs Cookie

**Decision**: Bearer token via `Authorization: Bearer <token>` header exclusively.

**Rationale**:
- The Streamlit admin app makes HTTP requests from a Python process — cookie handling adds complexity with no benefit.
- Service-to-service callers (future integrations) work naturally with Bearer headers.
- fastapi-users `BearerTransport` + `JWTStrategy` is the straightforward configuration.
- Cookie transport would require CSRF protection; Bearer with `Authorization` header is inherently CSRF-safe.

**Alternatives considered**:
- Cookie transport: rejected — extra CSRF complexity, harder to use from non-browser clients.
- Opaque session tokens (database-backed): rejected — adds a DB lookup on every request; JWT is self-contained.

---

## Decision 2: JWT Logout — Redis JTI Blacklist

**Decision**: On `POST /auth/logout`, write `revoked_jti:{jti}` to Redis with TTL = remaining token lifetime (`exp - now`). A middleware (or fastapi-users current_user dependency override) checks this key before accepting the token.

**Rationale**:
- JWTs are stateless by design — without a revocation store there is no way to invalidate a token before expiry.
- Redis is already available as `app.state.redis` (001-platform-foundation).
- The entry is self-expiring (TTL = remaining lifetime) — no cleanup job needed.
- Storage cost is negligible: one small Redis string per active session, auto-expiring.

**Alternatives considered**:
- Short expiry (15 min) with no revocation: rejected — users expect logout to work immediately; a 15-minute window is unacceptable for a stolen token scenario.
- Database revocation table: rejected — Redis is faster and already available; a DB lookup on every request adds latency and load.
- Refresh token pattern: out of scope for Week 8; adds complexity with minimal benefit at demo scale.

---

## Decision 3: Login Rate Limiting — Redis Counter per IP

**Decision**: Redis key `login_attempts:{client_ip}` with an atomic `INCR` + `EXPIRE 900` (15 minutes). If value > 10, return 429 with `Retry-After: {seconds_until_expiry}`.

**Rationale**:
- Redis is already available; no new infrastructure.
- `INCR` + `EXPIRE` is the standard Redis rate-limiting pattern — atomic and low-latency.
- Per-IP is the right granularity for login brute-force prevention.
- 10 attempts / 15 minutes is the OWASP-recommended starting threshold.

**Alternatives considered**:
- Per-email rate limiting: rejected as the primary control — an attacker can rotate source IPs but the account owner is punished; per-IP is more appropriate for brute-force.
- External rate-limit library (slowapi): considered; decided against adding a new dependency when a 10-line Redis implementation covers the spec exactly.
- No rate limiting: rejected — ENGINEERING_RULES §17 requires protection on auth endpoints.

---

## Decision 4: Vault Secret Guard — Sentinel Value Check

**Decision**: In the lifespan startup, after populating `app.state.secrets`, if `APP_ENV != "local"` and `app.state.secrets["jwt_secret"] == "change-me-local-dev-only"`, raise `RuntimeError("JWT secret is the placeholder value — refusing to start in non-local environment")`.

**Rationale**:
- The real failure mode is not "Vault is unreachable" but "Vault was skipped and the placeholder was used in production."
- A sentinel value check catches both cases: Vault down (fallback to placeholder) and Vault not configured.
- Simple to implement — no new Vault client code, no health-check polling.
- Local development is explicitly exempt — developers can work without Vault.

**Alternatives considered**:
- Check Vault reachability at startup: more complex; would require a separate HTTP call in the startup path; still doesn't prevent the case where Vault is reachable but the secret was never set.
- Require a non-empty secret: weaker — an attacker could set any non-empty value and bypass the check.

---

## Decision 5: Role Storage — Enum Column, Not RBAC Table

**Decision**: Add a `role: UserRole` enum column to the `users` table. `UserRole = Enum("tenant_manager", "tenant_admin", "member")`. No separate roles or permissions table.

**Rationale**:
- Exactly three roles with fixed, non-overlapping permissions — a RBAC table adds complexity with no benefit.
- Enum column is queryable, indexable, and type-safe via SQLAlchemy.
- fastapi-users custom `User` model supports adding columns.
- Constitution explicitly states: "three named roles, powers you can count on one hand."

**Alternatives considered**:
- Separate `roles` table with many-to-many: rejected — over-engineered for three fixed roles; constitution prohibits a configurable permission matrix.
- String column: rejected — no compile-time validation; enum enforces the three-value constraint at the DB level.

---

## Decision 6: Invite Flow — Tenant Manager Creates Tenant Admins

**Decision**: Self-registration via `POST /auth/register` always creates `member` role. `tenant_admin` accounts are created exclusively via `POST /platform/tenants/{tenant_id}/invite-admin` (requires `tenant_manager` JWT). The first `tenant_manager` is seeded via `scripts/seed_tenants.py`.

**Rationale**:
- Allowing self-registration as any role would let anyone gain admin access — that is a critical security hole.
- Seeding the first `tenant_manager` is the standard bootstrap pattern for platform admin accounts.
- The invite endpoint enforces that only a platform actor can elevate a user to tenant admin.

**Alternatives considered**:
- Allow self-registration with role in body but validate server-side: rejected — role field in the body creates confusion and a potential misconfiguration risk; cleaner to ignore/remove the field entirely.
- Pre-registration invite tokens (email verification): out of scope for Week 8; users are created active immediately per spec assumption.
