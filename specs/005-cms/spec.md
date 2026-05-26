# Feature Specification: CMS (Content Management)

> **Owner**: Person B — `feature/rag-agent-widget` branch

**Feature Branch**: `005-cms`

**Created**: 2026-05-27

**Status**: Draft

---

## User Scenarios & Testing *(mandatory)*

### User Story 1 — Tenant Admin Creates and Publishes a CMS Page (Priority: P1)

A tenant admin writes a new page (title, slug, body) and publishes it. Once published, the page content is available for embedding and retrieval by the AI agent.

**Why this priority**: CMS content is the knowledge base for the AI agent. Without at least one published page, RAG, embedding, and the agent have nothing to retrieve.

**Independent Test**: Create a page via `POST /cms/pages`; publish via `POST /cms/pages/{page_id}/publish`; confirm status is `published` and a reindex is triggered.

**Acceptance Scenarios**:

1. **Given** a `tenant_admin` JWT, **When** `POST /cms/pages` is called with title, slug, and body, **Then** a page is created in `draft` status scoped to the admin's tenant.
2. **Given** a `draft` page, **When** `POST /cms/pages/{page_id}/publish` is called, **Then** the page status becomes `published` and an embedding reindex job is triggered.
3. **Given** a `tenant_admin` for Tenant A, **When** pages are listed, **Then** only Tenant A pages are returned — Tenant B pages are never visible.
4. **Given** an unauthenticated request, **When** any CMS route is called, **Then** the response is HTTP 401.

---

### User Story 2 — Tenant Admin Updates and Deletes Pages (Priority: P2)

A tenant admin edits an existing page's body or title and saves the changes. The update triggers a reindex so the agent's retrieval uses fresh content. Deletion removes the page and its associated chunks.

**Why this priority**: Content must stay current. Stale embeddings from edited pages return wrong answers — triggering reindex on save prevents silent staleness.

**Independent Test**: Update a published page's body; confirm a reindex is triggered. Delete a page; confirm its chunk rows are removed from the vector store.

**Acceptance Scenarios**:

1. **Given** an existing page, **When** `PATCH /cms/pages/{page_id}` is called with updated body, **Then** the page body is updated and a reindex is triggered for the affected page.
2. **Given** a published page, **When** `DELETE /cms/pages/{page_id}` is called, **Then** the page is removed and all associated content chunks are deleted from the vector store.
3. **Given** a `tenant_admin` for Tenant A, **When** a page id belonging to Tenant B is used in `PATCH` or `DELETE`, **Then** the response is HTTP 404 (not 403 — existence is not revealed).

---

### User Story 3 — Tenant Admin Triggers Manual Reindex (Priority: P2)

A tenant admin can manually trigger reindexing for a single page or all pages, for example after changing the chunking strategy or embedding model.

**Why this priority**: Operational capability — without it, a change to the embedding configuration cannot be applied without deleting and recreating all content.

**Independent Test**: Call `POST /cms/pages/{page_id}/reindex`; confirm the page's old chunks are deleted and new chunks are generated and stored.

**Acceptance Scenarios**:

1. **Given** a published page with existing chunks, **When** `POST /cms/pages/{page_id}/reindex` is called, **Then** old chunks are deleted and new chunks are created from the current page body.
2. **Given** multiple published pages, **When** `POST /cms/reindex-all` is called, **Then** all pages belonging to the calling tenant are re-chunked and re-embedded.
3. **Given** a page in `draft` status, **When** `reindex` is triggered, **Then** the operation is a no-op (draft pages are not indexed).

---

### Edge Cases

- What happens when `POST /cms/pages` uses a slug that already exists for the same tenant? → 409 Conflict.
- What happens when the body is empty? → 422 Validation Error.
- What happens when a page is published but the embedding service is unavailable? → The publish succeeds; the reindex is queued and retried. The page status does not revert.
- What happens when a `tenant_admin` tries to access another tenant's page by guessing an id? → 404 (RLS returns no rows; existence is not revealed).
- What happens when `reindex-all` is called on a tenant with 100+ pages? → The operation is queued as a background job; the API returns 202 Accepted immediately.

---

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: `POST /cms/pages` MUST create a page in `draft` status scoped to the calling tenant's `tenant_id`.
- **FR-002**: `GET /cms/pages` MUST return only pages belonging to the calling tenant (RLS + repository filter).
- **FR-003**: `GET /cms/pages/{page_id}` MUST return 404 if the page does not exist or belongs to another tenant.
- **FR-004**: `PATCH /cms/pages/{page_id}` MUST update allowed fields (title, slug, body, status) and trigger a reindex if the body changes on a published page.
- **FR-005**: `DELETE /cms/pages/{page_id}` MUST delete the page and all associated `content_chunks` rows.
- **FR-006**: `POST /cms/pages/{page_id}/publish` MUST change status from `draft` to `published` and trigger reindex.
- **FR-007**: `POST /cms/pages/{page_id}/reindex` MUST delete existing chunks for the page and re-trigger the embedding pipeline.
- **FR-008**: `POST /cms/reindex-all` MUST trigger reindex for all `published` pages belonging to the calling tenant.
- **FR-009**: Draft pages MUST NOT be indexed (no chunks in the vector store for draft pages).
- **FR-010**: Page slugs MUST be unique per tenant; uniqueness is enforced at the database level.
- **FR-011**: All CMS routes MUST require `tenant_admin` or higher role.
- **FR-012**: The `created_by` field MUST be set to the authenticated user's id at creation time.

### Key Entities

- **CMS Page**: id, tenant_id, title, slug (unique per tenant), body (long text), status (`draft` | `published`), created_by (user id), created_at, updated_at.
- **Content Chunk**: id, tenant_id, cms_page_id (FK), chunk_text, embedding (vector), metadata (jsonb), created_at. Created by the embedding service — not directly by CMS routes.

---

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A tenant admin can create, publish, and reindex a page within 10 seconds end-to-end.
- **SC-002**: 100% of CMS queries return only the calling tenant's pages — zero cross-tenant pages in any response.
- **SC-003**: Every page deletion removes all associated chunks from the vector store within one reindex cycle.
- **SC-004**: A `PATCH` to a published page's body triggers a reindex within 1 second of the update being committed.
- **SC-005**: `GET /cms/pages` handles up to 500 pages per tenant without timeout.

---

## Assumptions

- CMS is a simple text-body CMS (Markdown or plain text); rich media uploads to MinIO are out of scope for Week 8.
- The embedding pipeline (chunking + vector storage) is implemented in the `006-embedding-and-rag` feature; the CMS spec only specifies that it is triggered.
- Reindex operations for large page sets are queued as background jobs (worker service); the CMS API returns 202 Accepted for bulk operations.
- Page slugs can be used as URL identifiers on the tenant's public site; they must be URL-safe (lowercase, alphanumeric, hyphens).
- `tenant_manager` cannot create or manage CMS pages — only `tenant_admin` and `member` roles with the correct tenant scope.
- There is no page versioning in Week 8; edits overwrite the current body.
