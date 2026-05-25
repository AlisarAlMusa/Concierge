import streamlit as st

st.set_page_config(page_title="Concierge Admin", layout="wide")

st.title("Concierge Admin")
st.info("Admin dashboard — Person A implements pages in admin_app/pages/")

with st.sidebar:
    st.markdown("### Navigation")
    st.markdown("- Tenant Manager")
    st.markdown("- CMS")
    st.markdown("- Agent Config")
    st.markdown("- Leads")
    st.markdown("- Embed Snippet")
