# Quickstart: Admin App — Spec 014

## Running the Admin App

```bash
# From repo root — starts everything including admin_app on port 8501
docker compose up --build

# Admin app only (requires api to be running)
cd admin_app
uv run streamlit run app.py --server.port 8501
```

Open: http://localhost:8501

---

## Smoke Test Scenarios

### 1. Login as Tenant Admin

1. Open http://localhost:8501
2. Enter email: `admin@tenant-a.com`, password: `password123`
3. ✅ Redirected to Dashboard showing Tenant A stats
4. ✅ Sidebar shows: Dashboard, CMS, Agent Config, Leads, Escalations, Embed Snippet
5. ✅ Sidebar does NOT show: Tenant Manager

### 2. Login with Wrong Password

1. Enter any email with wrong password
2. ✅ Error banner: "Invalid credentials"
3. ✅ No data visible, stays on login page

### 3. CMS Page Workflow

1. Log in as tenant admin
2. Navigate to CMS
3. Click "New Page"
4. Fill title, slug, body (markdown), status = draft
5. Click Save
6. ✅ Page appears in list with status "draft"
7. Click "Publish" on the page
8. ✅ Status changes to "published"
9. Verify: `curl http://localhost:8000/cms/ -H "Authorization: Bearer <token>"` shows the page

### 4. Agent Config Update

1. Navigate to Agent Config
2. Change persona text
3. Add "competitor pricing" to blocked topics
4. Click Save
5. ✅ Success message shown
6. Send a chat about competitor pricing via widget
7. ✅ Guardrail fires, refusal message returned

### 5. Embed Snippet Copy

1. Navigate to Embed Snippet
2. ✅ Script tag displayed with correct `data-widget-id`
3. Click Copy button
4. ✅ Clipboard contains the script tag

### 6. Login as Tenant Manager

1. Open http://localhost:8501
2. Enter Tenant Manager credentials
3. ✅ Redirected to Tenant Manager page (NOT Dashboard)
4. ✅ Sidebar shows: Tenant Manager only
5. ✅ Tenant list with usage summaries visible

### 7. Session Expiry

1. Log in as tenant admin
2. Manually clear `st.session_state` (or wait for token expiry)
3. Navigate to CMS
4. ✅ Redirected to login page, error: "Session expired"

### 8. API Unavailable

1. Stop the API container: `docker compose stop api`
2. Open http://localhost:8501 and try to log in
3. ✅ Error banner: "Cannot reach the API. Check your connection."
4. ✅ No Python traceback visible
