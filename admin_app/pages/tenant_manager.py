"""Platform manager dashboard — tenant onboarding, list, usage, audit logs.

Three tabs:
  • Tenants      — list, select, view per-tenant usage + audit (existing UX).
  • Create Tenant — manager-only form to provision a new tenant.
  • Invite Admin  — manager-only form to create a tenant_admin user and
                    surface the one-time temporary password.

Backed exclusively by manager-scoped routes:
  POST /platform/tenants/
  GET  /platform/tenants/
  POST /platform/tenants/{id}/invite-admin
  POST /platform/tenants/{id}/{suspend,reactivate}
  DELETE /platform/tenants/{id}
  GET  /platform/tenants/{id}/usage-summary
  GET  /platform/audit-logs

Page-level RBAC: ``require_auth(allowed_roles=["tenant_manager"])`` —
tenant_admin / member never see this page.
"""

from __future__ import annotations

import re

import streamlit as st

from api_client import APIError, get_client, require_auth

require_auth(allowed_roles=["tenant_manager"])

st.title("🏢 Platform Management")

client = get_client()

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]*[a-z0-9]$")


# ──────────────────────────────────────────────────────────────────────────────
# Tab layout
# ──────────────────────────────────────────────────────────────────────────────

tab_tenants, tab_create, tab_invite = st.tabs(
    ["📋 Tenants", "➕ Create Tenant", "👤 Invite Admin"]
)


# ──────────────────────────────────────────────────────────────────────────────
# Tab 1 — Tenants list, usage, audit (existing behaviour preserved)
# ──────────────────────────────────────────────────────────────────────────────

with tab_tenants:
    try:
        with st.spinner("Loading tenants…"):
            tenants = client.list_tenants()
    except APIError as exc:
        if exc.status_code == 401:
            st.session_state.clear()
            st.rerun()
            st.stop()
        st.error(str(exc))
        tenants = []

    st.subheader(f"Tenants ({len(tenants)})")

    if not tenants:
        st.info(
            "No tenants yet. Use the **➕ Create Tenant** tab to provision the "
            "first one."
        )
    else:
        tenant_names = [
            t.get("name") or t.get("slug") or str(t.get("id", i))
            for i, t in enumerate(tenants)
        ]
        selected_idx = st.selectbox(
            "Select tenant",
            range(len(tenants)),
            format_func=lambda i: tenant_names[i],
            key="tenant_select_main",
        )
        tenant = tenants[selected_idx]
        tid = str(tenant.get("id", ""))

        # Remember the selected tenant for the Invite-Admin tab.
        st.session_state["selected_tenant_id"] = tid
        st.session_state["selected_tenant_name"] = tenant.get("name", "")
        st.session_state["selected_tenant_status"] = tenant.get("status", "")

        with st.container(border=True):
            col_a, col_b = st.columns(2)
            col_a.markdown(f"**Name:** {tenant.get('name', '—')}")
            col_a.markdown(f"**Slug:** `{tenant.get('slug', '—')}`")
            col_b.markdown(f"**Status:** {tenant.get('status', '—')}")
            col_b.markdown(f"**ID:** `{tid}`")

            # Lifecycle actions — operate on the selected tenant only.
            tenant_status = tenant.get("status", "")
            action_cols = st.columns(3)
            if tenant_status == "active":
                if action_cols[0].button("⏸ Suspend", key=f"suspend_{tid}"):
                    try:
                        client.suspend_tenant(tid)
                        st.success("Tenant suspended.")
                        st.rerun()
                    except APIError as exc:
                        st.error(str(exc))
            elif tenant_status == "suspended":
                if action_cols[0].button("▶ Reactivate", key=f"reactivate_{tid}"):
                    try:
                        client.reactivate_tenant(tid)
                        st.success("Tenant reactivated.")
                        st.rerun()
                    except APIError as exc:
                        st.error(str(exc))

            # Destructive — always behind an extra confirm.
            with action_cols[2]:
                confirm_key = f"confirm_delete_{tid}"
                if st.session_state.get(confirm_key):
                    if st.button("⚠ Confirm delete", key=f"confirm_btn_{tid}"):
                        try:
                            client.delete_tenant(tid)
                            st.session_state.pop(confirm_key, None)
                            st.success("Tenant deletion triggered.")
                            st.rerun()
                        except APIError as exc:
                            st.error(str(exc))
                else:
                    if st.button("🗑 Delete", key=f"delete_{tid}"):
                        st.session_state[confirm_key] = True
                        st.rerun()

        st.divider()

        # ── Per-tenant usage ──────────────────────────────────────────────────
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

        # ── Audit logs ────────────────────────────────────────────────────────
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
                st.markdown(
                    f"- `{ts}` **{action}** on `{resource}` by _{actor}_"
                )


# ──────────────────────────────────────────────────────────────────────────────
# Tab 2 — Create Tenant
# ──────────────────────────────────────────────────────────────────────────────

with tab_create:
    st.subheader("Provision a new tenant")
    st.caption(
        "Creates the tenant row, audit-logs the action, and (if you supply "
        "a contact email or description) seeds a matching public-site "
        "config so the new tenant has branding on day one."
    )

    with st.form("create_tenant_form", clear_on_submit=False):
        col1, col2 = st.columns(2)
        new_name = col1.text_input(
            "Tenant name *", placeholder="Acme Corp", max_chars=255
        )
        new_slug = col2.text_input(
            "Tenant slug *",
            placeholder="acme-corp",
            help="Lowercase, alphanumeric and hyphens only; min 2 chars.",
        )
        new_contact_email = st.text_input(
            "Contact email", placeholder="ops@acme-corp.example"
        )
        new_description = st.text_area(
            "Description (optional)",
            placeholder="Short public-facing description shown on the tenant's site.",
            max_chars=2000,
        )
        submitted = st.form_submit_button(
            "Create tenant", type="primary", use_container_width=True
        )

    if submitted:
        if not new_name.strip() or not new_slug.strip():
            st.error("Tenant name and slug are required.")
        elif not _SLUG_RE.match(new_slug.strip().lower()):
            st.error(
                "Slug must be lowercase alphanumeric with hyphens only, "
                "start and end with alphanumeric, minimum 2 characters."
            )
        else:
            try:
                with st.spinner("Creating tenant…"):
                    tenant = client.create_tenant(
                        name=new_name.strip(),
                        slug=new_slug.strip().lower(),
                        contact_email=(new_contact_email.strip() or None),
                        description=(new_description.strip() or None),
                    )
                st.success(
                    f"Tenant **{tenant.get('name')}** created "
                    f"(slug `{tenant.get('slug')}`, id `{tenant.get('id')}`)."
                )
                st.info(
                    "Next step: switch to the **👤 Invite Admin** tab and "
                    "create the first tenant_admin for this tenant."
                )
                # Pre-select the new tenant so Invite-Admin picks it up.
                st.session_state["selected_tenant_id"] = str(tenant.get("id", ""))
                st.session_state["selected_tenant_name"] = tenant.get("name", "")
                st.session_state["selected_tenant_status"] = tenant.get("status", "")
            except APIError as exc:
                if exc.status_code == 409:
                    st.error(
                        "A tenant with that slug already exists. "
                        "Pick a different slug."
                    )
                else:
                    st.error(str(exc))


# ──────────────────────────────────────────────────────────────────────────────
# Tab 3 — Invite Admin
# ──────────────────────────────────────────────────────────────────────────────

with tab_invite:
    st.subheader("Create the first tenant admin")
    st.caption(
        "Creates a `tenant_admin` user bound to the selected tenant and "
        "returns a one-time temporary password. Copy and deliver it to "
        "the new admin out of band — it is never shown again."
    )

    try:
        with st.spinner("Loading tenants…"):
            invite_tenants = client.list_tenants()
    except APIError as exc:
        st.error(str(exc))
        invite_tenants = []

    if not invite_tenants:
        st.info("Create a tenant first (➕ Create Tenant tab).")
    else:
        # Default the dropdown to the tenant selected on the Tenants tab.
        preselected_id = st.session_state.get("selected_tenant_id", "")
        ids = [str(t.get("id", "")) for t in invite_tenants]
        default_idx = ids.index(preselected_id) if preselected_id in ids else 0

        def _label(i: int) -> str:
            t = invite_tenants[i]
            return f"{t.get('name', '—')} ({t.get('slug', '')}) — {t.get('status', '')}"

        chosen = st.selectbox(
            "Tenant",
            range(len(invite_tenants)),
            index=default_idx,
            format_func=_label,
            key="tenant_select_invite",
        )
        target = invite_tenants[chosen]
        target_id = str(target.get("id", ""))
        target_status = target.get("status", "")

        if target_status != "active":
            st.warning(
                f"This tenant is `{target_status}`. Admins can only be "
                "invited for **active** tenants."
            )

        with st.form("invite_admin_form", clear_on_submit=True):
            invite_email = st.text_input(
                "Admin email *", placeholder="admin@acme-corp.example"
            )
            invite_submitted = st.form_submit_button(
                "Create tenant admin",
                type="primary",
                use_container_width=True,
                disabled=(target_status != "active"),
            )

        if invite_submitted:
            if not invite_email.strip():
                st.error("Admin email is required.")
            else:
                try:
                    with st.spinner("Creating admin…"):
                        result = client.invite_admin(target_id, invite_email.strip())
                    temp_password = result.get("temporary_password") or ""
                    st.success(
                        f"Created admin **{result.get('email')}** for tenant "
                        f"**{target.get('name')}**."
                    )
                    st.markdown("#### Temporary password (shown once)")
                    st.code(temp_password, language="text")
                    st.warning(
                        "Save this password now — it is **not stored in "
                        "plaintext** anywhere and cannot be retrieved again. "
                        "Deliver it to the new admin securely; they should "
                        "rotate it on first login."
                    )
                    st.markdown(
                        f"**Login URL:** open this admin app and sign in as "
                        f"`{result.get('email')}` with the password above."
                    )
                except APIError as exc:
                    if exc.status_code == 409:
                        st.error("That email is already registered to a user.")
                    elif exc.status_code == 422:
                        st.error(
                            "This tenant is suspended — reactivate it first "
                            "(Tenants tab → ▶ Reactivate)."
                        )
                    elif exc.status_code == 404:
                        st.error("Tenant not found or already deleted.")
                    else:
                        st.error(str(exc))
