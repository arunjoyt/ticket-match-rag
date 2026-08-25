---
status: partially implemented
---

# Background-computed Matches, cached in Postgres, served natively inside Helpdesk

> **Partially superseded by [ADR 0007](0007-accept-match-cache-staleness.md):** the `corpus_version` read gate described below (point 1, "Corpus-version-gated cache") is dropped in favor of accepting cache staleness. Everything else here stands as written.

Every read today (`GET /tickets/{ticket_name}/matches`, called by both the standalone demo UI and, going forward, Helpdesk itself) runs the full pipeline from scratch: a live Helpdesk fetch, a dense embed, a sparse embed, a Qdrant hybrid query, and a full cross-encoder rerank pass over ~20 candidates. Fine at this corpus's scale, but the goal now is to showcase that this holds up at real production data volumes, and to show Matches where an agent actually works -- inside Helpdesk's own ticket view, not only a separate demo page.

Decision: precompute Matches in a background worker, cache them in Postgres keyed to a corpus-version counter, and serve Helpdesk's own (currently unimplemented) "similar tickets" UI slot from that cache via a Frappe API override. Three coordinated pieces:

## 1. Corpus-version-gated cache (Postgres)

Two tables:
- `corpus_version` -- single row, one integer, incremented every time the Qdrant index changes (any upsert or delete).
- `ticket_matches_cache` -- `ticket_name` (PK), `matches` (jsonb, same shape `get_matches` already returns), `corpus_version` (the version it was computed against), `computed_at`.

Read path in `api/main.py`'s `get_matches`, cache-aside: look up `ticket_matches_cache` for `ticket_name`; if found and its `corpus_version` equals the current one, return it; otherwise (miss or stale) run the existing live pipeline unchanged, upsert the result with the current version, and return it. This is the same pattern as an HTTP ETag or a materialized-view refresh trigger: a cached entry is only ever served when it's provably still valid, never on a time-based guess. Worst case (miss) is exactly today's behavior; there is no code path where a wrong answer can be served.

**Why not skip the version check and trust a background refresh to always keep the cache current:** the background refresh (below) is what makes cache hits the common case, but the version check is what makes them *safe* even if the refresh job is late, failed, or hasn't run yet. Decoupling "is this correct" from "is this fast" is the actual point of this design, not an afterthought.

**Why Postgres and not reusing Redis or Qdrant for this table:** the data is a plain key-value lookup with no relational structure, so relational-ness isn't the reason. It's about what each store is *for*. Qdrant already means "rebuildable index" (ADR 0002) -- overloading it as a generic cache store mixes concerns. Redis is used below purely as the RQ broker; sharing it with cache traffic risks a real conflict, not a hypothetical one: a cache wants entries evicted under memory pressure, a job queue cannot tolerate a pending job being evicted, and those two eviction policies don't coexist safely on one instance. Postgres also gets us durability without tuning Redis persistence (RDB/AOF), and free SQL inspectability (`SELECT * FROM ticket_matches_cache WHERE corpus_version < ...`) during development.

## 2. Background refresh (Redis + RQ)

Redis's only job is the RQ broker -- nothing else is stored there, specifically to avoid the eviction-policy conflict above.

RQ over Celery: this system needs exactly one background task type behind one broker, with no routing, no periodic scheduling beyond what the webhook already triggers, and no multi-broker flexibility. Celery's feature surface (canvases, routing, Beat) solves problems this project doesn't have; RQ is a plain queue on top of Redis with a much smaller mental model, and fits this codebase's existing preference for the right-sized tool over the maximal one.

Task: `refresh_all_open_tickets_cache()` -- fetches the current open-ticket list (`HelpdeskClient.list_open_tickets()`, already live) and recomputes+upserts a cache row for each, tagged with the *current* corpus version. Enqueued once per corpus-changing event, deduplicated (skip enqueueing if a refresh is already pending) so a burst of webhook events doesn't stack redundant full-refresh jobs.

**Why full recompute over every open ticket, not targeted invalidation of just the affected ones:** a newly-resolved ticket can become a better Match for *any* currently-open ticket, not a fixed subset -- there's no cheap, reliable way to know in advance which open tickets' cached answers a given corpus change affects without re-running retrieval anyway. This is a deliberate, ADR-0002-style accepted ceiling, not an oversight: full recompute is O(open tickets) per corpus change, which is fine at this project's scale and would need revisiting (e.g. targeted invalidation, or batching multiple corpus changes into one refresh) if open-ticket volume grew large enough for that cost to matter.

## 3. Single choke point for index mutations (`retrieval/indexing.py`)

New module wrapping every place the Qdrant index changes today (`ingestion/webhook_handler.py`'s upsert/delete, `api/main.py`'s `/ingest/full` loop): `index_ticket(...)` and `deindex_ticket(...)`, each doing, in order: (1) the `VectorStore` Qdrant write, (2) increment `corpus_version` in Postgres, (3) enqueue the RQ refresh task. Both existing call sites switch to calling these instead of `VectorStore` directly.

This exists specifically so the version bump can't be forgotten. A version increment that's scattered across call sites is exactly the kind of thing a future change silently drops -- and unlike a TTL cache's bounded staleness, a forgotten version bump means the cache is *permanently* wrong for that entry until something else happens to overwrite it. Centralizing it into one module makes "anything that changes the index also bumps the version" structural, not a convention someone has to remember.

## 4. Serving Helpdesk's own UI, not just the standalone demo

Frappe Helpdesk already ships a "Recent / Similar Tickets" section in its real agent ticket view (`desk/src/components/ticket-agent/TicketDetailsTab.vue`), fully wired end-to-end -- the Vue component, the `useTicket.ts` composable's `recentSimilarTickets` resource, the backend contract (`{recent_tickets: [...], similar_tickets: [...]}`, each item needing `subject`, `creation`, `name`, `status`). It's never been finished: `helpdesk/helpdesk/doctype/hd_ticket/api.py`'s `get_recent_similar_tickets()` hardcodes `similar_tickets = []` with the comment `# Update this with TextBlob or SQLite Vector Search`.

Decision: override that one whitelisted method via Frappe's standard `override_whitelisted_methods` hook, from a small new Frappe app in this repo (`frappe_bridge/ticket_match_bridge/`), installed onto the Helpdesk bench (`bench get-app <path> && bench install-app ticket_match_bridge`). The override calls this project's own `GET /tickets/{ticket_name}/matches` over HTTP -- the same endpoint the standalone demo UI already calls, now cache-backed -- and reshapes the response into the `{recent_tickets, similar_tickets}` contract the already-built frontend expects.

This is a materially different proposition than the "Option B" (real embedding inside Helpdesk's agent UI) rejected in the `open_decisions_resolved` memory in favor of a standalone demo: that rejection was specifically about the cost and fragility of forking/maintaining Helpdesk's own frontend against upstream changes. Overriding one backend method that was always a stub touches zero Vue code and carries none of that maintenance burden -- it's closer to filling in a documented gap than forking a UI. Revisiting the standalone-demo decision explicitly, not by drift: this ADR keeps `ui/app.py` as-is, unmodified and still useful as a lighter-weight way to demo the retrieval capability without the full stack (Postgres, Redis, worker, Helpdesk bridge app) running -- both UIs end up calling the same cache-backed endpoint, so neither is stale relative to the other.

**One deliberate exception to "zero Vue changes":** the existing stub's list-item template renders only `subject`, `creation`, `status` -- no room for the resolution text. Showing a Match without its resolution defeats CONTEXT.md's actual point ("the agent never opens the original ticket to find the fix"). This ADR includes one small, scoped edit to that list-item template to add a resolution snippet line -- not a restructuring of the UI, not a new component, one added line bound to a field the override already provides.

## Considered alternatives

**Redis for both the cache and the RQ broker, dropping Postgres.** Technically works -- Redis is a natural fit for the cache's actual (key-value, no joins) shape. Rejected because of the eviction-policy conflict above: a cache-tuned eviction policy on the same instance as pending jobs risks silently dropping a queued task under memory pressure, which is a correctness bug, not a performance tradeoff.

**SQLite instead of Postgres.** Would avoid adding a new service entirely. Rejected here specifically because the goal is showcasing a real production-shaped architecture, and SQLite's single-writer model is a real limitation once the API process and the RQ worker are both writing to the cache concurrently -- Postgres is the honest choice for that, not decoration.

**Targeted/incremental cache invalidation** (recompute only the open tickets plausibly affected by a given corpus change), instead of full recompute across all open tickets. Rejected: doing this correctly requires knowing which open tickets' candidate pools a change could affect, which is itself a retrieval problem -- there's no cheap proxy for it here, and a wrong guess reintroduces exactly the silent-staleness risk this whole design exists to avoid.

## Consequences

New services in `docker-compose.yml`: `postgres`, `redis`, `worker` (the RQ worker process). New config: `DATABASE_URL`, `REDIS_URL`. New dependencies: `rq`, `psycopg2-binary`. New repo content: `db/schema.sql` (the two tables), `retrieval/indexing.py`, `worker/tasks.py` + `worker/run.py`, `frappe_bridge/ticket_match_bridge/`. `api/main.py`'s `get_matches` gains the cache-aside branch; its external contract is unchanged, so both the standalone demo UI and the new Helpdesk override consume it identically.

Not addressed here: authentication on the API (issue #7, still open and now more relevant with a new external caller); production wiring of the Helpdesk-bridge-to-API path once a real Helpdesk instance exists (`docs/DEPLOYMENT_PLAN.md`, same local-dev-only caveat as ADR 0005's `host.docker.internal`).

## Verification

Points 1-3 (the Postgres cache, the RQ worker, and the `retrieval/indexing.py` choke point) implemented and verified end-to-end (issue #10); point 4 (the Frappe bridge app + Vue edit) deliberately deferred to a follow-up issue -- see ADR 0007, which also supersedes point 1's `corpus_version` gate.

One deviation from what's written above: `worker` was **not** added as a `docker-compose.yml` service. Locally the API already runs directly via `uvicorn` rather than containerized (the `Dockerfile` is for `docker-compose.prod.yml` only); the worker follows that same existing pattern and runs directly via `python -m worker.run`, not as a new compose service. Only `postgres` and `redis` (infra, like `qdrant`) were added to `docker-compose.yml`. Revisit when `docker-compose.prod.yml` is built (`docs/DEPLOYMENT_PLAN.md`).

**Status: partially implemented -- points 1-3 done, point 4 (Frappe bridge) deferred.**
