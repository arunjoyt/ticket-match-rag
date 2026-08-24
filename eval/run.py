"""Retrieval eval: IR metrics via ranx, sliced by query variant (ADR 0003),
plus a near-miss-distractor leakage check.

Two stages are measured separately so the report can tell which part of the
pipeline is doing the work, per ADR 0003's motivation for adding hard cases:
  - Retrieval stage: hybrid_search's fused dense+sparse candidate pool.
    recall@k here is a ceiling check -- if a relevant ticket isn't in the
    pool, no amount of reranking can recover it.
  - Final stage: post-rerank, cross-encoder-scored ranking -- what's actually
    closest to what an agent would see (modulo the Match Threshold cutoff).

Usage: python -m eval.run
Assumes the corpus is already indexed: run scripts/seed_helpdesk.py against
the local Helpdesk instance, then POST /ingest/full, before running this.
"""

from __future__ import annotations

from collections import defaultdict

from ranx import Qrels, Run, evaluate

import config
from eval.dataset import EvalQuery, build_qrels, build_queries, load_manifest, near_miss_by_cluster
from ingestion.embedder import Embedder, SparseEmbedder, match_text
from ingestion.helpdesk_client import HelpdeskClient
from retrieval.hybrid_search import HybridSearch
from retrieval.reranker import Reranker
from retrieval.vector_store import VectorStore

CANDIDATE_POOL_SIZE = 20
FINAL_K = 5
RETRIEVAL_METRICS = [f"recall@{CANDIDATE_POOL_SIZE}"]
FINAL_METRICS = [f"precision@{FINAL_K}", "mrr", f"ndcg@{FINAL_K}"]
VARIANTS = ["standard", "low-lexical-overlap"]


def main() -> None:
    manifest = load_manifest()
    queries = build_queries(manifest)
    qrels_dict = build_qrels(manifest)
    near_miss = near_miss_by_cluster(manifest)

    embedder = Embedder()
    sparse_embedder = SparseEmbedder()
    vector_store = VectorStore()
    vector_store.ensure_collection(embedder.dimension())
    hybrid_search = HybridSearch(embedder, sparse_embedder, vector_store)
    reranker = Reranker()
    reranker.warm_up()
    helpdesk_client = HelpdeskClient()

    retrieval_run, final_run, leakage_hits, leakage_totals = _run_queries(
        queries, near_miss, helpdesk_client, hybrid_search, reranker
    )

    _report_stage("Retrieval stage (hybrid fusion, self excluded)", qrels_dict, retrieval_run, RETRIEVAL_METRICS, queries)
    _report_stage("Final stage (post-rerank, self excluded)", qrels_dict, final_run, FINAL_METRICS, queries)
    _report_leakage(leakage_hits, leakage_totals)


def _run_queries(
    queries: list[EvalQuery],
    near_miss: dict[str, str],
    helpdesk_client: HelpdeskClient,
    hybrid_search: HybridSearch,
    reranker: Reranker,
) -> tuple[dict, dict, dict, dict]:
    retrieval_run: dict[str, dict[str, float]] = {}
    final_run: dict[str, dict[str, float]] = {}
    leakage_hits: dict[str, int] = defaultdict(int)
    leakage_totals: dict[str, int] = defaultdict(int)

    for query in queries:
        ticket = helpdesk_client.get_ticket(query.ticket_name)
        query_text = match_text(ticket["subject"], ticket["description"])

        candidates = [
            c
            for c in hybrid_search.search(query_text, top_k=CANDIDATE_POOL_SIZE)
            if c["ticket_name"] != query.ticket_name
        ]
        # hybrid_search.search() doesn't expose its fused RRF score, only rank
        # order -- a descending synthetic score preserves that order for ranx.
        retrieval_run[query.ticket_name] = {c["ticket_name"]: 1.0 / (rank + 1) for rank, c in enumerate(candidates)}

        ranked = reranker.rerank(query_text, candidates)
        final_run[query.ticket_name] = {payload["ticket_name"]: score for payload, score in ranked}

        distractor_name = near_miss.get(query.cluster_id)
        if distractor_name:
            leakage_totals[query.cluster_id] += 1
            cleared = any(
                payload["ticket_name"] == distractor_name and score >= config.MATCH_THRESHOLD
                for payload, score in ranked
            )
            if cleared:
                leakage_hits[query.cluster_id] += 1

    return retrieval_run, final_run, leakage_hits, leakage_totals


def _report_stage(
    label: str,
    qrels_dict: dict[str, dict[str, int]],
    run_dict: dict[str, dict[str, float]],
    metrics: list[str],
    queries: list[EvalQuery],
) -> None:
    print(f"\n=== {label} ===")
    all_ids = [q.ticket_name for q in queries]
    _print_slice("overall", qrels_dict, run_dict, metrics, all_ids)
    for variant in VARIANTS:
        ids = [q.ticket_name for q in queries if q.variant == variant]
        _print_slice(variant, qrels_dict, run_dict, metrics, ids)


def _print_slice(
    label: str,
    qrels_dict: dict[str, dict[str, int]],
    run_dict: dict[str, dict[str, float]],
    metrics: list[str],
    ids: list[str],
) -> None:
    if not ids:
        return
    sub_qrels = Qrels.from_dict({qid: qrels_dict[qid] for qid in ids})
    sub_run = Run.from_dict({qid: run_dict.get(qid, {}) for qid in ids})
    scores = evaluate(sub_qrels, sub_run, metrics)
    # ranx returns a bare float, not a {metric: value} dict, when len(metrics) == 1.
    if not isinstance(scores, dict):
        scores = {metrics[0]: scores}
    formatted = ", ".join(f"{name}={value:.3f}" for name, value in scores.items())
    print(f"  {label} (n={len(ids)}): {formatted}")


def _report_leakage(leakage_hits: dict[str, int], leakage_totals: dict[str, int]) -> None:
    print("\n=== Near-miss distractor leakage (cleared Match Threshold) ===")
    if not leakage_totals:
        print("  no near-miss distractors configured")
        return
    for cluster_id, total in leakage_totals.items():
        hits = leakage_hits.get(cluster_id, 0)
        print(f"  {cluster_id}: {hits}/{total} queries let the near-miss distractor clear threshold")


if __name__ == "__main__":
    main()
