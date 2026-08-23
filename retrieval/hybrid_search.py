"""BM25 + Qdrant vector search, fused via Reciprocal Rank Fusion (k=60).

The BM25 index is in-memory, rebuilt from scratch (not incrementally updated)
at API startup and after every ingest/webhook upsert -- see ADR 0001's
"Consequences" section on the re-embed lifecycle this implies. Both BM25 and
the vector index are built over `match_text` only (title + description),
never the Resolution Summary -- see CONTEXT.md: Match.
"""

from __future__ import annotations

from rank_bm25 import BM25Okapi

from ingestion.embedder import Embedder
from retrieval.vector_store import VectorStore

RRF_K = 60


class HybridSearch:
    def __init__(self, embedder: Embedder, vector_store: VectorStore) -> None:
        self._embedder = embedder
        self._vector_store = vector_store
        self._bm25: BM25Okapi | None = None
        self._bm25_docs: list[dict] = []

    def build_bm25_index(self, docs: list[dict]) -> None:
        self._bm25_docs = docs
        if not docs:
            self._bm25 = None
            return
        tokenized = [_tokenize(d["match_text"]) for d in docs]
        self._bm25 = BM25Okapi(tokenized)

    def rebuild_bm25_from_store(self) -> None:
        self.build_bm25_index(self._vector_store.get_all_match_texts())

    def search(self, query_text: str, top_k: int = 20) -> list[dict]:
        vector_hits = self._vector_store.search(self._embedder.embed_query(query_text), top_k)
        bm25_hits = self._bm25_search(query_text, top_k)

        fused: dict[str, float] = {}
        payloads: dict[str, dict] = {}
        for rank, hit in enumerate(vector_hits):
            key = hit["ticket_name"]
            fused[key] = fused.get(key, 0.0) + 1.0 / (RRF_K + rank + 1)
            payloads[key] = hit
        for rank, hit in enumerate(bm25_hits):
            key = hit["ticket_name"]
            fused[key] = fused.get(key, 0.0) + 1.0 / (RRF_K + rank + 1)
            payloads.setdefault(key, hit)

        ranked = sorted(fused.items(), key=lambda kv: kv[1], reverse=True)
        return [payloads[key] for key, _ in ranked[:top_k]]

    def _bm25_search(self, query_text: str, top_k: int) -> list[dict]:
        if self._bm25 is None:
            return []
        scores = self._bm25.get_scores(_tokenize(query_text))
        ranked_idx = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
        return [self._bm25_docs[i] for i in ranked_idx[:top_k] if scores[i] > 0]


def _tokenize(text: str) -> list[str]:
    return text.lower().split()
