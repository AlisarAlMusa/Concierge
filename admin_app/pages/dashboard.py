"""Tenant admin dashboard — overview of CMS pages, leads, and usage."""

from __future__ import annotations

import streamlit as st

from api_client import APIError, get_client, require_auth

require_auth()

st.title("Dashboard")

client = get_client()

# Fetch data with graceful error handling
cms_pages: list[dict] = []
usage: dict = {}
leads: list[dict] = []

try:
    with st.spinner("Loading…"):
        cms_pages = client.list_cms_pages()
        usage = client.get_usage_summary()
        leads = client.list_leads()
except APIError as exc:
    if exc.status_code == 401:
        st.session_state.clear()
        st.switch_page("app.py")
        st.stop()
    st.error(str(exc))

# ── Summary metrics ───────────────────────────────────────────────────────────
published = sum(1 for p in cms_pages if p.get("status") == "published")
draft = sum(1 for p in cms_pages if p.get("status") == "draft")
open_leads = sum(1 for l in leads if l.get("status") == "new")

col1, col2, col3, col4 = st.columns(4)
col1.metric("CMS Pages", len(cms_pages))
col2.metric("Published", published)
col3.metric("Drafts", draft)
col4.metric("New Leads", open_leads)

st.divider()

# ── Usage summary ─────────────────────────────────────────────────────────────
st.subheader("Usage This Period")

if usage:
    u1, u2, u3 = st.columns(3)
    total_cost = usage.get("total_cost_usd", 0)
    u1.metric("Total Cost (USD)", f"${float(total_cost):.4f}")
    u2.metric("Input Tokens", f"{usage.get('total_input_tokens', 0):,}")
    u3.metric("Output Tokens", f"{usage.get('total_output_tokens', 0):,}")

    with st.expander("Breakdown by operation"):
        for op in ("llm", "embedding", "classifier", "rerank"):
            data = usage.get(op, {})
            if data.get("input_tokens", 0) or data.get("cost_usd", 0):
                st.markdown(
                    f"**{op.upper()}** — "
                    f"{data.get('input_tokens', 0):,} in / "
                    f"{data.get('output_tokens', 0):,} out — "
                    f"${float(data.get('cost_usd', 0)):.5f}"
                )
else:
    st.info("No usage data yet.")

st.divider()

# ── Recent CMS pages ──────────────────────────────────────────────────────────
st.subheader("Recent CMS Pages")
if cms_pages:
    for page in cms_pages[:5]:
        status_icon = "🟢" if page.get("status") == "published" else "🟡"
        st.markdown(f"{status_icon} **{page.get('title', 'Untitled')}** — `{page.get('slug', '')}`")
else:
    st.info("No CMS pages yet. Go to the CMS page to create your first page.")
