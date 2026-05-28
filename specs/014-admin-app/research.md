# Research: Admin App (Streamlit) — Spec 014

**Date**: 2026-05-29 | **Author**: Person A

---

## Decision 1: Streamlit Multipage App Structure

**Decision**: Use Streamlit's native multipage support — `pages/` directory under `admin_app/`. Each `.py` file is a separate page. `app.py` is the login/home entrypoint.

**Rationale**: Already scaffolded in `admin_app/pages/`. Native multipage avoids any custom routing logic. Streamlit 1.35+ supports `st.navigation()` / `st.Page()` for programmatic page control, which lets us suppress pages for unauthenticated users.

**Alternatives considered**:
- Single-file app with `st.selectbox` navigation — rejected: poor URL support, worse UX
- Separate Streamlit apps per role — rejected: overkill for Week 8

---

## Decision 2: Authentication State Storage

**Decision**: Store `token`, `user_email`, and `user_role` in `st.session_state`. Session state is in-memory per browser session and cleared on tab close — matches the spec assumption.

**Rationale**: The spec explicitly states "tokens are not persisted to disk." `st.session_state` is the canonical Streamlit mechanism for per-session data. No cookies, no disk writes.

**Key guard pattern**: Every page starts with a `require_auth()` call that checks `st.session_state.get("token")`. If absent, `st.switch_page("app.py")` redirects to login.

**Alternatives considered**:
- `st.experimental_get_query_params` for token — rejected: exposes JWT in URL
- `streamlit-cookies` third-party component — rejected: adds dependency, disk write violates spec

---

## Decision 3: API Client Module

**Decision**: Shared `admin_app/api_client.py` module. All httpx calls live here — pages never call `httpx` directly. Functions are synchronous wrappers (Streamlit runs synchronously; async httpx is unnecessary overhead).

**Rationale**: Isolates API call logic from UI code. Errors are caught and returned as `None` or raised as typed `APIError` exceptions — never raw httpx errors. Pages call `api.list_cms_pages()` not `httpx.get(...)`.

**Base URL**: Read from `os.environ.get("API_BASE_URL", "http://api:8000")` — matches docker-compose service name.

**Auth header**: Injected in every call from `st.session_state["token"]`. Never read from env or hardcoded.

**Alternatives considered**:
- `requests` — rejected: constitution bans `requests` (sync HTTP), but Streamlit is sync anyway. However `httpx` has a synchronous client (`httpx.Client`) that satisfies the spirit of the rule for the Streamlit context.
- Per-page inline httpx calls — rejected: duplicates auth header logic everywhere

---

## Decision 4: Error Handling Strategy

**Decision**: `api_client.py` wraps all calls in try/except. Returns `(data, error_str)` tuples OR raises `APIError(message, status_code)`. Pages call `st.error(msg)` and `st.stop()` on failure. No raw Python tracebacks are ever shown.

**Rationale**: FR-011 requires graceful error handling. `st.stop()` halts page render without crashing. Error messages are user-readable strings, not `httpx.HTTPStatusError` objects.

**Alternatives considered**:
- Letting exceptions propagate — rejected: violates FR-011
- `st.exception()` — rejected: shows raw traceback to the user

---

## Decision 5: Role-Based Page Visibility

**Decision**: After login, the app reads `user_role` from `st.session_state`. `tenant_manager` role sees the Tenant Manager page only. `tenant_admin` role sees Dashboard, CMS, Agent Config, Leads, Escalations, Embed Snippet.

**Implementation**: `app.py` uses `st.navigation()` to build the page list conditionally based on `user_role`. Pages not in the nav list are inaccessible even by direct URL.

**Rationale**: FR-010 requires a separate platform view for `tenant_manager`. Hiding pages via `st.navigation()` is cleaner than per-page role checks (though `require_auth()` also checks role as a second layer).

---

## Decision 6: CMS Reindex — API Boundary

**Decision**: Reindex is triggered via `POST /cms/{page_id}/reindex` (to be confirmed). The admin app calls this endpoint and shows a spinner + success/error message. No direct embedding calls from the app.

**Rationale**: FR-008 — the app MUST NOT query the DB or call embedding services directly. All operations go through the API.

---

## Decision 7: Leads and Escalations — Stub-Tolerant UI

**Decision**: The leads and escalations API routes are stubs (Person B). The admin app pages must gracefully handle empty responses or 404s with a friendly "No data yet" message. The UI is implemented; the data dependency is external.

**Rationale**: Person A owns the admin app; Person B owns leads/escalations routes. Building stub-tolerant UI avoids blocking Week 8 delivery.

---

## Decision 8: httpx.Client vs httpx.AsyncClient in Streamlit

**Decision**: Use `httpx.Client` (synchronous) in `api_client.py`.

**Rationale**: Streamlit's execution model is synchronous — each user interaction re-runs the script from top to bottom. `asyncio.run()` inside a Streamlit script would conflict with Streamlit's own event loop. `httpx.Client` is the correct choice for a Streamlit context and aligns with the spirit of the constitution (httpx over requests).

---

## Decision 9: `st.cache_data` for Read-Heavy Calls

**Decision**: Use `@st.cache_data(ttl=30)` on list calls (CMS pages, leads, tenants) to reduce API round-trips on rerender. Cache is busted explicitly after write operations via `st.cache_data.clear()`.

**Rationale**: Streamlit reruns the entire script on every interaction. Without caching, a page with 5 API calls would make 5 round-trips every time the user clicks a button. 30s TTL balances freshness vs. performance.
