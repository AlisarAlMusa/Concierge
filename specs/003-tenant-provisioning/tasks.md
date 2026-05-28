# Tasks: Tenant Provisioning

**Input**: Design documents from `specs/003-tenant-provisioning/`

**Prerequisites**: plan.md ✅ | spec.md ✅ | research.md ✅ | data-model.md ✅ | contracts/platform-api.md ✅

**Format**: `[ID] [P?] [Story?] Description with file path`
- **[P]**: Can run in parallel (different files, no shared dependencies)
- **[Story]**: User story this task belongs to (US1–US5)

---

## Phase 1: Setup (No-op — Already Done)

The project, Docker Compose, DB schema, ORM models, auth dependencies, and base route structure all exist from specs 001–002. No project initialization needed.

**Checkpoint**: Skip directly to Foundational phase.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Repository layer, schema fixes, and audit schema — all user stories depend on these.

**⚠️ CRITICAL**: No user story implementation can begin until this phase is complete.

- [ ] T001 Implement `TenantRepository` in `backend/app/repositories/tenant_repository.py` with: `create_tenant`, `get_tenant`, `get_all_tenants` (excludes deleted), `update_tenant_status`, `get_usage_summary` (SUM query on cost_events)
- [ ] T002 [P] Implement `AuditRepository` in `backend/app/repositories/audit_repository.py` with: `list_audit_logs(session, limit, offset, tenant_id=None)`
- [ ] T003 [P] Fix slug validator in `backend/app/schemas/tenant.py`: change regex to `^[a-z0-9][a-z0-9-]*[a-z0-9]$` (reject underscores, enforce min 2 chars, per FR-012)
- [ ] T004 [P] Add `TenantUsageSummary` Pydantic schema to `backend/app/schemas/tenant.py` with fields: `tenant_id: UUID`, `total_input_tokens: int`, `total_output_tokens: int`, `total_cost_usd: Decimal`
- [ ] T005 [P] Create `backend/app/schemas/audit_log.py` with `AuditLogRead` schema: `id`, `actor_user_id`, `actor_role`, `tenant_id`, `action`, `target_type`, `target_id`, `metadata_`, `created_at`; `model_config = {"from_attributes": True}`

**Checkpoint**: Foundational complete — all user story phases can now proceed.

---

## Phase 3: User Story 1 — Tenant Manager Creates a New Tenant (Priority: P1) 🎯 MVP

**Goal**: `tenant_manager` can call `POST /platform/tenants` with `{name, slug}` and receive a 201 with the created tenant. Duplicate slug → 409. Wrong role → 403. Audit event `tenant_created` is written.

**Independent Test**: Authenticate as `tenant_manager`; `POST /platform/tenants` with unique slug → 201 TenantRead with `status=active`. Re-send same slug → 409. Authenticate as `tenant_admin` and call same route → 403. Check audit_logs table for `tenant_created` entry.

- [ ] T006 [US1] Implement `TenantService.create_tenant(session, name, slug, actor_id, actor_role) -> Tenant` in `backend/app/services/tenant_service.py`: call `TenantRepository.create_tenant`, catch DB unique-constraint violation → raise 409, call `write_audit_event("tenant_created", ...)` fire-and-forget
- [ ] T007 [US1] Add `POST /` route to `backend/app/api/routes/tenants.py`: body=`TenantCreate`, response=`TenantRead` (201), guard=`require_tenant_manager`, delegates to `TenantService.create_tenant`
- [ ] T008 [US1] Add `GET /{tenant_id}` route to `backend/app/api/routes/tenants.py`: response=`TenantRead`, returns 404 for deleted/missing tenants

**Checkpoint**: `POST /platform/tenants` and `GET /platform/tenants/{id}` are fully functional.

---

## Phase 4: User Story 2 — Tenant Manager Invites First Tenant Admin (Priority: P1)

**Goal**: After creating a tenant, `tenant_manager` calls `POST /platform/tenants/{id}/invite-admin` with an email and a `tenant_admin` user is created with the correct `tenant_id`. The invited user can then log in and access `/tenant/config` for their own tenant only.

**Independent Test**: Create a tenant via T007; call invite-admin with a new email → 201 UserRead with `role=tenant_admin` and `tenant_id`. Verify `POST /auth/login` works for the new user. Verify they can reach `/tenant/config`. Duplicate email → 409. Invite on suspended tenant → 422.

**Note**: `invite_admin()` in `auth_service.py` is already implemented. The route stub exists. This phase wires remaining edge cases and confirms the full flow.

- [ ] T009 [US2] Verify and harden `POST /{tenant_id}/invite-admin` in `backend/app/api/routes/tenants.py`: confirm suspended-tenant check returns 422 with `{"detail": "...", "X-Error-Code": "tenant_not_active"}` (currently `auth_service.invite_admin` raises 404 for non-active — align with spec edge case: suspended → 422 not 404)
- [ ] T010 [US2] Update `auth_service.invite_admin` in `backend/app/services/auth_service.py`: if tenant exists but `status == suspended` → raise 422 with error code `tenant_not_active`; if tenant missing → raise 404; if email duplicate → raise 409 (already done)

**Checkpoint**: Invite flow covers all acceptance scenarios including suspended-tenant edge case.

---

## Phase 5: User Story 3 — Tenant Manager Suspends and Reactivates a Tenant (Priority: P2)

**Goal**: `tenant_manager` can suspend/reactivate tenants. Suspended tenant's users immediately get 403 `tenant_suspended` on all authenticated requests.

**Independent Test**: Create tenant + admin user; suspend via `POST /platform/tenants/{id}/suspend`; confirm tenant_admin JWT for that tenant hits `/tenant/config` → 403 `tenant_suspended`. Reactivate; confirm access restores.

- [ ] T011 [US3] Implement `TenantService.suspend_tenant(session, tenant_id, actor_id) -> Tenant` and `TenantService.reactivate_tenant(session, tenant_id, actor_id) -> Tenant` in `backend/app/services/tenant_service.py`: idempotent suspend (return 200 if already suspended); raise 422 if deleting/deleted; `write_audit_event` for each; use `TenantRepository.update_tenant_status`
- [ ] T012 [US3] Add `POST /{tenant_id}/suspend` and `POST /{tenant_id}/reactivate` routes to `backend/app/api/routes/tenants.py`: both guarded by `require_tenant_manager`, response=`TenantRead`
- [ ] T013 [US3] Add suspension enforcement to `require_tenant_admin` in `backend/app/dependencies.py`: after confirming `role == tenant_admin` and `tenant_id` is not None, load the tenant row and raise 403 (`X-Error-Code: tenant_suspended`) if `status == suspended`; raise 403 if `status in (deleting, deleted)`

**Checkpoint**: Suspension takes effect within one request; reactivation restores access immediately.

---

## Phase 6: User Story 4 — Tenant Manager Views Tenant List and Usage (Priority: P2)

**Goal**: `tenant_manager` can list all tenants and view aggregate cost/token metrics. No private content (conversations, CMS, leads) is ever returned.

**Independent Test**: Authenticate as `tenant_manager`; `GET /platform/tenants` → list with only `id, name, slug, status, created_at, updated_at` — no content fields. `GET /platform/tenants/{id}/usage-summary` → `{total_input_tokens, total_output_tokens, total_cost_usd}` with no message content. Authenticate as `tenant_admin`; both routes → 403.

- [ ] T014 [US4] Replace stub body of `GET /` in `backend/app/api/routes/tenants.py`: call `TenantService.list_tenants(session)`, return `list[TenantRead]`
- [ ] T015 [P] [US4] Implement `TenantService.list_tenants(session) -> list[Tenant]` and `TenantService.get_usage_summary(session, tenant_id) -> TenantUsageSummary` in `backend/app/services/tenant_service.py`
- [ ] T016 [P] [US4] Add `GET /{tenant_id}/usage-summary` route to `backend/app/api/routes/tenants.py`: response=`TenantUsageSummary`, guarded by `require_tenant_manager`
- [ ] T017 [US4] Create `backend/app/api/routes/audit_logs.py` with `GET /platform/audit-logs` route: query params `limit` (default 50, max 200), `offset` (default 0), optional `tenant_id` filter; response=`list[AuditLogRead]`; guarded by `require_tenant_manager`; delegates to `AuditRepository.list_audit_logs`
- [ ] T018 [US4] Register `audit_logs.router` in `backend/app/api/router.py` at prefix `/platform`

**Checkpoint**: List, usage-summary, and audit-log routes all work and return no private content.

---

## Phase 7: User Story 5 — Tenant Manager Deletes a Tenant (Priority: P3)

**Goal**: `DELETE /platform/tenants/{id}` sets status to `deleting` and fires the erasure service async. Tenant is unreachable after deletion. Operator never reads content.

**Independent Test**: `DELETE /platform/tenants/{id}` → 202 `{"status": "deleting", "tenant_id": "..."}`. Tenant status in DB is `deleting`. Audit event `tenant_delete_triggered` is written. Any subsequent call with a `deleted` tenant ID → 404.

- [ ] T019 [US5] Implement `TenantService.delete_tenant(session, tenant_id, actor_id) -> Tenant` in `backend/app/services/tenant_service.py`: set status=`deleting`; fire `asyncio.create_task(erasure_service.purge_tenant(tenant_id))`; write `tenant_delete_triggered` audit event; return 409 if already `deleting`/`deleted`
- [ ] T020 [US5] Add `DELETE /{tenant_id}` route to `backend/app/api/routes/tenants.py`: response 202 with `{"status": "deleting", "tenant_id": str(tenant_id)}`; guarded by `require_tenant_manager`

**Checkpoint**: Delete flow is fully functional; erasure fires in background; deleted tenants return 404.

---

## Phase 8: Polish & Cross-Cutting Concerns

- [ ] T021 [P] Write `backend/tests/test_tenant_provisioning.py` covering all acceptance scenarios: US1 create+duplicate+403, US2 invite+409+422, US3 suspend+403+reactivate, US4 list-no-content+usage-summary+audit-log+403, US5 delete+deleting+404-after; use async test client pattern from `tests/test_auth.py`
- [ ] T022 [P] Run `uv run ruff check .` and `uv run black --check .` from `backend/`; fix any lint/format issues introduced by new files
- [ ] T023 Run `uv run pytest backend/tests/test_tenant_provisioning.py -v` from `backend/`; confirm all acceptance tests pass

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 2 (Foundational)**: No story deps. T001–T005 can all be parallelised across files.
- **Phase 3 (US1)**: Requires T001 (TenantRepository). T006, T007, T008 sequential within US1.
- **Phase 4 (US2)**: Requires T001. Can start after Phase 2 completes; independent of US1.
- **Phase 5 (US3)**: Requires T001 + T013 (dependency update). T011 before T012; T013 is independent.
- **Phase 6 (US4)**: Requires T001, T002. T014 depends on T015; T016/T017 independent.
- **Phase 7 (US5)**: Requires T001. T019 before T020.
- **Phase 8 (Polish)**: Requires all implementation phases complete.

### Within-Phase Parallel Opportunities

**Phase 2** — all tasks touch different files:
```
T001 tenant_repository.py  ||  T002 audit_repository.py
T003 fix slug validator     ||  T004 TenantUsageSummary
T005 AuditLogRead schema
```

**Phase 5** — T011 and T013 touch different files:
```
T011 tenant_service.py (suspend/reactivate)  ||  T013 dependencies.py (suspension check)
```

**Phase 6** — T015, T016, T017 touch different files:
```
T015 tenant_service.py  ||  T016 tenants.py usage route  ||  T017 audit_logs.py new route
```

---

## Implementation Strategy

### MVP (US1 + US2 only — Stories already worth demoing)

1. Complete Phase 2 (foundational) — ~30 min
2. Complete Phase 3 (US1: create tenant) — ~20 min
3. US2 is largely done; run T009–T010 to close edge cases — ~15 min
4. **STOP**: Demo tenant create + admin invite end-to-end

### Full Delivery Order

Phase 2 → Phase 3 → Phase 4 → Phase 5 → Phase 6 → Phase 7 → Phase 8

### Parallel Team Strategy (if two people)

- Person A: T001, T006, T007, T008, T011, T012, T019, T020
- Person B: T002, T003, T004, T005, T009, T010, T013, T014, T015, T016, T017, T018

---

## Notes

- No new Alembic migrations needed — all tables exist in `0001_initial`.
- `write_audit_event` is fire-and-forget; a failed write logs a warning and never rolls back the provisioning action.
- `require_tenant_admin` suspension check adds one DB round-trip per tenant-admin request — acceptable; no caching needed in Week 8.
- Slug immutability: no `PATCH slug` route; `TenantUpdate` schema intentionally omits slug.
- The `erasure_service.purge_tenant` call in T019 will be a no-op stub until spec 015 is implemented — that is fine.
