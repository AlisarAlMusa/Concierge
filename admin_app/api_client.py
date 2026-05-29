"""Shared API client for the Concierge admin Streamlit app.

All HTTP calls to the FastAPI backend go through this module.
Pages never import httpx directly.
"""

from __future__ import annotations

import os

import httpx
import streamlit as st

API_BASE_URL: str = os.environ.get("API_BASE_URL", "http://api:8000")


class APIError(Exception):
    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class APIClient:
    def __init__(self, base_url: str = API_BASE_URL, token: str | None = None) -> None:
        self._base_url = base_url.rstrip("/")
        self._token = token

    def _headers(self) -> dict[str, str]:
        h: dict[str, str] = {}
        if self._token:
            h["Authorization"] = f"Bearer {self._token}"
        return h

    def _request(self, method: str, path: str, **kwargs):
        url = f"{self._base_url}{path}"
        try:
            with httpx.Client(timeout=10.0) as client:
                resp = client.request(method, url, headers=self._headers(), **kwargs)
            resp.raise_for_status()
            if resp.status_code == 204 or not resp.content:
                return None
            return resp.json()
        except httpx.HTTPStatusError as exc:
            sc = exc.response.status_code
            if sc == 401:
                raise APIError("Session expired. Please log in again.", 401) from exc
            if sc == 403:
                raise APIError("You do not have permission to perform this action.", 403) from exc
            if sc == 404:
                raise APIError("Resource not found.", 404) from exc
            if sc == 422:
                try:
                    detail = exc.response.json().get("detail", str(exc))
                except Exception:
                    detail = str(exc)
                raise APIError(f"Validation error: {detail}", 422) from exc
            raise APIError(f"API error ({sc}). Please try again later.", sc) from exc
        except httpx.RequestError as exc:
            raise APIError("Cannot reach the API. Check your connection.") from exc

    # ── Auth ──────────────────────────────────────────────────────────────────

    def login(self, email: str, password: str) -> dict:
        return self._request("POST", "/auth/login", data={"username": email, "password": password})

    def me(self) -> dict:
        return self._request("GET", "/auth/me")

    def logout(self) -> None:
        try:
            self._request("POST", "/auth/logout")
        except APIError:
            pass

    # ── CMS ───────────────────────────────────────────────────────────────────

    def list_cms_pages(self) -> list[dict]:
        try:
            result = self._request("GET", "/cms/")
            return result or []
        except APIError as exc:
            if exc.status_code == 404:
                return []
            raise

    def get_cms_page(self, page_id: str) -> dict:
        return self._request("GET", f"/cms/{page_id}")

    def create_cms_page(self, title: str, body: str, slug: str, status: str = "draft") -> dict:
        return self._request(
            "POST", "/cms/", json={"title": title, "body": body, "slug": slug, "status": status}
        )

    def update_cms_page(self, page_id: str, **fields) -> dict:
        return self._request("PUT", f"/cms/{page_id}", json=fields)

    def delete_cms_page(self, page_id: str) -> None:
        try:
            self._request("DELETE", f"/cms/{page_id}")
        except APIError as exc:
            if exc.status_code == 404:
                return
            raise

    def reindex_cms_page(self, page_id: str) -> None:
        try:
            self._request("POST", f"/cms/{page_id}/reindex")
        except APIError as exc:
            if exc.status_code == 404:
                raise APIError("Reindex endpoint not yet available.", 404) from exc
            raise

    # ── Agent / Guardrail Config ───────────────────────────────────────────────

    def get_tenant_config(self) -> dict:
        try:
            return self._request("GET", "/tenant/config") or {}
        except APIError as exc:
            if exc.status_code == 404:
                return {}
            raise

    def update_tenant_config(self, **fields) -> dict:
        return self._request("PATCH", "/tenant/config", json=fields)

    # ── Leads ─────────────────────────────────────────────────────────────────

    def list_leads(self, status: str | None = None) -> list[dict]:
        params: dict = {}
        if status:
            params["status"] = status
        try:
            return self._request("GET", "/leads/", params=params) or []
        except APIError as exc:
            if exc.status_code == 404:
                return []
            raise

    def update_lead(self, lead_id: str, status: str, notes: str | None = None) -> dict:
        payload: dict = {"status": status}
        if notes is not None:
            payload["notes"] = notes
        return self._request("PATCH", f"/leads/{lead_id}", json=payload)

    # ── Escalations ───────────────────────────────────────────────────────────

    def list_escalations(self, status: str | None = None) -> list[dict]:
        params: dict = {}
        if status:
            params["status"] = status
        try:
            return self._request("GET", "/escalations/", params=params) or []
        except APIError as exc:
            if exc.status_code == 404:
                return []
            raise

    def update_escalation(self, escalation_id: str, status: str) -> dict:
        return self._request("PATCH", f"/escalations/{escalation_id}", json={"status": status})

    # ── Widgets ───────────────────────────────────────────────────────────────

    def list_widgets(self) -> list[dict]:
        try:
            return self._request("GET", "/widgets/") or []
        except APIError as exc:
            if exc.status_code == 404:
                return []
            raise

    # ── Usage summary (tenant admin) ──────────────────────────────────────────

    def get_usage_summary(self) -> dict:
        try:
            return self._request("GET", "/tenant/usage-summary") or {}
        except APIError as exc:
            if exc.status_code == 404:
                return {}
            raise

    # ── Platform (tenant_manager only) ────────────────────────────────────────

    def list_tenants(self) -> list[dict]:
        return self._request("GET", "/platform/tenants/") or []

    def get_tenant_usage(self, tenant_id: str) -> dict:
        return self._request("GET", f"/platform/tenants/{tenant_id}/usage-summary") or {}

    def list_audit_logs(self, tenant_id: str | None = None) -> list[dict]:
        params: dict = {}
        if tenant_id:
            params["tenant_id"] = tenant_id
        try:
            return self._request("GET", "/platform/audit-logs", params=params) or []
        except APIError as exc:
            if exc.status_code == 404:
                return []
            raise


def get_client() -> APIClient:
    """Return an APIClient pre-loaded with the current session token."""
    return APIClient(base_url=API_BASE_URL, token=st.session_state.get("token"))


def require_auth(allowed_roles: list[str] | None = None) -> None:
    """Page guard — redirect to login if unauthenticated or wrong role."""
    if not st.session_state.get("token"):
        st.switch_page("app.py")
        st.stop()
    if allowed_roles and st.session_state.get("user_role") not in allowed_roles:
        st.error("You do not have permission to view this page.")
        st.stop()
