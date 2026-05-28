"""Lightweight RAG evaluation script — Concierge demo tenant.

Loads ``evals/rag/golden_set.yaml`` and runs each question through
``RagService.search`` against the live demo database. Reports two
deterministic retrieval metrics:

* **hit@5** — fraction of questions whose top-5 retrieved chunks include
  at least one chunk from any ``ground_truth_chunks.page_slug``.
* **MRR**  — mean reciprocal rank of the FIRST ground-truth chunk in
  the top-5 (1.0 if the first hit is at rank 1, 0.5 at rank 2, …, 0 if
  no hit in top-5).

When invoked with ``--compare``, runs the eval twice:

1. baseline   = ``published_only=False`` (pre-improvement query)
2. improved   = ``published_only=True``  (v1 retrieval improvement)

and prints a one-screen before/after table. This is the "measurable
before/after comparison" referenced in ``docs/EVALS.md``.

Faithfulness / answer_relevancy via RAGAS are intentionally NOT wired
in the MVP — they add a heavy dependency tree and a per-question LLM
call. The script's metric loop is structured so adding a frozen-judge
``faithfulness`` step later is a small change; see ``docs/EVALS.md`` for
the rationale.

Owner: Person B.

Usage (inside the API container):

    docker exec -it concierge-api-1 \\
      uv run python /workspace/evals/rag/evaluate_rag.py --compare

Add ``--max-chunks 5`` to override K (default 5, matching production).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path
from uuid import UUID

import yaml
from sqlalchemy import select

# ``evals/rag/`` sits outside ``backend/``; the script is launched with
# ``backend/`` on PYTHONPATH (uv run from the container's /workspace/backend)
# so imports of ``app.*`` resolve normally.
from app.core.config import get_settings
from app.db.rls import reset_tenant_context, set_tenant_context
from app.db.session import close_engine, get_session_factory
from app.models.cms import CmsPage
from app.models.tenant import Tenant
from app.services.embedding_client import CohereEmbeddingClient
from app.services.rag_service import RagService

log = logging.getLogger("evaluate_rag")

# ---------------------------------------------------------------------------
# Golden-set loading
# ---------------------------------------------------------------------------

DEFAULT_GOLDEN_PATH = (
    Path(__file__).resolve().parent / "golden_set.yaml"
)


@dataclass
class Triple:
    id: str
    category: str
    question: str
    ideal_answer: str
    ground_truth_page_slugs: list[str] = field(default_factory=list)


@dataclass
class Golden:
    tenant_slug: str
    triples: list[Triple]


def load_golden(path: Path) -> Golden:
    raw = yaml.safe_load(path.read_text())
    triples = [
        Triple(
            id=t["id"],
            category=t["category"],
            question=t["question"],
            ideal_answer=t["ideal_answer"],
            ground_truth_page_slugs=[
                gt["page_slug"] for gt in (t.get("ground_truth_chunks") or [])
            ],
        )
        for t in raw["triples"]
    ]
    return Golden(tenant_slug=raw["tenant_slug"], triples=triples)


# ---------------------------------------------------------------------------
# Metric helpers
# ---------------------------------------------------------------------------


@dataclass
class TripleResult:
    triple_id: str
    category: str
    hit: bool                  # any ground-truth page in top-K
    reciprocal_rank: float     # 0 if no hit, else 1/rank
    retrieved_page_slugs: list[str]
    has_ground_truth: bool     # False for legitimately-empty triples


@dataclass
class RunSummary:
    label: str
    n: int
    n_with_truth: int
    hit_at_k: float
    mrr: float
    per_category: dict[str, tuple[float, float]]  # category → (hit@k, mrr)
    per_triple: list[TripleResult]


def _summarize(label: str, results: list[TripleResult], k: int) -> RunSummary:
    """Aggregate per-triple results into headline + per-category numbers.

    Triples with empty ``ground_truth_chunks`` are excluded from hit@K
    and MRR — the eval can't fairly score "should retrieve nothing"
    with these set-membership metrics. They're still listed in the
    per-triple table so reviewers can sanity-check what the retriever
    DID return for them.
    """
    scored = [r for r in results if r.has_ground_truth]
    n_with_truth = len(scored)
    hit_at_k = sum(1 for r in scored if r.hit) / n_with_truth if n_with_truth else 0.0
    mrr = sum(r.reciprocal_rank for r in scored) / n_with_truth if n_with_truth else 0.0

    by_cat: dict[str, list[TripleResult]] = {}
    for r in scored:
        by_cat.setdefault(r.category, []).append(r)
    per_cat = {
        cat: (
            sum(1 for r in rs if r.hit) / len(rs),
            sum(r.reciprocal_rank for r in rs) / len(rs),
        )
        for cat, rs in by_cat.items()
    }
    _ = k  # K is encoded in the result rows themselves
    return RunSummary(
        label=label,
        n=len(results),
        n_with_truth=n_with_truth,
        hit_at_k=hit_at_k,
        mrr=mrr,
        per_category=per_cat,
        per_triple=results,
    )


# ---------------------------------------------------------------------------
# Eval loop
# ---------------------------------------------------------------------------


async def _run_once(
    *,
    rag: RagService,
    tenant_id: UUID,
    slug_by_page_id: dict[UUID, str],
    triples: list[Triple],
    k: int,
    published_only: bool,
) -> list[TripleResult]:
    out: list[TripleResult] = []
    for triple in triples:
        rag_result = await rag.search(
            query=triple.question,
            tenant_id=tenant_id,
            max_chunks=k,
            published_only=published_only,
        )
        retrieved_slugs = [
            slug_by_page_id.get(c.source_page_id, f"<unknown:{c.source_page_id}>")
            for c in rag_result.chunks
        ]

        ground_truth = set(triple.ground_truth_page_slugs)
        has_truth = bool(ground_truth)
        first_hit_rank = 0
        if has_truth:
            for rank, slug in enumerate(retrieved_slugs, start=1):
                if slug in ground_truth:
                    first_hit_rank = rank
                    break
        out.append(
            TripleResult(
                triple_id=triple.id,
                category=triple.category,
                hit=first_hit_rank > 0,
                reciprocal_rank=(1.0 / first_hit_rank) if first_hit_rank else 0.0,
                retrieved_page_slugs=retrieved_slugs,
                has_ground_truth=has_truth,
            )
        )
    return out


async def _resolve_tenant_and_pages(
    session, tenant_slug: str
) -> tuple[UUID, dict[UUID, str]]:
    """Look up the tenant id + build the page_id → slug map.

    Runs OUTSIDE the RLS-scoped section because we need the tenant row
    itself (``tenants`` is not tenant-scoped). The CMS pages are read
    afterwards inside the RLS scope so the eval respects the same
    isolation the agent does in production.
    """
    tenant = (
        await session.execute(select(Tenant).where(Tenant.slug == tenant_slug))
    ).scalar_one_or_none()
    if tenant is None:
        raise SystemExit(
            f"Tenant slug '{tenant_slug}' not found. "
            f"Run ``uv run python -m scripts.seed_demo_data`` first."
        )
    return tenant.id, {}


async def evaluate(
    *,
    golden: Golden,
    k: int,
    do_compare: bool,
) -> list[RunSummary]:
    settings = get_settings()
    if not settings.COHERE_API_KEY:
        raise SystemExit("COHERE_API_KEY is required for the eval (real embeddings).")
    embedding = CohereEmbeddingClient.from_api_key(
        api_key=settings.COHERE_API_KEY,
        model=settings.EMBEDDING_MODEL,
    )

    factory = get_session_factory()
    summaries: list[RunSummary] = []
    try:
        async with factory() as session:
            tenant_id, _ = await _resolve_tenant_and_pages(session, golden.tenant_slug)
            await set_tenant_context(session, tenant_id)

            # Build the slug map inside the tenant context — RLS guarantees
            # we only see the demo tenant's pages, matching production
            # retrieval behavior.
            page_rows = (
                await session.execute(
                    select(CmsPage.id, CmsPage.slug).where(CmsPage.tenant_id == tenant_id)
                )
            ).all()
            slug_by_page_id: dict[UUID, str] = {row.id: row.slug for row in page_rows}
            log.info(
                "loaded tenant=%s pages=%d", tenant_id, len(slug_by_page_id)
            )

            rag = RagService(session=session, embedding_client=embedding)

            modes: list[tuple[str, bool]]
            if do_compare:
                modes = [
                    ("baseline (published_only=False)", False),
                    ("improved (published_only=True)", True),
                ]
            else:
                modes = [("default (published_only=True)", True)]

            for label, published_only in modes:
                results = await _run_once(
                    rag=rag,
                    tenant_id=tenant_id,
                    slug_by_page_id=slug_by_page_id,
                    triples=golden.triples,
                    k=k,
                    published_only=published_only,
                )
                summaries.append(_summarize(label, results, k))

            await reset_tenant_context(session)
    finally:
        await close_engine()

    return summaries


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def _format_pct(x: float) -> str:
    return f"{x * 100:5.1f}%"


def _print_summary(summary: RunSummary, k: int) -> None:
    print(f"\n=== {summary.label} ===")
    print(
        f"  triples scored : {summary.n_with_truth} of {summary.n} "
        f"(remaining {summary.n - summary.n_with_truth} have empty ground_truth)"
    )
    print(f"  hit@{k}         : {_format_pct(summary.hit_at_k)}")
    print(f"  MRR            : {summary.mrr:.3f}")
    if summary.per_category:
        print("  by category:")
        for cat, (h, m) in sorted(summary.per_category.items()):
            print(f"    {cat:<20s} hit@{k}={_format_pct(h)}  MRR={m:.3f}")


def _print_compare_table(summaries: list[RunSummary], k: int) -> None:
    if len(summaries) != 2:
        return
    base, imp = summaries
    print("\n=== Compare ===")
    print(f"{'metric':<14s}  {'baseline':>10s}  {'improved':>10s}  {'delta':>8s}")
    print("-" * 48)
    delta_hit = imp.hit_at_k - base.hit_at_k
    delta_mrr = imp.mrr - base.mrr
    print(
        f"{'hit@' + str(k):<14s}  {_format_pct(base.hit_at_k):>10s}  "
        f"{_format_pct(imp.hit_at_k):>10s}  {_format_pct(delta_hit):>8s}"
    )
    print(
        f"{'MRR':<14s}  {base.mrr:>10.3f}  {imp.mrr:>10.3f}  {delta_mrr:>+8.3f}"
    )


def _print_failures(summary: RunSummary, k: int) -> None:
    """List the triples that scored zero on the improved run.

    Failures are the most actionable output: they point at golden-set
    questions where the v1 retriever doesn't surface the expected page
    in the top-K. Fix the CMS content, the chunking, or the question.
    """
    misses = [r for r in summary.per_triple if r.has_ground_truth and not r.hit]
    if not misses:
        print("\nNo misses on the improved run — every scored triple hit top-K.")
        return
    print(f"\n=== Misses on {summary.label} (top-{k}) ===")
    for m in misses:
        print(f"  {m.triple_id} [{m.category}]")
        print(f"    retrieved: {m.retrieved_page_slugs}")


def _emit_json(summaries: list[RunSummary], path: Path, k: int) -> None:
    payload = {
        "k": k,
        "runs": [
            {
                "label": s.label,
                "n_with_truth": s.n_with_truth,
                "hit_at_k": s.hit_at_k,
                "mrr": s.mrr,
                "per_category": {
                    cat: {"hit_at_k": h, "mrr": m}
                    for cat, (h, m) in s.per_category.items()
                },
                "per_triple": [
                    {
                        "id": r.triple_id,
                        "category": r.category,
                        "hit": r.hit,
                        "reciprocal_rank": r.reciprocal_rank,
                        "retrieved_page_slugs": r.retrieved_page_slugs,
                        "has_ground_truth": r.has_ground_truth,
                    }
                    for r in s.per_triple
                ],
            }
            for s in summaries
        ],
    }
    path.write_text(json.dumps(payload, indent=2))
    print(f"\nWrote machine-readable report → {path}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Concierge RAG eval (hit@K + MRR)")
    p.add_argument(
        "--golden",
        type=Path,
        default=DEFAULT_GOLDEN_PATH,
        help="Path to the golden-set YAML (default: evals/rag/golden_set.yaml).",
    )
    p.add_argument(
        "--max-chunks",
        type=int,
        default=5,
        help="K for top-K retrieval (default: 5, matches production).",
    )
    p.add_argument(
        "--compare",
        action="store_true",
        help="Run both published_only=False and =True for a before/after table.",
    )
    p.add_argument(
        "--json",
        type=Path,
        default=None,
        help="Optional path to dump the full per-triple report as JSON.",
    )
    return p.parse_args()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stdout,
    )
    args = _parse_args()
    golden = load_golden(args.golden)
    log.info(
        "loaded golden tenant=%s triples=%d", golden.tenant_slug, len(golden.triples)
    )

    summaries = asyncio.run(
        evaluate(golden=golden, k=args.max_chunks, do_compare=args.compare)
    )
    for s in summaries:
        _print_summary(s, args.max_chunks)
    if args.compare:
        _print_compare_table(summaries, args.max_chunks)
    # Always print misses on the LAST run — that's the "improved" pass
    # when --compare is set, otherwise the only run.
    _print_failures(summaries[-1], args.max_chunks)

    if args.json:
        _emit_json(summaries, args.json, args.max_chunks)


if __name__ == "__main__":
    main()
