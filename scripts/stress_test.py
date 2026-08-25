"""Stress and performance test for the retrieval API (docs/PERFORMANCE.md).

Puts numbers on tradeoffs this project already reasoned about but never
measured: docs/ARCHITECTURE.md notes every route is `async def` but nothing
actually `await`s, so a cache-miss request blocks the whole process -- this
measures how much. ADR 0006 accepts worker/tasks.py's full-sweep refresh as
a scaling ceiling "fine at this project's scale" -- this projects what that
ceiling actually is.

Four things, each printed as its own section:
  1. Single-request cache-hit vs. cache-miss latency (cache-miss forced by
     deleting specific rows via db.cache.MatchCache.delete()).
  2. Concurrency ramp: N concurrent GET /tickets/{name}/matches requests
     (mixed cache-hit/miss) at N = 1, 5, 10, 20, reporting p50/p95/p99 and
     throughput at each level.
  3. Component breakdown of one cache-miss call: embed vs. Qdrant search vs.
     cross-encoder rerank, via direct Python calls (not HTTP) using the same
     Pipeline retrieval/matching.py builds for the API and the worker.
  4. Worker-sweep projection: mean single-ticket compute_matches() cost,
     projected out to open-ticket counts beyond today's actual corpus size --
     a labeled projection, not a live run against inflated Helpdesk data.

Assumes the API is already running (models warm at process startup, per
retrieval/matching.py's build_pipeline()) and the corpus is already indexed.

Usage: python -m scripts.stress_test [--base-url http://localhost:8001]
Requires API_KEY in the environment (same value the running API was started
with) and DATABASE_URL reachable for the cache-miss/hit setup.
"""

from __future__ import annotations

import argparse
import statistics
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

import requests

import config
from db.cache import MatchCache
from ingestion.embedder import match_text
from retrieval.matching import build_pipeline, compute_matches

TICKET_POOL = ["0121", "0122", "0123", "0124", "0125"]
CONCURRENCY_LEVELS = [1, 5, 10, 20]
REQUESTS_PER_LEVEL = 40
SINGLE_REQUEST_REPEATS = 20
COMPONENT_BREAKDOWN_ROUNDS = 2  # x len(TICKET_POOL) samples
PROJECTED_OPEN_TICKET_COUNTS = [50, 200, 500]


@dataclass
class Timing:
    label: str
    seconds: float
    ok: bool


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://localhost:8001")
    args = parser.parse_args()

    if not config.API_KEY:
        raise SystemExit("API_KEY not set -- export the same value the running API was started with.")
    headers = {"Authorization": f"Bearer {config.API_KEY}"}
    cache = MatchCache()

    print("=" * 70)
    print("1. Single-request cache-hit vs. cache-miss latency")
    print("=" * 70)
    _report_hit_vs_miss(args.base_url, headers, cache)

    print()
    print("=" * 70)
    print("2. Concurrency ramp (mixed cache-hit/miss)")
    print("=" * 70)
    _report_concurrency_ramp(args.base_url, headers)

    print()
    print("=" * 70)
    print("3. Component breakdown of a cache-miss call")
    print("=" * 70)
    per_ticket_seconds = _report_component_breakdown(cache)

    print()
    print("=" * 70)
    print("4. Worker-sweep projection")
    print("=" * 70)
    _report_sweep_projection(per_ticket_seconds)


def _timed_get(url: str, headers: dict) -> Timing:
    start = time.perf_counter()
    try:
        resp = requests.get(url, headers=headers, timeout=60)
        ok = resp.status_code == 200
    except requests.RequestException:
        ok = False
    return Timing(label=url, seconds=time.perf_counter() - start, ok=ok)


def _report_hit_vs_miss(base_url: str, headers: dict, cache: MatchCache) -> None:
    ticket = TICKET_POOL[0]

    # Warm the cache once, then time N repeated hits against the same row.
    _timed_get(f"{base_url}/tickets/{ticket}/matches", headers)
    hits = [_timed_get(f"{base_url}/tickets/{ticket}/matches", headers) for _ in range(SINGLE_REQUEST_REPEATS)]

    # Force a genuine miss each time by deleting the row right before the request.
    misses = []
    for _ in range(SINGLE_REQUEST_REPEATS):
        cache.delete(ticket)
        misses.append(_timed_get(f"{base_url}/tickets/{ticket}/matches", headers))

    _print_stats("cache-hit ", [t.seconds for t in hits if t.ok])
    _print_stats("cache-miss", [t.seconds for t in misses if t.ok])


def _report_concurrency_ramp(base_url: str, headers: dict) -> None:
    cache = MatchCache()
    for n in CONCURRENCY_LEVELS:
        # Force half the pool to miss so every level exercises both paths.
        for ticket in TICKET_POOL[: len(TICKET_POOL) // 2 + 1]:
            cache.delete(ticket)

        urls = [f"{base_url}/tickets/{TICKET_POOL[i % len(TICKET_POOL)]}/matches" for i in range(REQUESTS_PER_LEVEL)]
        start = time.perf_counter()
        with ThreadPoolExecutor(max_workers=n) as pool:
            results = list(pool.map(lambda u: _timed_get(u, headers), urls))
        wall_seconds = time.perf_counter() - start

        ok_seconds = [t.seconds for t in results if t.ok]
        errors = sum(1 for t in results if not t.ok)
        throughput = len(results) / wall_seconds if wall_seconds else 0.0
        print(f"\nconcurrency={n} ({len(results)} requests, {errors} errors, {throughput:.1f} req/s)")
        _print_stats("  latency", ok_seconds)


def _report_component_breakdown(cache: MatchCache) -> float:
    pipeline = build_pipeline()

    embed_seconds, search_seconds, rerank_seconds, total_seconds = [], [], [], []
    for round_num in range(COMPONENT_BREAKDOWN_ROUNDS):
        for ticket_name in TICKET_POOL:
            cache.delete(ticket_name)
            ticket = pipeline.helpdesk_client.get_ticket(ticket_name)
            query_text = match_text(ticket["subject"], ticket["description"])

            t0 = time.perf_counter()
            dense_vector = pipeline.embedder.embed_query(query_text)
            sparse_vector = pipeline.sparse_embedder.embed_query(query_text)
            t1 = time.perf_counter()
            candidates = pipeline.vector_store.hybrid_search(dense_vector, sparse_vector, top_k=20)
            t2 = time.perf_counter()
            pipeline.reranker.rerank(query_text, candidates)
            t3 = time.perf_counter()

            embed_seconds.append(t1 - t0)
            search_seconds.append(t2 - t1)
            rerank_seconds.append(t3 - t2)
            total_seconds.append(t3 - t0)
            tag = " (first call, pays one-time model warm-up)" if round_num == 0 and ticket_name == TICKET_POOL[0] else ""
            print(f"  {ticket_name}: embed={t1 - t0:.3f}s search={t2 - t1:.3f}s rerank={t3 - t2:.3f}s total={t3 - t0:.3f}s{tag}")

    print()
    _print_stats("embed  (all)", embed_seconds)
    _print_stats("search (all)", search_seconds)
    _print_stats("rerank (all)", rerank_seconds)
    _print_stats("total  (all)", total_seconds)
    print("\nExcluding the first call (one-time embedder/reranker warm-up, not paid by the already-running API):")
    _print_stats("total  (steady-state)", total_seconds[1:])

    # Cross-check against the real compute_matches() path (embed+search+rerank+gate).
    cache.delete(TICKET_POOL[0])
    t0 = time.perf_counter()
    compute_matches(TICKET_POOL[0], pipeline.helpdesk_client, pipeline.hybrid_search, pipeline.reranker)
    print(f"\ncompute_matches() end-to-end (single call): {time.perf_counter() - t0:.3f}s")

    return statistics.mean(total_seconds[1:])


def _report_sweep_projection(per_ticket_seconds: float) -> None:
    print(f"Mean per-ticket compute_matches() cost: {per_ticket_seconds:.3f}s (from section 3, serial)")
    print("Projected refresh_all_open_tickets_cache() wall-clock time (worker/tasks.py, serial full sweep):")
    for n in PROJECTED_OPEN_TICKET_COUNTS:
        print(f"  {n:>4} open tickets: ~{per_ticket_seconds * n:.1f}s (~{per_ticket_seconds * n / 60:.1f} min)")


def _print_stats(label: str, seconds: list[float]) -> None:
    if not seconds:
        print(f"{label}: no successful samples")
        return
    ordered = sorted(seconds)
    p50 = statistics.median(ordered)
    p95 = ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))]
    p99 = ordered[min(len(ordered) - 1, int(len(ordered) * 0.99))]
    print(
        f"{label}: n={len(ordered)} min={ordered[0]:.3f}s mean={statistics.mean(ordered):.3f}s "
        f"p50={p50:.3f}s p95={p95:.3f}s p99={p99:.3f}s max={ordered[-1]:.3f}s"
    )


if __name__ == "__main__":
    main()
