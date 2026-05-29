# Tasks: Public Tenant Website

**Input**: Design documents from `specs/019-public-tenant-site/`

**Prerequisites**: plan.md ✅ spec.md ✅ research.md ✅ data-model.md ✅ contracts/ ✅

**Organization**: Tasks grouped by user story for independent implementation and testing.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (US1–US4)

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Add missing dependencies and create directory scaffolding.

- [x] T001 Add `jinja2>=3.1` and `aiofiles>=23.0` to `backend/pyproject.toml` dependencies and run `uv sync` in `backend/`
- [x] T002 Create `backend/app/templates/` directory (Jinja2 template root for the api service)

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Data layer and shared infrastructure every user story depends on.

**⚠️ CRITICAL**: No user story work can begin until this phase is complete.

- [x] T003 Create `TenantConfig` SQLAlchemy model in `backend/app/models/tenant_config.py` — fields: `tenant_id` (PK, FK→tenants), `brand_name`, `theme_color`, `greeting`, `public_description`, `contact_email`, `allowed_origins`, `created_at`, `updated_at`; enable RLS on table
- [x] T004 Write Alembic migration `backend/app/db/migrations/versions/0006_tenant_config.py` — create `tenant_configs` table with FK, enable RLS, add policy using `current_setting('app.tenant_id')::uuid`
- [x] T005 [P] Add `get_tenant_by_slug(session, slug)` async function to `backend/app/repositories/tenant_repository.py` — exact match on `Tenant.slug`, returns `Tenant | None`
- [x] T006 [P] Implement `backend/app/repositories/cms_repository.py` — replace TODO stub with `list_published_pages(session, tenant_id) -> list[CmsPage]` scoped to `status = published` using `ix_cms_pages_tenant_status` index
- [x] T007 [P] Implement `backend/app/repositories/widget_repository.py` — replace TODO stub with `get_widget_by_tenant(session, tenant_id) -> Widget | None`
- [x] T008 Create Pydantic schemas in `backend/app/schemas/public_site.py` — `PublicCmsSection`, `PublicTenantConfig`, `PublicWidgetInfo`, `PublicSiteContext` (see data-model.md for field definitions)

**Checkpoint**: Foundation ready — user story implementation can begin.

---

## Phase 3: User Story 1 — Visitor Views Tenant Public Page (Priority: P1) 🎯 MVP

**Goal**: A visitor can open `/sites/{tenant_slug}` and see the tenant's name, public description, published CMS sections, and contact info.

**Independent Test**: Run `docker compose up -d`, seed demo tenant with published CMS pages, GET `/sites/demo-tenant` and assert 200 HTML response containing tenant name and CMS section title.

- [x] T009 [P] [US1] Create `backend/app/repositories/public_site_repository.py` — `get_tenant_by_slug`, `get_tenant_config`, `get_published_pages`, `get_widget` using asyncio.gather for parallel secondary queries; all queries use explicit `tenant_id` filter (no RLS context set on public route)
- [x] T010 [P] [US1] Create `backend/app/services/public_site_service.py` — `PublicSiteService.get_site_context(slug) -> PublicSiteContext`: resolves tenant, raises 404 if missing, raises 403 if suspended, loads config with fallback defaults, assembles `PublicSiteContext`
- [x] T011 [US1] Create `backend/app/templates/public_site.html` — Jinja2 template displaying: brand name as `<h1>`, public description as `<p>`, each CMS page as `<section><h2>title</h2><p>body</p></section>`, contact email if present, `<script src="/widget.js" data-widget-id="{{ widget.widget_id }}">` only if widget is not None; auto-escape enabled
- [x] T012 [US1] Create `backend/app/api/routes/public_site.py` — `GET /sites/{tenant_slug}` handler: validate slug with `Path(pattern=r'^[a-z0-9-]{1,100}$')`, call service, return `TemplateResponse("public_site.html", context)`; handle 404/403 with appropriate HTTP responses
- [x] T013 [US1] Register Jinja2Templates instance in `backend/app/api/routes/public_site.py` pointing to `backend/app/templates/`
- [x] T014 [US1] Register `public_site.router` in `backend/app/api/router.py` with prefix `/sites` and tag `public_site`
- [x] T015 [US1] Write unit tests in `backend/tests/test_public_site_service.py` — test: 404 on unknown slug, 403 on suspended tenant, 200 with correct context fields, missing TenantConfig row uses fallback defaults, no widget → widget field is None

**Checkpoint**: `GET /sites/demo-tenant` returns 200 HTML with CMS content. `GET /sites/unknown` returns 404.

---

## Phase 4: User Story 2 — Tenant Isolation Between Public Pages (Priority: P1)

**Goal**: Two tenants each serve only their own content — no cross-contamination.

**Independent Test**: Seed `abc-gym` and `green-clinic` with distinct published CMS pages. Assert `/sites/abc-gym` HTML does NOT contain green-clinic content, and vice versa.

- [x] T016 [US2] Add a second demo tenant (`green-clinic`) with distinct CMS content to `backend/scripts/seed_tenants.py` — name, slug, 2 published CMS pages, and a widget row
- [x] T017 [US2] Write integration test in `backend/tests/integration/test_public_site.py` — against real DB: verify `/sites/abc-gym` contains only abc-gym CMS titles, `/sites/green-clinic` contains only green-clinic CMS titles, no cross-tenant leakage
- [x] T018 [US2] Verify each repository query in `public_site_repository.py` has an explicit `WHERE tenant_id = :tenant_id` clause — grep/assert no unscoped query exists for `cms_pages` or `widgets` lookups

**Checkpoint**: Two tenants serve isolated pages. Isolation verified by integration test.

---

## Phase 5: User Story 3 — Chat Widget Loads on Public Page (Priority: P2)

**Goal**: The correct widget script tag appears on the page with the tenant's `public_widget_id`.

**Independent Test**: Load `/sites/abc-gym` HTML and assert `<script` tag contains `data-widget-id="<abc-gym-widget-public-id>"`.

- [x] T019 [US3] Update seed script `backend/scripts/seed_tenants.py` to create a `Widget` row for each demo tenant with a `public_widget_id` value
- [x] T020 [US3] Verify `public_site.html` template renders `data-widget-id` attribute using `{{ widget.widget_id }}` and only renders the script tag when `widget is not None`
- [x] T021 [US3] Add test case to `backend/tests/test_public_site_service.py` — tenant with no widget: `PublicSiteContext.widget` is None, template renders without script tag

**Checkpoint**: Widget script tag present with correct `public_widget_id`; absent when no widget configured.

---

## Phase 6: User Story 4 — Optional JSON API (Priority: P3)

**Goal**: `GET /api/public/sites/{tenant_slug}` returns the same data as the HTML page but as JSON.

**Independent Test**: Call `GET /api/public/sites/abc-gym` and assert JSON response matches the schema in `contracts/public_site_api.md`.

- [x] T022 [US4] Add `GET /api/public/sites/{tenant_slug}` JSON endpoint to `backend/app/api/routes/public_site.py` — reuses `PublicSiteService.get_site_context()`, returns `PublicSiteContext` as JSON with `response_model=PublicSiteContext`
- [x] T023 [US4] Register the JSON endpoint at prefix `/api/public/sites` OR add it to the existing `/sites` router under a sub-path — confirm no route collision with `GET /sites/{slug}` HTML route
- [ ] T024 [US4] Add test case in `backend/tests/test_public_site_service.py` verifying JSON response schema matches `contracts/public_site_api.md` field names

**Checkpoint**: JSON API returns correct structure and reuses all service/repo logic from HTML route.

---

## Phase 7: Polish & Cross-Cutting Concerns

- [x] T025 [P] Add structlog log lines to `public_site_service.py` — events: `public_site.tenant_resolved`, `public_site.tenant_suspended`, `public_site.tenant_not_found` with `slug`, `tenant_id` fields (redact no PII here)
- [x] T026 [P] Add `TenantConfig` to `backend/app/db/base.py` metadata import so Alembic autogenerate detects the model
- [x] T027 Run `uv run alembic upgrade head` inside the running api container to verify migration `0006` applies cleanly
- [x] T028 Update `backend/scripts/seed_tenants.py` to optionally insert a `TenantConfig` row for the demo tenant (brand_name, public_description, contact_email)
- [x] T029 Verify `uv run ruff check .` and `uv run black --check .` pass in `backend/`

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1 (Setup)**: No dependencies — start immediately
- **Phase 2 (Foundational)**: Depends on Phase 1 completion — BLOCKS all user stories
- **Phase 3 (US1)**: Depends on Phase 2 — no other story deps
- **Phase 4 (US2)**: Depends on Phase 3 (needs working page to test isolation)
- **Phase 5 (US3)**: Depends on Phase 3 (template must exist to add widget assertion)
- **Phase 6 (US4)**: Depends on Phase 3 (reuses service layer)
- **Phase 7 (Polish)**: Depends on all desired stories being complete

### Within Each Phase

- T003/T004 (model → migration) must be sequential
- T005, T006, T007, T008 within Phase 2 can run in parallel
- T009, T010, T011 within Phase 3 can run in parallel; T012–T014 depend on all three

### Parallel Opportunities

```bash
# Phase 2 — all independent:
Task T005: add get_tenant_by_slug to tenant_repository.py
Task T006: implement cms_repository.py
Task T007: implement widget_repository.py
Task T008: create schemas/public_site.py

# Phase 3 — repository, service, template in parallel:
Task T009: public_site_repository.py
Task T010: public_site_service.py
Task T011: public_site.html template
```

---

## Implementation Strategy

### MVP (Phase 1 + 2 + 3 only)

1. Complete Phase 1 — add deps, create templates dir
2. Complete Phase 2 — model, migration, repo stubs, schemas
3. Complete Phase 3 — repository, service, template, route, tests
4. **STOP and VALIDATE**: `GET /sites/demo-tenant` returns correct HTML
5. Demo: show CMS content + widget on public page

### Incremental Delivery

1. Phase 1 + 2 + 3 → MVP demo page working
2. Phase 4 → Isolation verified with two tenants
3. Phase 5 → Widget confirmed correct per tenant
4. Phase 6 → JSON API added (optional, extra time only)
5. Phase 7 → Logging, linting, cleanup

---

## Notes

- `body` is the CMS content field (not `content`) — confirmed from `cms.py` model
- `public_widget_id` is the widget public field name — confirmed from `widget.py` model
- Public routes do NOT set `app.tenant_id` RLS context — explicit `WHERE tenant_id` filter in every repo query is the isolation mechanism (per research Decision 4)
- `tenant_config` row absence is not an error — service applies fallback defaults
- The `allowed_origins` field in `TenantConfig` must NEVER be exposed in the public page HTML or JSON response
