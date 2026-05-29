# Implementation Plan: Admin App (Streamlit)

**Branch**: `feature/014-admin-app` | **Date**: 2026-05-29 | **Spec**: [spec.md](./spec.md)

**Owner**: Person A — `feature/platform-tenancy`

---

## Summary

A Streamlit multi-page admin app that is a **thin API client** — every data operation goes through the FastAPI backend over HTTP. The app handles tenant admin workflows (CMS, agent config, leads, escalations, embed snippet) and a separate tenant manager view (tenant list, usage, audit logs). Authentication state lives in `st.session_state`; no database connection exists in the Streamlit process.

---

## Technical Context

**Language/Version**: Python 3.11+ (matches `admin_app/pyproject.toml`)

**Framework**: Streamlit ≥ 1.35

**Primary Dependencies** (already in `admin_app/pyproject.toml`):
- `streamlit>=1.35` — multipage app, session state, `st.navigation()`, `st.cache_data`
- `httpx>=0.27` — synchronous HTTP client (`httpx.Client`) for API calls
- `pydantic>=2.7` — data validation for API responses (optional but available)

**API Base URL**: `os.environ.get("API_BASE_URL", "http://api:8000")` — matches docker-compose service name

**Streamlit port**: 8501 (as per docker-compose.yml)

**Auth mechanism**: JWT from `POST /auth/login` stored in `st.session_state["token"]`. All requests include `Authorization: Bearer <token>`.

**Multipage structure**: Streamlit native `pages/` directory (already scaffolded):
```
admin_app/
├── app.py                    # Login + navigation entrypoint
├── api_client.py             # Shared httpx wrapper (NEW)
├── pages/
│   ├── dashboard.py          # NEW — tenant admin landing page
│   ├── cms.py                # IMPLEMENT (currently stub)
│   ├── agent_config.py       # IMPLEMENT (currently stub)
│   ├── leads.py              # IMPLEMENT (currently stub)
│   ├── embed_snippet.py      # IMPLEMENT (currently stub)
│   └── tenant_manager.py     # IMPLEMENT (currently stub)
└── pyproject.toml
```

---

## Constitution Check

| Principle | Status | Notes |
|-----------|--------|-------|
| I. Tenant Isolation | ✅ Pass | App shows only the logged-in tenant's data. All data is scoped by the JWT — the API enforces RLS; the app never constructs tenant-scoped queries itself. FR-009 ensures zero cross-tenant data. |
| II. Clean Layered Architecture | ✅ Pass | `api_client.py` owns all HTTP calls (equivalent to repository layer). Pages own UI only. No SQL, no direct DB access. No `os.getenv` in page files. |
| III. Security by Default | ✅ Pass | Token stored in `st.session_state` only (in-memory, not disk). `require_auth()` guard on every page. FR-004: platform rail controls are never exposed. 401 from API clears session and redirects to login. |
| IV. Async All the Way Down | ⚠ N/A | Streamlit's execution model is synchronous. `httpx.Client` (sync) is correct here. The backend is async; only the Streamlit layer uses sync HTTP. |
| V. Lean Containers | ✅ Pass | No torch, no transformers. `admin_app/Dockerfile` installs only `streamlit + httpx + pydantic`. |
| VI. Evals Are the Grade | ⚠ Gap | Admin app UI has no automated eval gate. Covered by the quickstart.md smoke tests and the SC-001–SC-005 acceptance criteria manually. |

---

## Project Structure

### New Files

```text
admin_app/api_client.py              # Shared httpx wrapper — all API calls
admin_app/pages/dashboard.py         # Tenant admin dashboard (stats overview)
```

### Modified Files (stubs → full implementation)

```text
admin_app/app.py                     # Login form + role-based st.navigation()
admin_app/pages/cms.py               # CMS list, create, edit, publish, reindex
admin_app/pages/agent_config.py      # Persona, allowed/blocked topics, tools
admin_app/pages/leads.py             # Leads list + status update
admin_app/pages/embed_snippet.py     # Widget script tag + copy button
admin_app/pages/tenant_manager.py    # Tenant list, usage summary, audit logs
```

---

## Key Design Decisions

### 1. `httpx.Client` (sync) in Streamlit

Streamlit reruns scripts synchronously on every user interaction. `httpx.AsyncClient` with `asyncio.run()` would conflict with Streamlit's own event loop. `httpx.Client` is the correct choice. This is noted as an exception to the constitution's "async all the way down" principle — it applies to the FastAPI backend, not the Streamlit frontend.

### 2. `api_client.py` as the Single HTTP Layer

All pages import `APIClient` from `api_client.py`. No page file calls `httpx` directly. This mirrors the repository pattern: `api_client.py` ≈ repository layer for the Streamlit app. Errors are caught here and converted to `APIError` with user-readable messages.

### 3. `st.cache_data(ttl=30)` on List Calls

Streamlit reruns the full script on every widget interaction. Without caching, navigating a page with multiple API calls hammers the backend. `@st.cache_data(ttl=30)` caches read results for 30 seconds. Write operations call `st.cache_data.clear()` to bust the cache.

### 4. Role-Based Navigation via `st.navigation()`

`app.py` builds the page list based on `st.session_state.get("user_role")`:
- `tenant_admin` → Dashboard, CMS, Agent Config, Leads, Escalations, Embed Snippet
- `tenant_manager` → Tenant Manager only
- Not logged in → only the login form is shown; `st.navigation()` hides all pages

### 5. Stub-Tolerant Leads and Escalations Pages

Leads and escalations API routes are Person B stubs. The admin app pages are implemented but handle empty list responses and 404s gracefully with a "No data yet — check back after leads are captured" message. The UI is complete; the data dependency is external.

### 6. `/developing-with-streamlit` Skill for Implementation

The implementation phase uses the `/developing-with-streamlit` skill for all Streamlit-specific work — multipage navigation, session state patterns, custom components for the copy-to-clipboard button, and Streamlit theming.

---

## Open Gaps (Follow-up Required)

| Gap | Spec Ref | Owner | Notes |
|-----|----------|-------|-------|
| Leads PATCH endpoint | FR-005 | Person B | `PATCH /leads/{id}` stub; UI ready but status updates won't persist until Person B implements it |
| Escalations PATCH endpoint | FR-006 | Person B | Same as above |
| `PATCH /tenant/config` endpoint | FR-003 | Person A (this spec) | Currently only GET exists; PUT/PATCH needed for agent config saves |
| `DELETE /cms/{id}` endpoint | FR-002 | Person A (this spec) | CMS delete needs route if not yet present |
| `POST /cms/{id}/reindex` endpoint | FR-002 | Person B | Reindex trigger endpoint; may need to be added |
| Tenant Manager audit log pagination | FR-010 | Person A | First page only in Week 8; pagination deferred |
