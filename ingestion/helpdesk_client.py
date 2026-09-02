"""REST client for Frappe Helpdesk.

Token-authenticated (`Authorization: token <key>:<secret>`), not session-cookie
based -- this is how a real service integration authenticates against Frappe.

Ticket eligibility (Reusable Ticket, see CONTEXT.md / ADR 0001) is
content-based: a ticket is eligible once `resolution_details` is non-empty,
regardless of its `status` label, since `status` is a per-instance-configurable
Link field, not a fixed enum.
"""

from __future__ import annotations

import requests

import config

FIELDS = ["name", "subject", "description", "resolution_details", "status"]


class HelpdeskClient:
    def __init__(
        self,
        base_url: str = config.HELPDESK_URL,
        api_key: str | None = config.HELPDESK_API_KEY,
        api_secret: str | None = config.HELPDESK_API_SECRET,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._session = requests.Session()
        if api_key and api_secret:
            self._session.headers["Authorization"] = f"token {api_key}:{api_secret}"

    def get_ticket(self, name: str) -> dict:
        resp = self._session.get(f"{self._base_url}/api/resource/HD Ticket/{name}")
        resp.raise_for_status()
        return resp.json()["data"]

    def list_reusable_tickets(self) -> list[dict]:
        """Tickets eligible to be indexed: non-empty resolution_details (ADR 0001)."""
        tickets = self._list_all_tickets()
        return [t for t in tickets if t.get("resolution_details")]

    def _list_all_tickets(self) -> list[dict]:
        tickets: list[dict] = []
        start = 0
        page_size = 100
        while True:
            resp = self._session.get(
                f"{self._base_url}/api/resource/HD Ticket",
                params={
                    "fields": _json_field_list(),
                    "limit_start": start,
                    "limit_page_length": page_size,
                },
            )
            resp.raise_for_status()
            page = resp.json()["data"]
            tickets.extend(page)
            if len(page) < page_size:
                break
            start += page_size
        return tickets


def _json_field_list() -> str:
    import json

    return json.dumps(FIELDS)
