# Evaluations — Concierge (Owner B scope)

Lean, demo-honest evaluation of the two surfaces Owner B owns:

* **RAG retrieval** — `evals/rag/golden_set.yaml` + `evals/rag/evaluate_rag.py`.
* **Agent tool selection** — `evals/agent/tool_selection_golden.yaml` (golden set
  only; scoring is intentionally deferred — see "What's intentionally out of
  scope" below).

Thresholds for both live in `evals/eval_thresholds.yaml`; Owner C wires
CI gating against those numbers.

---

## 1. RAG golden set

`evals/rag/golden_set.yaml` — 15 hand-written triples grounded in
`backend/scripts/seed_demo_data.py`. Each triple has:

```yaml
- id: faq_growth_plan_conversations
  category: factual
  question: "How many chat conversations does the Growth plan include?"
  ideal_answer: "The Growth plan includes up to 10,000 chat conversations per month."
  ground_truth_chunks:
    - page_slug: pricing
```

Categories: **factual** (5), **pricing_policy** (4), **hours_contact** (3),
**escalation_worthy** (3). The last category has one triple with
`ground_truth_chunks: []` — that one probes "should retrieve nothing
high-confidence" and is excluded from headline metrics by design.

We key ground truth on `page_slug` rather than raw chunk text so the
set survives any chunking-parameter change without re-labeling work.

## 2. Lightweight evaluation script

`evals/rag/evaluate_rag.py` — single-file, no framework, no LLM-judge.

Run it from inside the API container so the live Cohere key and DB are
already wired:

```bash
docker exec -it concierge-api-1 bash -lc \
  'cd /app && PYTHONPATH=/app uv run python evals/rag/evaluate_rag.py --compare \
     --json /tmp/rag_eval.json'
```

Outputs two deterministic retrieval metrics:

* **hit@K** — fraction of scored triples whose top-K results contain at
  least one chunk from any `page_slug` in `ground_truth_chunks`.
* **MRR** — mean reciprocal rank of the first ground-truth chunk in
  top-K (1.0 at rank 1, 0.5 at rank 2, …, 0 if absent).

`--compare` runs the eval twice — once with `published_only=False`
(baseline) and once with `published_only=True` (improved) — and prints a
single before/after table. `--json` dumps the full per-triple report.

## 3. v1 retrieval improvement: published-only JOIN

**Choice.** One change, smallest safe surface: `RagService.search` now
JOINs `cms_pages` and filters `status = 'published'` (default behaviour).
`published_only=False` preserves the pre-improvement query shape so the
eval can produce a faithful baseline.

**Why this one** (out of metadata filter / query rewrite / rerank):

* No new dependencies (no torch, no transformers, no rerank model).
* Already-indexed composite `(tenant_id, status)` → the JOIN is cheap.
* Aligns with Spec 005 publication semantics — visitors must not see
  draft / unpublished content.
* As a free side-effect, the JOIN also excludes **orphan chunks**
  (rows whose `page_id` no longer corresponds to a `cms_pages` row,
  typically left behind by test cycles that wrote chunks and deleted
  the page row without cascading). Those orphans were the main reason
  baseline MRR was below 1.0 on factual queries — they kept winning
  rank 1 against the correct page.

**Measured before/after** (5 seeded pages, 6 chunks, K=5, real Cohere
embed-english-v3.0):

| metric  | baseline | improved | delta   |
| ------- | -------- | -------- | ------- |
| hit@5   | 92.9 %   | 92.9 %   |  +0.0 % |
| MRR     | 0.679    | 0.929    | +0.250  |

Per-category MRR (improved): factual = 1.000, hours_contact = 1.000,
pricing_policy = 1.000, escalation_worthy = 0.500.

The single remaining miss on the improved run is
`ambiguous_voice_chat` — the seed corpus mentions voice/video only in
passing on `product-overview`, so it correctly fails to surface at top-5.
Recorded as an honest negative in the report.

## 4. Agent tool-selection golden set

`evals/agent/tool_selection_golden.yaml` — 15 examples across the four
first-move tool choices the bounded agent can make:

| expected_tool   | n |
| --------------- | - |
| `rag_search`    | 6 |
| `capture_lead`  | 3 |
| `escalate`      | 3 |
| `no_tool`       | 3 |

Each example carries an `expected_reason` so reviewers can adjudicate
disagreements with the agent's actual choice without re-reading the
spec.

## 5. What's intentionally out of scope (with rationale)

* **RAGAS / LLM-judge faithfulness + answer_relevancy.** Both add a
  heavy dependency tree (datasets / pandas / openai pinned) and a
  per-question LLM API call. The script's metric loop is shaped so a
  future `faithfulness` step is a small addition — but for the demo,
  hit@K + MRR are sufficient retrieval signals and they run offline
  against the local stack in under 10 seconds.
* **Automated tool-selection scoring.** The agent path requires a live
  LLM client; we ship the labelled set so Owner C / future-us can wire
  it into CI when the model server's classifier endpoint is stabilised.
  The golden set itself is the deliverable here.
* **CI gating.** `evals/eval_thresholds.yaml` already has placeholders
  (`hit_at_5_min: 0.50`, `tool_selection_accuracy_min: 0.50`). CI
  wiring is Owner C's surface; the thresholds are tight enough to flag
  a real regression once the script runs in CI.

## 6. Reproducing

```bash
# 1. Bring the stack up
docker compose up -d postgres redis api

# 2. Migrate + seed (idempotent)
docker exec -it concierge-api-1 bash -lc 'cd /app && uv run alembic upgrade head'
docker exec -it concierge-api-1 bash -lc 'cd /app && uv run python -m scripts.seed_demo_data'

# 3. Run the eval
docker exec -it concierge-api-1 bash -lc \
  'cd /app && PYTHONPATH=/app uv run python evals/rag/evaluate_rag.py --compare'
```

The script connects via the API container's existing `DATABASE_URL` /
`COHERE_API_KEY` / `EMBEDDING_MODEL`; no extra env config required.
