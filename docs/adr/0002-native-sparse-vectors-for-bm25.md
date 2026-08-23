---
status: proposed
---

# Move BM25 into Qdrant's native sparse vectors

Qdrant's dense-vector side is genuinely incremental — every ticket upsert/delete is a single `uuid5`-keyed point operation (`retrieval/vector_store.py`). The BM25 side is not: `retrieval/hybrid_search.py`'s `rebuild_bm25_from_store()` does a full corpus rescan, retokenize, and `BM25Okapi` reconstruction, and `ingestion/webhook_handler.py` calls it after *every* webhook event. This is cheap at Contract Intelligence's low-velocity, rebuild-is-rare cadence, but a real bottleneck at ticket-churn velocity as the corpus grows — the same ~100k-doc ceiling flagged in the `contract_intelligence_carryforward` memory.

Decision: replace `rank_bm25` + full-rebuild with Qdrant's native sparse vectors, upserted per-point exactly like the dense vectors already are — this removes the rebuild step from the codebase entirely rather than just making it cheaper.

## How it works

Sparse vectors come from `fastembed.SparseTextEmbedding("Qdrant/bm25")` — `.embed()` for documents (term-frequency + doc-length normalization, no corpus knowledge needed at encode time), `.query_embed()` for queries (raw term presence). The collection's sparse field is configured with `SparseVectorParams(modifier=Modifier.IDF)`, which makes Qdrant maintain corpus-wide IDF statistics **server-side, incrementally, as points are upserted/deleted** — that's the actual mechanism that eliminates the rebuild, not application code working around it. Querying uses `query_points` with `prefetch` (one dense, one sparse) fused server-side via `FusionQuery(fusion=Fusion.RRF)`, replacing the hand-rolled Python RRF loop.

Verified against the installed `qdrant-client==1.12.0` directly (two web sources disagreed on the fusion query class name — `FusionQuery` is correct, confirmed by introspecting the library, not just docs) and live-tested against `fastembed==0.8.0`.

## Considered alternative

Debounce/batch the existing rebuild instead of removing it (rebuild on a timer or after N pending changes, not per-event). Rejected: still an eventual full-corpus operation that gets more expensive as the corpus grows, just less frequently — doesn't fix the underlying scaling ceiling, only delays hitting it.

## Consequences

Breaking schema change: the current collection has an unnamed dense vector, incompatible with the new named `dense`/`bm25` vectors. Requires collection recreation + full re-ingest (same category as `config.py`'s existing note on `EMBEDDING_MODEL` changes) — no data loss risk since Helpdesk remains the source of truth and Qdrant is a rebuildable index. `retrieval/hybrid_search.py` loses essentially all its logic (`BM25Okapi`, manual RRF, the rebuild methods); `ingestion/webhook_handler.py` loses its `rebuild_bm25` callback parameter entirely.

**Status: proposed, not yet implemented.**
