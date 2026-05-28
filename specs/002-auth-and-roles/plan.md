# Implementation Plan: Auth & Roles

**Branch**: `feature/platform-tenancy` | **Date**: 2026-05-27 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/002-auth-and-roles/spec.md`

---

## Summary

Implements email/password authentication via fastapi-users with a JWT Bearer transport, layered with three fixed roles (`tenant_manager`, `tenant_admin`, `member`). The JWT signing secret is sourced from `app.state.secrets["jwt_secret"]` (populated from Vault at startup). Logout is enforced via Redis JTI revocation. `tenant_id` is always derived from the authenticated user record — never from the request body. The `require_tenant_admin` dependency sets `app.tenant_id` for RLS and resets it unconditionally in `finally`. Login is rate-limited per IP via Redis. Tenant admin accounts are created exclusively via the invite endpoint; self-registration always produces `member` role.

---

## Technical Context

**Language/Version**: Python 3.11 (container)

**Primary Dependencies**: FastAPI, fastapi-users[sqlalchemy] ≥13.0, SQLAlchemy 2.x async, asyncpg, Pydantic v2, pydantic-settings, redis ≥5.0, structlog, tenacity, httpx

**Storage**: PostgreSQL 16 (users table, audit_logs); Redis 7 (JTI revocation blacklist, login rate-limit counters)

**Testing**: pytest, pytest-asyncio, httpx AsyncClient (test client), factory-boy or inline fixtures for users

**Target Platform**: Linux container (FastAPI backend service)

**Project Type**: Multi-tenant web service — authentication and authorisation layer

**Performance Goals**: Auth endpoints < 100 ms p95 under normal load; logout revocation visible to all requests within 1 second

**Constraints**:
- JWT payload MUST NOT contain email, tenant_id, or PII — only `sub`, `role`, `jti`, `exp`
- `tenant_id` MUST never be read from request body or query params
- Vault-sourced `jwt_secret` required in non-local environments; placeholder value causes startup refusal
- No `os.getenv` outside `core/config.py`
- All I/O (Redis, DB) MUST be async

**Scale/Scope**: Week-8 skeleton; token volume ~hundreds/day for demo; Redis blacklist entries are small (JTI string + TTL)

---

## Constitution Check

*Gate evaluated against [constitution.md](../../.specify/memory/constitution.md) v1.0.0*

| Principle | Status | Notes |
|-----------|--------|-------|
| I. Tenant Isolation | ✅ Pass | `tenant_id` derived exclusively from `user.tenant_id` in `get_current_user`; `require_tenant_admin` calls `set_tenant_context` and resets in `finally`; `tenant_manager` has no RLS bypass on content tables |
| II. Clean Layered Architecture | ✅ Pass | Routes → Services → Repositories → Models; fastapi-users provides identity plumbing; custom role layer in `dependencies.py`; no SQL in routes; no business logic in repositories |
| III. Security by Default | ✅ Pass | JWT secret from Vault; Redis JTI revocation; login rate limiting; service-to-service auth separate from user JWTs; CORS is not authentication; no PII in JWT payload |
| IV. Async All the Way Down | ✅ Pass | fastapi-users async SQLAlchemy backend; async Redis operations; async audit log writes; no blocking calls |
| V. Lean Containers — No Torch | ✅ Pass | Auth has no ML dependencies; no torch or transformers introduced |
| VI. Evals Are the Grade | ✅ Pass | 10 named auth/permission tests required by spec (Testing Requirements section); run in CI |

**Post-design re-check**: No new violations. RLS context set/reset pattern is the same as established in 001-platform-foundation.

---

## Project Structure

### Documentation (this feature)

```text
specs/002-auth-and-roles/
├── plan.md              # This file
├── research.md          # Key decisions: JWT transport, revocation, rate limiting, Vault sentinel
├── data-model.md        # User table, Role enum, Redis key schemas, AuditLog auth events
├── contracts/
│   └── auth-api.md      # Full endpoint contracts: request/response schemas, error shapes
└── tasks.md             # Phase 2 output (/speckit-tasks — not created here)
```

### Source Code (repository root)

```text
backend/
├── app/
│   ├── api/
│   │   └── routes/
│   │       └── auth.py                         # POST /auth/register, /auth/login, /auth/logout, GET /auth/me
│   ├── core/
│   │   └── security.py                         # JWT create/verify; JTI revocation check; startup secret guard
│   ├── db/
│   │   └── migrations/versions/
│   │       └── 0002_users_roles.py             # fastapi-users tables + role enum column + tenant_id FK
│   ├── models/
│   │   └── user.py                             # User ORM model: role enum, tenant_id (nullable), is_active
│   ├── repositories/
│   │   └── user_repository.py                  # get_by_email, get_by_id (tenant-unscoped — users are platform-level)
│   ├── schemas/
│   │   └── auth.py                             # RegisterRequest, UserResponse, TokenResponse
│   ├── services/
│   │   └── auth_service.py                     # invite_admin(), write_audit_event(), enforce_startup_secret()
│   └── dependencies.py                         # updated: get_current_user, require_tenant_manager,
│                                               # require_tenant_admin (with RLS set/reset)
├── tests/
│   └── test_auth.py                            # 10 required tests from spec Testing Requirements section
```

**Structure Decision**: Single backend service. Auth routes are a new file `api/routes/auth.py`. The `dependencies.py` already exists (scaffolded in 001); this feature fills in `get_current_user` which was a 501 stub.

---

## Implementation Decisions (from research.md)

### JWT Transport: Bearer only
Use fastapi-users `JWTStrategy` + `BearerTransport`. Cookie transport is not used — the Streamlit admin app and service-to-service clients work better with explicit Bearer headers. See [research.md](./research.md).

### Logout: Redis JTI Blacklist
JWTs are stateless — invalidation requires a revocation store. On `POST /auth/logout`, store `revoked_jti:{jti}` in Redis with TTL = remaining token lifetime. Middleware checks the blacklist on every request. Alternative (short expiry, no revocation) rejected: users must be able to log out immediately. See [research.md](./research.md).

### Rate Limiting: Redis counter per IP
Increment `login_attempts:{ip}` in Redis; expire after 15-minute window; reject at > 10. Uses the same `app.state.redis` singleton from 001-platform-foundation. No new dependency. See [research.md](./research.md).

### Vault Sentinel Check
In `APP_ENV != local`, if `app.state.secrets["jwt_secret"] == "change-me-local-dev-only"`, raise `RuntimeError` at startup before any route is served. This is simpler than a full Vault availability check and catches the real failure mode: starting with a weak default. See [research.md](./research.md).

### Role Storage: Enum column on User
Three fixed roles stored as a `role` enum column on the `users` table. No RBAC table, no permission matrix. fastapi-users `User` model is extended with `role: UserRole` and `tenant_id: UUID | None`. See [data-model.md](./data-model.md).

### tenant_id Derivation
`get_current_user` dependency resolves the user from JWT `sub` and returns the full ORM object. Downstream dependencies (`require_tenant_admin`) read `user.tenant_id`. The request body is never consulted. This is enforced by the dependency signature — routes that need tenant context accept `user: User = Depends(require_tenant_admin)`, not a `tenant_id: UUID` parameter.

---

## Open Questions / Risks

| Item | Decision | Risk |
|------|----------|------|
| fastapi-users version ≥13 breaking changes | Pin to `fastapi-users[sqlalchemy]>=13.0,<14` | API changes between minor versions |
| Redis unavailable at logout | Log warning, return 200 anyway — logout should not fail for users | Revoked token may be reusable until Redis recovers (acceptable for dev) |
| Audit log table exists from 001 migration | Confirmed — `0001_initial.py` creates `audit_logs` | None |
| `member` role routes | Spec says member = visitor-facing routes only; none are built in this feature | No blocker — enforced by absence of `member`-accessible routes until Person B builds them |

---

## Complexity Tracking

No constitution violations requiring justification. All decisions are within the established stack.
