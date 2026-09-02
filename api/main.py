"""FastAPI service: ingest Helpdesk tickets, serve similarity Matches.

No generation step anywhere -- retrieve (hybrid search) -> rerank -> filter by
Match Threshold -> surface directly. See CONTEXT.md for the domain model.

Reads are cache-aside (ADR 0006/0007): a row is served whenever one exists.
A stale row (ADR 0011) is still served as-is; the request then schedules a
single-ticket background refresh so the next read is fresh.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException

import config
from api.auth import require_api_key
from api.refresh import BackgroundRefresher
from db.cache import MatchCache
from ingestion.webhook_handler import create_webhook_router, prepare_doc_for_indexing
from retrieval.indexing import index_ticket
from retrieval.matching import build_pipeline, compute_matches


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    pipeline = build_pipeline()
    match_cache = MatchCache()
    match_cache.ensure_schema()
    refresher = BackgroundRefresher(pipeline, match_cache)

    app.state.pipeline = pipeline
    app.state.match_cache = match_cache
    app.state.refresher = refresher

    app.include_router(
        create_webhook_router(
            helpdesk_client=pipeline.helpdesk_client,
            embedder=pipeline.embedder,
            sparse_embedder=pipeline.sparse_embedder,
            vector_store=pipeline.vector_store,
            cache=match_cache,
            refresher=refresher,
            webhook_secret=config.WEBHOOK_SECRET,
        )
    )

    yield


app = FastAPI(lifespan=_lifespan)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/ingest/full", dependencies=[Depends(require_api_key)])
async def ingest_full() -> dict[str, int]:
    pipeline = app.state.pipeline
    cache: MatchCache = app.state.match_cache

    tickets = pipeline.helpdesk_client.list_reusable_tickets()
    for ticket in tickets:
        dense_vector, sparse_vector, payload = prepare_doc_for_indexing(
            ticket, pipeline.embedder, pipeline.sparse_embedder
        )
        index_ticket(ticket["name"], dense_vector, sparse_vector, payload, pipeline.vector_store, cache)

    return {"indexed": len(tickets)}


@app.get("/tickets/{ticket_name}/matches", dependencies=[Depends(require_api_key)])
async def get_matches(ticket_name: str, background_tasks: BackgroundTasks) -> list[dict]:
    cache: MatchCache = app.state.match_cache
    refresher: BackgroundRefresher = app.state.refresher

    cached = cache.get(ticket_name)
    if cached is not None:
        if cached.stale:
            refresher.schedule(background_tasks, ticket_name)
        return cached.matches

    pipeline = app.state.pipeline
    try:
        matches = compute_matches(ticket_name, pipeline.helpdesk_client, pipeline.hybrid_search, pipeline.reranker)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=404, detail=f"Ticket {ticket_name} not found") from exc

    cache.put(ticket_name, matches)
    return matches
