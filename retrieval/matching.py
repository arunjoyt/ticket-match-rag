"""Shared retrieve -> rerank -> filter pipeline, and the object graph it
runs on -- used identically by the API's live-miss path and the background
worker's refresh sweep (ADR 0006), so neither can drift from the other.
"""

from __future__ import annotations

from dataclasses import dataclass

import config
from ingestion.embedder import Embedder, SparseEmbedder, match_text
from ingestion.helpdesk_client import HelpdeskClient
from retrieval.hybrid_search import HybridSearch
from retrieval.reranker import Reranker
from retrieval.vector_store import VectorStore

MAX_MATCHES = 5
CANDIDATE_POOL_SIZE = 20


@dataclass
class Pipeline:
    embedder: Embedder
    sparse_embedder: SparseEmbedder
    vector_store: VectorStore
    hybrid_search: HybridSearch
    reranker: Reranker
    helpdesk_client: HelpdeskClient


def build_pipeline() -> Pipeline:
    embedder = Embedder()
    sparse_embedder = SparseEmbedder()
    vector_store = VectorStore()
    vector_store.ensure_collection(embedder.dimension())
    hybrid_search = HybridSearch(embedder, sparse_embedder, vector_store)
    reranker = Reranker()
    reranker.warm_up()
    helpdesk_client = HelpdeskClient()
    return Pipeline(
        embedder=embedder,
        sparse_embedder=sparse_embedder,
        vector_store=vector_store,
        hybrid_search=hybrid_search,
        reranker=reranker,
        helpdesk_client=helpdesk_client,
    )


def compute_matches(
    ticket_name: str, helpdesk_client: HelpdeskClient, hybrid_search: HybridSearch, reranker: Reranker
) -> list[dict]:
    ticket = helpdesk_client.get_ticket(ticket_name)

    query_text = match_text(ticket["subject"], ticket["description"])
    candidates = hybrid_search.search(query_text, top_k=CANDIDATE_POOL_SIZE)
    ranked = reranker.rerank(query_text, candidates)

    matches = [
        {**payload, "score": score}
        for payload, score in ranked
        if score >= config.MATCH_THRESHOLD and payload["ticket_name"] != ticket_name
    ]
    return matches[:MAX_MATCHES]
