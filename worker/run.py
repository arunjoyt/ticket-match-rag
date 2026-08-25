"""RQ worker entrypoint: `python -m worker.run`.

Uses SimpleWorker, not RQ's default forking Worker, on purpose: the default
Worker forks a fresh child process per job, which would reload the
embedding and reranker models (worker/tasks.py's Pipeline singleton) on
every single refresh -- expensive, and pointless when nothing about the
models changes between jobs. SimpleWorker runs jobs in-process, so the
Pipeline built by the first job stays warm for every job after it, matching
how api/main.py's lifespan already loads its models once, not per-request.
"""

from __future__ import annotations

from redis import Redis
from rq import Queue, SimpleWorker

import config

if __name__ == "__main__":
    redis_conn = Redis.from_url(config.REDIS_URL)
    queue = Queue("default", connection=redis_conn)
    SimpleWorker([queue], connection=redis_conn).work()
