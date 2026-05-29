# Architecture decisions — Concierge (Owner B scope)

One-paragraph entries. Each captures the choice, the alternatives we
weighed, and the smallest reason the chosen path won. Optimised for
re-reading three months from now without re-deriving the trade-offs.

---

## D-001 — Hybrid router + bounded agent

**Choice.** Inbound chat turns hit `RouterService` (a deterministic
classifier → label) first; the label routes to a fast specialised path
(`faq`, `sales`, `human`, `drop`) OR — for `ambiguous`, low-confidence,
and unknown labels — to `AgentService`, a bounded tool-calling LLM
agent with exactly three tools (`rag_search`, `capture_lead`,
`escalate`).

**Alternatives weighed.**

1. *Pure router*: every utterance dispatches to a hard-coded handler.
   Falls over on anything off-script; visitors get refusal walls.
2. *Pure agent*: every turn pays the LLM tool-loop tax. Latency +
   cost balloon for trivial FAQ-shaped queries, and reasoning loops
   sometimes pick the wrong tool for cases a one-line rule would
   catch.
3. *Hybrid* (chosen): cheap classifier handles the easy 70 %, agent
   absorbs the messy long tail and is the safety net when the
   classifier is unavailable (fail-open to `agent`, never drop).

**Smallest reason it wins.** Catches the easy cases for ~zero LLM
spend, never drops user traffic on classifier failure, and the agent's
tool set is small enough (3) that tool-selection is observable and
testable as its own surface (`evals/agent/tool_selection_golden.yaml`).

References: `backend/app/services/router_service.py`, `backend/app/services/agent_service.py`,
`docs/PLAN.md`.

---

## D-002 — Retrieval improvement = `published_only` JOIN

**Choice.** `RagService.search` JOINs `cms_pages` and filters
`status = 'published'` by default. `published_only=False` keeps the
pre-improvement query shape for A/B eval and admin debugging.

**Alternatives weighed.**

1. *Query rewrite / LLM expansion* — needs prompt-engineering, hurts
   determinism, costs an extra LLM call per search.
2. *Cross-encoder rerank* — meaningful uplift but pulls in
   transformers/torch, explicitly off-budget for Week 8.
3. *MMR / page-diversity rerank* — small implementation, helps
   coverage but doesn't address the actual baseline failure mode we
   observed (see below).
4. *Status JOIN* (chosen) — one extra clause, zero new dependencies,
   directly addresses two real bugs at once.

**Smallest reason it wins.** Demo-honest measured uplift (MRR
0.679 → 0.929 on 14 scored triples) with no API/dep impact. The JOIN
also excludes **orphan chunks** (`page_id` no longer in `cms_pages`,
typically test-cycle leftovers) for free — those were the dominant
cause of baseline MRR being below 1.0. Spec 005 already mandates that
draft pages must not surface to widget visitors; the change makes the
production query honest about that contract.

References: `backend/app/services/rag_service.py::RagService.search`,
`docs/EVALS.md §3`.

---

## D-003 — Public widget runtime as `/public/*` aliases (PR-B)

**Choice.** Mount `POST /widgets/session` and `POST /chat` a second
time under the `/public/*` prefix via `add_api_route` (same handler
functions, same auth chain). Add `GET /public/widgets/config` as a
net-new endpoint exposed *only* under `/public`.

**Alternatives weighed.**

1. *Rename the legacy routes* — breaks any in-progress integration
   work and downstream admin tooling that already hits `/chat` and
   `/widgets/session`.
2. *Include the same router twice in `api_router`* — adds the new
   `GET /config` endpoint at both `/widgets/config` *and*
   `/public/widgets/config`, leaking the runtime config under the
   admin prefix. Also produces duplicate OpenAPI operationIds.
3. *Dedicated `public.py` aliases* (chosen) — explicit, no
   surface-leak, runtime config stays under `/public` only, and the
   underlying handlers stay the single source of truth for the
   security model (`tenant_id` from token, server-side origin check,
   etc.).

**Smallest reason it wins.** Zero behaviour change to the existing
handlers (same code paths, same tests), one net-new endpoint with its
own focused security contract, and OpenAPI cleanly shows the public
surface as a distinct `public-widget-runtime` tag.

References: `backend/app/api/routes/public.py`, `backend/tests/test_public_widget_runtime.py`,
`docs/RUNBOOK.md`.

---

## D-004 — Alembic migration graph linearisation (PR-A)

**Choice.** Resolved a pre-existing multi-head + duplicate-revision-id
graph by renaming `0003_chat_persistence` → `0003b_chat_persistence`
(re-parented to `0003`) and `0004_remaining_tables` →
`0004b_post_cms_extras` (re-parented to `0004`, trimmed to its
non-overlapping operations only). One single head:
`0005_leads_admin`.

**Alternatives weighed.**

1. *Squash everything into a fresh `0001`* — destroys history, breaks
   any production database we ever ship.
2. *Add an Alembic merge revision* — works on paper but two of the
   duplicate revisions had **incompatible** `upgrade()` bodies
   (different table column sets), so a merge would still need one of
   them rewritten.
3. *Linearise + trim* (chosen) — keeps every historically-applied SQL
   operation exactly once, in a coherent order, and the trimmed
   `0004b` only retains operations no earlier revision performed.

**Smallest reason it wins.** Fresh DBs migrate cleanly to a single
head; existing local DBs only need a one-row `alembic_version` reset
(documented in `docs/RUNBOOK.md`). No production data loss path.

References: `backend/app/db/migrations/versions/`, `docs/RUNBOOK.md`
("Migrations" section).

---

## D-005 — Evaluation stack: pure-Python script, no RAGAS at MVP

**Choice.** Hand-rolled `evals/rag/evaluate_rag.py` computing hit@K and
MRR against the live demo DB. No RAGAS, no LLM-judge.

**Alternatives weighed.**

1. *Full RAGAS integration* — adds `ragas`, `datasets`, `pandas`,
   `openai` to the dependency tree; per-question LLM call burns API
   credits on every CI run; opinionated metric definitions.
2. *Frozen-judge heuristic* — fewer deps but still needs a labeled
   judge prompt and a deterministic LLM; bigger blast radius for
   little additional signal beyond retrieval metrics.
3. *Pure Python hit@K + MRR* (chosen) — zero new deps (PyYAML is
   already transitively present), deterministic, runs offline in
   <10 seconds, and the script is shaped so adding `faithfulness`
   later is a small change rather than a rewrite.

**Smallest reason it wins.** The actual gap we needed to close at
Week 8 was *retrieval quality* (Spec 006), and hit@K + MRR are the
right signals for that. Faithfulness and answer_relevancy are listed
as next steps in `docs/EVALS.md §5`, not as deferred TODOs.

References: `evals/rag/evaluate_rag.py`, `docs/EVALS.md`.
