# Feature Specification: Embedding & RAG

> **Owner**: Person B — `feature/rag-agent-widget` branch

**Feature Branch**: `006-embedding-and-rag`

**Created**: 2026-05-27

**Status**: Implemented (chunking + Cohere embeddings + pgvector retrieval; CMS publish hook + golden-set eval pending)

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

- **FR-001**: The embedding pipeline MUST split page bodies into chunks using a non-naive chunking strategy (paragraph-aware, character-bounded, deterministic with overlap; see Implementation Addendum §A).
- **FR-002**: Each chunk MUST be embedded via the hosted embedding API. The Week 8 provider is **Cohere `embed-english-v3.0`** (1024-dim, normalized). Provider swap is a single-file change to `app/services/embedding_client.py` only.
- **FR-003**: Chunks MUST be stored in `cms_chunks` with `tenant_id`, `page_id`, `chunk_index`, `text`, `embedding` (`vector(1024)`), and `created_at`. (`metadata` is not stored in Week 8; the chunking pipeline keeps no per-chunk metadata beyond `chunk_index`.)
- **FR-004**: All similarity searches MUST include a `WHERE tenant_id = $1` filter — retrieval without a tenant filter MUST NOT be possible through the `RagService` interface. RLS is the second wall, never the only one.
- **FR-005**: Retrieval MUST return the top-k chunks sorted by cosine distance (default k=5, configurable). Score is normalized to `[0.0, 1.0]` via `score = 1.0 - distance/2.0` (higher = better).
- **FR-006**: One retrieval improvement (reranking, query rewriting, or metadata filtering) MUST be implemented and validated on the 15-triple golden set.
- **FR-007**: The golden set results (baseline hit@5 and improved hit@5) MUST be committed in the eval scripts and referenced in `docs/DECISIONS.md`.
- **FR-008**: Every embedding API call MUST produce a `cost_event` row tagged with the tenant's id and `operation=embedding`. *Status: cost-event emission is pending — see feature 013.*
- **FR-009**: The embedding client MUST implement timeout, retry with backoff, and structured error handling. SDK-level retries MUST be disabled so the retry budget lives in exactly one place.
- **FR-010**: `RagService.index_page` MUST be idempotent: existing chunks for `(tenant_id, page_id)` are deleted before new chunks are inserted, inside a single transaction. `delete_page` MUST be tenant-scoped.
- **FR-011**: No raw PII or secrets appearing in chunk text MUST be stored without redaction; the guardrail redaction layer applies before storage.

### Key Entities

- **CMS Chunk** (`cms_chunks` table): `id` (uuid), `tenant_id` (uuid), `page_id` (uuid), `chunk_index` (int, 0-based, stable across re-indexes), `text` (text), `embedding` (`vector(1024)`), `created_at` (timestamptz). UNIQUE `(tenant_id, page_id, chunk_index)`. INDEX `(tenant_id)`, `(tenant_id, page_id)`. RLS enabled.
- **RAG Golden Set**: 15 triples of (query, ideal_answer, ground_truth_chunk_ids) stored in `evals/rag/golden_set.yaml`.
- **Cost Event** (embedding): `operation="embedding"`, `provider="cohere"`, `model="embed-english-v3.0"`, `input_tokens` (chunk length proxy), `estimated_cost_usd`, `tenant_id`.

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

- The hosted embedding model is **Cohere `embed-english-v3.0`** (1024 dimensions, normalized vectors); the `cms_chunks.embedding` column dimension matches.
- Chunking strategy is paragraph-aware, character-bounded (default `max_chars=800`, `overlap_chars=150`); see Implementation Addendum §A for the deterministic algorithm.
- The 15-triple golden set is hand-labelled by Person B during Day 2; the eval script is wired to CI in feature 016.
- Reranking (if chosen as the improvement) uses the hosted LLM API, not a dedicated reranker model, to keep containers lean.
- Cohere's batch limit (96 inputs per `embed` call) is enforced inside `CohereEmbeddingClient`; callers may pass any number of texts.
- The embedding pipeline runs in-process during the CMS publish hook (Person A's slice); a background worker is not required for Week 8 page sizes.

---

## Implementation Addendum (Owner B — frozen contracts)

> Merged from the retired `specs/{chunking-pipeline,embedding-service,rag-service}/spec.md` files (May 2026). This section is the source of truth for the three implemented surfaces.

### A. Chunking pipeline (`backend/app/services/chunking.py`)

Pure deterministic function. No I/O, no async, no tokenizer dependency, no third-party deps beyond `pydantic`.

```python
class CmsChunk(BaseModel):
    page_id: UUID
    chunk_index: int   # 0-based ordinal within the page; stable across re-indexes
    text: str          # post-normalization

def chunk_page(
    *, page_id: UUID, content: str,
    max_chars: int = 800, overlap_chars: int = 150,
) -> list[CmsChunk]: ...
```

#### Behavior

1. Normalize: strip outer whitespace; collapse `\n{3,}` → `\n\n`; collapse runs of spaces/tabs within a paragraph to one space.
2. Split on blank-line paragraph boundaries.
3. Accumulate paragraphs into chunks ≤ `max_chars`. If a single paragraph itself exceeds `max_chars`, hard-split at character boundaries (no mid-word split if a space is within ±20 chars of the boundary).
4. Emit `overlap_chars` of trailing context from chunk N as a prefix of chunk N+1.
5. Drop trailing empty/whitespace-only chunks.
6. Assign `chunk_index` 0, 1, 2, … in emission order.

#### Chunking invariants

1. **Deterministic** — identical `(page_id, content, max_chars, overlap_chars)` → identical output.
2. **Pure** — no global state, no module-level caches, no I/O.
3. **Bounded** — every emitted chunk satisfies `len(text) <= max_chars + overlap_chars`.
4. **Non-empty only** — chunks where `text.strip() == ""` are never emitted.
5. **Contiguous indices** — `chunk_index` is `0..N-1` with no gaps.
6. **Empty input is not an error** — `content == ""` or whitespace-only → `[]`.
7. `max_chars <= 0`, `overlap_chars < 0`, or `overlap_chars >= max_chars` → `ValueError`.

### B. CohereEmbeddingClient (`backend/app/services/embedding_client.py`)

The single, provider-isolated implementation of CMS-chunk and query embedding. The **only** file that imports the `cohere` SDK.

```python
class CohereEmbeddingClient:
    def __init__(
        self, *,
        client: AsyncClient,             # cohere.AsyncClient with max_retries=0
        model: str = "embed-english-v3.0",
        batch_size: int = 96,            # 1..96 (Cohere v3 hard cap)
        max_attempts: int = 3,
        backoff_base_seconds: float = 0.5,
        backoff_max_seconds: float = 5.0,
    ) -> None: ...

    @classmethod
    def from_api_key(cls, *, api_key: str, **kwargs) -> "CohereEmbeddingClient": ...

    async def embed_documents(self, texts: list[str]) -> list[list[float]]: ...
    async def embed_query(self, text: str) -> list[float]: ...
```

#### Embedding invariants

1. SDK retries are **disabled** (`max_retries=0`). The retry loop in this module is the single source of truth.
2. `embed_documents` forces `input_type="search_document"`; `embed_query` forces `input_type="search_query"`. The caller cannot pick `input_type` — fixed by method name.
3. `len(embed_documents(texts)) == len(texts)` and order is preserved across batches.
4. Each vector has length **1024**. Mismatch raises `ValueError` (defensive guard against silent model swaps).
5. `embed_documents([])` returns `[]` and makes zero API calls.
6. `embed_query("")` raises `ValueError` — callers (`RagService.search`) guard before this is reachable.
7. Batching is sequential, not concurrent.
8. Retryable: connection errors, 429, 5xx. Non-retryable: other 4xx + unexpected exceptions → `ExternalServiceError(service="cohere", …)` immediately.
9. Exhaustion → `ExternalServiceError("max retries (N) exhausted: …")`.
10. Embedding chat memory or any non-CMS-chunk text through this client is a contract violation.

### C. RagService (`backend/app/services/rag_service.py`)

Owns both the read path (search) and the write path (index/delete). All SQL carries an explicit `WHERE tenant_id = $1`.

```python
class RagService:
    def __init__(
        self, *,
        session: AsyncSession,                       # request-scoped, RLS-set
        embedding_client: CohereEmbeddingClient,
        default_max_chunks: int = 5,
    ) -> None: ...

    async def search(
        self, *, query: str, tenant_id: UUID, max_chunks: int = 5,
    ) -> RagSearchResult: ...

    async def index_page(
        self, *, tenant_id: UUID, page_id: UUID, content: str,
    ) -> int: ...      # number of chunks indexed (0 on empty content)

    async def delete_page(
        self, *, tenant_id: UUID, page_id: UUID,
    ) -> int: ...      # number of chunks deleted
```

#### Distance → score

- pgvector operator: `<=>` (cosine distance). Normalized vectors → distance ∈ `[0, 2]`.
- Cohere `embed-english-v3.0` returns normalized vectors.
- Score formula: `score = 1.0 - (distance / 2.0)` → `[0.0, 1.0]`, higher = better.

#### RAG invariants

1. **Tenant filter present in every SQL statement** (read or write). RLS is the second wall.
2. `search` returns at most `max_chunks` chunks.
3. `search(query="")` returns `RagSearchResult(chunks=[], total_found=0)` and makes **zero** embedding/DB calls.
4. `search` never raises on "no match" — returns empty result.
5. `index_page` is idempotent: existing chunks for `(tenant_id, page_id)` are deleted before new ones are inserted, inside the caller's transaction.
6. `index_page` with empty content stores zero chunks (no error, no row).
7. `delete_page` removes only the chunks for `(tenant_id, page_id)`.
8. Cross-tenant access is impossible by SQL clause — even if RLS were misconfigured.
9. Embedding dimension is asserted to be 1024 at insert time.
10. `RagService` never accepts pre-computed embeddings from callers — the `embedding_client` is the sole vector source.
11. `total_found == len(chunks)` (the name is preserved for compatibility with `docs/SPEC.md §3.1`; a true corpus-wide hit count is a future enhancement).

### D. Database schema (migration `0002_cms_chunks.py`)

```
cms_chunks
----------
id            uuid PRIMARY KEY DEFAULT gen_random_uuid()
tenant_id     uuid NOT NULL REFERENCES tenants(id)
page_id       uuid NOT NULL                            -- FK to cms_pages once that lands
chunk_index   int  NOT NULL
text          text NOT NULL
embedding     vector(1024) NOT NULL
created_at    timestamptz NOT NULL DEFAULT now()

UNIQUE (tenant_id, page_id, chunk_index)
INDEX  (tenant_id)
INDEX  (tenant_id, page_id)
RLS:    ENABLE; policy USING (tenant_id::text = current_setting('app.tenant_id', true))
```

No ANN index at MVP (sequential scan acceptable for seed corpus). HNSW (`USING hnsw (embedding vector_cosine_ops)`) is a follow-up migration when corpus or query latency warrants.

### E. Settings (frozen)

| Setting | Default | Source |
|---|---|---|
| `COHERE_API_KEY` | (env, secret, required) | `Settings` |
| `EMBEDDING_MODEL` | `embed-english-v3.0` | `Settings` |
| `EMBEDDING_DIM` | `1024` (constant) | module-level |
| `EMBEDDING_BATCH_SIZE` | `96` | constructor (Cohere v3 cap) |
| `RAG_DEFAULT_MAX_CHUNKS` | `5` | constructor |
| `DEFAULT_MAX_CHARS` (chunking) | `800` | module-level |
| `DEFAULT_OVERLAP_CHARS` (chunking) | `150` | module-level |

`cohere>=5` and `pgvector` Python bindings are pinned in `backend/pyproject.toml`.

### F. Test coverage (frozen counts)

| Surface | File | Scenarios |
|---|---|---|
| Chunking | `backend/tests/test_chunking.py` | 19 unit |
| Cohere embedding client | `backend/tests/test_embedding_client.py` | 27 unit (no network; `_FakeAsyncCohere`) |
| `RagService` (unit) | `backend/tests/test_rag_service.py` | 20 unit (fake session + fake embedding) |
| `RagService` (integration) | `backend/tests/integration/test_rag_pgvector.py` | 5 (opt-in via `RUN_INTEGRATION=1`) |

### G. Implementation status

| Component | Status |
|---|---|
| `chunk_page` pure deterministic chunker | Implemented |
| `CohereEmbeddingClient.embed_documents` / `embed_query` | Implemented |
| `CohereEmbeddingClient.from_api_key` factory | Implemented |
| `RagService.search` (tenant-filtered cosine top-K, score-normalized) | Implemented |
| `RagService.index_page` (idempotent, transactional) | Implemented |
| `RagService.delete_page` | Implemented |
| `cms_chunks` Alembic migration with RLS + indexes | Implemented (`0002_cms_chunks`) |
| Bound into `ToolRegistry.rag_search` via `build_registry(rag_service=...)` | Implemented |
| Cost-event emission for embedding calls | **Pending** — see feature 013 |
| CMS publish-hook wiring (Person A) | Pending — coordination point |
| Golden-set retrieval evaluation (`evals/rag/`) | Pending — feature 016 gate |
| HNSW ANN index | Deferred (post-eval evidence required) |

### H. Future integration points

- **CMS publish flow (Person A)**: on `cms_pages` create/update → `RagService.index_page(tenant_id=…, page_id=…, content=…)`. On unpublish → `delete_page`.
- **Admin re-index endpoint** (post-RAG): `POST /admin/pages/{id}/reindex` calls `index_page` after fetching the latest content.
- **Provider swap**: replace `embedding_client.py` with `<provider>_embedding_client.py` exposing the same two methods and the same 1024-dim contract. `RagService` does not change.
- **Phase 2 candidates** (each conditional on golden-set evidence): HNSW index; score-threshold filtering; hybrid retrieval (BM25 + dense merge); query rewriting via the LLM; cross-encoder re-ranking.

Frozen baseline: **chunk → cohere v3 embed → pgvector cosine top-K → tenant-filtered → score-normalized → return**.
