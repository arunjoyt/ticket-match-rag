"""Central configuration, env-var driven.

No generation step anywhere in this pipeline, so no LLM API config here --
EMBEDDING_MODEL and RERANK_MODEL are both local sentence-transformers models,
loaded once and run in-process.
"""

from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv()

EMBEDDING_MODEL = os.environ.get("EMBEDDING_MODEL", "BAAI/bge-small-en-v1.5")
# cross-encoder/ms-marco-MiniLM-L-6-v2 was tried first and rejected -- see
# ADR 0004. It's trained on asymmetric search-query/passage pairs judged for
# topical relevance, a shape and objective mismatch for this project's
# symmetric ticket-vs-ticket Root-Cause Similarity judgment (CONTEXT.md).
# stsb-roberta-base (sentence-pair semantic equivalence) empirically won
# every metric tested against three alternatives, including two "drop
# reranking entirely" baselines that beat the old default outright.
RERANK_MODEL = os.environ.get("RERANK_MODEL", "cross-encoder/stsb-roberta-base")
SPARSE_MODEL = os.environ.get("SPARSE_MODEL", "Qdrant/bm25")

# Gates Match Threshold (see CONTEXT.md) -- calibrated against the seeded
# dataset's rerank score distribution via scripts/calibrate_threshold.py,
# optimizing F0.5 (precision-weighted, per CONTEXT.md's bias against
# low-confidence Matches). Recalibrated for stsb-roberta-base's 0-1 score
# scale (see ADR 0004) -- this value is meaningless for a different
# RERANK_MODEL and must be recalibrated if that changes, or if the corpus
# changes materially.
MATCH_THRESHOLD = float(os.environ.get("MATCH_THRESHOLD", "0.6221"))

QDRANT_URL = os.environ.get("QDRANT_URL", "http://localhost:6333")
QDRANT_COLLECTION = os.environ.get("QDRANT_COLLECTION", "tickets")

HELPDESK_URL = os.environ.get("HELPDESK_URL", "http://helpdesk.localhost:8000")
HELPDESK_API_KEY = os.environ.get("HELPDESK_API_KEY")
HELPDESK_API_SECRET = os.environ.get("HELPDESK_API_SECRET")

WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET")

# Where the demo UI (ui/app.py) reaches the FastAPI service.
API_BASE_URL = os.environ.get("API_BASE_URL", "http://localhost:8000")

# Where scripts/register_webhook.py points Helpdesk's Webhook doctype so it
# can deliver HD Ticket events to /webhook/helpdesk. host.docker.internal,
# not localhost -- Helpdesk runs inside its own Docker Compose project and
# has to reach the API on the host machine, not inside its own container.
# Production wiring (Contabo Helpdesk -> AWS EC2 API) is separate, tracked
# in DEPLOYMENT_PLAN.md -- this default is local-dev only.
API_WEBHOOK_URL = os.environ.get("API_WEBHOOK_URL", "http://host.docker.internal:8000/webhook/helpdesk")
