---
description: "Task list for Auth & Roles — fastapi-users JWT, three roles, Vault secret, Redis JTI revocation, invite flow, rate limiting"
---

# Tasks: Auth & Roles

**Input**: Design documents from `specs/002-auth-and-roles/`

**Branch**: `002-auth-and-roles`

**Owner**: Person A — `feature/platform-tenancy`

---

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no shared state)
- **[Story]**: Maps to user stories in spec.md (US1–US6)
- **~~strikethrough~~**: Already implemented

---

## Phase 1: Setup

**Purpose**: Add the fastapi-users dependency and prepare the migration for the users table. Blocks everything.

- [X] T001 Add `fastapi-users[sqlalchemy]>=13.0,<14` to `backend/pyproject.toml` dependencies and run `uv pip install --system -e .`
- [X] T002 Create Alembic migration `backend/app/db/migrations/versions/0002_users_roles.py` — creates `user_role_enum` PostgreSQL ENUM, `users` table (fastapi-users columns + `role`, `tenant_id`, `created_at`), FK to `tenants.id`, CHECK constraint enforcing `tenant_manager → tenant_id IS NULL`, indexes on `tenant_id` and `role`

**Checkpoint**: `uv run alembic upgrade head` succeeds; `users` table exists with correct schema.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: fastapi-users wiring, User ORM model, schemas, and base security module. MUST be complete before any user story can be implemented.

**⚠️ CRITICAL**: No user story work can begin until this phase is complete.

- [X] T003 [P] Create `UserRole` string enum (`tenant_manager` | `tenant_admin` | `member`) and `User` SQLAlchemy model extending `fastapi_users_db_sqlalchemy.SQLAlchemyBaseUserTableUUID` with `role: Mapped[UserRole]` and `tenant_id: Mapped[UUID | None]` in `backend/app/models/user.py`
- [X] T004 [P] Create Pydantic schemas `UserRead`, `UserCreate`, `UserUpdate` in `backend/app/schemas/auth.py` — `UserRead` includes `id`, `email`, `role`, `tenant_id`, `is_active`; `UserCreate` strips any `role` field (always defaults to `member`); no PII beyond email
- [X] T005 [P] Create `UserDatabase`, `UserManager` (overrides `on_after_register`, `on_after_login`), `BearerTransport`, `JWTStrategy` (reads secret from `app.state.secrets["jwt_secret"]`), and fastapi-users `FastAPIUsers` instance in `backend/app/core/security.py`
- [X] T006 Wire fastapi-users routers (auth router with login/logout, users router with /me) into `backend/app/api/router.py` under `/auth` prefix; add `get_async_session` and `get_user_db` dependencies

**Checkpoint**: `POST /auth/login` returns a JWT; `GET /auth/me` returns 401 without token.

---

## Phase 3: User Story 1 — Register, Login, /auth/me, Logout (Priority: P1) 🎯 MVP

**Goal**: Full identity lifecycle — a user can register, log in, call /auth/me, and log out with immediate token invalidation.

**Independent Test**: Register → login → GET /auth/me (200 with profile) → logout → GET /auth/me with same token (401).

### Implementation for User Story 1

- [X] T007 [US1] Override `POST /auth/register` in `backend/app/api/routes/auth.py` to force `role=member` regardless of request body; return `UserRead` schema (201)
- [X] T008 [US1] Implement JTI revocation in `backend/app/core/security.py`: on logout write `revoked_jti:{jti}` to `app.state.redis` with TTL = remaining token lifetime (`exp - now`); on every authenticated request check Redis for the JTI and raise 401 `token_revoked` if found
- [X] T009 [US1] Override `get_current_user` in `backend/app/dependencies.py` to check Redis JTI blacklist via `app.state.redis` before returning the user; raise `HTTPException(401, detail="Token has been revoked", headers={"WWW-Authenticate": "Bearer"})` if JTI is blacklisted
- [X] T010 [US1] Implement `write_audit_event()` async helper in `backend/app/services/auth_service.py` — inserts into `audit_logs` table fire-and-forget (`asyncio.create_task`); catches and logs any DB errors without raising
- [X] T011 [US1] Wire `write_audit_event` calls in `UserManager.on_after_register` (action=`register`), `on_after_login` (action=`login`), logout handler (action=`logout`); write `failed_login` on `InvalidPasswordException` or `UserNotExists`
- [X] T012 [P] [US1] Write `test_register_creates_member_role` — POST /auth/register with `role=tenant_admin` in body; assert response role is `member` — in `backend/tests/test_auth.py`
- [X] T013 [P] [US1] Write `test_login_returns_jwt` — valid credentials return `access_token`; `test_protected_route_401_without_jwt` — missing token returns 401 with `code=auth_required` — in `backend/tests/test_auth.py`
- [X] T014 [P] [US1] Write `test_logout_revokes_token` — login, logout, reuse token → 401 with `code=token_revoked`; `test_jwt_payload_has_no_pii` — decode token and assert no `email` or `tenant_id` fields — in `backend/tests/test_auth.py`

**Checkpoint**: Full register → login → /auth/me → logout cycle works. Token is dead after logout.

---

## Phase 4: User Story 2 — Tenant Manager Platform Routes (Priority: P1)

**Goal**: `require_tenant_manager` dependency enforces that only `tenant_manager` role reaches `/platform/*` routes. `tenant_admin` gets 403. `tenant_manager` also gets 403 on tenant-content routes.

**Independent Test**: Login as `tenant_manager` → GET /platform/tenants → 200. Login as `tenant_admin` → same → 403. Login as `tenant_manager` → GET /tenant/config → 403.

### Implementation for User Story 2

- [X] T015 [US2] Implement `require_tenant_manager` dependency in `backend/app/dependencies.py`: checks `user.role == UserRole.tenant_manager`; raises `HTTPException(403, detail="Tenant manager role required", headers={"X-Error-Code": "permission_denied"})` otherwise
- [X] T016 [US2] Apply `Depends(require_tenant_manager)` to all existing `/platform/*` route stubs in `backend/app/api/routes/` (tenants.py, any platform admin routes); confirm `tenant_manager` passes and all other roles receive 403
- [X] T017 [P] [US2] Write `test_tenant_admin_403_on_platform_route` — tenant_admin JWT on GET /platform/tenants → 403 with `code=permission_denied`; `test_tenant_manager_403_on_tenant_content` — tenant_manager JWT on GET /tenant/config → 403 — in `backend/tests/test_auth.py`

**Checkpoint**: Role boundary between platform and tenant routes is enforced in both directions.

---

## Phase 5: User Story 3 — Tenant Admin RLS Context (Priority: P2)

**Goal**: `require_tenant_admin` dependency derives `tenant_id` from the user record, sets RLS context, and resets it unconditionally in `finally`. Request body `tenant_id` is never consulted.

**Independent Test**: Login as `tenant_admin` → GET /tenant/config with `{"tenant_id": "<other-tenant>"}` in body → returns own tenant's data only; RLS context is reset after request.

### Implementation for User Story 3

- [X] T018 [US3] Implement `require_tenant_admin` in `backend/app/dependencies.py`: checks `user.role in (UserRole.tenant_admin, UserRole.tenant_manager)` (manager blocked separately by route); reads `tenant_id` from `user.tenant_id`; raises 500 with structured log if `tenant_id` is None; calls `set_tenant_context(tenant_id)` on the DB session; wraps in `try/finally` calling `reset_tenant_context()` unconditionally
- [X] T019 [US3] Apply `Depends(require_tenant_admin)` to all `/tenant/*` and `/cms/*` route stubs in `backend/app/api/routes/`; confirm body `tenant_id` fields are absent from route signatures
- [X] T020 [P] [US3] Write `test_tenant_id_never_from_request_body` — confirm route handler signatures for all tenant-scoped routes contain no `tenant_id` parameter sourced from body; `test_rls_context_reset_after_request` — verify `app.tenant_id` is cleared after request completes — in `backend/tests/test_auth.py`

**Checkpoint**: Tenant admin can access their own data; cross-tenant access is impossible via request body manipulation.

---

## Phase 6: User Story 4 — Tenant Manager Invites First Admin (Priority: P2)

**Goal**: `POST /platform/tenants/{tenant_id}/invite-admin` creates a `tenant_admin` user with the correct `tenant_id`. Self-registration cannot produce `tenant_admin` or `tenant_manager`.

**Independent Test**: Login as `tenant_manager` → POST /platform/tenants/{id}/invite-admin → 201 with `role=tenant_admin` and correct `tenant_id`. POST /auth/register with any role in body → always `member`.

### Implementation for User Story 4

- [X] T021 [US4] Implement `invite_admin(tenant_id, email, db, user_manager)` in `backend/app/services/auth_service.py` — creates user with `role=tenant_admin`, `tenant_id=tenant_id`, `is_active=True`; raises 404 if tenant does not exist or is not active; raises 409 if email already registered; writes `invite_admin` audit event
- [X] T022 [US4] Implement `POST /platform/tenants/{tenant_id}/invite-admin` route in `backend/app/api/routes/platform.py` — `Depends(require_tenant_manager)`; calls `auth_service.invite_admin`; returns `UserRead` (201)
- [X] T023 [US4] Update `scripts/seed_tenants.py` to create the first `tenant_manager` user and at least one demo `tenant_admin` (with `tenant_id` FK set correctly) so the stack has seeded auth data on first `docker compose up`
- [X] T024 [P] [US4] Write `test_invite_admin_creates_correct_role` — tenant_manager invites email → response has `role=tenant_admin` and correct `tenant_id`; `test_self_registration_cannot_elevate_role` — register with `role=tenant_manager` in body → response has `role=member` — in `backend/tests/test_auth.py`

**Checkpoint**: Only `tenant_manager` can create `tenant_admin` accounts. Seeded data is available on stack startup.

---

## Phase 7: User Story 5 — Login Rate Limiting (Priority: P2)

**Goal**: More than 10 failed login attempts from the same IP in a 15-minute window returns 429 with `Retry-After`.

**Independent Test**: Send 11 POST /auth/login requests with wrong credentials from same IP → 11th returns 429 with `Retry-After` header.

### Implementation for User Story 5

- [X] T025 [US5] Implement `check_login_rate_limit(ip: str, redis: aioredis.Redis)` in `backend/app/services/auth_service.py` — atomic `INCR` on `login_attempts:{ip}`; sets `EXPIRE 900` on first write; raises `HTTPException(429, headers={"Retry-After": str(ttl)})` if count > 10; resets counter on successful login
- [X] T026 [US5] Call `check_login_rate_limit` in the login route handler before credential validation (in `backend/app/api/routes/auth.py` or `UserManager.authenticate` override); pass `Depends(get_redis)` for the Redis client
- [X] T027 [P] [US5] Write `test_login_rate_limit` — mock Redis; simulate 11 failed attempts; assert 429 with `Retry-After` header on 11th — in `backend/tests/test_auth.py`

**Checkpoint**: Brute-force login is blocked at 10 attempts per 15-minute window.

---

## Phase 8: User Story 6 — Vault Startup Sentinel Check (Priority: P2)

**Goal**: In non-local environments, if the JWT secret is the placeholder value, the API refuses to start.

**Independent Test**: Set `APP_ENV=staging` and `jwt_secret=change-me-local-dev-only` in `app.state.secrets`; confirm app lifespan raises `RuntimeError` and the process exits non-zero.

### Implementation for User Story 6

- [X] T028 [US6] Add `enforce_jwt_secret(settings, secrets)` check in `backend/app/main.py` lifespan — after `app.state.secrets` is populated: if `settings.APP_ENV != "local"` and `app.state.secrets["jwt_secret"] == "change-me-local-dev-only"`, raise `RuntimeError("JWT secret is the placeholder value — refusing to start in non-local environment")`

**Checkpoint**: Deploying to staging/prod with a placeholder secret is impossible; local development is unaffected.

---

## Phase 9: Polish & Cross-Cutting Concerns

**Purpose**: Ensure 401/403 error shapes match platform contract, clean up, verify CI test coverage.

- [X] T029 [P] Verify all 401 responses include `{"detail": "...", "code": "auth_required" | "token_expired" | "invalid_token" | "token_revoked"}` — update fastapi-users exception handlers in `backend/app/core/errors.py` to map `fastapi_users` exceptions to the platform error contract
- [X] T030 [P] Verify all 403 responses include `{"detail": "...", "code": "permission_denied"}` — confirm `require_tenant_manager` and `require_tenant_admin` use the correct shape
- [X] T031 Remove `|| true` from pytest step in `.github/workflows/ci.yml` now that real auth tests exist (per T030 in 001 tasks.md)
- [X] T032 [P] Update `docs/ENGINEERING_RULES.md` reference: confirm `CORS is not authentication` note is visible in project documentation

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1 (Setup)**: No dependencies — start immediately
- **Phase 2 (Foundational)**: Depends on Phase 1 (migration + pyproject) — **BLOCKS all user stories**
- **Phase 3 (US1)**: Depends on Phase 2 — MVP deliverable
- **Phase 4 (US2)**: Depends on Phase 2 + Phase 3 (`get_current_user` must exist)
- **Phase 5 (US3)**: Depends on Phase 3 (`get_current_user`, `set_tenant_context`)
- **Phase 6 (US4)**: Depends on Phase 3 (`require_tenant_manager`) + tenant model from 001
- **Phase 7 (US5)**: Depends on Phase 3 (login endpoint exists)
- **Phase 8 (US6)**: Depends on Phase 2 (lifespan `app.state.secrets`)
- **Phase 9 (Polish)**: Depends on all phases complete

### User Story Dependencies

- **US1 (P1)**: No inter-story dependencies — first and most critical
- **US2 (P1)**: Depends on US1 (`get_current_user` stub must be real before role check works)
- **US3 (P2)**: Depends on US1 (`get_current_user`, `set_tenant_context`)
- **US4 (P2)**: Depends on US2 (`require_tenant_manager` must exist)
- **US5 (P2)**: Depends on US1 (login endpoint must exist)
- **US6 (P2)**: Independent of user stories — depends only on Foundational phase

### Within Each Phase

- T003, T004, T005 in Phase 2 are parallel (different files)
- Tests within each story (marked `[P]`) can be written in parallel with implementation
- T021 (service) before T022 (route) in US4

### Parallel Opportunities

```bash
# Phase 2 — all parallel:
Developer: T003 (User model)
Developer: T004 (Pydantic schemas)
Developer: T005 (fastapi-users config)

# After Phase 2:
Developer A: Phase 3 (US1 — login/logout/revocation)
Developer B: Phase 8 (US6 — Vault sentinel, touches only main.py)
```

---

## Implementation Strategy

### MVP First (US1 only)

1. Complete Phase 1: Setup (T001–T002)
2. Complete Phase 2: Foundational (T003–T006)
3. Complete Phase 3: US1 register/login/logout (T007–T014)
4. **STOP and VALIDATE**: full auth lifecycle works end-to-end
5. Push — Phase 3 is a shippable increment

### Full Feature Delivery

1. Setup + Foundational → JWT auth working
2. US1 → identity lifecycle complete
3. US2 → role enforcement on platform routes
4. US3 → tenant_admin RLS context wiring
5. US4 → invite flow + seed data
6. US5 + US6 → rate limiting + Vault sentinel
7. Polish → error shapes, CI cleanup

---

## Notes

- `[P]` tasks touch different files and have no shared state — safe to parallelize
- fastapi-users v13 uses `SQLAlchemyUserDatabase` with async sessions — ensure `get_async_session` dependency is wired correctly from 001's `get_db_session`
- The `JWTStrategy` secret is read lazily from `app.state.secrets` — it must not be read at import time (would bypass the Vault fetch)
- `write_audit_event` MUST use `asyncio.create_task` — never `await` in the request path (fire-and-forget per spec FR-021)
- Do not remove the `UserManager.on_after_register` hook even if it only writes an audit log — it is the correct extension point for future email verification
- T031 (remove `|| true` from pytest) is gated on tests actually existing — do it as part of this feature's PR
