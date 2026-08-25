"""Incremental re-indexing via Frappe webhooks.

POST /webhook/helpdesk -- verifies HMAC-SHA256 signature, fetches the
updated ticket, deletes its existing Qdrant point, and re-indexes if still
eligible. If the ticket no longer exists (HD Ticket was trashed), deletes
the Qdrant point and stops there -- see ADR 0005's "Known limitation"
section, since fixed: on_trash is now registered alongside on_update.

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

from fastapi import APIRouter, HTTPException, Request
from requests.exceptions import HTTPError
from rq import Queue

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
    queue: Queue,
    webhook_secret: str | None,
) -> APIRouter:
    router = APIRouter()

    @router.post("/webhook/helpdesk")
    async def handle_webhook(request: Request) -> dict[str, Any]:
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
                deindex_ticket(ticket_name, vector_store, queue)
                return {"status": "deleted", "ticket_name": ticket_name}
            raise

        deindex_ticket(ticket_name, vector_store, queue)

        if ticket.get("resolution_details"):
            dense_vector, sparse_vector, doc_payload = prepare_doc_for_indexing(ticket, embedder, sparse_embedder)
            index_ticket(ticket_name, dense_vector, sparse_vector, doc_payload, vector_store, queue)
            return {"status": "indexed", "ticket_name": ticket_name}

        return {"status": "removed", "ticket_name": ticket_name}

    return router
