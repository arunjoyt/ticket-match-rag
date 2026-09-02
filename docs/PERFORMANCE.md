# Stress and performance test results

Implementer-facing detail: methodology, raw numbers, and how they tie back to specific architecture decisions. For a short, decision-facing version aimed at someone evaluating whether this system fits their requirements, see [PERFORMANCE_SUMMARY.md](PERFORMANCE_SUMMARY.md).

> **Measured against the pre-ADR-0011 design** (RQ worker + full open-ticket sweep). The read-path costs below (cache hit, true miss, component breakdown) carry over unchanged, but ADR 0011's lazy revalidation adds a new behaviour these runs never exercised: a stale read serves fast *and* schedules a single-ticket rerank in the API threadpool, so a burst of reads against a freshly-invalidated cache now competes for CPU in a way the old "serve cached, do nothing" path did not. Re-run `scripts/stress_test.py` and refresh this doc as part of issue #18.

`docs/ARCHITECTURE.md` already flags that every route in `api/main.py` is `async def` but nothing actually `await`s -- "a cache-miss request still blocks the process for the full live-pipeline duration... `async def` here is a FastAPI convention, not a concurrency guarantee." ADR 0011 makes cache-refresh work proportional to reads (one single-ticket compute per stale row observed). This document puts numbers on the `async def` claim, plus a cache-hit/miss baseline, a breakdown of what a cache-miss spends its time on, and the per-refresh cost.

**Tool:** `scripts/stress_test.py` (`python -m scripts.stress_test`) -- reruns anytime, e.g. after issue #6's prod deployment lands or issue #9's reranker fine-tuning changes the rerank cost measured here.

## Methodology

- **Stack:** local dev only (Qdrant, Postgres via `docker-compose.yml`; `uvicorn api.main:app`, single process, no `--workers`) -- this characterizes this project's dev machine, not a sized prod instance. (The original run also had a Redis + RQ worker, removed by ADR 0011.)
- **Machine:** Apple M1 (arm64), macOS 26.5.2. Inference is CPU-bound (`sentence-transformers`/`fastembed`, no GPU), so absolute numbers are machine-specific; the *shape* of the results (what dominates, how latency scales with concurrency) is the portable part.
- **Corpus:** 67 indexed (Reusable) tickets, 5 open (Query) tickets -- the seeded synthetic dataset (`data/seed_manifest.json`), not a production-scale corpus.
- **"Warm":** the API process has been running since before these tests; `retrieval/matching.py`'s `build_pipeline()` loads and warms every model at `_lifespan` startup, so section 1 and 2's numbers reflect steady-state, not cold start. Section 3 builds a *fresh* `Pipeline` inside the script itself, so its first sample pays a one-time model warm-up cost the long-running API process doesn't -- called out explicitly below, not hidden in the average.
- **Concurrency ramp caveat:** each level cycles 40 requests through the same 5-ticket pool with only ~3 tickets forced to miss at the start of that level. The API caches a miss's result on write (cache-aside), so within one level only the *first* occurrence of each forced-miss ticket is a true miss -- the rest are hits. This is deliberate: it approximates real traffic (mostly hits, per ADR 0007, with occasional misses) rather than an unrealistic "N independent slow computations at once." The finding below (latency scaling despite this) is if anything an understatement of the effect a heavier miss rate would produce.
- **Run-to-run variance:** every section was run twice to sanity-check reproducibility. Cache-miss/component timings varied by roughly 0.3-0.5s mean between runs (host load, not a bug) -- reported as ranges where it matters; concurrency-ramp *shape* and component *proportions* were consistent across both runs.

## 1. Cache-hit vs. cache-miss latency

Single `GET /tickets/{name}/matches` requests, 20 repetitions each (cache-miss forced by deleting the row via `db/cache.py`'s new `MatchCache.delete()` right before each request):

| | n | min | mean | p50 | p95 | max |
|---|---|---|---|---|---|---|
| cache-hit  | 20 | 0.009s | 0.010-0.013s | 0.010-0.012s | 0.014-0.020s | 0.014-0.024s |
| cache-miss | 20 | 0.315-0.340s | 0.345-0.486s | 0.347-0.391s | 0.371-1.383s | 0.371-2.809s |

A cache hit is a single Postgres lookup, consistently ~10ms -- a stale hit costs the same (it also serves from that lookup; the refresh it schedules runs after the response). A true miss runs the full pipeline and is **25-45x slower**, with a long tail (occasional multi-second outliers) -- keeping true misses rare (populate-on-create, ADR 0011) is what keeps that tail off the common path.

## 2. Concurrency ramp

40 requests per level, `ThreadPoolExecutor`, mixed hit/miss per the caveat above:

| concurrency | throughput | p50 | p95 | mean |
|---|---|---|---|---|
| 1  | 16-19 req/s | 0.009-0.012s | 0.34-0.40s | 0.05-0.06s |
| 5  | 26-30 req/s | 0.04-0.05s  | 1.01-1.19s | 0.17-0.19s |
| 10 | 26-30 req/s | 0.09-0.10s  | 1.09-1.24s | 0.33-0.37s |
| 20 | 26-28 req/s | 0.63-0.71s  | 1.21-1.33s | 0.67-0.73s |

**Throughput stays roughly flat (~26-30 req/s) from concurrency 5 upward, while p50 latency climbs 15-20x (0.04s to 0.65s) from concurrency 5 to 20.** That's the direct, empirical confirmation of `docs/ARCHITECTURE.md`'s theoretical claim: this single `uvicorn` process doesn't actually run requests concurrently. Under load, fast cache-hit requests queue up behind whichever slow cache-miss request is being processed -- most of the 40 requests per level are structurally cheap (cache hits), yet the *batch's* median latency still degrades sharply as more of them are in flight at once. The obvious fix is more `uvicorn` workers -- tested directly in section 6 below, with a more complicated answer than "yes" -- worth reading before assuming it's a solved problem for `docs/DEPLOYMENT_PLAN.md`'s Part B.

## 3. Component breakdown of a cache-miss call

Direct Python calls (not HTTP) timing each stage of `retrieval/matching.py`'s pipeline, 10 samples (5 tickets x 2 rounds):

| stage | steady-state (excl. first call) |
|---|---|
| embed (dense + sparse) | ~0.012-0.028s |
| Qdrant hybrid search | ~0.006-0.018s |
| cross-encoder rerank | ~0.25-0.31s |
| **total** | **~0.28-0.35s** |

**The cross-encoder rerank is 85-90% of a cache-miss's cost.** Embedding and Qdrant search are both effectively free at this corpus size (under 20ms combined). This directly informs issue #9 (fine-tuning the reranker): any latency cost of a larger/fine-tuned reranker model lands almost entirely on the tail this section measures, and any win from a *smaller* reranker (traded against issue #9's accuracy goal) would move this number directly.

The very first sample paid a one-time cost not present in the already-running API (`embed=0.51s, rerank=1.11s` vs. steady-state `~0.02s / ~0.27s`) -- `SentenceTransformer`/`fastembed`/`CrossEncoder` all load weights at construction (`build_pipeline()`), but evidently still pay extra on their first real inference call (thread-pool spin-up, JIT-ish warm-up). Confirms the methodology note above: this is a script artifact of building a fresh `Pipeline`, not something the long-running API process pays per request.

## 4. Background-refresh catch-up cost

ADR 0011 replaced ADR 0006's serial full sweep with per-ticket refreshes driven by reads: an index mutation flips every row's `stale` flag (one `UPDATE`, negligible), and each stale row is recomputed only when its ticket is next read. One refresh = one `compute_matches()` = the section-3 cost (~0.30s), run in the API threadpool after the response.

Cumulative CPU to reconcile N stale rows after one index change, using ~0.30s/ticket:

| stale rows reconciled | cumulative CPU |
|---|---|
| 50  | ~15s |
| 200 | ~60s |
| 500 | ~150s |

Same totals the old sweep would have spent -- but now spread across N separate reads over time, one refresh per read that observes a stale row, and **only for rows that are actually read**. A row for a ticket nobody opens is never recomputed. The old ceiling ("multi-minute sweep per corpus-changing event past the low hundreds of open tickets") is gone as a wall-clock event; what replaces it is per-read background load during a catch-up window, bounded by read volume. The `stress_test.py` re-run for issue #18 should measure that window directly (a burst of reads immediately after an `/ingest/full`).

## 5. 20 agents at once, cold cache

Section 2's ramp is a synthetic worst case (40 requests fired as fast as possible) -- it doesn't map cleanly onto "N helpdesk agents are online at once," since real agents arrive spread out over time, not in one burst. This section asks the sharper, realistic question directly: if 20 agents happened to load the ticket panel at the same moment, what would they actually feel? Two variants, both with every relevant cache row deleted first to force a genuinely cold cache:

| scenario | wall-clock (all complete) | median wait | worst-case wait |
|---|---|---|---|
| 20 requests over 5 shared uncached tickets | 2.44-2.52s | 2.32-2.44s | 2.42-2.52s |
| 20 truly distinct uncached tickets | 8.14-8.77s | 4.65-5.18s | 8.11-8.75s |

**These two numbers tell different stories.** In the shared-pool case, cost scales with *unique* tickets, not request count: because the server processes requests strictly one at a time, by the time it gets around to the 2nd, 3rd, or 4th request for the same ticket, an earlier one has usually already finished and cached the result -- only the first request per ticket actually pays the miss cost, the rest become cheap hits. The same serialization that causes the queueing delay also happens to prevent redundant recompute (no request coalescing needed to get that benefit here, it falls out of the single-process design for free).

The distinct-tickets case is the real worst case, and it does scale roughly linearly with agent count (~0.4s/ticket x 20 ~ 8s here, consistent with section 3's per-ticket cost): every agent in that window waits for every uncached ticket ahead of them in the queue, including agents whose *own* ticket would otherwise be a fast hit.

**When this actually happens:** not "20 agents working tickets" by itself. Under ADR 0011 a ticket's row is populated on creation and served on every read (stale or not), so in steady state an agent's panel is a fast hit regardless of how many agents are online. The real risk window is the first moments after a cold start / `POST /ingest/full`, when a cluster of agents could hit genuinely uncached tickets within the same few seconds -- plus a lighter version right after any index change, when the first read of each stale ticket also spawns a background rerank. Both are bounded, identifiable windows, not a standing capacity problem. See section 6 below for whether the obvious mitigation (more `uvicorn` workers) actually helps.

## 6. Multi-worker scaling test

Section 2's takeaway implied the obvious fix for the single-process bottleneck is `uvicorn --workers N` (or `gunicorn -k uvicorn.workers.UvicornWorker -w N`). Tested that directly rather than assuming it: `uvicorn api.main:app --workers 4` (`OMP_NUM_THREADS=2`, `MKL_NUM_THREADS=2` to avoid the more obvious failure mode of 4 processes each defaulting to all 8 CPU threads), each of the 4 worker processes warmed with 30-40 real requests first so none of them were paying the section-3-style first-inference tax mid-measurement.

**Result: throughput did not improve, and was sometimes lower, than the single-process baseline** -- 0.7-1.9 req/s across concurrency 1-40, versus the single process's steady ~2.5-2.6 req/s (both isolated runs, nothing else competing for CPU at the time).

Ruled out the two obvious confounds before accepting that result:
- **Not thread oversubscription** -- same result with and without the `OMP_NUM_THREADS`/`MKL_NUM_THREADS` limits.
- **Not the upstream Helpdesk dev server serializing `get_ticket()`** -- tested directly: 20 concurrent `get_ticket()` calls straight to Helpdesk complete in 0.38s, nowhere near the bottleneck.
- **Not the multi-worker plumbing itself** -- cache-hit-only traffic (pure Postgres lookup, no CPU-bound work) hit **82-146 req/s** with 4 workers, well above single-process throughput for the same traffic. The workers do run in parallel; it's specifically the CPU-bound compute path (embed + rerank) that doesn't scale across them here.

Most likely explanation, not confirmed further: memory-bandwidth or core contention specific to this test machine (Apple M1, 4 performance + 4 efficiency cores, not 8 uniform cores) -- four independent cross-encoder inference processes competing for the same limited fast-core/memory-bandwidth budget. A properly resourced multi-core server, not a laptop chip, is a plausible fix, but that's a hypothesis, not something measured here.

**Practical takeaway:** don't treat `--workers N` as a free, given fix for the concurrency finding above. It needs its own validation against real target hardware before a production deployment (issue #6) relies on it -- the honest current answer is "single-process behavior is well characterized; multi-process behavior on this hardware was tested and didn't help; multi-process behavior on production-class hardware is untested."

## Baseline: `POST /ingest/full`

One run, full re-index of all 67 tickets: **2.5s** (~38ms/ticket -- no rerank in this path, just embed + Qdrant upsert). Not load-tested; it's an admin/rare operation, not a hot path.

## Takeaways

1. **The single-process concurrency limitation is real, not theoretical**, and it affects *all* traffic (including cheap cache hits) whenever a slow request is in flight. In practice it's bounded to a specific window (a burst of uncached tickets, not "N agents" by itself, per section 5) rather than a standing problem -- but that window is real: ~8s worst-case wait for the last of 20 agents if it lands on a cluster of genuinely uncached tickets.
2. **The obvious fix (more `uvicorn` workers) is not a given -- tested, and it didn't help on this hardware** (section 6). Don't write "add workers" into issue #6's production deployment plan as a solved problem; it needs its own validation against production-class hardware.
3. **The cross-encoder rerank is the bottleneck**, by a wide margin (85-90% of a cache-miss). Issue #9's reranker fine-tuning should track this latency alongside its accuracy goals -- a slower model wins nothing if it makes miss latency and background-refresh cost meaningfully worse.
4. **ADR 0011 turned the sweep ceiling into a per-read cost.** There is no multi-minute wall-clock event on a corpus change any more -- just background refresh load spread across reads during a catch-up window, bounded by read volume. Re-run this script (issue #18) to measure that window: a burst of reads right after an `/ingest/full`.
5. **Cache-aside (ADR 0007) is doing real work**: a hit is ~25-45x faster than a miss, so keeping hits the common case (populate-on-create + serve-stale, ADR 0011) is load-bearing for the concurrency finding above, not just a staleness tradeoff.
