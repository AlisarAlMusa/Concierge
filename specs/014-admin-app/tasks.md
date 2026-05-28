---
description: "Task list for Spec 014 — Admin App (Streamlit)"
---

# Tasks: Admin App (Streamlit)

**Input**: `/specs/014-admin-app/`

**Prerequisites**: plan.md ✅ | spec.md ✅ | data-model.md ✅ | contracts/ ✅ | research.md ✅ | quickstart.md ✅

**Organization**: Tasks grouped by user story to enable independent implementation and testing.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (US1–US5 + US-MGR for Tenant Manager view)

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Verify dependencies, clean up stubs, establish the shared API client skeleton.

- [X] T001 Verify `admin_app/pyproject.toml` has `streamlit>=1.35`, `httpx>=0.27`, `pydantic>=2.7` — add any missing deps
- [X] T002 Create `admin_app/api_client.py` with `APIError` exception class and `APIClient.__init__(base_url, token)` using `httpx.Client`
- [X] T003 Add `_request(method, path, **kwargs)` private method to `APIClient` in `admin_app/api_client.py` — injects `Authorization: Bearer` header, catches all httpx exceptions, maps status codes to `APIError` with user-readable messages (401 → "Session expired", 403 → "Permission denied", 422 → "Validation error", 5xx → "API unavailable", network error → "Cannot reach the API")
- [X] T004 Add `API_BASE_URL = os.environ.get("API_BASE_URL", "http://api:8000")` constant to `admin_app/api_client.py` and a module-level `get_client()` helper that reads `st.session_state.get("token")`

**Checkpoint**: `APIClient` base class ready — all story phases can import and extend it.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Auth methods + `require_auth()` guard that every page depends on.

**⚠️ CRITICAL**: All user story pages depend on `require_auth()` and the auth API methods.

- [X] T005 Implement `APIClient.login(email, password)` in `admin_app/api_client.py` — `POST /auth/login`, returns `{"access_token": str}`; raises `APIError` on failure
- [X] T006 Implement `APIClient.me()` in `admin_app/api_client.py` — `GET /auth/me`, returns user dict with `id`, `email`, `role`, `tenant_id`
- [X] T007 Implement `APIClient.logout()` in `admin_app/api_client.py` — `POST /auth/logout`, best-effort (does not raise on failure)
- [X] T008 Add `require_auth(allowed_roles=None)` helper function to `admin_app/api_client.py` — checks `st.session_state.get("token")`; if missing calls `st.switch_page("app.py")` + `st.stop()`; if `allowed_roles` given, checks `st.session_state.get("user_role")` against the list

**Checkpoint**: Auth layer ready — login, session guard, and logout all functional.

---

## Phase 3: User Story 1 — Tenant Admin Logs In and Sees Their Dashboard (Priority: P1) 🎯 MVP

**Goal**: A tenant admin logs in, session state is populated with token + role + tenant_id, and they land on their tenant dashboard. Wrong credentials show an error. `tenant_manager` role gets redirected to the platform view. Session is cleared on logout.

**Independent Test**: Log in as Tenant A admin → confirm Dashboard shows Tenant A name. Log in as Tenant B admin → confirm Tenant B data. Wrong password → error banner, no data visible.

### Implementation for User Story 1

- [X] T009 [US1] Rewrite `admin_app/app.py` as a login form using `st.form`: email + password inputs, submit button; on success call `client.login()` + `client.me()`, populate `st.session_state["token"]`, `["user_email"]`, `["user_role"]`, `["tenant_id"]`, then call `st.rerun()`
- [X] T010 [US1] Add role-based `st.navigation()` to `admin_app/app.py` — builds page list from `user_role`: `tenant_admin` gets Dashboard + CMS + Agent Config + Leads + Escalations + Embed Snippet; `tenant_manager` gets Tenant Manager only; unauthenticated shows only the login form
- [X] T011 [US1] Add logout button to sidebar in `admin_app/app.py` (shown only when logged in) — calls `client.logout()`, clears `st.session_state`, calls `st.rerun()`
- [X] T012 [US1] Create `admin_app/pages/dashboard.py` — calls `client.me()` for user info; calls `GET /tenant/usage-summary` for cost stats; displays tenant name, CMS page count (from `client.list_cms_pages()`), usage totals; catches `APIError(401)` and redirects to login
- [X] T013 [US1] Add `require_auth()` call at the top of every existing page stub in `admin_app/pages/` (cms.py, agent_config.py, leads.py, embed_snippet.py, tenant_manager.py) before any other code

**Checkpoint**: US1 fully functional — login, dashboard, logout, session guard all working. Role-based nav hides Tenant Manager from tenant admins.

---

## Phase 4: User Story 2 — Tenant Admin Manages CMS Content (Priority: P1)

**Goal**: Tenant admin can list, create, edit, publish, and reindex CMS pages. All operations go through the API. Changes are reflected immediately.

**Independent Test**: Create a page in the admin app → query `GET /cms/` directly → confirm page exists with correct status. Edit and publish → confirm status = "published".

### Implementation for User Story 2

- [X] T014 [P] [US2] Implement `APIClient.list_cms_pages()` in `admin_app/api_client.py` — `GET /cms/`, decorated with `@st.cache_data(ttl=30)`; returns `[]` on 404 or empty
- [X] T015 [P] [US2] Implement `APIClient.get_cms_page(page_id)` in `admin_app/api_client.py` — `GET /cms/{page_id}`, returns full page dict including `body`
- [X] T016 [P] [US2] Implement `APIClient.create_cms_page(title, body, slug, status)` in `admin_app/api_client.py` — `POST /cms/`, calls `st.cache_data.clear()` on success
- [X] T017 [P] [US2] Implement `APIClient.update_cms_page(page_id, **fields)` in `admin_app/api_client.py` — `PUT /cms/{page_id}`, calls `st.cache_data.clear()` on success
- [X] T018 [P] [US2] Implement `APIClient.delete_cms_page(page_id)` in `admin_app/api_client.py` — `DELETE /cms/{page_id}`, calls `st.cache_data.clear()` on success
- [X] T019 [P] [US2] Implement `APIClient.reindex_cms_page(page_id)` in `admin_app/api_client.py` — `POST /cms/{page_id}/reindex`; if endpoint returns 404 (stub), show warning "Reindex not yet available"
- [X] T020 [US2] Implement CMS list view in `admin_app/pages/cms.py` — `st.dataframe` or `st.table` of pages with columns: title, slug, status, updated_at; each row has Edit / Publish / Delete buttons using `st.button` with unique keys
- [X] T021 [US2] Add "New Page" section to `admin_app/pages/cms.py` — `st.expander("+ New Page")` containing a form with title, slug, body (st.text_area), status select; on submit calls `client.create_cms_page()`, shows success/error
- [X] T022 [US2] Add edit inline form in `admin_app/pages/cms.py` — when Edit clicked, show `st.expander` with pre-filled fields; on save calls `client.update_cms_page()`, closes expander
- [X] T023 [US2] Add Publish/Unpublish toggle in `admin_app/pages/cms.py` — calls `client.update_cms_page(id, status="published"/"draft")`, shows spinner during call
- [X] T024 [US2] Add Reindex button in `admin_app/pages/cms.py` — calls `client.reindex_cms_page()` with `st.spinner("Reindexing…")`, shows success or warning message

**Checkpoint**: US2 fully functional — full CMS CRUD + publish + reindex working from admin app.

---

## Phase 5: User Story 3 — Tenant Admin Configures Agent and Guardrails (Priority: P2)

**Goal**: Tenant admin updates persona, allowed/blocked topics, refusal tone, and enabled tools. Platform rails are never shown or editable. Changes take effect on next chat request.

**Independent Test**: Change persona in admin app → send chat → confirm agent uses new persona. Add blocked topic → send chat about that topic → confirm refusal fires.

### Backend prerequisite (Person A)

- [ ] T025 [US3] Add `PATCH /tenant/config` endpoint to `backend/app/api/routes/admin_config.py` — accepts partial update of `persona`, `allowed_topics`, `blocked_topics`, `refusal_tone`, `enabled_tools`; requires `tenant_admin`; persists via `guardrail_config` service; returns updated config

### Admin App

- [X] T026 [P] [US3] Implement `APIClient.get_tenant_config()` in `admin_app/api_client.py` — `GET /tenant/config`; returns `{}` if config not yet set
- [X] T027 [P] [US3] Implement `APIClient.update_tenant_config(**fields)` in `admin_app/api_client.py` — `PATCH /tenant/config`; calls `st.cache_data.clear()` on success
- [X] T028 [US3] Implement `admin_app/pages/agent_config.py` — load current config with `client.get_tenant_config()`; show form sections: Persona (st.text_area), Allowed Topics (st.text_input + add/remove), Blocked Topics (same), Refusal Tone (st.selectbox), Enabled Tools (st.multiselect from fixed list); Save button calls `client.update_tenant_config()`
- [X] T029 [US3] Add platform-rail guard note in `admin_app/pages/agent_config.py` — `st.info("Platform safety rails (prompt injection, PII redaction) are managed by the platform and cannot be modified.")` visible above the form; no platform rail fields exposed

**Checkpoint**: US3 fully functional — agent config editable, platform rails not exposed.

---

## Phase 6: User Story 4 — Tenant Admin Views Leads and Escalations (Priority: P2)

**Goal**: Tenant admin views leads and escalations lists with detail, updates statuses. Pages degrade gracefully when Person B's routes are still stubs.

**Independent Test**: Create test leads/escalations via API. View in admin app. Update lead status. Confirm change persists (when Person B routes are live).

### Admin App

- [X] T030 [P] [US4] Implement `APIClient.list_leads(status=None)` in `admin_app/api_client.py` — `GET /leads/?status={status}`; returns `[]` on 404 or empty (stub-tolerant)
- [X] T031 [P] [US4] Implement `APIClient.update_lead(lead_id, status, notes=None)` in `admin_app/api_client.py` — `PATCH /leads/{lead_id}`; raises `APIError` on failure; shows "Not yet available" on 404
- [X] T032 [P] [US4] Implement `APIClient.list_escalations(status=None)` in `admin_app/api_client.py` — `GET /escalations/?status={status}`; returns `[]` on 404 or empty (stub-tolerant)
- [X] T033 [P] [US4] Implement `APIClient.update_escalation(escalation_id, status)` in `admin_app/api_client.py` — `PATCH /escalations/{escalation_id}`; raises `APIError` on failure
- [X] T034 [US4] Implement leads section in `admin_app/pages/leads.py` — `st.tabs(["Leads", "Escalations"])`; Leads tab: `st.dataframe` with columns name, email, intent_summary, score, status, created_at; status filter `st.selectbox`; clicking a row shows detail in `st.expander` with status update `st.selectbox` + Save button; if list empty shows `st.info("No leads captured yet.")`
- [X] T035 [US4] Implement escalations section in `admin_app/pages/leads.py` — Escalations tab: `st.dataframe` with columns conversation_id, reason, status, created_at; status update button per row; if list empty shows `st.info("No escalations yet.")`

**Checkpoint**: US4 fully functional — leads and escalations lists rendered; status updates work when Person B routes are live.

---

## Phase 7: User Story 5 — Tenant Admin Copies the Widget Embed Snippet (Priority: P2)

**Goal**: Tenant admin sees their widget's script tag pre-filled with `data-widget-id` and copies it with one click.

**Independent Test**: Navigate to Embed Snippet page. Confirm `data-widget-id` matches `GET /widgets/` response. Confirm copy button works.

### Admin App

- [X] T036 [P] [US5] Implement `APIClient.list_widgets()` in `admin_app/api_client.py` — `GET /widgets/`; returns `[]` if no widgets configured
- [X] T037 [US5] Implement `admin_app/pages/embed_snippet.py` — call `client.list_widgets()`; if empty show `st.warning("No widget configured yet.")`; for each widget display its name and the script tag in `st.code()` block; use `st.components.v1.html()` to render a copy-to-clipboard button via a small JS snippet that calls `navigator.clipboard.writeText()`

**Checkpoint**: US5 fully functional — embed snippet shown and copyable.

---

## Phase 8: Tenant Manager Platform View (FR-010, Priority: P2)

**Goal**: Tenant Manager sees tenant list with statuses, per-tenant usage summaries, and audit log. No tenant-admin pages are accessible.

**Independent Test**: Log in as Tenant Manager. Confirm only Tenant Manager page is visible. Confirm tenant list and usage summaries load. Confirm no cross-tenant content fields in usage summary.

### Admin App

- [X] T038 [P] [US-MGR] Implement `APIClient.list_tenants()` in `admin_app/api_client.py` — `GET /platform/tenants/`; raises `APIError(403)` if not tenant_manager
- [X] T039 [P] [US-MGR] Implement `APIClient.get_tenant_usage(tenant_id)` in `admin_app/api_client.py` — `GET /platform/tenants/{tenant_id}/usage-summary`
- [X] T040 [P] [US-MGR] Implement `APIClient.list_audit_logs(tenant_id=None)` in `admin_app/api_client.py` — `GET /platform/audit-logs?tenant_id={id}`
- [X] T041 [US-MGR] Implement `admin_app/pages/tenant_manager.py` — add `require_auth(allowed_roles=["tenant_manager"])` guard; `st.tabs(["Tenants", "Audit Logs"])`; Tenants tab: `st.dataframe` of tenants with name, status, created_at; clicking a row expands usage summary (total cost, per-operation breakdown); Audit Logs tab: `st.dataframe` of recent events with tenant filter

**Checkpoint**: Tenant Manager view fully functional — tenant list, usage summary, audit logs all visible.

---

## Phase 9: Polish & Cross-Cutting Concerns

- [X] T042 [P] Add `st.set_page_config(page_title="Concierge Admin", layout="wide", page_icon="🤖")` at top of `admin_app/app.py` if not already set
- [X] T043 [P] Add consistent sidebar footer to `admin_app/app.py` showing logged-in user email and role
- [X] T044 [P] Add `st.spinner()` wrappers around all API calls in every page that don't already have them — ensures loading feedback on slow network
- [X] T045 [P] Add `API_BASE_URL` to `.env.example` and docker-compose `admin_app` service uses `env_file: .env` which picks it up
- [ ] T046 Review all pages against SC-005: confirm no raw Python tracebacks can reach the user — all `APIError` catches must use `st.error()` + `st.stop()`
- [ ] T047 Run the quickstart.md smoke tests (scenarios 1–8) manually and confirm all pass

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — start immediately
- **Foundational (Phase 2)**: Depends on Phase 1 (needs `APIClient` base class)
- **US1 (Phase 3)**: Depends on Phase 2 (needs `login()`, `me()`, `require_auth()`) — **blocks all other pages**
- **US2 (Phase 4)**: Depends on Phase 3 (needs working auth + session state)
- **US3 (Phase 5)**: Depends on Phase 3 + backend T025 (`PATCH /tenant/config`)
- **US4 (Phase 6)**: Depends on Phase 3 — independent of US2/US3
- **US5 (Phase 7)**: Depends on Phase 3 — fully independent
- **US-MGR (Phase 8)**: Depends on Phase 3 — independent of US2–US5
- **Polish (Phase 9)**: Depends on all story phases complete

### User Story Dependencies

- **US1 (P1)**: Must complete first — all pages require auth session
- **US2 (P1)**: Depends only on US1 auth — no dependency on US3/US4/US5
- **US3 (P2)**: Depends on US1 + backend `PATCH /tenant/config` endpoint
- **US4 (P2)**: Depends on US1 only — stub-tolerant for Person B's routes
- **US5 (P2)**: Depends on US1 only — no dependency on other stories
- **US-MGR (P2)**: Depends on US1 only — uses different API endpoints

### Within Each User Story

- `api_client.py` methods [P] before page implementation
- Page implementation after API methods complete
- Backend route (T025) must land before US3 config saves work end-to-end

### Parallel Opportunities

- T001, T002, T003, T004 — all setup tasks: independent files, run together
- T005, T006, T007 — auth API methods: different methods, run together
- T014, T015, T016, T017, T018, T019 — all CMS api_client methods: different methods, run together
- T026, T027 — config api_client methods: run together
- T030, T031, T032, T033 — leads/escalation api_client methods: run together
- T036 — widget api_client: independent
- T038, T039, T040 — platform api_client methods: run together

---

## Parallel Example: User Story 2 (CMS)

```bash
# Launch all CMS api_client methods together (different functions, same file):
Task T014: "list_cms_pages()"
Task T015: "get_cms_page()"
Task T016: "create_cms_page()"
Task T017: "update_cms_page()"
Task T018: "delete_cms_page()"
Task T019: "reindex_cms_page()"

# After all T014–T019 complete, implement page sequentially:
Task T020 → T021 → T022 → T023 → T024
```

## Parallel Example: User Story 4 (Leads & Escalations)

```bash
# Launch all api_client methods together:
Task T030: "list_leads()"
Task T031: "update_lead()"
Task T032: "list_escalations()"
Task T033: "update_escalation()"

# Then implement the page:
Task T034 → T035
```

---

## Implementation Strategy

### MVP First (US1 + US2 — both P1)

1. Complete Phase 1: Setup (T001–T004)
2. Complete Phase 2: Foundational auth layer (T005–T008)
3. Complete Phase 3: Login + Dashboard (T009–T013)
4. Complete Phase 4: CMS Management (T014–T024)
5. **STOP and VALIDATE**: Tenant admin can login, create/publish CMS pages
6. Run quickstart scenarios 1, 2, 3

### Incremental Delivery

1. Setup + Auth → Login working → Deploy
2. CMS → Tenant admin can manage content → Demo
3. Agent Config → Guardrails configurable → Deploy
4. Leads + Escalations → Operational visibility → Deploy
5. Embed Snippet + Tenant Manager → Full feature complete → Demo

### Parallel Team Strategy

With US1 complete as the base:
- **Developer A**: US2 CMS (T014–T024)
- **Developer B**: US3 Agent Config (T025–T029) + backend T025
- **Developer C**: US4 Leads/Escalations (T030–T035) + US5 Embed Snippet (T036–T037)

---

## Notes

- [P] tasks = different files or different methods in the same file — no shared-state conflicts
- The `/developing-with-streamlit` skill handles all Streamlit-specific implementation details (session state, `st.navigation()`, `st.cache_data`, clipboard component)
- US4 leads/escalations pages are stub-tolerant — implement the UI fully, but Person B's routes may return 404 until their branch lands
- US3 requires backend T025 (`PATCH /tenant/config`) — this is a same-owner task (Person A); it must land before the config save button works end-to-end
- `tenant_id` is NEVER read from the request body in any admin app API call — the backend derives it from the JWT; the app just sends the token
