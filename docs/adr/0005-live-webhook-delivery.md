---
status: implemented
---

# Wire up live Frappe webhook delivery for local dev

`ingestion/webhook_handler.py`'s `/webhook/helpdesk` route existed since Phase 1 but was never actually reachable from Helpdesk -- its own docstring called wiring live delivery "deployment work, deferred to a later phase." That meant new or updated tickets only entered the index via a manual `POST /ingest/full`, so the demo UI (ADR-adjacent, issue #3) couldn't show a ticket someone just created in Helpdesk as a Match candidate without a separate manual re-ingest step.

Decision: register a real Frappe `Webhook` document against the local Helpdesk dev instance, pointed at this service's `/webhook/helpdesk`, so ticket creates/updates are indexed live. Scripted as `scripts/register_webhook.py`, idempotent (deletes any existing webhook at the same URL before creating a fresh one), matching the project's existing pattern for seed/setup scripts.

## Two non-obvious gotchas, found by reading Frappe's own source

**Docker networking.** Helpdesk runs inside its own Docker Compose project (`ticket-rag-helpdesk-dev`); the API runs directly on the host machine for local dev. The webhook's `request_url` has to be `http://host.docker.internal:<port>/webhook/helpdesk`, not `http://localhost:<port>` -- from inside the Frappe container, `localhost` means the container itself. Verified reachable with `docker exec ... curl http://host.docker.internal:8001/health` before registering anything.

**Empty webhook body.** A `Webhook` document with neither `webhook_data` nor `webhook_json` set sends an empty `{}` body -- there's no "send the full document" fallback, contrary to what the ADR 0002-era assumption in `webhook_handler.py`'s docstring implied. Confirmed by reading `frappe/integrations/doctype/webhook/webhook.py`'s `get_webhook_data()`: `if webhook.webhook_data: ... elif webhook.webhook_json: ... ` -- no `else`. Fixed by setting `webhook_data: [{"fieldname": "name", "key": "name"}]`, which is also the *right* fix, not just a workaround: `handle_webhook()` only ever reads `payload["name"]` and refetches everything else via `HelpdeskClient.get_ticket()`, so sending the full document was never necessary.

The signature header (`X-Frappe-Webhook-Signature`) and HMAC-SHA256-over-the-raw-body scheme already implemented in `verify_signature()` matched Frappe's actual implementation exactly, confirmed by reading the same source file -- no changes needed there.

## Verification

Live end-to-end test against the real running stack (not simulated): created ticket `0126` directly via Helpdesk's REST API (same as a real user would), confirmed it appeared in `GET /tickets/queryable` immediately (that path was already live, no webhook involved), then resolved it via `PUT`. The webhook delivered `{"name": "0126"}`, `handle_webhook()` returned `200`, and the ticket appeared in Qdrant (`67 -> 68` points) without ever calling `/ingest/full`. Confirmed it was retrievable and had a sane vector via `GET /tickets/0126/matches` (correctly surfaced `0054`, a real `vpn-crash-after-update` cluster member, above threshold). Cleaned up afterward (deleted from both Helpdesk and Qdrant) to keep the corpus matching `seed_manifest.json`.

## Known limitation: deletion isn't synced

The registered webhook only covers `on_update`. Deleting a ticket in Helpdesk does **not** currently clean up its Qdrant point -- `on_trash` isn't a registered event, and `handle_webhook()` doesn't have a code path for "ticket no longer exists" (it calls `HelpdeskClient.get_ticket()` unconditionally, which would raise on a 404 rather than degrade to a delete-only cleanup). Discovered and worked around manually during verification (deleted the stray point by hand); not fixed here. Deliberate scope boundary, not an oversight -- the request that prompted this ADR was about *creation* visibility, and orphaned points are a real but separate problem worth its own pass (register a second `on_trash` webhook + a `handle_webhook()` branch that catches the 404 and just deletes).

## Consequences

Local dev only -- `config.API_WEBHOOK_URL`'s default (`host.docker.internal`) is meaningless outside this specific Docker-Helpdesk-plus-host-API setup. Production wiring (Contabo Helpdesk -> AWS EC2 API) needs its own equivalent registration step against the real Helpdesk instance once that exists, tracked in `docs/DEPLOYMENT_PLAN.md`, not solved here.

**Status: implemented.**
