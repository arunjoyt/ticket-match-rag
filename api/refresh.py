"""Background Match-cache refresh, scheduled off the API request path (ADR 0011).

Replaces ADR 0006's RQ worker. The two single-ticket jobs -- populate-on-create
(webhook) and refresh-on-stale-read (GET /tickets/{name}/matches) -- run as
FastAPI BackgroundTasks in the API process, which already holds the pipeline
models resident (api/main.py's lifespan). No broker, no second process, no
second model load.

An in-process set collapses concurrent requests that would schedule the same
refresh into one. It is process-local: with multiple API replicas two of them
could refresh the same ticket, which is wasteful but not wrong.
"""

from __future__ import annotations

import logging
import threading

from fastapi import BackgroundTasks

from db.cache import MatchCache
from retrieval.matching import Pipeline, refresh_ticket_cache

logger = logging.getLogger(__name__)


class BackgroundRefresher:
    def __init__(self, pipeline: Pipeline, cache: MatchCache) -> None:
        self._pipeline = pipeline
        self._cache = cache
        self._in_flight: set[str] = set()
        self._lock = threading.Lock()

    def schedule(self, background_tasks: BackgroundTasks, ticket_name: str) -> None:
        with self._lock:
            if ticket_name in self._in_flight:
                return
            self._in_flight.add(ticket_name)
        background_tasks.add_task(self._run, ticket_name)

    def _run(self, ticket_name: str) -> None:
        try:
            refresh_ticket_cache(ticket_name, self._pipeline, self._cache)
        except Exception:
            # Best-effort: the row stays as it was and heals on a later read.
            logger.exception("background Match-cache refresh failed for %s", ticket_name)
        finally:
            with self._lock:
                self._in_flight.discard(ticket_name)
