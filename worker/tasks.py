"""RQ task: recompute and cache Matches for every open ticket (ADR 0006).

Full recompute over every open ticket, not targeted invalidation -- a
newly-resolved ticket can become a better Match for any currently-open
ticket, so there's no cheap way to know in advance which cached rows a given
corpus change affects. Accepted scaling ceiling at this project's size; see
ADR 0006's "Considered alternatives".

The Pipeline is a module-level singleton, built once per worker process and
reused across jobs -- see worker/run.py's docstring for why that requires
RQ's SimpleWorker rather than the default forking Worker.
"""

from __future__ import annotations

from db.cache import MatchCache
from retrieval.matching import Pipeline, build_pipeline, compute_matches

REFRESH_JOB_ID = "refresh_all_open_tickets_cache"

_pipeline: Pipeline | None = None


def _get_pipeline() -> Pipeline:
    global _pipeline
    if _pipeline is None:
        _pipeline = build_pipeline()
    return _pipeline


def refresh_all_open_tickets_cache() -> None:
    pipeline = _get_pipeline()
    cache = MatchCache()
    for ticket in pipeline.helpdesk_client.list_open_tickets():
        matches = compute_matches(
            ticket["name"], pipeline.helpdesk_client, pipeline.hybrid_search, pipeline.reranker
        )
        cache.put(ticket["name"], matches)
