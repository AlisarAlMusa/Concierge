"""CMS management — list, create, edit, publish, delete, reindex pages."""

from __future__ import annotations

import streamlit as st

from api_client import APIError, get_client, require_auth

require_auth()

st.title("📝 CMS Pages")

client = get_client()


def _load_pages() -> list[dict]:
    try:
        return client.list_cms_pages()
    except APIError as exc:
        if exc.status_code == 401:
            st.warning("Session expired. Please log in again.")
            st.session_state.clear()
            st.rerun()
            st.stop()
        st.error(str(exc))
        return []


# ── New page form ─────────────────────────────────────────────────────────────
with st.expander("➕ New Page", expanded=False):
    with st.form("new_page_form", clear_on_submit=True):
        new_title = st.text_input("Title")
        new_slug = st.text_input("Slug", placeholder="my-page-slug")
        new_body = st.text_area("Body (Markdown)", height=200)
        new_status = st.selectbox("Status", ["draft", "published"])
        save_new = st.form_submit_button("Create Page", use_container_width=True)

    if save_new:
        if not new_title or not new_slug:
            st.error("Title and slug are required.")
        else:
            try:
                with st.spinner("Creating…"):
                    client.create_cms_page(
                        title=new_title, body=new_body, slug=new_slug, status=new_status
                    )
                st.success(f"Page **{new_title}** created.")
                st.rerun()
            except APIError as exc:
                st.error(str(exc))

st.divider()

# ── Page list ─────────────────────────────────────────────────────────────────
pages = _load_pages()

if not pages:
    st.info("No CMS pages yet. Create your first page above.")
else:
    st.caption(f"{len(pages)} page(s) total")

    for page in pages:
        pid = str(page.get("id", ""))
        title = page.get("title", "Untitled")
        slug = page.get("slug", "")
        status = page.get("status", "draft")
        status_icon = "🟢" if status == "published" else "🟡"

        with st.container(border=True):
            col_info, col_actions = st.columns([3, 2])

            with col_info:
                st.markdown(f"{status_icon} **{title}**")
                st.caption(f"slug: `{slug}` · status: {status}")

            with col_actions:
                b1, b2, b3, b4 = st.columns(4)

                toggle_label = "Unpublish" if status == "published" else "Publish"
                new_status_val = "draft" if status == "published" else "published"
                if b1.button(toggle_label, key=f"pub_{pid}"):
                    try:
                        with st.spinner("Updating…"):
                            client.update_cms_page(pid, status=new_status_val)
                        st.rerun()
                    except APIError as exc:
                        st.error(str(exc))

                if b2.button("Reindex", key=f"reindex_{pid}"):
                    try:
                        with st.spinner("Reindexing…"):
                            client.reindex_cms_page(pid)
                        st.success("Reindex triggered.")
                    except APIError as exc:
                        if exc.status_code == 404:
                            st.warning(str(exc))
                        else:
                            st.error(str(exc))

                if b3.button("Edit", key=f"edit_btn_{pid}"):
                    st.session_state[f"edit_{pid}"] = not st.session_state.get(
                        f"edit_{pid}", False
                    )

                if b4.button("🗑", key=f"del_{pid}"):
                    st.session_state[f"confirm_del_{pid}"] = True

            # Delete confirmation
            if st.session_state.get(f"confirm_del_{pid}"):
                st.warning(f"Delete **{title}**? This cannot be undone.")
                c1, c2 = st.columns(2)
                if c1.button("Yes, delete", key=f"yes_{pid}"):
                    try:
                        with st.spinner("Deleting…"):
                            client.delete_cms_page(pid)
                        st.session_state.pop(f"confirm_del_{pid}", None)
                        st.rerun()
                    except APIError as exc:
                        st.error(str(exc))
                if c2.button("Cancel", key=f"no_{pid}"):
                    st.session_state.pop(f"confirm_del_{pid}", None)
                    st.rerun()

            # Inline edit form
            if st.session_state.get(f"edit_{pid}"):
                if f"body_{pid}" not in st.session_state:
                    try:
                        full = client.get_cms_page(pid)
                        st.session_state[f"body_{pid}"] = full.get("body", "")
                    except APIError:
                        st.session_state[f"body_{pid}"] = ""

                with st.form(f"edit_form_{pid}"):
                    edit_title = st.text_input("Title", value=title)
                    edit_slug = st.text_input("Slug", value=slug)
                    edit_body = st.text_area(
                        "Body (Markdown)",
                        value=st.session_state.get(f"body_{pid}", ""),
                        height=250,
                    )
                    edit_status = st.selectbox(
                        "Status",
                        ["draft", "published"],
                        index=0 if status == "draft" else 1,
                    )
                    save_edit = st.form_submit_button("Save Changes", use_container_width=True)

                if save_edit:
                    try:
                        with st.spinner("Saving…"):
                            client.update_cms_page(
                                pid,
                                title=edit_title,
                                slug=edit_slug,
                                body=edit_body,
                                status=edit_status,
                            )
                        st.session_state.pop(f"edit_{pid}", None)
                        st.session_state.pop(f"body_{pid}", None)
                        st.success("Saved.")
                        st.rerun()
                    except APIError as exc:
                        st.error(str(exc))
