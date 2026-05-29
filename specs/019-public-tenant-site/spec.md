# Feature Specification: Public Tenant Website

**Feature Branch**: `022-public-tenant-site`

**Created**: 2026-05-29

**Status**: Draft

**Input**: User description: "Implement a simple public website page for each tenant displaying CMS content and embedded Concierge chat widget."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Visitor Views Tenant Public Page (Priority: P1)

A website visitor navigates to a tenant's public page URL and sees the company's published CMS content along with an embedded chat widget. The page is completely read-only and public — no authentication required.

**Why this priority**: This is the core deliverable. Without a working public page, there is nothing to demo. All other stories depend on this.

**Independent Test**: Navigate to `GET /sites/abc-gym` in a browser and verify the page renders with the tenant's name, description, published CMS sections, and the chat widget script tag.

**Acceptance Scenarios**:

1. **Given** tenant `abc-gym` exists and is active with published CMS content, **When** a visitor opens `/sites/abc-gym`, **Then** the page displays the tenant's name, public description, all published CMS sections, and the embedded widget script.
2. **Given** tenant `abc-gym` has a CMS page with status `draft`, **When** a visitor opens `/sites/abc-gym`, **Then** the draft page is not shown.
3. **Given** tenant `abc-gym` has a CMS page with status `archived`, **When** a visitor opens `/sites/abc-gym`, **Then** the archived page is not shown.
4. **Given** the slug `unknown-tenant` does not match any tenant, **When** a visitor opens `/sites/unknown-tenant`, **Then** the server returns a 404 response.
5. **Given** tenant `abc-gym` has status `suspended`, **When** a visitor opens `/sites/abc-gym`, **Then** the server returns a 403 or renders an unavailable page — no CMS content is exposed.

---

### User Story 2 - Tenant Isolation Between Public Pages (Priority: P1)

Two different tenants each have their own separate public page. Visiting one tenant's URL shows only that tenant's content — never the other tenant's.

**Why this priority**: Tenant isolation is a non-negotiable platform rule. Leaking one tenant's content onto another tenant's page is a critical security failure.

**Independent Test**: Seed two tenants (`abc-gym` and `green-clinic`) with distinct published CMS content. Visit each URL and confirm the content is strictly separated.

**Acceptance Scenarios**:

1. **Given** `abc-gym` and `green-clinic` both have published CMS content, **When** a visitor opens `/sites/abc-gym`, **Then** only ABC Gym content appears — no Green Clinic content.
2. **Given** `abc-gym` and `green-clinic` both have published CMS content, **When** a visitor opens `/sites/green-clinic`, **Then** only Green Clinic content appears — no ABC Gym content.
3. **Given** a request to `/sites/abc-gym` is received, **When** the backend resolves the tenant, **Then** the tenant is derived from `tenant_slug` in the path — never from a request body or query parameter.

---

### User Story 3 - Chat Widget Loads on Public Page (Priority: P2)

The embedded chat widget appears on the tenant's public page and uses the correct `widget_id` for that tenant. The widget's authentication still follows the signed token flow — the public page only injects the script tag with the public `widget_id`.

**Why this priority**: The widget is the core product feature being demonstrated. Without it, the page proves nothing about the end-to-end flow.

**Independent Test**: Load `/sites/abc-gym` and verify the `<script>` tag is present with the correct `data-widget-id` for ABC Gym's widget.

**Acceptance Scenarios**:

1. **Given** tenant `abc-gym` has a widget configured, **When** the page renders, **Then** a `<script src="/widget.js" data-widget-id="<abc-gym-widget-id>">` tag is present in the HTML.
2. **Given** tenant `abc-gym`'s page loads the widget, **When** a visitor sends a chat message, **Then** the widget requests a signed session token from `POST /public/widgets/session` — it does not pass `tenant_id` directly.

---

### User Story 4 - Optional JSON API for Public Site Data (Priority: P3)

A JSON endpoint returns the same tenant public data for use by a React frontend or external integration. This is optional and only built if there is extra time.

**Why this priority**: Jinja2 server-rendered HTML is the MVP. JSON API is a nice-to-have for future React migration.

**Independent Test**: Call `GET /api/public/sites/abc-gym` and verify the response contains tenant, config, published pages, and widget fields.

**Acceptance Scenarios**:

1. **Given** tenant `abc-gym` exists and is active, **When** `GET /api/public/sites/abc-gym` is called, **Then** the response is JSON containing `tenant`, `config`, `pages` (published only), and `widget` fields.
2. **Given** tenant slug does not exist, **When** the JSON endpoint is called, **Then** the response is `404`.

---

### Edge Cases

- What happens when a tenant exists but has no published CMS content? → Page renders with tenant name and description but shows an empty content area (no error).
- What happens when a tenant exists but has no `tenant_config` row? → Page renders with fallback values (blank description, default theme color); no 500 error.
- What happens when a tenant has no widget configured? → Page renders without the widget script tag; no crash.
- What happens when `tenant_slug` contains invalid characters (e.g., SQL injection attempt, path traversal)? → Slug is validated as alphanumeric + hyphens; invalid slugs return 400 or 404.
- What happens when two tenants have similar slugs (e.g., `abc-gym` vs `abc-gym-2`)? → Each resolves independently by exact slug match.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST resolve the tenant exclusively from `tenant_slug` in the URL path — never from the request body or query parameters.
- **FR-002**: System MUST return HTTP 404 when no tenant matches the provided slug.
- **FR-003**: System MUST return HTTP 403 (or render an unavailable page) when the matched tenant has status `suspended`.
- **FR-004**: System MUST only render CMS pages with status `published` — drafts and archived pages MUST NOT appear.
- **FR-005**: System MUST render the tenant's name and public description on the page.
- **FR-006**: System MUST render each published CMS page as a titled content section on the public page.
- **FR-007**: System MUST include the tenant's widget embed script tag (`<script src="/widget.js" data-widget-id="...">`) when a widget is configured for the tenant.
- **FR-008**: System MUST NOT expose leads, conversations, prompts, guardrail configs, cost data, audit logs, or any private configuration on the public page.
- **FR-009**: System MUST NOT accept or trust `tenant_id` from the request body or query parameters.
- **FR-010**: The public page MUST be accessible without authentication.
- **FR-011**: Widget authentication on the public page MUST use the existing signed short-lived token flow — the page only injects the `widget_id`, not the `tenant_id`.
- **FR-012**: System MUST display contact email from `tenant_config` when it is available.

### Key Entities *(include if feature involves data)*

- **Tenant**: Represents a registered business. Key public fields: `id`, `name`, `slug`, `status`. Only `active` tenants serve a public page.
- **TenantConfig**: Optional per-tenant branding and contact configuration. Key public fields: `brand_name`, `theme_color`, `greeting`, `public_description`, `contact_email`. Private fields (`allowed_origins`, secrets) are never exposed.
- **CmsPage**: A content page authored by the tenant admin. Only pages with `status = published` appear on the public site. Key fields: `title`, `slug`, `content`, `status`.
- **Widget**: The chat widget configured for the tenant. Provides `public_widget_id` used in the embed script tag.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A visitor can load a tenant public page in under 500 ms under normal conditions.
- **SC-002**: At least two seeded tenants (`abc-gym` and `green-clinic`) each serve a distinct, correctly isolated public page.
- **SC-003**: Zero pieces of private data (leads, conversations, prompts, guardrail configs, audit logs) appear on any public page under any test condition.
- **SC-004**: Visiting `/sites/<slug>` for a non-existent slug always returns 404 — never a 500 error or partial page.
- **SC-005**: A suspended tenant's public page is blocked 100% of the time — no content leaks.
- **SC-006**: The chat widget script tag is present and uses the correct tenant `widget_id` on every rendered public page that has a widget configured.
- **SC-007**: Draft CMS content is never shown on the public page under any test condition.

## Assumptions

- The MVP rendering approach is Jinja2 server-rendered HTML. A React alternative is out of scope unless explicitly added later.
- `tenant_config` is optional; the page renders gracefully when no config row exists.
- Each tenant has at most one widget; if none exists, the widget script tag is omitted rather than erroring.
- CMS content body is plain text or safe HTML — no XSS sanitization work is required beyond standard Jinja2 auto-escaping.
- The `tenant_slug` is always a lowercase alphanumeric string with hyphens, matching the existing `Tenant.slug` column definition.
- Platform-level authentication (platform manager token) is not required to access public site routes — these routes are intentionally public.
- The `GET /api/public/sites/{tenant_slug}` JSON endpoint (FR P3) is optional and only built after the Jinja2 HTML route is complete.
- Existing `tenants`, `tenant_config`, `cms_pages`, and `widgets` tables are already migrated — no new migrations are required.
- CORS is not used as an authentication mechanism for public site routes.
