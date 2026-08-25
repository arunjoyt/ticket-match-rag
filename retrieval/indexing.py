"""Single choke point for every Qdrant index mutation (ADR 0006).

Both call sites that used to hit VectorStore directly (webhook_handler.py,
api/main.py's /ingest/full) go through index_ticket/deindex_ticket instead,
so "the index changed" and "a cache refresh got enqueued" can never drift
apart -- there is no other path into the index.
"""

from __future__ import annotations

from rq import Queue
from rq.job import JobStatus

from ingestion.embedder import SparseVector
from retrieval.vector_store import VectorStore
from worker.tasks import REFRESH_JOB_ID, refresh_all_open_tickets_cache

_TERMINAL_STATUSES = {JobStatus.FINISHED, JobStatus.FAILED, JobStatus.STOPPED, JobStatus.CANCELED}


def index_ticket(
    ticket_name: str,
    dense_vector: list[float],
    sparse_vector: SparseVector,
    payload: dict,
    vector_store: VectorStore,
    queue: Queue,
) -> None:
    vector_store.upsert_ticket(ticket_name, dense_vector, sparse_vector, payload)
    enqueue_refresh(queue)


def deindex_ticket(ticket_name: str, vector_store: VectorStore, queue: Queue) -> None:
    vector_store.delete_by_ticket_name(ticket_name)
    enqueue_refresh(queue)


def enqueue_refresh(queue: Queue) -> None:
    """Collapse a burst of index-changing events into one pending refresh
    job, by reusing a fixed job_id and only enqueueing when no job with
    that id is already pending or running."""
    existing = queue.fetch_job(REFRESH_JOB_ID)
    if existing is not None and existing.get_status() not in _TERMINAL_STATUSES:
        return
    queue.enqueue(refresh_all_open_tickets_cache, job_id=REFRESH_JOB_ID)
