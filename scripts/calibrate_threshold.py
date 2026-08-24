"""Calibrates MATCH_THRESHOLD against the seeded dataset's rerank score
distribution, per the note in config.py.

For every Duplicate Cluster member (held out as a query, same as
eval/run.py), reranks its full candidate pool and labels each candidate
relevant (true cluster sibling, per the qrels in eval/dataset.py) or not
(every other ticket -- other clusters' members and all Distractor kinds).
Sweeps threshold values over the observed rerank scores and reports the one
maximizing F1: the Match Threshold's job (CONTEXT.md) is a precision/recall
trade-off on a binary show/don't-show gate, not a ranking concern, so this
optimizes the metric a binary gate is actually supposed to hit rather than
picking a threshold that just looks reasonable.

Usage: python -m scripts.calibrate_threshold
Assumes the corpus is already indexed (same precondition as eval/run.py).
"""

from __future__ import annotations

import config
from eval.dataset import EvalQuery, build_qrels, build_queries, load_manifest
from ingestion.embedder import Embedder, match_text
from ingestion.helpdesk_client import HelpdeskClient
from retrieval.hybrid_search import HybridSearch
from retrieval.reranker import Reranker
from retrieval.vector_store import VectorStore

CANDIDATE_POOL_SIZE = 20


def main() -> None:
    manifest = load_manifest()
    queries = build_queries(manifest)
    qrels = build_qrels(manifest)

    embedder = Embedder()
    vector_store = VectorStore()
    vector_store.ensure_collection(embedder.dimension())
    hybrid_search = HybridSearch(embedder, vector_store)
    hybrid_search.rebuild_bm25_from_store()
    reranker = Reranker()
    reranker.warm_up()
    helpdesk_client = HelpdeskClient()

    labeled = _collect_labeled_scores(queries, qrels, helpdesk_client, hybrid_search, reranker)
    positives = [score for score, relevant in labeled if relevant]
    negatives = [score for score, relevant in labeled if not relevant]

    print(f"Collected {len(labeled)} scored candidates ({len(positives)} relevant, {len(negatives)} not).")
    print(f"  relevant score range:     min={min(positives):.3f} max={max(positives):.3f} mean={sum(positives) / len(positives):.3f}")
    print(f"  non-relevant score range: min={min(negatives):.3f} max={max(negatives):.3f} mean={sum(negatives) / len(negatives):.3f}")

    threshold, stats = _best_threshold(labeled)
    print(f"\nRecommended MATCH_THRESHOLD = {threshold:.4f}")
    print(f"  precision={stats['precision']:.3f} recall={stats['recall']:.3f} f1={stats['f1']:.3f} (tp={stats['tp']} fp={stats['fp']} fn={stats['fn']})")
    print(f"\nCurrent config.MATCH_THRESHOLD = {config.MATCH_THRESHOLD}")


def _collect_labeled_scores(
    queries: list[EvalQuery],
    qrels: dict[str, dict[str, int]],
    helpdesk_client: HelpdeskClient,
    hybrid_search: HybridSearch,
    reranker: Reranker,
) -> list[tuple[float, bool]]:
    labeled: list[tuple[float, bool]] = []
    for query in queries:
        ticket = helpdesk_client.get_ticket(query.ticket_name)
        query_text = match_text(ticket["subject"], ticket["description"])

        candidates = [
            c
            for c in hybrid_search.search(query_text, top_k=CANDIDATE_POOL_SIZE)
            if c["ticket_name"] != query.ticket_name
        ]
        ranked = reranker.rerank(query_text, candidates)
        relevant_names = set(qrels.get(query.ticket_name, {}))
        labeled.extend((score, payload["ticket_name"] in relevant_names) for payload, score in ranked)
    return labeled


def _best_threshold(labeled: list[tuple[float, bool]]) -> tuple[float, dict]:
    """Sweeps every observed score as a candidate `score >= threshold` cutoff,
    picking the one maximizing F1 (ties broken by precision, then by higher
    threshold -- a Match should stay conservative when the trade-off is even).
    """
    best_stats: dict | None = None
    for candidate in sorted({score for score, _ in labeled}):
        tp = sum(1 for score, relevant in labeled if relevant and score >= candidate)
        fp = sum(1 for score, relevant in labeled if not relevant and score >= candidate)
        fn = sum(1 for score, relevant in labeled if relevant and score < candidate)
        precision = tp / (tp + fp) if (tp + fp) else 0.0
        recall = tp / (tp + fn) if (tp + fn) else 0.0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
        stats = {"threshold": candidate, "precision": precision, "recall": recall, "f1": f1, "tp": tp, "fp": fp, "fn": fn}
        if best_stats is None or (f1, precision, candidate) > (best_stats["f1"], best_stats["precision"], best_stats["threshold"]):
            best_stats = stats
    return best_stats["threshold"], best_stats


if __name__ == "__main__":
    main()
