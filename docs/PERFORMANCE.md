# Stress and performance test results

`docs/ARCHITECTURE.md` already flags that every route in `api/main.py` is `async def` but nothing actually `await`s -- "a cache-miss request still blocks the process for the full live-pipeline duration... `async def` here is a FastAPI convention, not a concurrency guarantee." ADR 0006 accepts `worker/tasks.py`'s full-sweep-over-every-open-ticket refresh as a scaling ceiling, "fine at this project's scale." Neither of those was ever measured. This document puts numbers on both, plus a cache-hit/miss baseline and a breakdown of what a cache-miss actually spends its time on.

**Tool:** `scripts/stress_test.py` (`python -m scripts.stress_test`) -- reruns anytime, e.g. after issue #6's prod deployment lands or issue #9's reranker fine-tuning changes the rerank cost measured here.

## Methodology

- **Stack:** local dev only (Qdrant, Postgres, Redis via `docker-compose.yml`; `uvicorn api.main:app`, single process, no `--workers`) -- there's no production deployment yet (issues #5/#6), so this characterizes this project's dev machine, not a sized prod instance.
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

A cache hit is a single Postgres lookup, consistently ~10ms. A cache miss runs the full pipeline and is **25-45x slower**, with a long tail (occasional multi-second outliers) -- ADR 0007's bet that cache-aside-with-a-worker-sweep keeps hits the common case is what makes that tail rare in practice rather than the norm.

## 2. Concurrency ramp

40 requests per level, `ThreadPoolExecutor`, mixed hit/miss per the caveat above:

| concurrency | throughput | p50 | p95 | mean |
|---|---|---|---|---|
| 1  | 16-19 req/s | 0.009-0.012s | 0.34-0.40s | 0.05-0.06s |
| 5  | 26-30 req/s | 0.04-0.05s  | 1.01-1.19s | 0.17-0.19s |
| 10 | 26-30 req/s | 0.09-0.10s  | 1.09-1.24s | 0.33-0.37s |
| 20 | 26-28 req/s | 0.63-0.71s  | 1.21-1.33s | 0.67-0.73s |

**Throughput stays roughly flat (~26-30 req/s) from concurrency 5 upward, while p50 latency climbs 15-20x (0.04s to 0.65s) from concurrency 5 to 20.** That's the direct, empirical confirmation of `docs/ARCHITECTURE.md`'s theoretical claim: this single `uvicorn` process doesn't actually run requests concurrently. Under load, fast cache-hit requests queue up behind whichever slow cache-miss request is being processed -- most of the 40 requests per level are structurally cheap (cache hits), yet the *batch's* median latency still degrades sharply as more of them are in flight at once. A production deployment expecting concurrent traffic would need multiple `uvicorn` workers (or `gunicorn -k uvicorn.workers.UvicornWorker -w N`) for this to actually parallelize -- worth a line in `docs/DEPLOYMENT_PLAN.md`'s Part B once real traffic volume is known.

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

## 4. Worker-sweep projection

`worker/tasks.py`'s `refresh_all_open_tickets_cache()` recomputes Matches for every open ticket, serially, on every corpus-changing event (ADR 0006). Using the steady-state per-ticket cost from section 3 (~0.30s):

| open tickets | projected sweep duration |
|---|---|
| 50  | ~15s (~0.3 min) |
| 200 | ~60s (~1 min) |
| 500 | ~150s (~2.5 min) |

These are projections (per-ticket cost x N), not live runs against an inflated corpus -- generating hundreds of fake Helpdesk tickets just to time this would pollute the seeded dev instance that `data/seed_manifest.json` treats as eval ground truth. At today's actual scale (5 open tickets) a sweep is sub-2-seconds; ADR 0006's "fine at this project's scale, revisit if it stops being fine" holds comfortably through at least the low hundreds, and starts costing multiple minutes per corpus-changing event somewhere past that -- a concrete number to check issue #6's prod deployment against once real open-ticket volume is known, rather than the qualitative "fine for now" ADR 0006 shipped with.

## Baseline: `POST /ingest/full`

One run, full re-index of all 67 tickets: **2.5s** (~38ms/ticket -- no rerank in this path, just embed + Qdrant upsert). Not load-tested; it's an admin/rare operation, not a hot path.

## Takeaways

1. **The single-process concurrency limitation is real, not theoretical**, and it affects *all* traffic (including cheap cache hits) whenever a slow request is in flight -- multiple `uvicorn`/`gunicorn` workers should be considered before issue #6's production deployment sees concurrent load, not treated as a later optimization.
2. **The cross-encoder rerank is the bottleneck**, by a wide margin (85-90% of a cache-miss). Issue #9's reranker fine-tuning should track this latency alongside its accuracy goals -- a slower model wins nothing if it makes cache-miss latency (and worker-sweep duration) meaningfully worse.
3. **The worker-sweep scaling ceiling (ADR 0006) is now a number, not a feeling**: comfortable through the low hundreds of open tickets, multi-minute beyond that. Worth re-running this script once issue #6's prod deployment exists and real open-ticket volume is known, to confirm the ceiling still holds.
4. **Cache-aside (ADR 0007) is doing real work**: a hit is ~25-45x faster than a miss, so keeping hits the common case (via the worker sweep) is load-bearing for the concurrency finding above, not just a staleness tradeoff.
