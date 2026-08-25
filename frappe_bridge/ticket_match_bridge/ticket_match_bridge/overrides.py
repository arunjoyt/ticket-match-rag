"""Overrides Helpdesk's stubbed get_recent_similar_tickets() (ADR 0006).

recent_tickets is untouched -- reuses Helpdesk's own get_recent_tickets().
similar_tickets calls Ticket Match RAG's already cache-backed
GET /tickets/{ticket_name}/matches and enriches each Match with the
status/priority/creation fields the frontend needs -- Frappe-owned ticket
metadata the retrieval API has no reason to know about (CONTEXT.md: Match).
"""

from __future__ import annotations

import frappe
import requests
from frappe.utils import strip_html_tags
from helpdesk.helpdesk.doctype.hd_ticket.api import get_recent_tickets

DEFAULT_API_URL = "http://host.docker.internal:8001"


@frappe.whitelist()
def get_recent_similar_tickets(ticket: str) -> dict:
    frappe.has_permission("HD Ticket", "read", str(ticket), throw=True)
    if not frappe.db.exists("HD Ticket", ticket):
        return {"recent_tickets": [], "similar_tickets": []}

    return {
        "recent_tickets": get_recent_tickets(ticket),
        "similar_tickets": _get_similar_tickets(ticket),
    }


def _get_similar_tickets(ticket: str) -> list[dict]:
    api_url = frappe.conf.get("ticket_match_api_url", DEFAULT_API_URL)
    try:
        response = requests.get(f"{api_url}/tickets/{ticket}/matches", timeout=10)
        response.raise_for_status()
        matches = response.json()
    except Exception:
        # A hiccup in the retrieval service degrades this one panel, not the
        # whole ticket view.
        frappe.log_error(title="Ticket Match Bridge: get_similar_tickets failed")
        return []

    if not matches:
        return []

    ticket_names = [m["ticket_name"] for m in matches]
    meta = {
        row.name: row
        for row in frappe.get_all(
            "HD Ticket",
            filters={"name": ["in", ticket_names]},
            fields=["name", "status", "priority", "creation"],
        )
    }
    return [
        {
            "name": m["ticket_name"],
            "subject": m["subject"],
            "resolution_details": strip_html_tags(m.get("resolution_details", "")),
            "status": meta[m["ticket_name"]].status,
            "priority": meta[m["ticket_name"]].priority,
            "creation": str(meta[m["ticket_name"]].creation),
        }
        for m in matches
        if m["ticket_name"] in meta
    ]
