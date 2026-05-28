"""Leads & escalations management."""

from __future__ import annotations

import streamlit as st

from api_client import APIError, get_client, require_auth

require_auth()

st.title("📋 Leads & Escalations")

client = get_client()

tab_leads, tab_esc = st.tabs(["Leads", "Escalations"])


# ── Leads tab ─────────────────────────────────────────────────────────────────
with tab_leads:
    status_filter = st.selectbox(
        "Filter by status",
        ["all", "new", "contacted", "qualified", "closed"],
        key="leads_filter",
    )

    try:
        with st.spinner("Loading leads…"):
            leads = client.list_leads(
                status=None if status_filter == "all" else status_filter
            )
    except APIError as exc:
        if exc.status_code == 401:
            st.session_state.clear()
            st.switch_page("app.py")
            st.stop()
        st.error(str(exc))
        leads = []

    if not leads:
        st.info("No leads found.")
    else:
        st.caption(f"{len(leads)} lead(s)")
        for lead in leads:
            lid = str(lead.get("id", ""))
            name = lead.get("name") or lead.get("visitor_name") or "Unknown"
            email = lead.get("email", "")
            lstatus = lead.get("status", "new")
            notes = lead.get("notes", "")

            with st.container(border=True):
                c_info, c_actions = st.columns([3, 2])
                with c_info:
                    st.markdown(f"**{name}**")
                    if email:
                        st.caption(email)
                    st.caption(f"Status: {lstatus}")
                    if notes:
                        st.caption(f"Notes: {notes}")

                with c_actions:
                    new_status_options = [s for s in ["new", "contacted", "qualified", "closed"] if s != lstatus]
                    chosen = st.selectbox(
                        "Change status",
                        new_status_options,
                        key=f"lead_status_{lid}",
                        label_visibility="collapsed",
                    )
                    new_notes = st.text_input(
                        "Notes",
                        value=notes,
                        key=f"lead_notes_{lid}",
                        label_visibility="collapsed",
                        placeholder="Notes…",
                    )
                    if st.button("Update", key=f"lead_update_{lid}"):
                        try:
                            with st.spinner("Updating…"):
                                client.update_lead(lid, status=chosen, notes=new_notes or None)
                            st.rerun()
                        except APIError as exc:
                            st.error(str(exc))


# ── Escalations tab ───────────────────────────────────────────────────────────
with tab_esc:
    esc_filter = st.selectbox(
        "Filter by status",
        ["all", "open", "in_progress", "resolved"],
        key="esc_filter",
    )

    try:
        with st.spinner("Loading escalations…"):
            escalations = client.list_escalations(
                status=None if esc_filter == "all" else esc_filter
            )
    except APIError as exc:
        st.error(str(exc))
        escalations = []

    if not escalations:
        st.info("No escalations found.")
    else:
        st.caption(f"{len(escalations)} escalation(s)")
        for esc in escalations:
            eid = str(esc.get("id", ""))
            reason = esc.get("reason") or esc.get("summary", "No reason given")
            estatus = esc.get("status", "open")

            with st.container(border=True):
                c_info, c_actions = st.columns([3, 2])
                with c_info:
                    st.markdown(f"**{reason[:80]}**")
                    st.caption(f"Status: {estatus}")

                with c_actions:
                    new_esc_options = [s for s in ["open", "in_progress", "resolved"] if s != estatus]
                    chosen_esc = st.selectbox(
                        "Change status",
                        new_esc_options,
                        key=f"esc_status_{eid}",
                        label_visibility="collapsed",
                    )
                    if st.button("Update", key=f"esc_update_{eid}"):
                        try:
                            with st.spinner("Updating…"):
                                client.update_escalation(eid, status=chosen_esc)
                            st.rerun()
                        except APIError as exc:
                            st.error(str(exc))
