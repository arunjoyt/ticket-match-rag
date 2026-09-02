"""Single choke point for every Qdrant index mutation (ADR 0006, ADR 0011).

Both call sites that mutate the index (ingestion/webhook_handler.py,
api/main.py's /ingest/full) go through index_ticket / deindex_ticket instead
of touching VectorStore directly, so "the index changed" and "the Match cache
was flagged for revalidation" can never drift apart -- there is no other path
into the index.

ADR 0011 replaced ADR 0006's enqueued RQ refresh with a plain
`cache.mark_all_stale()`. Reads reconcile lazily: a stale row is still served,
and the read that saw it schedules a single-ticket refresh.
"""

from __future__ import annotations

from db.cache import MatchCache
from ingestion.embedder import SparseVector
from retrieval.vector_store import VectorStore


def index_ticket(
    ticket_name: str,
    dense_vector: list[float],
    sparse_vector: SparseVector,
    payload: dict,
    vector_store: VectorStore,
    cache: MatchCache,
) -> None:
    vector_store.upsert_ticket(ticket_name, dense_vector, sparse_vector, payload)
    cache.mark_all_stale()


def deindex_ticket(ticket_name: str, vector_store: VectorStore, cache: MatchCache) -> None:
    if vector_store.delete_by_ticket_name(ticket_name):
        cache.mark_all_stale()
