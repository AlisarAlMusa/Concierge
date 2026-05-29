"""Embed snippet — show the widget script tag and allow clipboard copy."""

from __future__ import annotations

import streamlit as st
import streamlit.components.v1 as components

from api_client import API_BASE_URL, APIError, get_client, require_auth

require_auth()

st.title("🔗 Embed Snippet")

client = get_client()

# ── Load widgets ──────────────────────────────────────────────────────────────
try:
    with st.spinner("Loading widgets…"):
        widgets = client.list_widgets()
except APIError as exc:
    if exc.status_code == 401:
        st.warning("Session expired. Please log in again.")
        st.session_state.clear()
        st.rerun()
        st.stop()
    st.error(str(exc))
    widgets = []

if not widgets:
    st.info("No widgets configured yet. Contact support to set up your widget.")
    st.stop()

# ── Widget selector ───────────────────────────────────────────────────────────
widget_labels = [w.get("name") or w.get("public_widget_id", str(w.get("id", i))) for i, w in enumerate(widgets)]
selected_idx = st.selectbox("Select widget", range(len(widgets)), format_func=lambda i: widget_labels[i])
widget = widgets[selected_idx]

pub_id = widget.get("public_widget_id") or widget.get("id", "")

# Derive the API base for the snippet (strip trailing slash)
api_origin = API_BASE_URL.rstrip("/")

snippet = f'<script src="{api_origin}/widget.js" data-widget-id="{pub_id}" async></script>'

# ── Display snippet ───────────────────────────────────────────────────────────
st.subheader("Paste this into your website's `<head>` or before `</body>`")
st.code(snippet, language="html")

# ── Clipboard copy button via components.html ─────────────────────────────────
copy_html = f"""
<button
  onclick="navigator.clipboard.writeText({snippet!r}).then(()=>{{
    this.textContent='✓ Copied!';
    setTimeout(()=>this.textContent='Copy to clipboard', 2000);
  }})"
  style="
    padding: 0.4rem 1rem;
    background: #0e1117;
    color: #fafafa;
    border: 1px solid #555;
    border-radius: 4px;
    cursor: pointer;
    font-size: 0.9rem;
  "
>Copy to clipboard</button>
"""
components.html(copy_html, height=50)

st.divider()
st.caption(
    "The widget loads asynchronously and identifies your tenant via the `data-widget-id` attribute. "
    "No API keys are exposed in the browser."
)
