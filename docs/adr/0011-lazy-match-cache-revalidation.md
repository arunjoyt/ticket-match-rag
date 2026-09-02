---
status: proposed
---

# Lazy Match-cache revalidation: drop the full sweep, drop RQ

[ADR 0006](0006-background-compute-and-helpdesk-integration.md) precomputes Matches in a background
RQ worker and caches them in Postgres; [ADR 0007](0007-accept-match-cache-staleness.md) then drops
the `corpus_version` read gate and accepts whatever staleness the worker's lag produces. The
mechanism that keeps cached rows warm under that accepted staleness is
`worker/tasks.py`'s `refresh_all_open_tickets_cache()`: on every index mutation it recomputes and
re-upserts a `ticket_matches_cache` row for *every* currently-open ticket, deduplicated to one
pending job per burst.

Revisiting that sweep now that ADR 0007's "staleness is fine" is settled. The sweep does
`O(open tickets)` full-pipeline recomputes (dense embed, sparse embed, Qdrant hybrid query,
cross-encoder rerank) on every corpus-changing event. Almost all of that work is thrown away: a
newly-resolved ticket is a pure *add* to the index, so it can only *enter* an open ticket's top-5
Matches or displace its current worst one — most open tickets' cached rows come out of the sweep
byte-for-byte unchanged. And the sweep recomputes rows for open tickets no agent is looking at,
whose freshness nobody observes. `docs/PERFORMANCE.md` projects this sweep at ~1 min for 200 open
tickets, ~2.5 min for 500 — a ceiling ADR 0006 explicitly flagged for revisiting "if open-ticket
volume grew large enough for that cost to matter."

There is also a positioning point. The sweep is what lets the system be described as serving
"agent lookups against a live-updating ticket index" — the sweep keeps the served cache tracking
the index closely. Dropping it makes the honest description "a live-updating index, plus a
precomputed Match cache reconciled lazily on read." The index stays live (webhook-driven
incremental indexing, unchanged); the *lookups* become reads of an eventually-consistent cache.
This is already true under ADR 0007 between an index change and the sweep completing — this ADR
widens that window and removes its upper bound in exchange for making refresh work strictly
proportional to reads.

## Decision

Replace the eager full sweep with lazy, per-ticket, read-driven revalidation, and remove the RQ
broker and worker process along with it. Five parts:

### 1. Corpus change marks rows stale, does not recompute them

`ticket_matches_cache` gains a `stale BOOLEAN NOT NULL DEFAULT false` column. The
`retrieval/indexing.py` choke point keeps its Qdrant write and, in place of enqueuing a refresh,
issues one `UPDATE ticket_matches_cache SET stale = true` — an `O(1)` statement regardless of how
many rows exist. `index_ticket()` / `deindex_ticket()` no longer take a queue.

### 2. Stale-while-revalidate read path

`GET /tickets/{ticket_name}/matches` (`api/main.py`):

- **No row (true miss):** run the live pipeline, upsert the result with `stale = false`, return it. Unchanged from ADR 0006/0007.
- **Fresh row (`stale = false`):** serve as-is. Unchanged.
- **Stale row (`stale = true`):** serve the row as-is *immediately*, and schedule a single-ticket refresh for this `ticket_name` only.

A stale hit is still served in one Postgres lookup; the requester never waits on a recompute. The
row the *current* reader sees is still stale — the refresh they triggered lands afterward, so it
is the *next* read of that ticket that sees fresh Matches.

### 3. Single-ticket populate on Query Ticket creation

The webhook handler already fires on `on_update` for new tickets (`scripts/register_webhook.py`).
When a webhook arrives for a ticket that is not eligible (no `resolution_details` — a Query Ticket,
CONTEXT.md) and has no cache row yet, schedule a single-ticket populate for it. This closes the
same gap ADR 0007 § "Why the live fallback survives for a true miss" identified: without it, the
first agent to open a newly-created ticket pays the full ~0.3–0.6 s live-pipeline cost. With it,
that ticket's row usually exists before anyone opens it.

### 4. No convergence sweep

A stale row is reconciled only when its ticket is next read. A ticket that is open but that no
agent opens keeps a stale row indefinitely — harmless, because nothing reads it. The first open of
a long-untouched ticket serves stale, then heals for the next open. Deliberately not adding a
background drain (see Considered alternatives).

### 5. Drop RQ, Redis, and the worker process

The remaining asynchronous work is two single-ticket `compute_matches()` calls — populate-on-create
and refresh-on-stale-read — each ~0.3–0.6 s, mostly cross-encoder rerank. Run them as FastAPI
`BackgroundTasks` in the API process, which already builds the full `Pipeline` (models loaded) in
its lifespan for the true-miss path. Deduplicate with a module-level `set[str]` of in-flight
ticket names plus a lock, replacing RQ's `job_id` dedup.

Removed: the `redis` service, the `worker` process, `rq` and `redis` from `requirements.txt`,
`REDIS_URL` from config, `worker/`, and the queue plumbing threaded through `api/main.py`,
`ingestion/webhook_handler.py`, and `retrieval/indexing.py`.

This supersedes ADR 0006 point 2 (background refresh via RQ) and the `refresh_all_open_tickets_cache`
full-sweep design in its entirety, and extends ADR 0007's miss-only live fallback with the
create-time populate in part 3. ADR 0006 points 1 (Postgres cache), 3 (the choke point — kept, now
marking stale instead of enqueuing), and 4 (the Helpdesk bridge) stand.

## Why lazy revalidation over the eager sweep

ADR 0007 already accepts that a served row may lag the index without bound. Once that is true, the
sweep's only remaining job is to make the lag *small in the common case* — and it pays
`O(open tickets)` per corpus change to do it, for every open ticket, whether or not anyone reads
that row before the next change. Read-driven revalidation pays for exactly the rows that are
observed, one at a time, and never recomputes a row nobody looks at. The total catch-up work after
one corpus change is still up to `O(open tickets)` in the worst case — but only if every open
ticket is actually opened before its row is next needed, and it is spread across real reads instead
of landing as one burst on the worker.

Marking every row stale on any index change (rather than tracking which rows a specific change
affects) is the same accepted-imprecision trade ADR 0006 made for the sweep: knowing which open
tickets a given corpus change affects is itself a retrieval problem. The stale flag is the
cheapest possible over-approximation — one `UPDATE`, and the cost of the imprecision is paid lazily
by whichever reads happen next.

## Why drop RQ rather than keep it with a smaller job

ADR 0006's reasoning for a separate worker process was the sweep: an `O(open tickets)` job that
could run for minutes needed isolation from the request path and a process where the models stay
resident. Neither argument survives the job shrinking to a single `compute_matches()` call. The
API process already loads the models. A `BackgroundTask` runs after the response is sent, so the
stale-fast guarantee holds; `sentence-transformers` releases the GIL during the torch forward
pass, so the background rerank contends for CPU with request handling for ~0.5 s rather than
blocking the event loop. Keeping RQ would mean keeping a broker, a second process, a second copy
of the embedding and reranker models in memory, and the queue plumbing, to buy crash isolation and
job durability that a passive suggestion panel with accepted staleness does not need.

## What this costs

**The reader who most needs the update is the one who does not get it.** Right after resolving a
ticket, an agent who opens a related open ticket sees that open ticket's *old* Matches — the newly
relevant one is absent until the refresh their own read triggered completes, i.e. until their
*next* open or a page reload. Under the current sweep this same agent would also usually see stale
Matches (the sweep races their read), but the sweep's window is seconds and this ADR's is
unbounded. This is the sharpest regression and the one to sanity-check against how agents actually
work a queue. Mitigations exist (part below) and are deferred.

**Rows for unopened tickets are never fresh.** With the sweep, every open ticket's row is current
within one sweep of any change. With this ADR, a row is only as fresh as the last read of its
ticket. The `computed_at` column (kept since ADR 0007 for observability) is the only signal of how
stale a given row is; nothing acts on it.

**Loss of process isolation and job durability.** A bug in refresh code now runs in the API
process and can take it down; RQ contained that. A refresh in flight during an API restart is
lost, and its row stays stale until the ticket is next opened — which is the same "no convergence"
behaviour part 4 already accepts, so this is a small incremental loss.

**Horizontal scale-out gets harder.** In-process `BackgroundTasks` and in-process dedup do not
survive multiple API replicas: two replicas could redundantly refresh the same ticket (wasteful,
not wrong), and `BackgroundTasks` do not survive a rolling deploy. `docs/PERFORMANCE.md` already
records that multi-process did not improve throughput on the test hardware and is not a proven
path, and the system is single-process today — but a shared queue (RQ, or Postgres
`SELECT ... FOR UPDATE SKIP LOCKED`) would be the honest re-introduction if scale-out becomes real.

## Considered alternatives

**Keep the sweep.** The status quo. Rejected: it is the `O(open tickets)`-per-change ceiling ADR
0006 flagged, spent largely on rows that are unchanged or unobserved, once ADR 0007 has already
conceded the freshness guarantee the sweep was buying.

**Targeted invalidation.** On resolve, search an index of *open-ticket* vectors for the newly
resolved ticket's nearest open neighbours and recompute only those rows; for edits/removals, also
recompute rows whose cached Match list already names the changed ticket (a `matches @> ...` lookup
with a GIN index). Correct and eager, and the right design at large scale. Rejected for now: it
needs a second Qdrant collection of open-ticket vectors kept in sync at the choke point, plus the
JSONB GIN index — real infrastructure for a benefit (bounded, eager freshness) this project's
scale and the passive-panel UX do not currently require. Worth its own ADR if open-ticket volume
or a freshness SLA makes eager reconciliation necessary.

**Keep RQ, shrink the job to `refresh_one_ticket(name)`.** Zero infrastructure change; swap one
function in `worker/tasks.py`. Rejected: keeps a broker, a process, and a second model load to buy
isolation/durability the workload no longer justifies (see "Why drop RQ" above). Reasonable
fallback if the in-process background-task approach causes trouble under load.

**Convergence drain — piggyback 1–2 stale rows per read.** On every read, also recompute a couple
of other `stale` rows (oldest `computed_at` first), so the stale backlog drains proportional to
traffic without a sweep or a timer. Rejected for v1: it adds read-path cost (up to 2 extra
single-ticket recomputes per read, the same worker-saturation concern scaled up) to buy "the first
open of any ticket is usually fresh too" — a property this project does not need. Cheap to add
later if agents report first-open staleness on tickets they have not been watching.

**Update the panel in place after the stale serve.** Return `{"matches": [...], "stale": <bool>}`
from the API, pass `stale` through the bridge, and have the Vue snippet re-fetch once or twice on
a short delay while `stale` is true, swapping fresh Matches in without a reload. Deferred, not
rejected: it is the mitigation for this ADR's sharpest cost, but it adds refetch logic against
Helpdesk's shared `recentSimilarTickets` resource — more of the fragile Vue-patch surface ADR 0008
and the bridge README already call out. Ship v1 as "fresh on next open"; add the single delayed
refetch if agents notice the lag.

## Consequences

- `db/schema.sql` — `ticket_matches_cache` gains `stale BOOLEAN NOT NULL DEFAULT false`.
- `db/cache.py` — `MatchCache` gains `mark_all_stale()` (one `UPDATE`) and a
  `refresh_one_ticket(name)` path that recomputes via the shared pipeline and writes `stale = false`;
  `put()` sets `stale = false`.
- `retrieval/indexing.py` — `index_ticket()` / `deindex_ticket()` drop the `queue` parameter and the
  enqueue; they call `cache.mark_all_stale()` after the Qdrant write. Still the sole choke point.
- `api/main.py` — `get_matches` gains the stale branch (serve + schedule refresh via
  `BackgroundTasks`); lifespan drops the `Queue` and its Redis connection; an in-process in-flight
  set + lock provides dedup.
- `ingestion/webhook_handler.py` — on a webhook for an ineligible ticket with no cache row, schedule
  a single-ticket populate; drop the `queue` wiring.
- `worker/`, `worker/run.py`, `worker/tasks.py` — deleted.
- `config.py` — `REDIS_URL` removed.
- `docker-compose.yml` — `redis` service removed. `docker-compose.prod.yml` — `redis` and `worker`
  services removed (6 services → 4: nginx, app, qdrant, postgres).
- `requirements.txt` — `rq`, `redis` removed.
- `.env.example` — `REDIS_URL` removed.
- `docs/ARCHITECTURE.md` — both diagrams and the "three asynchronous hops" prose rewritten: no RQ,
  no sweep; the async boundary is now Frappe's webhook dispatch plus in-process background tasks.
  The "every Qdrant mutation triggers a background refresh" framing becomes "every mutation marks
  the cache stale; reads reconcile lazily."
- `docs/PERFORMANCE.md` / `docs/PERFORMANCE_SUMMARY.md` — the worker-sweep projection section is
  replaced with the per-read revalidation cost; `scripts/stress_test.py`'s sweep-projection stage
  (section 4) is repointed at single-ticket refresh under read load.
- ADR 0006 and ADR 0007 are left as written and superseded on the points named above, per this
  repo's convention of ADRs as historical record.

Not addressed here: the in-place panel refresh (deferred, see Considered alternatives); targeted
invalidation (future ADR if scale demands it).

**Status: proposed.**
