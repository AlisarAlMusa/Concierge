"""Platform manager dashboard — tenant list, per-tenant usage, audit logs."""

from __future__ import annotations

import streamlit as st

from api_client import APIError, get_client, require_auth

require_auth(allowed_roles=["tenant_manager"])

st.title("🏢 Platform Management")

client = get_client()

# ── Load tenants ──────────────────────────────────────────────────────────────
try:
    with st.spinner("Loading tenants…"):
        tenants = client.list_tenants()
except APIError as exc:
    if exc.status_code == 401:
        st.session_state.clear()
        st.switch_page("app.py")
        st.stop()
    st.error(str(exc))
    tenants = []

# ── Tenant list ───────────────────────────────────────────────────────────────
st.subheader(f"Tenants ({len(tenants)})")

if not tenants:
    st.info("No tenants found.")
else:
    tenant_names = [t.get("name") or t.get("slug") or str(t.get("id", i)) for i, t in enumerate(tenants)]
    selected_idx = st.selectbox("Select tenant", range(len(tenants)), format_func=lambda i: tenant_names[i])
    tenant = tenants[selected_idx]
    tid = str(tenant.get("id", ""))

    with st.container(border=True):
        col_a, col_b = st.columns(2)
        col_a.markdown(f"**Name:** {tenant.get('name', '—')}")
        col_a.markdown(f"**Slug:** `{tenant.get('slug', '—')}`")
        col_b.markdown(f"**Plan:** {tenant.get('plan', '—')}")
        col_b.markdown(f"**Status:** {tenant.get('status', '—')}")

    st.divider()

    # ── Per-tenant usage ──────────────────────────────────────────────────────
    st.subheader("Usage This Period")
    try:
        with st.spinner("Loading usage…"):
            usage = client.get_tenant_usage(tid)
    except APIError as exc:
        st.error(str(exc))
        usage = {}

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
        st.info("No usage data for this tenant yet.")

    st.divider()

    # ── Audit logs ────────────────────────────────────────────────────────────
    st.subheader("Audit Logs")
    try:
        with st.spinner("Loading audit logs…"):
            logs = client.list_audit_logs(tenant_id=tid)
    except APIError as exc:
        st.error(str(exc))
        logs = []

    if not logs:
        st.info("No audit logs for this tenant.")
    else:
        st.caption(f"{len(logs)} log entries")
        for entry in logs[:50]:
            actor = entry.get("actor_email") or entry.get("actor_id", "system")
            action = entry.get("action", "unknown")
            ts = entry.get("created_at") or entry.get("timestamp", "")
            resource = entry.get("resource_type", "")
            st.markdown(f"- `{ts}` **{action}** on `{resource}` by _{actor}_")
