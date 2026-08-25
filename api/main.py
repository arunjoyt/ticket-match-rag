"""FastAPI service: ingest Helpdesk tickets, serve similarity Matches.

No generation step anywhere -- retrieve (hybrid search) -> rerank -> filter by
Match Threshold -> surface directly. See CONTEXT.md for the domain model.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from redis import Redis
from rq import Queue

import config
from db.cache import MatchCache
from ingestion.webhook_handler import create_webhook_router, prepare_doc_for_indexing
from retrieval.indexing import index_ticket
from retrieval.matching import build_pipeline, compute_matches


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    pipeline = build_pipeline()
    match_cache = MatchCache()
    match_cache.ensure_schema()
    queue = Queue("default", connection=Redis.from_url(config.REDIS_URL))

    app.state.pipeline = pipeline
    app.state.match_cache = match_cache
    app.state.queue = queue

    app.include_router(
        create_webhook_router(
            helpdesk_client=pipeline.helpdesk_client,
            embedder=pipeline.embedder,
            sparse_embedder=pipeline.sparse_embedder,
            vector_store=pipeline.vector_store,
            queue=queue,
            webhook_secret=config.WEBHOOK_SECRET,
        )
    )

    yield


app = FastAPI(lifespan=_lifespan)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/ingest/full")
async def ingest_full() -> dict[str, int]:
    pipeline = app.state.pipeline
    queue: Queue = app.state.queue

    tickets = pipeline.helpdesk_client.list_reusable_tickets()
    for ticket in tickets:
        dense_vector, sparse_vector, payload = prepare_doc_for_indexing(
            ticket, pipeline.embedder, pipeline.sparse_embedder
        )
        index_ticket(ticket["name"], dense_vector, sparse_vector, payload, pipeline.vector_store, queue)

    return {"indexed": len(tickets)}


@app.get("/tickets/queryable")
async def queryable_tickets() -> list[dict]:
    return app.state.pipeline.helpdesk_client.list_open_tickets()


@app.get("/tickets/{ticket_name}/matches")
async def get_matches(ticket_name: str) -> list[dict]:
    match_cache: MatchCache = app.state.match_cache

    cached = match_cache.get(ticket_name)
    if cached is not None:
        return cached

    pipeline = app.state.pipeline
    try:
        matches = compute_matches(ticket_name, pipeline.helpdesk_client, pipeline.hybrid_search, pipeline.reranker)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=404, detail=f"Ticket {ticket_name} not found") from exc

    match_cache.put(ticket_name, matches)
    return matches
