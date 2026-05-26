# Feature Specification: Admin App (Streamlit)

> **Owner**: Person A — `feature/platform-tenancy` branch

**Feature Branch**: `014-admin-app`

**Created**: 2026-05-27

**Status**: Draft

---

## User Scenarios & Testing *(mandatory)*

### User Story 1 — Tenant Admin Logs In and Sees Their Dashboard (Priority: P1)

A tenant admin logs in via the Streamlit admin app with their email and password. After login, they see their tenant's dashboard — CMS pages, leads count, escalation count, and usage summary. Data from other tenants is never shown.

**Why this priority**: The admin app is the operational control plane for tenant admins. Without login, nothing else in the app is accessible.

**Independent Test**: Log in as Tenant A admin. Confirm only Tenant A data appears. Log in as Tenant B admin. Confirm only Tenant B data appears. Confirm no cross-tenant bleed.

**Acceptance Scenarios**:

1. **Given** the admin app is open, **When** valid credentials are entered, **Then** the user is authenticated and redirected to their tenant dashboard.
2. **Given** invalid credentials, **When** login is attempted, **Then** an error is shown and no data is accessible.
3. **Given** Tenant A admin is logged in, **When** the dashboard loads, **Then** only Tenant A CMS pages, leads, escalations, and usage are shown.

---

### User Story 2 — Tenant Admin Manages CMS Content (Priority: P1)

A tenant admin can view, create, edit, and publish CMS pages from the admin app. They can trigger a reindex for a page or all pages. Changes are reflected immediately in the API.

**Why this priority**: CMS management is the primary content workflow for tenant admins. Without this page, they cannot build the knowledge base for their AI agent.

**Independent Test**: Create and publish a page in the admin app. Query the API directly; confirm the page is published and indexed.

**Acceptance Scenarios**:

1. **Given** the CMS page in the admin app, **When** a new page is created and published, **Then** the page appears in the API and its chunks are indexed.
2. **Given** an existing page, **When** it is edited and saved, **Then** the API reflects the update and a reindex is triggered.
3. **Given** the CMS list page, **When** it is viewed, **Then** all the tenant's pages are shown with status (draft/published).

---

### User Story 3 — Tenant Admin Configures Agent and Guardrails (Priority: P2)

A tenant admin can update their agent persona, set allowed/blocked topics, configure the refusal tone, and enable or disable agent tools from the admin app. Changes take effect on the next chat request.

**Why this priority**: Agent personalisation is a key selling point for the SaaS. Admins must be able to configure their agent's behaviour without a code deploy.

**Independent Test**: Update the persona and blocked topics via the admin app. Send a chat message. Confirm the agent uses the new persona and blocks the configured topic.

**Acceptance Scenarios**:

1. **Given** the agent config page, **When** a new persona is saved, **Then** the next chat request uses the updated persona.
2. **Given** a blocked topic is configured, **When** a visitor asks about that topic, **Then** the guardrail fires and the configured refusal message is returned.
3. **Given** the admin configures platform-protected rail settings, **When** those settings are saved, **Then** platform rails are unaffected (the app does not expose platform rail controls).

---

### User Story 4 — Tenant Admin Views Leads and Escalations (Priority: P2)

A tenant admin views their leads list and escalations list in the admin app. They can click into a lead or escalation for detail, update statuses, and add notes.

**Why this priority**: Operational visibility for converting leads and resolving escalations is the business value of the whole system.

**Independent Test**: Create test leads and escalations via the API. View them in the admin app. Update a lead status. Confirm the change persists.

**Acceptance Scenarios**:

1. **Given** leads exist for a tenant, **When** the leads page is viewed, **Then** all leads are listed with name, email, intent, score, and status.
2. **Given** a lead is selected, **When** the admin updates its status, **Then** the change is saved via the API.
3. **Given** escalations exist, **When** the escalations page is viewed, **Then** all open escalations are shown with conversation context.

---

### User Story 5 — Tenant Admin Copies the Widget Embed Snippet (Priority: P2)

A tenant admin visits the embed snippet page, sees their widget's script tag pre-filled with their `data-widget-id`, and can copy it to their clipboard with one click.

**Why this priority**: The embed snippet is the final step for going live. If the admin cannot easily find or copy it, adoption friction increases.

**Independent Test**: Navigate to the embed snippet page. Confirm the script tag is pre-filled with the correct `public_widget_id`. Confirm copy-to-clipboard works.

**Acceptance Scenarios**:

1. **Given** a tenant has a configured widget, **When** the embed snippet page is visited, **Then** the correct script tag is displayed.
2. **Given** the script tag, **When** the copy button is clicked, **Then** the tag is copied to the clipboard.

---

### Edge Cases

- What happens when the Streamlit app cannot reach the API? → An error banner is shown; the user is not locked out but cannot see data.
- What happens when a session expires while the admin is working? → The admin is redirected to the login page.
- What happens when the admin app loads before the API is ready? → A loading indicator is shown; no crash.

---

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The admin app MUST require login with the same credentials as the main API (email/password → JWT).
- **FR-002**: The app MUST expose a CMS management page: list, create, edit, publish, delete, and reindex pages.
- **FR-003**: The app MUST expose an agent/guardrail config page: update persona, allowed/blocked topics, refusal tone, enabled tools.
- **FR-004**: The agent config page MUST NOT expose platform rail controls — only tenant-configurable rails are editable.
- **FR-005**: The app MUST expose a leads page: list, detail view, status update, notes.
- **FR-006**: The app MUST expose an escalations page: list, detail view, status update.
- **FR-007**: The app MUST expose an embed snippet page: display the script tag with `data-widget-id`, one-click copy.
- **FR-008**: All data shown in the admin app MUST be fetched from the API — the app MUST NOT query the database directly.
- **FR-009**: The app MUST only show data belonging to the authenticated tenant admin's tenant — no cross-tenant data.
- **FR-010**: The Tenant Manager login MUST show a platform view (tenant list, usage summary, audit logs) rather than a tenant dashboard.
- **FR-011**: The app MUST handle API errors gracefully with user-visible error messages (not raw stack traces).

### Key Entities

- **Admin App Pages**: Login, Dashboard, CMS Management, Agent Config, Leads, Escalations, Embed Snippet, (Tenant Manager: Tenant List, Usage, Audit Logs).
- **All data** is fetched via the API using the authenticated JWT — the Streamlit app is a thin API client.

---

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A tenant admin can complete a full workflow (login → create CMS page → publish → view lead) in under 5 minutes.
- **SC-002**: 100% of data shown in the admin app is scoped to the logged-in tenant — zero cross-tenant data leakage.
- **SC-003**: Agent config changes are reflected in chat behaviour within one request of being saved.
- **SC-004**: The embed snippet page shows the correct script tag for 100% of configured widgets.
- **SC-005**: The app handles API unavailability gracefully — no unhandled Python exceptions visible to the user.

---

## Assumptions

- The Streamlit admin app is a thin API client — it calls the FastAPI backend; it does not have its own DB connection.
- Authentication state is stored in Streamlit session state (in-memory for the browser session); tokens are not persisted to disk.
- The Tenant Manager view is a separate page/section in the same app, accessible only after login as `tenant_manager` role.
- The admin app does not implement file upload for CMS content in Week 8 (MinIO blob management is out of scope).
- Pagination in list views uses the API's pagination parameters; the UI loads the first page by default.
- The embed snippet copy uses the browser clipboard API via a Streamlit component or a simple copy button.
