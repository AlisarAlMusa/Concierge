"""Concierge Admin — entry point.

Handles login and builds role-based navigation via st.navigation().
All pages are only accessible after authentication.
"""

from __future__ import annotations

import streamlit as st

from api_client import API_BASE_URL, APIClient, APIError

st.set_page_config(page_title="Concierge Admin", layout="wide", page_icon="🤖")


def _do_login(email: str, password: str) -> None:
    client = APIClient(base_url=API_BASE_URL)
    try:
        with st.spinner("Logging in…"):
            data = client.login(email, password)
            token = data["access_token"]
            authed = APIClient(base_url=API_BASE_URL, token=token)
            me = authed.me()
        st.session_state["token"] = token
        st.session_state["user_email"] = me.get("email", "")
        st.session_state["user_role"] = me.get("role", "")
        st.session_state["tenant_id"] = me.get("tenant_id")
        st.rerun()
    except APIError as exc:
        st.error(str(exc))


if not st.session_state.get("token"):
    # ── Login page ────────────────────────────────────────────────────────────
    col_l, col_m, col_r = st.columns([1, 1, 1])
    with col_m:
        st.title("🤖 Concierge Admin")
        st.subheader("Log in to your account")
        with st.form("login_form"):
            email = st.text_input("Email", placeholder="admin@example.com")
            password = st.text_input("Password", type="password")
            submitted = st.form_submit_button("Log In", use_container_width=True)
        if submitted:
            if not email or not password:
                st.error("Email and password are required.")
            else:
                _do_login(email, password)

else:
    # ── Authenticated — build navigation ──────────────────────────────────────
    with st.sidebar:
        st.markdown(f"**{st.session_state.get('user_email', '')}**")
        st.caption(f"Role: {st.session_state.get('user_role', '').replace('_', ' ').title()}")
        st.divider()
        if st.button("Log out", use_container_width=True):
            client = APIClient(base_url=API_BASE_URL, token=st.session_state["token"])
            client.logout()
            st.session_state.clear()
            st.rerun()

    role = st.session_state.get("user_role", "")

    if role == "tenant_manager":
        pages = [
            st.Page("pages/tenant_manager.py", title="Platform", icon="🏢"),
        ]
    else:
        pages = [
            st.Page("pages/dashboard.py", title="Dashboard", icon="🏠"),
            st.Page("pages/cms.py", title="CMS", icon="📝"),
            st.Page("pages/agent_config.py", title="Agent Config", icon="🤖"),
            st.Page("pages/leads.py", title="Leads & Escalations", icon="📋"),
            st.Page("pages/embed_snippet.py", title="Embed Snippet", icon="🔗"),
        ]

    pg = st.navigation(pages)
    pg.run()
