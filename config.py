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
RERANK_MODEL = os.environ.get("RERANK_MODEL", "cross-encoder/ms-marco-MiniLM-L-6-v2")
SPARSE_MODEL = os.environ.get("SPARSE_MODEL", "Qdrant/bm25")

# Gates Match Threshold (see CONTEXT.md) -- calibrated against the seeded
# dataset's rerank score distribution via scripts/calibrate_threshold.py,
# optimizing F0.5 (precision-weighted, per CONTEXT.md's bias against
# low-confidence Matches). Not a fixed domain rule -- recalibrate if the
# corpus changes materially.
MATCH_THRESHOLD = float(os.environ.get("MATCH_THRESHOLD", "-0.3623"))

QDRANT_URL = os.environ.get("QDRANT_URL", "http://localhost:6333")
QDRANT_COLLECTION = os.environ.get("QDRANT_COLLECTION", "tickets")

HELPDESK_URL = os.environ.get("HELPDESK_URL", "http://helpdesk.localhost:8000")
HELPDESK_API_KEY = os.environ.get("HELPDESK_API_KEY")
HELPDESK_API_SECRET = os.environ.get("HELPDESK_API_SECRET")

WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET")
