# Feature Specification: Embedding & RAG

> **Owner**: Person B — `feature/rag-agent-widget` branch

**Feature Branch**: `006-embedding-and-rag`

**Created**: 2026-05-27

**Status**: Draft

---

## User Scenarios & Testing *(mandatory)*

### User Story 1 — CMS Page Is Chunked and Embedded on Publish (Priority: P1)

When a tenant admin publishes a CMS page, the content is split into chunks and each chunk is embedded via the hosted embedding API. The embeddings are stored with the chunk text and the tenant's `tenant_id`. Subsequent chat queries retrieve only that tenant's chunks.

**Why this priority**: Embedding is what makes the AI agent able to answer from tenant content. Without it, RAG retrieval returns nothing and the agent is blind.

**Independent Test**: Publish a page with known text. Query the `content_chunks` table; confirm chunk rows exist with the correct `tenant_id` and non-null embedding vectors.

**Acceptance Scenarios**:

1. **Given** a CMS page is published, **When** the embedding pipeline runs, **Then** the page body is split into chunks, each chunk is embedded via the hosted API, and rows are inserted into `content_chunks` with the correct `tenant_id`.
2. **Given** embedded chunks exist for Tenant A, **When** the embedding query is run with Tenant B's context, **Then** zero chunks are returned (tenant isolation in the vector store).
3. **Given** a chunking run completes, **When** cost is recorded, **Then** a `cost_event` row is written for each embedding API call, tagged with the tenant's id.

---

### User Story 2 — Retrieval Returns Tenant-Filtered Top-K Chunks (Priority: P1)

When the agent or router performs a RAG search for a visitor query, the retrieval query uses the visitor's verified tenant context and returns the top-k most semantically similar chunks — only from that tenant.

**Why this priority**: Tenant-filtered retrieval is the vector-layer isolation guarantee. A query that forgets the filter leaks chunks from other tenants.

**Independent Test**: Insert chunks for two tenants with overlapping content. Run a similarity query with Tenant A's `tenant_id`. Confirm zero Tenant B chunks appear in the results regardless of similarity score.

**Acceptance Scenarios**:

1. **Given** chunks for Tenant A and Tenant B with similar text, **When** a similarity search is run with `tenant_id = Tenant A`, **Then** only Tenant A chunks are returned.
2. **Given** a query, **When** retrieval runs, **Then** the results are sorted by cosine similarity and the top-k (configurable, default 5) chunks are returned.
3. **Given** a tenant with no matching chunks for a query, **When** retrieval runs, **Then** an empty list is returned gracefully (no error).

---

### User Story 3 — One Justified Retrieval Improvement Outperforms the Baseline (Priority: P2)

The team implements one improvement beyond naive top-k dense retrieval (reranking, query rewriting, or metadata filtering). The improvement is validated on a golden set of 15 question/answer/chunk triples with a measurable improvement in hit@5.

**Why this priority**: "We chose X because it improved hit@5 from 0.6 to 0.8" is an engineering decision. Choosing a technique without a number is not.

**Independent Test**: Run the RAG evaluation script against the 15-triple golden set with the baseline (dense only) and the improved retrieval. Confirm the improvement metric is higher than the baseline metric.

**Acceptance Scenarios**:

1. **Given** the 15-triple golden set, **When** the RAG eval runs with the baseline (dense top-5), **Then** a baseline hit@5 score is recorded.
2. **Given** the same golden set, **When** the RAG eval runs with the chosen improvement, **Then** hit@5 is higher than the baseline (minimum improvement threshold in `eval_thresholds.yaml`).
3. **Given** the improvement is selected, **When** it is documented in `docs/DECISIONS.md`, **Then** the entry includes the two numbers (baseline vs improved) and the rationale.

---

### User Story 4 — Embedding Costs Are Attributed Per Tenant (Priority: P2)

Every call to the hosted embedding API is tracked as a cost event tagged with the tenant's id. A tenant admin can see how many embeddings their content has generated.

**Why this priority**: Embedding is a per-call paid API cost. Per-tenant attribution is how the platform knows what each customer costs.

**Independent Test**: Trigger embedding for a known number of chunks. Query `cost_events` for that tenant; confirm the correct number of events with the `embedding` operation type.

**Acceptance Scenarios**:

1. **Given** a page with 10 chunks, **When** the embedding pipeline runs, **Then** 10 (or batched equivalents) `cost_event` rows are written for that tenant with `operation=embedding`.
2. **Given** cost events exist, **When** the usage summary endpoint is called, **Then** the embedding total is included in the aggregate.

---

### Edge Cases

- What happens when the embedding API is unavailable? → The job is retried with exponential backoff (up to a configured limit); the page status does not revert to draft.
- What happens when a page body is very long (10,000+ words)? → The chunking strategy splits it into appropriately sized chunks; no single chunk exceeds the embedding model's token limit.
- What happens when a page is deleted? → All associated `content_chunks` rows (including embeddings) are deleted from the vector store.
- What happens when the embedding model is changed? → All existing chunks are invalidated and a `reindex-all` must be run. This is an operational procedure, not an automatic process.
- What happens when a query produces no embedding (empty string)? → The embedding service returns an error; retrieval returns an empty list.

---

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The embedding pipeline MUST split page bodies into chunks using a non-naive chunking strategy (e.g., sentence-aware or paragraph-aware, not purely fixed-size).
- **FR-002**: Each chunk MUST be embedded via the hosted embedding API (`text-embedding-3-small` or equivalent).
- **FR-003**: Chunks MUST be stored in `content_chunks` with `tenant_id`, `cms_page_id`, `chunk_text`, `embedding` (vector), and `metadata` (jsonb).
- **FR-004**: All similarity searches MUST include a `WHERE tenant_id = $1` filter — retrieval without a tenant filter MUST NOT be possible through the RAG service interface.
- **FR-005**: Retrieval MUST return the top-k chunks sorted by cosine similarity (default k=5, configurable).
- **FR-006**: One retrieval improvement (reranking, query rewriting, or metadata filtering) MUST be implemented and validated on the 15-triple golden set.
- **FR-007**: The golden set results (baseline hit@5 and improved hit@5) MUST be committed in the eval scripts and referenced in `docs/DECISIONS.md`.
- **FR-008**: Every embedding API call MUST produce a `cost_event` row tagged with the tenant's id and `operation=embedding`.
- **FR-009**: The embedding pipeline MUST implement timeout, retry with backoff, and structured error handling for the hosted API call.
- **FR-010**: Chunk deletion MUST occur when a CMS page is deleted or when a reindex is triggered (old chunks are replaced by new ones, not accumulated).
- **FR-011**: No raw PII or secrets appearing in chunk text must be stored without redaction; the guardrail redaction layer applies before storage.

### Key Entities

- **Content Chunk**: id, tenant_id, cms_page_id, chunk_text, embedding (vector[1536]), metadata (jsonb: e.g., page_slug, chunk_index, chunk_size), created_at.
- **RAG Golden Set**: 15 triples of (query, ideal_answer, ground_truth_chunk_ids) stored in `evals/rag/golden_set.yaml`.
- **Cost Event** (embedding): operation=`embedding`, input_tokens (chunk length), estimated_cost_usd, tenant_id.

---

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of published pages have their chunks indexed in the vector store within 60 seconds of publication.
- **SC-002**: Zero Tenant B chunks are returned when querying with Tenant A's context — verified by automated test.
- **SC-003**: The chosen retrieval improvement achieves hit@5 ≥ 0.70 on the 15-triple golden set (threshold in `eval_thresholds.yaml`).
- **SC-004**: The improved retrieval outperforms the naive baseline by a measurable margin (documented in `DECISIONS.md`).
- **SC-005**: 100% of embedding API calls produce a corresponding cost event tagged with the correct tenant.
- **SC-006**: Embedding pipeline retries on transient API failures without dropping chunks or producing duplicate chunk rows.

---

## Assumptions

- The hosted embedding model is `text-embedding-3-small` (1536 dimensions); the vector column dimension must match.
- Chunking strategy is sentence-aware with a configurable target chunk size (default ~500 tokens); exact implementation is Person B's decision, justified by the eval numbers.
- The 15-triple golden set is hand-labelled by Person B during Day 2; the eval script is wired to CI in feature 016.
- Reranking (if chosen as the improvement) uses the hosted LLM API, not a dedicated reranker model, to keep containers lean.
- Batch embedding is used where the API supports it, to reduce cost and latency per page.
- The embedding pipeline runs in-process for small pages and as a background worker job for large or bulk reindex operations.
