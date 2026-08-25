---
status: implemented
---

# Add API-key auth to the retrieval API

Issue #7: every route in `api/main.py` was open — no auth at all, unlike Contract Intelligence's JWT-based per-user role gate on its `/query` endpoint. Not a problem for local dev, but it needed addressing before the AWS production deployment (issue #6) exposes it publicly.

## Decision

A single static shared-secret key (`API_KEY`), sent as `Authorization: Bearer <key>`, checked with `hmac.compare_digest` in a new `api/auth.py` — not Contract Intelligence's JWT/per-user-role pattern. That pattern solves a different problem: individual end users logging in with different permissions. This API has no such concept — every caller is a trusted backend or operator, not a human with an account:

- The Frappe bridge app (`ticket-match-bridge`, ADR 0008), calling `GET /tickets/{ticket}/matches` server-to-server from inside the Helpdesk bench.
- Whoever runs `POST /ingest/full` by hand (documented in `docs/DEPLOYMENT_PLAN.md`).

A single shared key matches that trust model, and matches this codebase's existing pattern for shared secrets — `WEBHOOK_SECRET`'s HMAC check in `ingestion/webhook_handler.py`. Fails closed the same way: an unset `API_KEY` rejects every protected request outright (500), never validated against an empty-string key (`contract_intelligence_carryforward` memory item 2).

**Routes covered:** `POST /ingest/full`, `GET /tickets/{ticket_name}/matches` — one mutates the index, the other exposes ticket data.

**Routes exempt:**
- `GET /health` — uptime checks and deployment verification curls need to work with no credentials.
- `POST /webhook/helpdesk` — already has its own auth (HMAC-SHA256 via `WEBHOOK_SECRET`, a different, already-solved mechanism); layering a second key on top of an already-verified caller adds nothing.

## A route removed along the way

Mapping every real HTTP caller of this API to scope this work surfaced that `GET /tickets/queryable` had exactly one caller: the standalone Streamlit demo UI (`ui/app.py`). See [ADR 0010](0010-retire-standalone-demo-ui.md) — that UI is retired as a direct consequence, and the route went with it rather than being gated for a caller that no longer exists.

## Consequences

`config.py` gains `API_KEY`. `.env.example` gets a blank `API_KEY=` entry, same convention as `WEBHOOK_SECRET=`. `docs/DEPLOYMENT_PLAN.md`'s bootstrap and verification steps now generate and pass the key. The bridge app reads it via `frappe.conf.get("ticket_match_api_key")` (new `bench set-config` entry) and sends it on every call.

## Verification

Local: with `API_KEY` unset, `POST /ingest/full` and `GET /tickets/{name}/matches` both returned 500; with it set, both returned 401 with no header and with a wrong key, 200 with the correct one; `GET /health` returned 200 unauthenticated throughout; `GET /tickets/queryable` returned 404 (removed). Bridge app: pulled the new commit into the dev bench's cloned copy (`git pull`), `bench --site helpdesk.localhost set-config ticket_match_api_key <key>`, restarted the dev container, then browser-verified ticket `0122`'s Helpdesk agent view still renders Similar Tickets correctly with the header now required and enforced end to end.

**Status: implemented.**
