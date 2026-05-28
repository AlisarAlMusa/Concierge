# API Client Contract — Admin App

**File**: `admin_app/api_client.py`

All API calls from the Streamlit admin app go through this module. Pages never call httpx directly.

---

## Module Interface

```python
class APIError(Exception):
    def __init__(self, message: str, status_code: int | None = None): ...

class APIClient:
    def __init__(self, base_url: str, token: str | None = None): ...

    # ── Auth ──────────────────────────────────────────────────────────
    def login(self, email: str, password: str) -> dict:
        """POST /auth/login. Returns {"access_token": str, "token_type": str}.
        Raises APIError on failure."""

    def me(self) -> dict:
        """GET /auth/me. Returns user profile dict.
        Raises APIError if token is invalid or expired."""

    def logout(self) -> None:
        """POST /auth/logout. Best-effort — does not raise on failure."""

    # ── CMS ───────────────────────────────────────────────────────────
    def list_cms_pages(self) -> list[dict]:
        """GET /cms/. Returns list of page summaries (no body).
        Returns [] if API returns 404 or empty."""

    def get_cms_page(self, page_id: str) -> dict:
        """GET /cms/{page_id}. Returns full page including body.
        Raises APIError(404) if not found."""

    def create_cms_page(self, title: str, body: str, slug: str, status: str) -> dict:
        """POST /cms/. Returns created page.
        Raises APIError on validation or server error."""

    def update_cms_page(self, page_id: str, **fields) -> dict:
        """PUT /cms/{page_id}. Returns updated page.
        Raises APIError if not found or validation error."""

    def delete_cms_page(self, page_id: str) -> None:
        """DELETE /cms/{page_id}.
        Raises APIError if not found."""

    def reindex_cms_page(self, page_id: str) -> None:
        """POST /cms/{page_id}/reindex. Triggers async reindex.
        Raises APIError on failure."""

    # ── Agent / Guardrail Config ───────────────────────────────────────
    def get_tenant_config(self) -> dict:
        """GET /tenant/config. Returns config dict.
        Returns {} if not yet configured."""

    def update_tenant_config(self, **fields) -> dict:
        """PATCH /tenant/config. Partial update.
        Raises APIError on failure."""

    # ── Leads ─────────────────────────────────────────────────────────
    def list_leads(self, status: str | None = None) -> list[dict]:
        """GET /leads/?status={status}. Returns [] if no leads or route stub."""

    def update_lead(self, lead_id: str, status: str, notes: str | None = None) -> dict:
        """PATCH /leads/{lead_id}. Returns updated lead.
        Raises APIError on failure."""

    # ── Escalations ───────────────────────────────────────────────────
    def list_escalations(self, status: str | None = None) -> list[dict]:
        """GET /escalations/?status={status}. Returns [] if no escalations or route stub."""

    def update_escalation(self, escalation_id: str, status: str) -> dict:
        """PATCH /escalations/{escalation_id}. Returns updated escalation.
        Raises APIError on failure."""

    # ── Widgets ───────────────────────────────────────────────────────
    def list_widgets(self) -> list[dict]:
        """GET /widgets/. Returns list of widgets for the tenant.
        Returns [] if none configured."""

    # ── Platform (tenant_manager only) ────────────────────────────────
    def list_tenants(self) -> list[dict]:
        """GET /platform/tenants/. Returns tenant list.
        Raises APIError(403) if caller is not tenant_manager."""

    def get_tenant_usage(self, tenant_id: str) -> dict:
        """GET /platform/tenants/{tenant_id}/usage-summary.
        Raises APIError(403) if caller is not tenant_manager."""

    def list_audit_logs(self, tenant_id: str | None = None) -> list[dict]:
        """GET /platform/audit-logs?tenant_id={id}.
        Raises APIError(403) if caller is not tenant_manager."""
```

---

## Error Handling Contract

- All methods catch `httpx.HTTPStatusError` and `httpx.RequestError`.
- On 401: raise `APIError("Session expired. Please log in again.", 401)` — caller should clear session state and redirect to login.
- On 403: raise `APIError("You do not have permission to perform this action.", 403)`.
- On 422: raise `APIError("Validation error: {detail}", 422)`.
- On 5xx: raise `APIError("API is unavailable. Please try again later.", status_code)`.
- On network error: raise `APIError("Cannot reach the API. Check your connection.")`.

---

## Usage Pattern in Pages

```python
# At top of every page:
from api_client import APIClient, APIError
import streamlit as st

def require_auth():
    if not st.session_state.get("token"):
        st.switch_page("app.py")
        st.stop()

require_auth()
client = APIClient(
    base_url=os.environ.get("API_BASE_URL", "http://api:8000"),
    token=st.session_state["token"],
)

try:
    pages = client.list_cms_pages()
except APIError as e:
    if e.status_code == 401:
        st.session_state.clear()
        st.switch_page("app.py")
        st.stop()
    st.error(e.args[0])
    st.stop()
```
