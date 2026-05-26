# Feature Specification: Leads & Escalations

> **Owner**: Person B — `feature/rag-agent-widget` branch

**Feature Branch**: `012-leads-and-escalations`

**Created**: 2026-05-27

**Status**: Draft

---

## User Scenarios & Testing *(mandatory)*

### User Story 1 — Agent Captures a Qualified Lead (Priority: P1)

The agent calls `capture_lead` after a visitor expresses purchase intent and provides contact details. The lead is validated, rate-limited, scored by the classifier, and written to the tenant's leads table. A tenant admin can then view it in the admin page.

**Why this priority**: Lead capture is the primary revenue-generating action. A misconfigured or exploitable `capture_lead` write is both a data quality problem and a security risk.

**Independent Test**: Invoke `capture_lead` via the agent with a valid name and email. Confirm a lead row is created for the correct tenant, with the correct conversation id, and a lead score. Confirm the tenant admin can see it via `GET /leads`.

**Acceptance Scenarios**:

1. **Given** a valid lead payload (name, email, intent), **When** `capture_lead` is invoked, **Then** a lead row is created in the tenant's leads table scoped to the correct `tenant_id`.
2. **Given** a lead is captured, **When** the lead is inspected, **Then** it has `conversation_id`, `intent`, `lead_score`, `source`, and `created_at` populated.
3. **Given** a `tenant_admin` for Tenant A, **When** `GET /leads` is called, **Then** only Tenant A leads are returned.
4. **Given** an invalid payload (no email, no name), **When** `capture_lead` is invoked, **Then** the lead is rejected with a 422 validation error and no row is written.

---

### User Story 2 — Visitor Session Lead Rate Limit Prevents Spam (Priority: P1)

A visitor (or an injected prompt) attempts to create many leads in rapid succession from the same session. After the rate limit is reached, further `capture_lead` calls for that session are rejected. Existing leads are unaffected.

**Why this priority**: `capture_lead` is an unauthenticated, LLM-triggered write. Without a per-session rate limit, a prompt injection could turn it into a spam cannon.

**Independent Test**: Fire `capture_lead` 6 times from the same visitor session. Confirm the first 5 succeed (or whatever the configured limit is) and subsequent calls return a rate-limit error. Confirm no lead is written for the blocked calls.

**Acceptance Scenarios**:

1. **Given** a visitor session, **When** `capture_lead` is called beyond the session rate limit, **Then** the call is rejected with a rate-limit error and no row is written.
2. **Given** a different visitor session, **When** `capture_lead` is called, **Then** the rate limit is independent — it is not shared across sessions.
3. **Given** a rate-limited session, **When** the rate limit window expires, **Then** the visitor can capture leads again.

---

### User Story 3 — Agent Escalates a Conversation to a Human (Priority: P1)

The agent calls `escalate` when the visitor requests a human or the agent cannot resolve the turn. An escalation row is created, the conversation status is updated, and the visitor receives a handoff message. The tenant admin can view and update escalations.

**Why this priority**: Escalation is the safety valve when the agent is out of its depth. A broken escalation path leaves visitors stranded.

**Independent Test**: Trigger `escalate` via the agent. Confirm an escalation row is created with the correct tenant, conversation, and reason. Confirm the conversation status is updated. Confirm the tenant admin can see it via `GET /escalations`.

**Acceptance Scenarios**:

1. **Given** the agent calls `escalate` with a reason, **When** the call runs, **Then** an escalation row is created with `tenant_id`, `conversation_id`, `reason`, and status `open`.
2. **Given** an escalation is created, **When** the conversation is inspected, **Then** its status is `escalated`.
3. **Given** a `tenant_admin`, **When** `GET /escalations` is called, **Then** only Tenant A escalations are returned.
4. **Given** a `tenant_admin`, **When** `PATCH /escalations/{id}` is called with status `resolved`, **Then** the escalation status is updated.

---

### User Story 4 — Tenant Admin Views and Manages Leads and Escalations (Priority: P2)

A tenant admin reviews their leads and escalations in the admin app. They can update lead status, add notes, and mark escalations as resolved.

**Why this priority**: The admin view is how tenant admins convert leads and close escalations. Without it, the data collected by the agent has no operational home.

**Independent Test**: Create leads and escalations for Tenant A. Log in as Tenant A admin; call `GET /leads` and `GET /escalations`. Confirm only Tenant A records are returned. Update a lead status; confirm the change persists.

**Acceptance Scenarios**:

1. **Given** multiple leads for a tenant, **When** `GET /leads` is called, **Then** all tenant leads are returned with pagination support.
2. **Given** a lead, **When** `PATCH /leads/{lead_id}` is called with updated status or notes, **Then** the lead is updated.
3. **Given** a `tenant_admin` for Tenant A, **When** they try to access a lead id belonging to Tenant B, **Then** HTTP 404 is returned.

---

### Edge Cases

- What happens when `capture_lead` is called with an email that is identical to a recent lead for the same tenant? → The lead is created as a new row (deduplication is not in scope for Week 8); the admin can review duplicates.
- What happens when an escalation is created for a conversation that has already been escalated? → The call is idempotent; the existing escalation is returned with its current status.
- What happens when the agent calls `capture_lead` with only partial contact info (e.g., just intent, no email)? → The lead is created with the available fields; it is not rejected for missing optional fields.
- What happens when `DELETE /leads/{lead_id}` is called? → The lead is soft-deleted (status=deleted) or hard-deleted depending on the tenant erasure policy; it is no longer returned in `GET /leads`.

---

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: `capture_lead` (agent tool) MUST schema-validate: intent is required; name, email, phone are optional but at least one contact field is encouraged.
- **FR-002**: Lead writes MUST be scoped to the request's token-derived `tenant_id`. `tenant_id` MUST NOT come from the request body.
- **FR-003**: A per-visitor-session rate limit MUST apply to `capture_lead`. Default: 5 lead writes per session per hour.
- **FR-004**: Each lead MUST have a `lead_score` populated from the model server's `/predict-lead-score` endpoint.
- **FR-005**: `GET /leads` MUST return only leads belonging to the calling tenant (RLS + repository filter).
- **FR-006**: `PATCH /leads/{lead_id}` MUST allow the tenant admin to update status and notes.
- **FR-007**: `DELETE /leads/{lead_id}` MUST be available to tenant admins; leads belonging to other tenants MUST return 404.
- **FR-008**: `escalate` (agent tool) MUST create an escalation row with `tenant_id`, `conversation_id`, `reason`, and status `open`.
- **FR-009**: After `escalate`, the conversation status MUST be updated to `escalated`.
- **FR-010**: `GET /escalations` MUST return only escalations belonging to the calling tenant.
- **FR-011**: `PATCH /escalations/{id}` MUST allow the tenant admin to update escalation status (e.g., `resolved`, `in_progress`).
- **FR-012**: Escalation creation MUST be idempotent per conversation — a second escalation for the same conversation returns the existing one.

### Key Entities

- **Lead**: id, tenant_id, conversation_id, name, email, phone, intent, lead_score (0.0–1.0), source (`router` | `agent`), status (`new` | `contacted` | `converted` | `rejected`), notes, created_at.
- **Escalation**: id, tenant_id, conversation_id, reason, status (`open` | `in_progress` | `resolved`), created_at.
- **Conversation Status**: `active` | `escalated` | `closed`. Updated by escalation and agent tool calls.

---

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of lead captures are scoped to the correct tenant — zero cross-tenant lead writes.
- **SC-002**: Per-session rate limit prevents more than the configured number of lead writes per session (verified by automated test).
- **SC-003**: 100% of escalation calls create an escalation row and update the conversation status atomically.
- **SC-004**: A tenant admin can view their leads and escalations in under 2 seconds for up to 500 records.
- **SC-005**: Lead score is populated for 100% of captured leads.

---

## Assumptions

- Lead deduplication (same email within a time window) is out of scope for Week 8; admins handle duplicates manually.
- The lead scoring model is the same classifier used for intent routing (`/predict-lead-score` endpoint from feature 007).
- `source` field distinguishes between leads captured by the deterministic router (`router`) and those captured by the agent tool (`agent`).
- Pagination is implemented for `GET /leads` and `GET /escalations` (default page size 50).
- Tenant admins cannot delete escalations — they can only update status. Full erasure happens through the tenant erasure flow (feature 015).
