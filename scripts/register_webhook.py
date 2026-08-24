"""Registers a Frappe Webhook so HD Ticket create/update events are
delivered live to this service's /webhook/helpdesk endpoint (see
ingestion/webhook_handler.py), instead of only picking up changes on the
next manual POST /ingest/full.

Idempotent: deletes any existing Webhook already pointed at
config.API_WEBHOOK_URL before creating a fresh one, so re-running this
after changing WEBHOOK_SECRET or the API's port doesn't leave stale
duplicates delivering with the wrong secret.

Local dev only -- see config.API_WEBHOOK_URL's docstring for why it targets
host.docker.internal. Requires the API (api/main.py) already reachable at
that address, since Frappe will start delivering events immediately once
this is registered.
"""

import json

import requests

import config

BASE_URL = "http://helpdesk.localhost:8000"
ADMIN_USER = "Administrator"
ADMIN_PASS = "admin"


def login(session: requests.Session) -> None:
    resp = session.post(f"{BASE_URL}/api/method/login", data={"usr": ADMIN_USER, "pwd": ADMIN_PASS})
    resp.raise_for_status()


def delete_existing(session: requests.Session) -> None:
    resp = session.get(
        f"{BASE_URL}/api/resource/Webhook",
        params={"filters": json.dumps([["request_url", "=", config.API_WEBHOOK_URL]])},
    )
    resp.raise_for_status()
    for row in resp.json()["data"]:
        del_resp = session.delete(f"{BASE_URL}/api/resource/Webhook/{row['name']}")
        del_resp.raise_for_status()
        print(f"Deleted existing webhook {row['name']}")


def create_webhook(session: requests.Session) -> str:
    resp = session.post(
        f"{BASE_URL}/api/resource/Webhook",
        json={
            "name": "ticket-match-rag-hd-ticket-on-update",  # Webhook uses Prompt autonaming, name is required
            "webhook_doctype": "HD Ticket",
            "webhook_docevent": "on_update",
            "request_url": config.API_WEBHOOK_URL,
            "request_method": "POST",
            # Frappe's Webhook only sends a body if webhook_data or webhook_json
            # is set -- an empty webhook (neither) sends an empty {} body, which
            # webhook_handler.py rejects. webhook_handler.py only needs the
            # ticket name; it refetches the rest via HelpdeskClient.get_ticket().
            "webhook_data": [{"fieldname": "name", "key": "name"}],
            "enable_security": 1,
            "webhook_secret": config.WEBHOOK_SECRET,
            "enabled": 1,
        },
    )
    resp.raise_for_status()
    return resp.json()["data"]["name"]


def main() -> None:
    if not config.WEBHOOK_SECRET:
        raise SystemExit("WEBHOOK_SECRET is not set -- webhook_handler.py fails closed without it.")

    session = requests.Session()
    login(session)
    delete_existing(session)
    name = create_webhook(session)
    print(f"Registered Webhook {name}: HD Ticket on_update -> {config.API_WEBHOOK_URL}")


if __name__ == "__main__":
    main()
