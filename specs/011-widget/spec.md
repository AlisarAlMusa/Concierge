# Feature Specification: Embeddable Widget

> **Owner**: Person B — `feature/rag-agent-widget` branch

**Feature Branch**: `011-widget`

**Created**: 2026-05-27

**Status**: Draft

---

## User Scenarios & Testing *(mandatory)*

### User Story 1 — Tenant Embeds the Widget on Their Website (Priority: P1)

A tenant admin copies their embed snippet from the admin page and pastes one `<script>` tag into their website's HTML. The widget loads, displays the tenant's configured greeting, and is ready for visitors to chat — all without the site owner writing any JavaScript.

**Why this priority**: The embed UX is the customer-facing deliverable. If embedding requires more than one tag or custom JS, the product is harder to sell.

**Independent Test**: Paste the script tag into a plain HTML file served from an allowed origin. Confirm the widget loads, the tenant's greeting is shown, and no console errors appear.

**Acceptance Scenarios**:

1. **Given** a `<script src=".../widget.js" data-widget-id="pub_wid_abc123">` tag on an allowed-origin page, **When** the page loads, **Then** the widget renders with the tenant's greeting and theme colours.
2. **Given** the widget loads, **When** the visitor reads the greeting, **Then** it matches the tenant's configured greeting from their widget record.
3. **Given** no `data-widget-id`, **When** the loader script runs, **Then** the widget fails silently without throwing unhandled errors on the host page.

---

### User Story 2 — Widget Authenticates with a Signed Session Token (Priority: P1)

When the widget loads, it exchanges the public widget id and the page's origin for a signed, short-lived session token from the API. All subsequent chat requests carry this token. The visitor never sends a raw `tenant_id`.

**Why this priority**: CORS is browser-enforced. A curl with a copied widget id ignores CORS entirely. The signed token is what the API actually trusts — not the origin header.

**Independent Test**: Capture the `POST /public/widgets/session` request. Confirm the response is a signed token. Send a chat message with the token; confirm it is accepted. Send a chat message without the token or with a stale token; confirm 401.

**Acceptance Scenarios**:

1. **Given** the widget loads on an allowed origin, **When** `POST /public/widgets/session` is called with the public widget id and origin, **Then** a signed session token is returned.
2. **Given** a valid session token, **When** `POST /public/chat` is called with the token, **Then** the request is processed and a reply is returned.
3. **Given** no token or an expired/invalid token, **When** `POST /public/chat` is called, **Then** HTTP 401 is returned.
4. **Given** the token is decoded, **When** its claims are inspected, **Then** it encodes `tenant_id`, `widget_id`, `visitor_session_id`, and `exp` — the API derives `tenant_id` from the token, never from the request body.

---

### User Story 3 — Widget Is Blocked on a Disallowed Origin (Priority: P1)

When a page on a domain not in the tenant's `allowed_origins` list tries to load the widget session, the API rejects the request with 403. A CORS error blocks the browser on a disallowed page. A raw curl with a copied widget id and a fake origin is also rejected server-side.

**Why this priority**: Origin checking stops embedders from loading the widget on unauthorised domains. But it also proves CORS alone is insufficient — the server-side check is what stops non-browser callers.

**Independent Test**: Attempt `POST /public/widgets/session` from a disallowed origin — confirm 403. Attempt the same request with `curl -H "Origin: https://evil.com"` — confirm 403.

**Acceptance Scenarios**:

1. **Given** a request from an origin not in the tenant's `allowed_origins`, **When** `POST /public/widgets/session` is called, **Then** HTTP 403 is returned.
2. **Given** a `curl` request with a spoofed `Origin` header not in `allowed_origins`, **When** the session endpoint is called, **Then** HTTP 403 is returned (server-side check, not CORS alone).
3. **Given** a browser on an allowed origin, **When** the widget session is requested, **Then** the response succeeds and the CORS headers allow the browser to proceed.

---

### User Story 4 — Widget Theme and Greeting Come from Tenant Config at Runtime (Priority: P2)

The widget reads the tenant's configured theme colours and greeting from `GET /public/widgets/config` at load time. A tenant admin who updates their theme sees the change reflected in the widget on the next page load without a redeploy.

**Why this priority**: White-label config is a selling point. Hardcoded theme or greeting means every tenant gets the same experience.

**Independent Test**: Update a tenant's widget theme via the admin config endpoint. Load the widget. Confirm the new theme colours and greeting are applied.

**Acceptance Scenarios**:

1. **Given** a tenant has configured a custom greeting, **When** the widget loads, **Then** the greeting shown matches the tenant's config.
2. **Given** a tenant updates their theme, **When** a visitor loads the page, **Then** the new theme is used without a redeployment.
3. **Given** no tenant theme config, **When** the widget loads, **Then** a default platform theme is applied.

---

### Edge Cases

- What happens when the session token expires mid-conversation? → The widget automatically refreshes the token via a silent re-exchange; the visitor does not see an error.
- What happens when the widget bundle fails to load (CDN error)? → The host page is unaffected — the widget fails silently, consistent with the "one tag" embed contract.
- What happens when the widget is loaded in an iframe on a disallowed domain? → The `Content-Security-Policy: frame-ancestors` header blocks the iframe in compliant browsers; the session endpoint also returns 403.
- What happens when a visitor sends a message with no session token (direct API call)? → HTTP 401 — no chat without a valid widget session token.
- What happens when the visitor's `visitor_session_id` in the token does not match any conversation? → A new conversation is created automatically.

---

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: A loader script MUST be served at `GET /widget.js`; it reads `data-widget-id` from the script tag and injects the widget UI into the host page.
- **FR-002**: `POST /public/widgets/session` MUST accept `{public_widget_id, origin}` and return a signed short-lived JWT/HMAC token.
- **FR-003**: The session endpoint MUST validate `origin` against the tenant's `allowed_origins` list. Mismatched origins MUST receive HTTP 403.
- **FR-004**: The origin check MUST be performed server-side in the request handler — CORS headers alone are insufficient.
- **FR-005**: The session token MUST encode `tenant_id`, `widget_id`, `visitor_session_id`, and an expiry. The API MUST derive `tenant_id` from the verified token — never from the request body.
- **FR-006**: `GET /public/widgets/config` MUST return the tenant's greeting and theme config, resolved from the verified session token.
- **FR-007**: `POST /public/chat` MUST require a valid session token; requests without a valid token MUST receive HTTP 401.
- **FR-008**: The widget bundle MUST be a standalone React/Vite build, served with appropriate cache headers. Bundle size MUST be under 200KB gzipped.
- **FR-009**: The `Content-Security-Policy: frame-ancestors` header for widget responses MUST be set per tenant from `allowed_origins`.
- **FR-010**: CORS and CSP are defence-in-depth around the token — never the primary authentication mechanism.
- **FR-011**: The widget MUST display the tenant's greeting and apply the tenant's theme colours on load.
- **FR-012**: Widget session tokens MUST be short-lived (default expiry: 1 hour); the widget MUST silently refresh them before expiry.

### Key Entities

- **Widget Record**: id, tenant_id, public_widget_id (public-facing, used in script tag), name, theme_json, greeting, allowed_origins (text[]), enabled_tools (jsonb), created_at.
- **Widget Session Token**: Signed JWT/HMAC encoding tenant_id, widget_id, visitor_session_id, exp. Never contains raw secrets.
- **Visitor Session**: Anonymous, identified by a `visitor_session_id` generated at session creation. Scoped to one widget/tenant.

---

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Widget loads and first greeting is shown within 2 seconds on a standard connection.
- **SC-002**: 100% of requests from disallowed origins receive 403 — including direct API calls with spoofed Origin headers.
- **SC-003**: 100% of chat requests without a valid session token receive 401.
- **SC-004**: Widget bundle is under 200KB gzipped.
- **SC-005**: A tenant theme change is reflected in the widget on the next page load with zero redeployment.
- **SC-006**: Zero instances of `tenant_id` sourced from the request body in the widget chat flow — verified by code review and integration test.

---

## Assumptions

- The widget is a React + Vite single-page component injected via an iframe or shadow DOM into the host page.
- The widget is served from the main API (not MinIO) for Week 8 to simplify deployment.
- `visitor_session_id` is generated by the API at session creation and stored in the signed token; the client does not generate or supply it.
- The token signing secret is the `WIDGET_TOKEN_SECRET` from Vault; it is distinct from the user JWT secret.
- `allowed_origins` is managed per widget by the tenant admin through the admin app or the widget management API.
- Token refresh is handled by the widget JS before the token expires; the refresh calls `POST /public/widgets/session` again with the original `data-widget-id`.
