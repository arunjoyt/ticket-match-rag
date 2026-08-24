---
status: implemented
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

## Verification

Implemented per the plan above with no deviations. Verified against the eval harness (#2, `eval/run.py`) before/after: final-stage metrics (precision@5, MRR, nDCG@5) came out identical pre- and post-migration -- expected, since this is a storage/fusion-mechanism swap, not a retrieval-quality change. Retrieval-stage recall@20 dipped marginally (1.000 → 0.994 overall, one low-lexical-overlap ticket at the candidate-pool edge), attributable to native Qdrant RRF fusion mechanics differing slightly from the hand-rolled version. `scripts/calibrate_threshold.py`'s recommended `MATCH_THRESHOLD` was unchanged (-0.3623) after the migration, and the rerank scores on the #8 spot-check ticket were bit-for-bit identical -- confirming the reranker is unaffected by which retrieval/fusion mechanism feeds it, which also narrows #8's open investigation away from the fusion-mechanism hypothesis.

**Status: implemented.**
