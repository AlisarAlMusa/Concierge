"""Widgets & Embed Snippet — tenant-admin self-service widget management.

Three views in one page:

  • Empty state — "Create your first widget" form (one origin per line).
  • Widget list — selector + currently-selected snippet + copy button.
  • Edit panel — name, greeting, enabled, allowed_origins; delete with confirm.

Every write goes through the API (``POST/PATCH/DELETE /widgets/*``); no
SQL, no seed-script fallback. ``tenant_id`` is bound to the calling
admin's JWT server-side, so this page can only ever touch the caller's
own widgets.
"""

from __future__ import annotations

import streamlit as st
import streamlit.components.v1 as components

from api_client import API_BASE_URL, APIError, get_client, require_auth

require_auth()

st.title("🔗 Widgets & Embed Snippet")

client = get_client()


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────


def _parse_origins(text: str) -> list[str]:
    """Split a multiline textarea into one-origin-per-line, trimmed.

    Server-side validation is authoritative — this helper just shapes the
    payload before it's sent so the user doesn't see a 422 for trailing
    whitespace or blank lines.
    """
    return [line.strip() for line in (text or "").splitlines() if line.strip()]


def _snippet_for(public_widget_id: str) -> str:
    """Build the <script> tag that goes on the customer's site."""
    api_origin = API_BASE_URL.rstrip("/")
    return (
        f'<script src="{api_origin}/widget.js" '
        f'data-widget-id="{public_widget_id}" async></script>'
    )


def _render_copy_button(snippet: str) -> None:
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


# ──────────────────────────────────────────────────────────────────────────────
# Load widgets for the caller's tenant
# ──────────────────────────────────────────────────────────────────────────────


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


# ──────────────────────────────────────────────────────────────────────────────
# Empty state — "Create your first widget"
# ──────────────────────────────────────────────────────────────────────────────


if not widgets:
    st.info(
        "No widgets configured yet. Create one below — it only takes a "
        "name and the list of origins (host pages) the chat is allowed "
        "to load on."
    )
    with st.form("create_first_widget"):
        new_name = st.text_input("Widget name *", value="default", max_chars=255)
        new_greeting = st.text_area(
            "Greeting",
            value="Hi! How can we help?",
            max_chars=500,
            help="First message the chat shows when it opens.",
        )
        new_origins = st.text_area(
            "Allowed origins *",
            value="http://localhost:5500\nhttp://localhost:8000",
            height=150,
            help=(
                "One full origin per line — scheme + host + optional port. "
                "Example: `https://www.example.com` or `http://localhost:5500`. "
                "No paths, no wildcards. Exact-match comparison server-side."
            ),
        )
        new_enabled = st.checkbox("Enabled", value=True)
        submitted = st.form_submit_button(
            "Create widget", type="primary", use_container_width=True
        )

    if submitted:
        origins = _parse_origins(new_origins)
        if not new_name.strip():
            st.error("Widget name is required.")
        elif not origins:
            st.error("At least one allowed origin is required.")
        else:
            try:
                with st.spinner("Creating widget…"):
                    widget = client.create_widget(
                        name=new_name.strip(),
                        allowed_origins=origins,
                        greeting=new_greeting,
                        theme={},
                        enabled=new_enabled,
                    )
                st.success(
                    f"Widget **{widget['name']}** created — "
                    f"public id `{widget['public_widget_id']}`."
                )
                st.rerun()
            except APIError as exc:
                if exc.status_code == 422:
                    st.error(
                        "One of your origins didn't pass validation. "
                        "Use full origins like `https://example.com` or "
                        "`http://localhost:5500` — no paths, no wildcards. "
                        f"Server said: {exc}"
                    )
                else:
                    st.error(str(exc))
    st.stop()


# ──────────────────────────────────────────────────────────────────────────────
# Existing widgets — picker, snippet, editor, delete
# ──────────────────────────────────────────────────────────────────────────────


col_picker, col_action = st.columns([3, 1])

with col_picker:
    labels = [
        f"{w['name']}  ({'enabled' if w.get('enabled') else 'disabled'})"
        for w in widgets
    ]
    selected_idx = st.selectbox(
        "Widget",
        range(len(widgets)),
        format_func=lambda i: labels[i],
        key="widget_picker",
    )
    widget = widgets[selected_idx]
    pub_id = widget["public_widget_id"]
    wid = widget["id"]

with col_action:
    st.markdown("&nbsp;")  # vertical alignment
    if st.button("➕ New widget", use_container_width=True):
        st.session_state["_show_new_widget_form"] = True


# ── Embed snippet for the selected widget ────────────────────────────────────

st.subheader("Embed snippet")
st.caption("Paste this into your website's `<head>` or right before `</body>`.")
snippet = _snippet_for(pub_id)
st.code(snippet, language="html")
_render_copy_button(snippet)

st.divider()


# ── Editor ───────────────────────────────────────────────────────────────────

st.subheader("Edit widget")

with st.form(f"edit_widget_{wid}"):
    ed_name = st.text_input("Widget name", value=widget.get("name") or "", max_chars=255)
    ed_greeting = st.text_area(
        "Greeting",
        value=widget.get("greeting") or "",
        max_chars=500,
        help="First message the chat shows when it opens.",
    )
    ed_origins = st.text_area(
        "Allowed origins (one per line)",
        value="\n".join(widget.get("allowed_origins") or []),
        height=150,
        help=(
            "Full origins only — scheme + host + optional port. "
            "Server validates exact-match against this list."
        ),
    )
    ed_enabled = st.checkbox("Enabled", value=bool(widget.get("enabled", True)))
    saved = st.form_submit_button("Save changes", type="primary", use_container_width=True)

if saved:
    payload: dict = {}
    parsed_origins = _parse_origins(ed_origins)
    payload["name"] = ed_name.strip()
    payload["greeting"] = ed_greeting
    payload["allowed_origins"] = parsed_origins
    payload["enabled"] = ed_enabled
    if not payload["name"]:
        st.error("Widget name is required.")
    elif not parsed_origins and ed_enabled:
        st.error(
            "An enabled widget must have at least one allowed origin — "
            "otherwise no host site can mint a session token."
        )
    else:
        try:
            with st.spinner("Saving…"):
                client.update_widget(wid, **payload)
            st.success("Widget updated.")
            st.rerun()
        except APIError as exc:
            if exc.status_code == 422:
                st.error(
                    "Validation failed — likely a malformed origin. "
                    f"Server said: {exc}"
                )
            else:
                st.error(str(exc))


st.divider()


# ── Delete ───────────────────────────────────────────────────────────────────

st.subheader("Danger zone")

confirm_key = f"_confirm_delete_widget_{wid}"
if st.session_state.get(confirm_key):
    st.warning(
        f"Delete widget **{widget.get('name')}** "
        f"(`{pub_id}`)? Visitor sessions already minted "
        "will keep working until their token expires (≤ 15 minutes)."
    )
    confirm_col1, confirm_col2 = st.columns(2)
    if confirm_col1.button("⚠ Confirm delete", type="primary", use_container_width=True):
        try:
            client.delete_widget(wid)
            st.success("Widget deleted.")
            st.session_state.pop(confirm_key, None)
            st.rerun()
        except APIError as exc:
            st.error(str(exc))
    if confirm_col2.button("Cancel", use_container_width=True):
        st.session_state.pop(confirm_key, None)
        st.rerun()
else:
    if st.button("🗑 Delete this widget"):
        st.session_state[confirm_key] = True
        st.rerun()


# ──────────────────────────────────────────────────────────────────────────────
# "New widget" form (when user clicks the ➕ New widget button)
# ──────────────────────────────────────────────────────────────────────────────


if st.session_state.get("_show_new_widget_form"):
    st.divider()
    st.subheader("Create another widget")
    with st.form("create_additional_widget"):
        add_name = st.text_input("Widget name *", value="", max_chars=255)
        add_greeting = st.text_area("Greeting", value="", max_chars=500)
        add_origins = st.text_area(
            "Allowed origins *",
            value="",
            height=150,
            help="One full origin per line — e.g. `https://example.com`.",
        )
        add_enabled = st.checkbox("Enabled", value=True)
        col_create, col_cancel = st.columns(2)
        create_clicked = col_create.form_submit_button(
            "Create widget", type="primary", use_container_width=True
        )
        cancel_clicked = col_cancel.form_submit_button(
            "Cancel", use_container_width=True
        )

    if cancel_clicked:
        st.session_state.pop("_show_new_widget_form", None)
        st.rerun()

    if create_clicked:
        origins = _parse_origins(add_origins)
        if not add_name.strip():
            st.error("Widget name is required.")
        elif not origins:
            st.error("At least one allowed origin is required.")
        else:
            try:
                with st.spinner("Creating widget…"):
                    client.create_widget(
                        name=add_name.strip(),
                        allowed_origins=origins,
                        greeting=add_greeting,
                        theme={},
                        enabled=add_enabled,
                    )
                st.session_state.pop("_show_new_widget_form", None)
                st.success("Widget created.")
                st.rerun()
            except APIError as exc:
                if exc.status_code == 422:
                    st.error(
                        "Validation failed — likely a malformed origin. "
                        f"Server said: {exc}"
                    )
                else:
                    st.error(str(exc))


st.caption(
    "The widget loads asynchronously and identifies your tenant via the "
    "`data-widget-id` attribute. Origins are matched exactly on the "
    "server (no wildcards, no path prefixes), and CORS / CSP are layered "
    "on top as defense-in-depth — they are never the boundary."
)
