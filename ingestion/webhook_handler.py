"""Incremental re-indexing via Frappe webhooks.

POST /webhook/helpdesk -- verifies HMAC-SHA256 signature, fetches the updated
ticket, and either re-indexes it (still eligible) or drops its Qdrant point
(no longer eligible, or the HD Ticket was trashed -- 404). on_trash is
registered alongside on_update; see ADR 0005's since-fixed "Known limitation".

For an ineligible ticket with no cached Matches yet -- a brand-new Query
Ticket -- the handler schedules a single-ticket cache populate (ADR 0011), so
the first agent to open it gets a cache hit instead of a cold live compute.

Fails closed: if WEBHOOK_SECRET is unset, requests are rejected outright,
never validated against an empty-string key. See
contract_intelligence_carryforward memory, item 2.

Wired up for local dev via scripts/register_webhook.py, which registers two
Frappe Webhooks (HD Ticket, on_update and on_trash -- Frappe's
webhook_docevent is single-select, so one document can't cover both) pointed
at this route -- see ADR 0005 for the setup and its non-obvious gotchas
(Frappe sends an empty body unless webhook_data is explicitly configured;
this endpoint only needs the ticket name in the payload since it refetches
the rest). Production wiring (Contabo Helpdesk -> AWS EC2 API) is separate,
tracked in docs/DEPLOYMENT_PLAN.md.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from requests.exceptions import HTTPError

from api.refresh import BackgroundRefresher
from db.cache import MatchCache
from ingestion.embedder import Embedder, SparseEmbedder, SparseVector, match_text
from ingestion.helpdesk_client import HelpdeskClient
from retrieval.indexing import deindex_ticket, index_ticket
from retrieval.vector_store import VectorStore

SIGNATURE_HEADER = "X-Frappe-Webhook-Signature"


def verify_signature(body: bytes, signature: str | None, secret: str | None) -> None:
    if not secret:
        raise HTTPException(status_code=500, detail="Webhook secret not configured")
    if not signature:
        raise HTTPException(status_code=401, detail="Missing webhook signature")
    expected = base64.b64encode(
        hmac.new(secret.encode(), body, hashlib.sha256).digest()
    ).decode()
    if not hmac.compare_digest(expected, signature):
        raise HTTPException(status_code=401, detail="Invalid webhook signature")


def prepare_doc_for_indexing(
    ticket: dict, embedder: Embedder, sparse_embedder: SparseEmbedder
) -> tuple[list[float], SparseVector, dict]:
    text = match_text(ticket["subject"], ticket["description"])
    dense_vector = embedder.embed_query(text)
    sparse_vector = sparse_embedder.embed_document(text)
    payload = {
        "ticket_name": ticket["name"],
        "subject": ticket["subject"],
        "description": ticket["description"],
        "resolution_details": ticket.get("resolution_details", ""),
        "match_text": text,
    }
    return dense_vector, sparse_vector, payload


def create_webhook_router(
    helpdesk_client: HelpdeskClient,
    embedder: Embedder,
    sparse_embedder: SparseEmbedder,
    vector_store: VectorStore,
    cache: MatchCache,
    refresher: BackgroundRefresher,
    webhook_secret: str | None,
) -> APIRouter:
    router = APIRouter()

    @router.post("/webhook/helpdesk")
    async def handle_webhook(request: Request, background_tasks: BackgroundTasks) -> dict[str, Any]:
        body = await request.body()
        verify_signature(body, request.headers.get(SIGNATURE_HEADER), webhook_secret)

        payload = await request.json()
        ticket_name = payload.get("name")
        if not ticket_name:
            raise HTTPException(status_code=400, detail="Missing ticket name in payload")

        try:
            ticket = helpdesk_client.get_ticket(ticket_name)
        except HTTPError as exc:
            if exc.response is not None and exc.response.status_code == 404:
                deindex_ticket(ticket_name, vector_store, cache)
                return {"status": "deleted", "ticket_name": ticket_name}
            raise

        if ticket.get("resolution_details"):
            dense_vector, sparse_vector, doc_payload = prepare_doc_for_indexing(ticket, embedder, sparse_embedder)
            # upsert overwrites the ticket's one deterministic point, so no delete-first
            index_ticket(ticket_name, dense_vector, sparse_vector, doc_payload, vector_store, cache)
            return {"status": "indexed", "ticket_name": ticket_name}

        # Ineligible: a Query Ticket, or a resolution that was cleared. Drop any
        # existing point; if the ticket has no cached Matches yet, populate its
        # row now rather than making the first agent open pay a cold live compute.
        deindex_ticket(ticket_name, vector_store, cache)
        if cache.get(ticket_name) is None:
            refresher.schedule(background_tasks, ticket_name)
        return {"status": "removed", "ticket_name": ticket_name}

    return router
