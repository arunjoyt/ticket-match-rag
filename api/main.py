"""FastAPI service: ingest Helpdesk tickets, serve similarity Matches.

No generation step anywhere -- retrieve (hybrid search) -> rerank -> filter by
Match Threshold -> surface directly. See CONTEXT.md for the domain model.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException

import config
from ingestion.embedder import Embedder, match_text
from ingestion.helpdesk_client import HelpdeskClient
from ingestion.webhook_handler import create_webhook_router, prepare_doc_for_indexing
from retrieval.hybrid_search import HybridSearch
from retrieval.reranker import Reranker
from retrieval.vector_store import VectorStore

MAX_MATCHES = 5
CANDIDATE_POOL_SIZE = 20


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    embedder = Embedder()
    vector_store = VectorStore()
    vector_store.ensure_collection(embedder.dimension())
    hybrid_search = HybridSearch(embedder, vector_store)
    hybrid_search.rebuild_bm25_from_store()
    reranker = Reranker()
    reranker.warm_up()
    helpdesk_client = HelpdeskClient()

    app.state.embedder = embedder
    app.state.vector_store = vector_store
    app.state.hybrid_search = hybrid_search
    app.state.reranker = reranker
    app.state.helpdesk_client = helpdesk_client

    app.include_router(
        create_webhook_router(
            helpdesk_client=helpdesk_client,
            embedder=embedder,
            vector_store=vector_store,
            rebuild_bm25=hybrid_search.rebuild_bm25_from_store,
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
    helpdesk_client: HelpdeskClient = app.state.helpdesk_client
    embedder: Embedder = app.state.embedder
    vector_store: VectorStore = app.state.vector_store
    hybrid_search: HybridSearch = app.state.hybrid_search

    tickets = helpdesk_client.list_reusable_tickets()
    for ticket in tickets:
        vector, payload = prepare_doc_for_indexing(ticket, embedder)
        vector_store.upsert_ticket(ticket["name"], vector, payload)

    hybrid_search.rebuild_bm25_from_store()
    return {"indexed": len(tickets)}


@app.get("/tickets/queryable")
async def queryable_tickets() -> list[dict]:
    helpdesk_client: HelpdeskClient = app.state.helpdesk_client
    return helpdesk_client.list_open_tickets()


@app.get("/tickets/{ticket_name}/matches")
async def get_matches(ticket_name: str) -> list[dict]:
    helpdesk_client: HelpdeskClient = app.state.helpdesk_client
    hybrid_search: HybridSearch = app.state.hybrid_search
    reranker: Reranker = app.state.reranker

    try:
        ticket = helpdesk_client.get_ticket(ticket_name)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=404, detail=f"Ticket {ticket_name} not found") from exc

    query_text = match_text(ticket["subject"], ticket["description"])
    candidates = hybrid_search.search(query_text, top_k=CANDIDATE_POOL_SIZE)
    ranked = reranker.rerank(query_text, candidates)

    matches = [
        {**payload, "score": score}
        for payload, score in ranked
        if score >= config.MATCH_THRESHOLD and payload["ticket_name"] != ticket_name
    ]
    return matches[:MAX_MATCHES]
