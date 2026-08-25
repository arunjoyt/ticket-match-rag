---
status: proposed
---

# Accept Match cache staleness, drop the corpus_version read gate

ADR 0006 proposed gating every cache read on a `corpus_version` counter: `ticket_matches_cache` stores the version each row was computed against, and `get_matches` only serves a row when that version equals the index's current one -- otherwise it falls back to the live pipeline and re-caches the result. The stated reason was to decouple "is this correct" from "is this fast": a stale or missing entry degrades to slow-but-correct, never to fast-but-wrong.

Revisiting that one piece: the version check adds a real table (`corpus_version`), a bump on every index mutation, and a comparison on every read, all in service of a single property -- bounding staleness to zero. This system already tolerates the worker lagging arbitrarily behind the index (ADR 0006 puts no SLA on refresh latency, only "deduplicated, enqueued once per corpus-changing event"), so the question is whether a zero-staleness guarantee is worth that cost, versus accepting whatever staleness the worker's own lag already produces.

Decision: drop the version gate. `get_matches` serves a `ticket_matches_cache` row unconditionally once one exists, regardless of `computed_at`. The live pipeline still runs, but only when no row exists yet for that `ticket_name` -- a true miss, not a stale hit -- and its result is upserted into the cache before returning, exactly as ADR 0006 already specified for that branch. This supersedes only the version-gating mechanism in ADR 0006; the Postgres cache, the RQ worker, the `retrieval/indexing.py` choke point, and the Helpdesk bridge all stand as written there.

## Why drop it

The version check exists to answer one question: can this row be trusted? Once staleness itself is accepted rather than treated as a defect, that question has no useful answer left to give -- every row is trusted, so nothing reads `corpus_version` anymore. The table, the bump inside `index_ticket()` / `deindex_ticket()`, and the comparison inside `get_matches` all come out along with it. `ticket_matches_cache` keeps `computed_at`, retained for observability (how old is this row, useful for debugging or a future staleness dashboard), but nothing branches on it.

## What this costs

Staleness stops being bounded by a check and becomes bounded only by "whenever the worker next runs." ADR 0006's refresh trigger is purely event-driven -- the worker only runs when a corpus-changing event fires -- so there is no periodic refresh either. If the worker falls behind, crashes, or the queue backs up, agents see Matches computed against an old corpus indefinitely, with nothing surfacing that fact. This is a real regression from ADR 0006's stated goal ("worst case is exactly today's behavior; there is no code path where a wrong answer can be served") -- a stale Match is not "wrong" in the sense of a corrupted read, but it can genuinely mislead an agent: the best-fit past ticket for their Query Ticket may have entered the index after the cached row was computed, and they would never see it.

Accepted anyway, at this project's scale: seed-corpus size and query volume keep worker lag on the order of seconds to low minutes, not hours, and the cost of that window is a possibly-suboptimal-but-still-relevant Match, not a broken one -- the Match Threshold gate (CONTEXT.md) still applied to whatever got cached, so a stale row is never a low-confidence one shown just to fill the panel. It may simply not be the *most current* candidate.

## Why the live fallback survives for a true miss, and not for staleness

The fallback in ADR 0006's design does two jobs, not one: it is the freshness safety net this ADR removes, and it is also the only mechanism that creates a cache row for a ticket in the first place -- the `UPSERT` after a live-pipeline run is what a later `LOOKUP` finds. The worker's sweep (`refresh_all_open_tickets_cache()`) only covers whatever `list_open_tickets()` returns at the moment a *resolution* triggers it; a ticket created since the last sweep is not in that list yet, because creating a new open ticket touches nothing about the index. Dropping the fallback for a true miss as well as for staleness would leave a brand-new ticket with no Matches until some unrelated resolution happens to trigger the next sweep -- a worse gap than the staleness this ADR already accepts. Keeping the miss-only fallback closes it: the first read for a never-computed ticket pays full pipeline cost, identical to today's live system; every read after that is a cache hit.

## Considered alternative: bounded staleness

Instead of removing the version check outright, cap it -- serve a stale row if it is within some age or version-lag threshold, fall back to live beyond that. Rejected for now: it reintroduces exactly the complexity being removed here (a comparison, a threshold to tune) for a benefit that is hard to justify without production traffic data on actual worker lag. Worth revisiting once ADR 0006 is implemented and real lag is observable, not a decision to make speculatively.

## Consequences

`db/schema.sql` (ADR 0006, not yet written) drops the `corpus_version` table; `ticket_matches_cache` keeps `computed_at` only. `retrieval/indexing.py`'s `index_ticket()` / `deindex_ticket()` drop the version-bump step, keeping the Qdrant write and the deduplicated refresh-enqueue. `api/main.py`'s `get_matches` cache-aside branch simplifies to an existence check instead of a version comparison. `docs/PROPOSED_ARCHITECTURE.md` reflects this design as the current proposal. ADR 0006 is left as written and superseded on this one point, not edited, per this repo's convention of ADRs as a historical record rather than living documents.

**Status: proposed, not yet implemented.**
