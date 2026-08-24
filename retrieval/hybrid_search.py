"""Dense + sparse (BM25) search, fused server-side by Qdrant's native RRF
fusion -- see ADR 0002 and retrieval/vector_store.py. Both sides are built
over `match_text` only (title + description), never the Resolution Summary
-- see CONTEXT.md: Match.
"""

from __future__ import annotations

from ingestion.embedder import Embedder, SparseEmbedder
from retrieval.vector_store import VectorStore


class HybridSearch:
    def __init__(self, embedder: Embedder, sparse_embedder: SparseEmbedder, vector_store: VectorStore) -> None:
        self._embedder = embedder
        self._sparse_embedder = sparse_embedder
        self._vector_store = vector_store

    def search(self, query_text: str, top_k: int = 20) -> list[dict]:
        dense_vector = self._embedder.embed_query(query_text)
        sparse_vector = self._sparse_embedder.embed_query(query_text)
        return self._vector_store.hybrid_search(dense_vector, sparse_vector, top_k)
