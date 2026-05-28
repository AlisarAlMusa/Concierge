"""Agent & guardrail configuration for the current tenant."""

from __future__ import annotations

import streamlit as st

from api_client import APIError, get_client, require_auth

require_auth()

st.title("🤖 Agent Config")

client = get_client()

# ── Load current config ───────────────────────────────────────────────────────
try:
    with st.spinner("Loading config…"):
        config = client.get_tenant_config()
except APIError as exc:
    if exc.status_code == 401:
        st.session_state.clear()
        st.switch_page("app.py")
        st.stop()
    st.error(str(exc))
    config = {}

st.info(
    "Platform safety rails (prompt injection, PII redaction) are managed by the platform "
    "and cannot be modified here."
)

# ── Helper: editable list widget ─────────────────────────────────────────────

def _editable_list(label: str, key: str, initial: list[str]) -> list[str]:
    """Render an editable list using session state; returns current items."""
    if key not in st.session_state:
        st.session_state[key] = list(initial)

    items: list[str] = st.session_state[key]
    st.markdown(f"**{label}**")

    to_remove: int | None = None
    for i, item in enumerate(items):
        c_item, c_del = st.columns([5, 1])
        c_item.text(item)
        if c_del.button("✕", key=f"{key}_del_{i}"):
            to_remove = i

    if to_remove is not None:
        items.pop(to_remove)
        st.session_state[key] = items
        st.rerun()

    new_val = st.text_input(f"Add to {label.lower()}", key=f"{key}_input", label_visibility="collapsed", placeholder=f"New entry…")
    if st.button(f"Add", key=f"{key}_add"):
        stripped = new_val.strip()
        if stripped and stripped not in items:
            items.append(stripped)
            st.session_state[key] = items
            st.rerun()

    return items


# ── Config form ───────────────────────────────────────────────────────────────
with st.form("agent_config_form"):
    st.subheader("Persona")
    persona = st.text_area(
        "System persona / instructions",
        value=config.get("persona", ""),
        height=150,
        help="The agent's personality and guiding instructions.",
    )

    st.divider()
    st.subheader("Refusal Tone")
    tone_options = ["polite", "firm", "neutral"]
    current_tone = config.get("refusal_tone", "polite")
    tone_idx = tone_options.index(current_tone) if current_tone in tone_options else 0
    refusal_tone = st.selectbox("Refusal tone", tone_options, index=tone_idx)

    st.divider()
    st.subheader("Enabled Tools")
    all_tools = ["rag_search", "capture_lead", "escalate"]
    enabled_tools = st.multiselect(
        "Enabled tools",
        options=all_tools,
        default=[t for t in config.get("enabled_tools", all_tools) if t in all_tools],
    )

    save = st.form_submit_button("Save Config", use_container_width=True)

# Topic lists live outside the form (dynamic add/remove)
st.divider()
allowed_topics = _editable_list(
    "Allowed Topics",
    "allowed_topics",
    config.get("allowed_topics", []),
)
st.divider()
blocked_topics = _editable_list(
    "Blocked Topics",
    "blocked_topics",
    config.get("blocked_topics", []),
)

if save:
    try:
        with st.spinner("Saving…"):
            client.update_tenant_config(
                persona=persona,
                refusal_tone=refusal_tone,
                enabled_tools=enabled_tools,
                allowed_topics=allowed_topics,
                blocked_topics=blocked_topics,
            )
        st.success("Config saved.")
    except APIError as exc:
        if exc.status_code in (404, 405, 501):
            st.warning(
                "Agent config endpoint not yet available (Person C's scope). "
                "Your changes were not persisted."
            )
        else:
            st.error(str(exc))
